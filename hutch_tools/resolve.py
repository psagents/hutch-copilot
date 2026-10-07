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
    T_QUERY,
    BridgeError,
    caget,
    daq_config,
    exec_code,
)
from .state import RESOLVED, State

HAPPI_DB = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
HUTCH_PYTHON = "/cds/group/pcds/pyps/apps/hutch-python"
PSCONDA = "/sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh"
PCDS_PYTHON = "/cds/group/pcds/pyps/conda/py312/envs/pcds-6.1.0/bin/python3.12"


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


def _lightpath_names(db_path: str, beamline: str) -> set[str]:
    """Names on this beamline that lightpath tracks, so check_hutch can defer."""
    try:
        with open(db_path) as fh:
            db = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolveError(f"cannot read happi db {db_path}: {exc}") from exc
    want = beamline.strip().upper()
    return {
        entry.get("name")
        for entry in db.values()
        if (entry.get("beamline") or "").upper() == want and entry.get("lightpath")
    }


#: happi device_class suffixes whose prefix addresses an EPICS motor record, so
#: `prefix + ".RBV"` is a real PV. Anything else is a composite device.
_MOTOR_CLASSES = ("IMS", "EpicsMotor", "Newport", "DelayNewport", "BeckhoffAxis", "PCDSMotorBase")


def _has_motor_record(row: dict) -> bool:
    return (row.get("device_class") or "").rsplit(".", 1)[-1] in _MOTOR_CLASSES


def _inventory(hutch: str) -> list[dict]:
    """The hutch's happi rows, or a ResolveError naming the likely mistake."""
    rows = _happi_rows(HAPPI_DB, hutch)
    if not rows:
        raise ResolveError(
            f"happi has no devices with beamline={hutch.upper()!r}. "
            f"Beamline names in db.json are UPPERCASE; check the hutch name."
        )
    return rows


def check_hutch(hutch: str, *, inventory_only: bool = False,
                timeout: int = T_QUERY) -> dict:
    """The hutch's own devices and where they are. Beam-path devices defer to check_beam.

    Returns one row per device carrying exactly one of ``value`` (a live
    readback), ``on_lightpath`` (ask ``check_beam``, which is authoritative for
    it) or ``needs_session`` (a composite device whose component suffixes live
    in its class, not its prefix).
    """
    rows = _inventory(hutch)
    source = f"resolved:happi {HAPPI_DB} beamline={hutch.upper()}"
    if inventory_only:
        return _record(rows, source, count=len(rows))

    lightpath_names = _lightpath_names(HAPPI_DB, hutch)
    readable = {r["name"]: r["prefix"] + ".RBV"
                for r in rows
                if r["name"] not in lightpath_names and _has_motor_record(r)}
    # One caget for the whole hutch. Individually, because caget exits non-zero
    # and prints nothing on stdout when ANY name in the batch is missing -- so a
    # single absent record would blank every other reading.
    values: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    for name, pvname in readable.items():
        try:
            values[name] = caget([pvname], timeout=timeout)[pvname]
        except BridgeError as exc:
            unreadable[name] = str(exc)

    out_rows = []
    for row in rows:
        entry = dict(row)
        if row["name"] in lightpath_names:
            entry["on_lightpath"] = True
        elif row["name"] in values:
            entry["value"] = values[row["name"]]
            entry["read_from"] = readable[row["name"]]
        elif row["name"] in unreadable:
            entry["unreadable"] = unreadable[row["name"]]
        else:
            # Composite ophyd device: the readable records are components named
            # by the device class, which happi does not record. Guessing a
            # suffix here would invent a PV, so stop and say so.
            entry["needs_session"] = True
        out_rows.append(entry)

    return _record(
        out_rows,
        source + f" + caget on {len(readable)} motor record(s)",
        count=len(out_rows),
        on_lightpath=len(lightpath_names & {r["name"] for r in rows}),
    )


