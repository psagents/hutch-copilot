"""hutch-resolve: ask the authoritative source, return the answer with provenance.

Every resolver returns ``{value, source, retrieved_at, ...}`` with a ``source``
specific enough to re-run, never falls back to a hardcoded literal (it raises,
and the caller chooses between stale state and asking), and reports which path
produced the answer when there is a fallback chain.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import shutil
import subprocess
from typing import Any

from .common import UNREACHABLE, USAGE, fail, out, parser, utcnow, xtc2_dir, xtc2_files
from .daq import (
    T_DEVICE,
    T_QUERY,
    BridgeError,
    caget,
    exec_code,
)
from .state import RESOLVED, State

HAPPI_DB = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
HUTCH_PYTHON = "/cds/group/pcds/pyps/apps/hutch-python"
PSCONDA = "/sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh"


class ResolveError(RuntimeError):
    """A source was reachable but could not answer. Never silently defaulted."""


def _record(value: Any, source: str, **extra: Any) -> dict:
    rec = {"value": value, "source": source, "retrieved_at": utcnow()}
    rec.update(extra)
    return rec


def persist(st: State, field: str, rec: dict, *, kind: str = RESOLVED) -> dict:
    """Write a resolver result into state, carrying its provenance."""
    return st.set(field, rec["value"], source=rec["source"], kind=kind)


# --------------------------------------------------------------------------
# happi: device inventory
# --------------------------------------------------------------------------
def _read_local(path: str) -> str:
    """Read a file that is authoritative in itself. Raises rather than defaulting."""
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except OSError as exc:
        raise ResolveError(f"cannot read {path}: {exc}") from exc


def _happi_rows(db_path: str, beamline: str) -> list[dict]:
    """Devices on one beamline from the happi db, ordered along the beam (z).

    Reads the db file rather than importing happi: it is plain JSON, so this
    needs nothing beyond the standard library and works in any interpreter.
    """
    try:
        with open(db_path) as fh:
            db = json.load(fh)
    except OSError as exc:
        raise ResolveError(f"cannot read happi db {db_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolveError(f"happi db {db_path} is not valid JSON: {exc}") from exc

    beamline = beamline.strip().upper()
    rows = []
    for entry in db.values():
        if (entry.get("beamline") or "").upper() != beamline:
            continue
        try:
            z = float(entry.get("z"))
        except (TypeError, ValueError):
            z = None
        rows.append({
            "name": entry.get("name"),
            "prefix": entry.get("prefix"),
            "device_class": entry.get("device_class"),
            "z": z,
        })
    # Unknown z sorts last rather than at the origin: a device with no recorded
    # position is not at the front of the beamline.
    rows.sort(key=lambda r: (r["z"] is None, r["z"] if r["z"] is not None else 0))
    return rows


def devices(hutch: str) -> dict:
    """Full device inventory for a beamline, ordered along the beam (z)."""
    rows = _happi_rows(HAPPI_DB, hutch)
    if not rows:
        raise ResolveError(
            f"happi has no devices with beamline={hutch.upper()!r}. "
            f"Beamline names in db.json are UPPERCASE; check the hutch name."
        )
    return _record(rows, f"resolved:happi {HAPPI_DB} beamline={hutch.upper()}", count=len(rows))


def device(hutch: str, name: str, **kw: Any) -> dict:
    """One happi entry by name."""
    rec = devices(hutch, **kw)
    match = next((d for d in rec["value"] if d["name"] == name), None)
    if match is None:
        raise ResolveError(f"no happi device named {name!r} on {hutch.upper()}")
    return _record(match, rec["source"] + f" name={name}")


def pv(hutch: str, name: str, suffix: str = "", **kw: Any) -> dict:
    """PV name for a device: the happi prefix plus a caller-supplied suffix.

    No device-class -> suffix table ships here; the suffix is echoed back so
    the composed name stays auditable.
    """
    rec = device(hutch, name, **kw)
    prefix = rec["value"].get("prefix")
    if not prefix:
        raise ResolveError(f"happi entry {name!r} has no prefix")
    return _record(
        prefix + suffix,
        rec["source"] + (f" suffix={suffix}" if suffix else ""),
        prefix=prefix,
        device_class=rec["value"].get("device_class"),
    )


# --------------------------------------------------------------------------
# lightpath: what is actually blocking the beam
# --------------------------------------------------------------------------
_BEAMPATH_SCRIPT = """
import json
report = {"path": None, "blocking": [], "devices": [], "method": None}
try:
    from lightpath import LightController
    lc = LightController({hutch!r})
    try:
        # Preferred: lightpath computes blocking devices itself.
        bp = lc.beamlines[{hutch!r}.upper()]
        report["blocking"] = [d.name for d in bp.blocking_devices]
        report["method"] = "lightpath.BeamPath.blocking_devices"
    except Exception as exc:
        report["method"] = "lightpath.devices + inserted/removed (fallback: %s)" % exc
    for dev in lc.devices:
        row = {{"name": getattr(dev, "name", "?"), "state": "UNKNOWN"}}
        try:
            if dev.inserted:
                row["state"] = "IN-BEAM"
            elif dev.removed:
                row["state"] = "OUT"
        except Exception as exc:
            row["state"] = "ERROR: %s" % exc
        try:
            row["transmission"] = float(dev.transmission)
        except Exception:
            pass
        report["devices"].append(row)
    if not report["blocking"]:
        report["blocking"] = [d["name"] for d in report["devices"] if d["state"] == "IN-BEAM"]
