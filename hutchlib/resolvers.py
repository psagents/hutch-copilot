"""Resolvers: ask the authoritative source, return the answer with provenance.

This replaces the hand-maintained knowledge files
(``references/hutches/mfx.md``, ``references/beam-status-pvs.md``,
``analyze-data/references/sfx-analysis-defaults.md``) rather than reformatting
them. Those files were a cache of something authoritative, kept by hand, and
they went stale silently -- ``references/hutches/`` had exactly one file, and
``sfx-analysis-defaults.md`` opened by telling the reader not to trust it.

Evidence for doing it this way, from the sfx-hutch-copilot-integration
campaign: every value read from its source was right first time (detector
address from ``psana2 detinfo``, unit cell from ``CRYST1``), and every value a
human had pre-encoded into text was wrong (the mask path was missing its
``.mask`` extension; the unit-cell filter target was 2.2% off the observed
cell for the whole campaign).

Contract for every resolver here:

  1. Return ``{value, source, retrieved_at, ...}``. ``source`` is specific
     enough to re-run.
  2. Never fall back to a hardcoded literal. If the source is unreachable,
     raise -- the caller decides between explicitly-stale state and asking a
     human.
  3. Report which path produced the answer when there is a fallback chain.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from . import bridge, state

HAPPI_DB = "/cds/group/pcds/pyps/apps/hutch-python/device_config/db.json"
HUTCH_PYTHON = "/cds/group/pcds/pyps/apps/hutch-python"
PSCONDA = "/sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh"


class ResolveError(RuntimeError):
    """A source was reachable but could not answer. Never silently defaulted."""


def _record(value: Any, source: str, **extra: Any) -> dict:
    rec = {"value": value, "source": source, "retrieved_at": state.utcnow()}
    rec.update(extra)
    return rec


def persist(st: state.State, field: str, rec: dict, *, kind: str = state.RESOLVED) -> dict:
    """Write a resolver result into state, carrying its provenance."""
    return st.set(field, rec["value"], source=rec["source"], kind=kind)


# --------------------------------------------------------------------------
# happi: device inventory
# --------------------------------------------------------------------------
_DEVICES_SCRIPT = r"""
import json
with open(P["db"]) as fh:
    db = json.load(fh)
beamline = P["beamline"].upper()
rows = []
for entry in db.values():
    if (entry.get("beamline") or "").upper() != beamline:
        continue
    z = entry.get("z")
    try:
        z = float(z)
    except (TypeError, ValueError):
        z = float("inf")
    rows.append({
        "name": entry.get("name"),
        "prefix": entry.get("prefix"),
        "device_class": entry.get("device_class"),
        "z": None if z == float("inf") else z,
    })
rows.sort(key=lambda r: (r["z"] is None, r["z"] if r["z"] is not None else 0))
print(json.dumps(rows))
"""


def devices(hutch: str, *, timeout: int = bridge.T_QUERY) -> dict:
    """Full device inventory for a beamline, ordered along the beam (z).

    Source: the happi database, which *is* the device and PV registry. There is
    no reason for a skill to carry a device list in prose.
    """
    out = bridge.psdev_python(
        _DEVICES_SCRIPT, {"db": HAPPI_DB, "beamline": hutch}, timeout=timeout
    )
    try:
        rows = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ResolveError(f"happi query returned non-JSON: {out[:200]!r}") from exc
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
    """PV name for a device, composed from its happi prefix.

    Deliberately does NOT ship a device-class -> record-suffix table: that
    would be the stale-knowledge-file problem again. The prefix comes from
    happi; the suffix is supplied by the caller and echoed back so the composed
    name is visible and auditable.
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


def beampath(hutch: str, *, timeout: int = bridge.T_DEVICE) -> dict:
    """Live beampath state, including which devices are blocking.

    ``are-we-ready`` used to re-implement this determination in prose plus 75
    lines of inline Python embedded in markdown. ``lightpath`` already computes
    it; the fallback path is reported in ``method`` rather than taken silently.
    """
    code = _BEAMPATH_SCRIPT.format(hutch=hutch.strip().lower())
    res = bridge.exec_code(code, hutch=hutch, timeout=timeout)
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
        f"resolved:lightpath via {res['host']} ({report.get('method')})",
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
    """Detector aliases for a run, from psana2 itself.

    The campaign had to discover ``'jungfrau'`` this way because the contract
    did not state it; the query was right first time. This is the canonical
    answer -- never accept a detector alias from prose.
    """
    snippet = _DETNAMES_SNIPPET % {"exp": experiment, "run": int(run)}
    cmd = f"source {PSCONDA} >/dev/null 2>&1 && python -c {json.dumps(snippet)}"
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
def hutch_conf(hutch: str, *, timeout: int = bridge.T_QUERY) -> dict:
    """The hutch's conf.yml, source of the DAQ host and platform."""
    path = f"{HUTCH_PYTHON}/{hutch.strip().lower()}/conf.yml"
    text = bridge.psdev_read(path, timeout=timeout)
    parsed: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2).strip():
            parsed[m.group(1)] = m.group(2).strip()
    return _record(parsed, f"resolved:{path}", raw=text)


def daq(hutch: str, *, timeout: int = bridge.T_QUERY) -> dict:
    """Live DAQ state and configuration.

    One implementation for both ``take-run`` Phase 0/0b and ``are-we-ready``
    Phase H -- they previously described the same reads in two different voices.
    """
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
    res = bridge.exec_code(code, hutch=hutch, timeout=timeout)
    if not res["ok"]:
        raise ResolveError(f"DAQ query failed: {res.get('error') or res['output'][:200]}")
    try:
        payload = json.loads(res["output"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ResolveError(f"DAQ query returned non-JSON: {res['output'][:200]!r}") from exc
    return _record(payload, f"resolved:daq signals via {res['host']}")


def preset(hutch: str, device_name: str, *, timeout: int = bridge.T_QUERY) -> dict:
    """A motor's saved positions.

    Nominal setpoints belong here, not as literals in a checker script.
    ``check_beam_ready_mfx.py`` hardcodes ``mr1l4_homs.pitch ~= -562.035``;
    that number should come from a preset or from last-known-good state.
    """
    path = f"{HUTCH_PYTHON}/{hutch.strip().lower()}/presets/{device_name}.yml"
    text = bridge.psdev_read(path, timeout=timeout)
    return _record(text, f"resolved:{path}")


# --------------------------------------------------------------------------
# Local files that are authoritative in themselves
# --------------------------------------------------------------------------
def cell(pdb_path: str) -> dict:
    """Unit cell and space group from a PDB CRYST1 record.

    Never accept a hand-typed unit cell. In the campaign the CRYST1 value was
    correct and an independently-stated target was not.
    """
    with open(pdb_path) as fh:
        for line in fh:
            if line.startswith("CRYST1"):
                a, b, c = line[6:15], line[15:24], line[24:33]
                al, be, ga = line[33:40], line[40:47], line[47:54]
                sg = line[55:66].strip()
                nums = [float(x) for x in (a, b, c, al, be, ga)]
                # Format exactly as setup-cctbx-pipeline's read_cryst1 does
                # (plain str(float), so 90.0 not 90). That skill's validation
                # assertion greps for this string verbatim in the rendered YAML,
                # and this spelling is the one in production use. Note its own
                # docstring advertises "90 90 90", which the code does not
                # produce -- follow the code, not the docstring.
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