def device(hutch: str, name: str, **kw: Any) -> dict:
    """One happi entry by name."""
    rows = _inventory(hutch)
    match = next((d for d in rows if d["name"] == name), None)
    if match is None:
        raise ResolveError(f"no happi device named {name!r} on {hutch.upper()}")
    return _record(
        match,
        f"resolved:happi {HAPPI_DB} beamline={hutch.upper()} name={name}",
    )


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
#: Runs under PCDS_PYTHON, not the tools' interpreter. %(…)s-formatted rather
#: than str.format: the body is full of dict literals.
_CHECK_BEAM_SNIPPET = '''
import collections, json, time
import happi
from lightpath import LightController
from lightpath.path import find_device_state

lc = LightController(happi.Client(path=%(db)r), endstations=[%(end)r])
# active_path, not beamlines[end]: the latter is a list of node names, so it has
# no .blocking_devices and len() on it is meaningless.
bp = lc.active_path(%(end)r)


def histogram():
    return collections.Counter(find_device_state(d)[0].name for d in bp.path)


# Channel Access connects asynchronously, and lightpath counts Disconnected as
# blocking -- correctly, it will not call an unreadable device clear. Reading
# immediately therefore reports every device as blocking, which looks like a
# facility-wide fault rather than an unfinished connection.
#
# Measured on mfx-daq, the MFX path goes from ~55/59 disconnected to 2/59 in
# under 6 s, twice, converging on the same end state. So wait a flat interval
# rather than trying to detect convergence: watching for a stable count reads
# as settled during the first seconds, when nothing has connected yet and the
# count is stable at its worst value.
time.sleep(%(settle)r)
states = histogram()
settled = states["Disconnected"] <= len(bp.path) // 2

blocking = [d.name for d in bp.blocking_devices]
imped = getattr(bp.impediment, "name", None)
rows = []
for dev in sorted(bp.path, key=lambda d: d.md.z):
    rows.append({
        "name": dev.name,
        "z": dev.md.z,
        "state": find_device_state(dev)[0].name,
        "blocking": dev.name in blocking,
    })
print("HUTCHLIB_JSON:" + json.dumps({
    "settled": settled,
    "states": dict(states),
    "devices": rows,
    "blocking": blocking,
    "impediment": imped,
    "unreadable": [r["name"] for r in rows
                   if r["state"] in ("Disconnected", "Unknown", "Error")],
    "minimum_transmission": bp.minimum_transmission,
    "method": "lightpath.BeamPath.blocking_devices (local)",
}))
'''