except Exception as exc:
    report["error"] = "lightpath unavailable: %s" % exc
print(json.dumps(report))
"""


def beampath(hutch: str, *, timeout: int = T_DEVICE) -> dict:
    """Live beampath state, including which devices are blocking.

    ``lightpath`` computes it. Which path produced the answer is reported in
    ``method`` rather than taken silently.
    """
    code = _BEAMPATH_SCRIPT.format(hutch=hutch.strip().lower())
    res = exec_code(code, timeout=timeout)
    if not res["ok"]:
        raise ResolveError(f"beampath query failed: {res.get('error') or res['output'][:200]}")
    try:
        report = json.loads(res["output"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ResolveError(f"beampath returned non-JSON: {res['output'][:200]!r}") from exc
    if "error" in report:
        raise ResolveError(report["error"])
    return _record(
        report,
        f"resolved:lightpath via the hutch-python session ({report.get('method')})",
        blocking=report.get("blocking", []),
    )


# --------------------------------------------------------------------------
# psana2: detector aliases (runs locally on S3DF, no bridge needed)
# --------------------------------------------------------------------------
_DETNAMES_SNIPPET = """
import json, psana
ds = psana.DataSource(exp=%(exp)r, run=%(run)d)
run = next(ds.runs())
keys = sorted({k[0] for k in run.detinfo.keys()})
print("HUTCHLIB_JSON:" + json.dumps(keys))
"""


def detectors(experiment: str, run: int, *, timeout: int = 300) -> dict:
    """Detector aliases for a run, from psana2. Never accept an alias from prose."""
    snippet = _DETNAMES_SNIPPET % {"exp": experiment, "run": int(run)}
    # shlex.quote, not json.dumps: the latter escapes newlines, which bash does
    # not expand inside double quotes, so python would see a literal backslash-n.
    cmd = f"source {PSCONDA} >/dev/null 2>&1 && python -c {shlex.quote(snippet)}"
    if not shutil.which("bash"):  # pragma: no cover
        raise ResolveError("bash not available")
    proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace")
    marker = "HUTCHLIB_JSON:"
    if marker not in out:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise ResolveError(
            f"psana2 could not open {experiment} run {run} "
            f"(rc={proc.returncode}): {err[-400:] or out[-400:]}"
        )
    keys = json.loads(out.split(marker, 1)[1].splitlines()[0])
    area = [k for k in keys if not k.startswith(("timing", "control", "epics"))]
    return _record(
        keys,
        f"resolved:psana2 detinfo exp={experiment} run={run}",
        area_candidates=area,
    )


# --------------------------------------------------------------------------
# hutch-python config: DAQ host, load list
# --------------------------------------------------------------------------
def hutch_conf(hutch: str) -> dict:
    """The hutch's conf.yml, source of the DAQ host and platform."""
    path = f"{HUTCH_PYTHON}/{hutch.strip().lower()}/conf.yml"
    text = _read_local(path)
    parsed: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2).strip():
            parsed[m.group(1)] = m.group(2).strip()
    return _record(parsed, f"resolved:{path}", raw=text)


def daq(hutch: str, *, timeout: int = T_QUERY) -> dict:
    """Live DAQ state: state, config alias, experiment, recording flag, run number."""
    code = (
        "import json\n"
        "out = {}\n"
        "for key, expr in ("
        "  ('state', 'daq.state'),"
        "  ('config_alias', 'daq.config_alias_sig.get()'),"
        "  ('experiment', 'daq.experiment_name_sig.get()'),"
        "  ('recording', 'daq.recording_sig.get()'),"
        "  ('run_number', 'get_run()'),"
        "):\n"
        "    try:\n"
        "        out[key] = str(eval(expr))\n"
        "    except Exception as exc:\n"
        "        out[key] = 'ERROR: %s' % exc\n"
        "print(json.dumps(out))\n"
    )
    res = exec_code(code, timeout=timeout)
    if not res["ok"]:
        raise ResolveError(f"DAQ query failed: {res.get('error') or res['output'][:200]}")
    try:
        payload = json.loads(res["output"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ResolveError(f"DAQ query returned non-JSON: {res['output'][:200]!r}") from exc
    return _record(payload, "resolved:daq signals via the hutch-python session")


def preset(hutch: str, device_name: str) -> dict:
    """A motor's saved positions. Nominal setpoints come from here, never a literal."""
    path = f"{HUTCH_PYTHON}/{hutch.strip().lower()}/presets/{device_name}.yml"
    text = _read_local(path)
    return _record(text, f"resolved:{path}")


# --------------------------------------------------------------------------
# Local files that are authoritative in themselves
# --------------------------------------------------------------------------
def cell(pdb_path: str) -> dict:
    """Unit cell and space group from a PDB CRYST1 record. Never hand-type a cell."""
    with open(pdb_path) as fh:
        for line in fh:
            if line.startswith("CRYST1"):
                a, b, c = line[6:15], line[15:24], line[24:33]
                al, be, ga = line[33:40], line[40:47], line[47:54]
                sg = line[55:66].strip()
                nums = [float(x) for x in (a, b, c, al, be, ga)]
                # Plain str(float), so "90.0" not "90" -- the spelling downstream
                # phil validation greps for verbatim.
                return _record(
                    {
                        "unit_cell": " ".join(str(v) for v in nums),
                        "space_group": sg,
                        "a": nums[0], "b": nums[1], "c": nums[2],
                        "alpha": nums[3], "beta": nums[4], "gamma": nums[5],
                    },
                    f"resolved:CRYST1 {pdb_path}",
                )
    raise ResolveError(f"no CRYST1 record in {pdb_path}")


def xtc2(hutch: str, experiment: str, run: int) -> dict:
    """Has this run's data landed, and how much of it? Count and total size."""
    files = xtc2_files(hutch, experiment, run)
    total = sum(f.stat().st_size for f in files)
    return _record(
        {"present": bool(files), "count": len(files),
         "total_bytes": total, "total_human": f"{total / 1e9:.1f} GB",
         "files": [f.name for f in files]},
        f"resolved:ls {xtc2_dir(hutch, experiment)} run={int(run)}",
    )


def cds_file(path: str) -> dict:
    """Read any /cds file. Mounted locally on the hutch machines."""
    return _record(_read_local(path), f"resolved:{path}")


def pv_values(pvs: list[str], *, timeout: int = T_QUERY) -> dict:
    """Live PV values over Channel Access. Compose the name with ``pv()``."""
    return _record(
        caget(pvs, timeout=timeout),
        f"resolved:caget {' '.join(pvs)}",
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-resolve: devices | device | pv | caget | beampath | daq | conf |
    detectors | preset | cell | cat | skills | xtc2."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""

    ap, sub = parser("hutch-resolve", _DOC)
    ap.add_argument("--persist-to", nargs=2, metavar=("HUTCH", "EXPERIMENT"),
                    help="also write the result into that experiment's state")
    ap.add_argument("--field", default=None, help="state field to persist under")

    for name in ("devices", "beampath", "daq", "conf"):
        sub.add_parser(name).add_argument("--hutch", required=True)

    device_parser = sub.add_parser("device")
    device_parser.add_argument("--hutch", required=True)
    device_parser.add_argument("name")

    pv_parser = sub.add_parser("pv", help="compose a PV name from the happi prefix")
    pv_parser.add_argument("--hutch", required=True)
    pv_parser.add_argument("name")
    pv_parser.add_argument("--suffix", default="")

    # No --hutch: CA is read locally and reaches the IOCs from any hutch
    # machine, so the hutch was never part of the question.
    caget_parser = sub.add_parser("caget", help="read PVs over Channel Access")
    caget_parser.add_argument("pv", nargs="+")

    detectors_parser = sub.add_parser("detectors")
    detectors_parser.add_argument("--experiment", required=True)
    detectors_parser.add_argument("--run", required=True, type=int)

    preset_parser = sub.add_parser("preset")
    preset_parser.add_argument("--hutch", required=True)
    preset_parser.add_argument("device")

    sub.add_parser("cell").add_argument("pdb")

    skills_parser = sub.add_parser("skills", help="which skills are installed, and where")
    skills_parser.add_argument("name", nargs="?", default=None)

    xtc2_parser = sub.add_parser("xtc2", help="has this run's data landed on S3DF?")
    xtc2_parser.add_argument("--hutch", required=True)
    xtc2_parser.add_argument("--experiment", required=True)
    xtc2_parser.add_argument("--run", required=True, type=int)
    sub.add_parser("cat", help="read a /cds file").add_argument("path")

    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-resolve: devices | device | pv | caget | beampath | daq | conf |
    detectors | preset | cell | cat | skills | xtc2."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "devices":
            rec = devices(args.hutch)
        elif args.cmd == "device":
            rec = device(args.hutch, args.name)
        elif args.cmd == "pv":
            rec = pv(args.hutch, args.name, args.suffix)
        elif args.cmd == "caget":
            rec = pv_values(args.pv)
        elif args.cmd == "beampath":
            rec = beampath(args.hutch)
        elif args.cmd == "daq":
            rec = daq(args.hutch)
        elif args.cmd == "conf":
            rec = hutch_conf(args.hutch)
        elif args.cmd == "detectors":
            rec = detectors(args.experiment, args.run)
        elif args.cmd == "preset":
            rec = preset(args.hutch, args.device)
        elif args.cmd == "cell":
            rec = cell(args.pdb)
        elif args.cmd == "cat":
            rec = cds_file(args.path)
        elif args.cmd == "skills":
            rec = skills(args.name)
        elif args.cmd == "xtc2":
            rec = xtc2(args.hutch, args.experiment, args.run)
        else:
            return USAGE

        if args.persist_to:
            rec["persisted"] = persist(
                State(*args.persist_to), args.field or args.cmd, rec)
        return out(rec)
    except (ResolveError, BridgeError, OSError) as exc:
        return fail(exc, UNREACHABLE)


# --------------------------------------------------------------------------
# Which skills are installed
# --------------------------------------------------------------------------
#: Roots that hold Claude Code skills at LCLS, in precedence order. Overridable
#: with HUTCH_SKILL_ROOTS (colon-separated) for other sites and for tests.
DEFAULT_SKILL_ROOTS = (
    "~/.claude/skills",
    "/sdf/group/lcls/ds/dm/apps/dev/opencode/skills",
)


def skill_roots() -> list[pathlib.Path]:
    import os

    raw = os.environ.get("HUTCH_SKILL_ROOTS")
    roots = raw.split(":") if raw else list(DEFAULT_SKILL_ROOTS)
    return [pathlib.Path(r).expanduser() for r in roots if r]


def skills(name: str | None = None) -> dict:
    """Installed skills across every known root.

    A skill is a directory containing SKILL.md. The roots searched are returned
    even when nothing is found, so "not installed" and "looked in the wrong
    place" stay distinguishable.
    """
    roots = skill_roots()
    found: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if (entry / "SKILL.md").is_file() and entry.name not in found:
                found[entry.name] = str(root)

    source = "resolved:skill roots " + ", ".join(str(r) for r in roots)
    if name is not None:
        return _record(
            {"name": name, "installed": name in found, "root": found.get(name)},
            source + f" name={name}",
            roots_searched=[str(r) for r in roots],
        )
    return _record(found, source, count=len(found),
                   roots_searched=[str(r) for r in roots])