def check_beam(hutch: str, *, settle_s: float = 10.0, timeout: int = 180) -> dict:
    """Devices between the source and this endstation, which block beam, and the first impediment.

    Needs no hutch-python session: lightpath builds the path from the happi db
    and reads device state over Channel Access directly.
    """
    snippet = _CHECK_BEAM_SNIPPET % {
        "db": HAPPI_DB, "end": hutch.strip().upper(), "settle": float(settle_s),
    }
    try:
        proc = subprocess.run([PCDS_PYTHON, "-c", snippet],
                              capture_output=True, timeout=timeout)
    except OSError as exc:
        raise ResolveError(f"cannot run {PCDS_PYTHON}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ResolveError(f"lightpath did not answer within {timeout}s") from exc

    text = proc.stdout.decode("utf-8", "replace")
    marker = "HUTCHLIB_JSON:"
    if marker not in text:
        # No fallback: the obvious one (lc.devices) is every lightpath-active
        # device in the facility rather than this path, and quietly reporting
        # another beamline's devices is worse than failing.
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise ResolveError(
            f"lightpath could not compute the path to {hutch.upper()} "
            f"(rc={proc.returncode}): {err[-400:] or text[-400:]}"
        )
    # Split on the marker: lightpath logs to stdout, so the payload is not
    # necessarily the last line.
    report = json.loads(text.split(marker, 1)[1].splitlines()[0])
    return _record(
        report,
        f"resolved:lightpath via {PCDS_PYTHON} ({report['method']})",
        blocking=report["blocking"],
        settled=report["settled"],
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


#: Every DAQ state, from psdaq ControlDef.states. Anything outside this set is
#: not a state we failed to reach readiness in -- it is a string we could not
#: interpret (an 'ERROR: ...', a renamed state), which must read as unknown
#: rather than as a confident no.
_DAQ_STATES = ("reset", "unallocated", "allocated", "connected",
               "configured", "starting", "paused", "running")
#: Of those, the ones a run can be taken from. 'configured' is ready-to-begin;
#: starting/paused/running mean a run is already underway, also not "blocked".
_DAQ_READY_STATES = ("configured", "starting", "paused", "running")

#: Detector DRP processes, by the `-D <type>` their command carries. The ids
#: themselves are hutch-chosen names, so match on the driver instead.
_DRP_DETECTORS = ("jungfrau", "epix100", "epix10k", "ts", "wave8", "opal", "archon")


def _config_summary(hutch: str) -> dict:
    """Readiness-relevant fields from the DAQ config, not its 200 lines."""
    rec = daq_config(hutch)
    text = rec["content"]
    # Commented-out alternatives are everywhere in these files -- MFX carries
    # three dead feespecImg_0 variants and two qadc_0. Counting them would
    # overstate what is meant to be running.
    live = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]

    def scalar(name: str) -> str | None:
        m = re.search(rf'^{name}\s*=\s*["\']([^"\']+)', text, re.M)
        return m.group(1) if m else None

    summary: dict[str, Any] = {
        "path": rec["path"],
        "lines": rec["lines"],
        "collect_host": scalar("collect_host"),
        "data_dir": scalar("data_dir"),
        "station": None,
        "user": None,
    }
    m = re.search(r'^hutch,\s*station,\s*user\s*=\s*\(([^)]*)\)', text, re.M)
    if m:
        parts = [p.strip().strip('"\'') for p in m.group(1).split(",")]
        if len(parts) == 3:
            summary["station"], summary["user"] = parts[1], parts[2]

    # f-string templates (ami-node_{N}_{instance}) are reported verbatim:
    # expanding them means reimplementing the config's own loops, and a wrong
    # expansion is worse than an obviously-templated name.
    ids: list[str] = []
    dets: list[str] = []
    for line in live:
        m = re.search(r'id:\s*f?"([^"]+)"', line)
        if not m:
            continue
        ids.append(m.group(1))
        driver = re.search(r'-D\s*(\w+)', line)
        if driver and driver.group(1) in _DRP_DETECTORS:
            dets.append(m.group(1))
    summary["processes"] = len(ids)
    summary["process_ids"] = ids
    summary["detectors"] = dets

    # Files the config reads at startup. An unreadable epicsArch.txt is a real
    # readiness problem that no DAQ state field reports.
    summary["also_reads"] = {}
    for ref in dict.fromkeys(
            re.findall(r'["\'](/cds/[^"\']+\.(?:txt|json|ya?ml))["\']', text)):
        p = pathlib.Path(ref)
        try:
            summary["also_reads"][ref] = {
                "readable": True, "lines": p.read_text(errors="replace").count("\n") + 1}
        except OSError as exc:
            summary["also_reads"][ref] = {"readable": False, "error": str(exc)}
    return summary


#: A dict literal on ONE line, not a loop over eval(). The classifier checks
#: line by line and `out[key] = str(eval(expr))` is not provably read-only, so
#: the old snippet refused itself; spread over several lines, the closing `}))`
#: is not a recognised read either. Keeping it to one line means one line to
#: recognise -- and the gate stays strict for everything else.
_CHECK_DAQ_CODE = (
    "import json\n"
    "print(json.dumps({"
    "'state': str(daq.state), "
    "'config_alias': str(daq.config_alias_sig.get()), "
    "'experiment': str(daq.experiment_name_sig.get()), "
    "'recording': str(daq.recording_sig.get()), "
    "'run_number': str(get_run())"
    "}))\n"
)


def check_daq(hutch: str, *, experiment: str | None = None,
              timeout: int = T_QUERY) -> dict:
    """Is the DAQ able to record? State, config alias, experiment, recording flag, config.

    ``can_record`` is None when the DAQ state cannot be read: an unreachable
    DAQ is unknown, never a confident no. ``run_number`` and ``config`` are
    context, not part of the verdict -- whether data has already landed is not
    a readiness question, and a config we could not parse does not make the
    DAQ unready.
    """
    res = exec_code(_CHECK_DAQ_CODE, timeout=timeout)
    if not res["ok"]:
        raise ResolveError(f"DAQ query failed: {res.get('error') or res['output'][:200]}")
    try:
        payload = json.loads(res["output"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ResolveError(f"DAQ query returned non-JSON: {res['output'][:200]!r}") from exc

    state = (payload.get("state") or "").strip().lower()
    payload["can_record"] = (
        state in _DAQ_READY_STATES if state in _DAQ_STATES else None
    )
    if experiment and payload.get("experiment") not in (None, "", experiment):
        payload["experiment_mismatch"] = {
            "daq": payload["experiment"], "expected": experiment,
        }
    # Informational, and deliberately after can_record: an unparseable config
    # is worth reporting but is not a readiness failure, so it must not be able
    # to change the verdict.
    try:
        payload["config"] = _config_summary(hutch)
    except (BridgeError, OSError, re.error) as exc:
        payload["config"] = {"error": str(exc)}
    return _record(payload, "resolved:daq signals via the hutch-python session")


def preset(hutch: str, device_name: str, experiment: str | None = None) -> dict:
    """A motor's saved positions. Nominal setpoints come from here, never a literal.

    Presets live in ``presets/beamline/`` for the standing beamline ones and in
    ``presets/<experiment>/`` for per-experiment ones -- not directly under
    ``presets/``, which is where this used to look and never found anything.
    """
    root = f"{HUTCH_PYTHON}/{hutch.strip().lower()}/presets"
    subdirs = ["beamline"] + ([experiment] if experiment else [])
    tried = [f"{root}/{sub}/{device_name}.yml" for sub in subdirs]
    for path in tried:
        if pathlib.Path(path).is_file():
            return _record(_read_local(path), f"resolved:{path}")
    raise ResolveError(
        f"no preset for {device_name!r}: looked in {', '.join(tried)}. "
        f"Many devices have none -- ask the operator for the setpoint rather "
        f"than using a literal."
    )


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
_DOC = """hutch-resolve: check-beam | check-hutch | check-daq | device | pv |
    caget | conf | detectors | preset | cell | cat | skills | xtc2."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""

    ap, sub = parser("hutch-resolve", _DOC)
    ap.add_argument("--persist-to", nargs=2, metavar=("HUTCH", "EXPERIMENT"),
                    help="also write the result into that experiment's state")
    ap.add_argument("--field", default=None, help="state field to persist under")

    sub.add_parser("conf").add_argument("--hutch", required=True)

    beam_parser = sub.add_parser("check-beam", help="is beam reaching the hutch?")
    beam_parser.add_argument("--hutch", required=True)
    beam_parser.add_argument("--settle-s", type=float, default=10.0,
                             help="seconds to wait for Channel Access to settle")

    hutch_parser = sub.add_parser("check-hutch", help="are the hutch's devices placed?")
    hutch_parser.add_argument("--hutch", required=True)
    hutch_parser.add_argument("--inventory-only", action="store_true",
                              help="happi listing only, no live readback")

    daq_parser = sub.add_parser("check-daq", help="is the DAQ able to record?")
    daq_parser.add_argument("--hutch", required=True)
    daq_parser.add_argument("--experiment", default=None,
                            help="cross-check against the DAQ's own experiment")

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
    preset_parser.add_argument("--experiment", default=None,
                               help="also look in that experiment's preset dir")

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
    """hutch-resolve: check-beam | check-hutch | check-daq | device | pv |
    caget | conf | detectors | preset | cell | cat | skills | xtc2."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "check-hutch":
            rec = check_hutch(args.hutch, inventory_only=args.inventory_only)
        elif args.cmd == "device":
            rec = device(args.hutch, args.name)
        elif args.cmd == "pv":
            rec = pv(args.hutch, args.name, args.suffix)
        elif args.cmd == "caget":
            rec = pv_values(args.pv)
        elif args.cmd == "check-beam":
            rec = check_beam(args.hutch, settle_s=args.settle_s)
        elif args.cmd == "check-daq":
            rec = check_daq(args.hutch, experiment=args.experiment)
        elif args.cmd == "conf":
            rec = hutch_conf(args.hutch)
        elif args.cmd == "detectors":
            rec = detectors(args.experiment, args.run)
        elif args.cmd == "preset":
            rec = preset(args.hutch, args.device, args.experiment)
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
