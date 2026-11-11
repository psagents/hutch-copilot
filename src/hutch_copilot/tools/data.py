"""hutch-data: what has landed on disk, and what is in it.

The filesystem for XTC arrival and the DAQ generation (``.xtc2`` is LCLS-II,
``.xtc`` LCLS-I), psana2 or psana1 for detector aliases, and two file readers
whose contents are authoritative in themselves (a PDB CRYST1 record, a ``/cds``
file). Nothing here touches a live session or Channel Access.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
from typing import Any

from .common import (
    PSCONDA_BY_DAQ,
    UNREACHABLE,
    USAGE,
    external_path,
    fail,
    out,
    parser,
    resolve_daq,
    utcnow,
    xtc2_dir,
    xtc_files,
)


class DataError(RuntimeError):
    """A source was reachable but could not answer. Never silently defaulted."""


def _record(value: Any, source: str, **extra: Any) -> dict:
    rec = {"value": value, "source": source, "retrieved_at": utcnow()}
    rec.update(extra)
    return rec


def _read_local(path: str) -> str:
    """Read a file that is authoritative in itself. Raises rather than defaulting."""
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc


# --------------------------------------------------------------------------
# psana: detector aliases (runs locally on S3DF, no bridge needed)
# --------------------------------------------------------------------------
_DETNAMES_SNIPPET = {
    "lcls2": """
import json, psana
ds = psana.DataSource(exp=%(exp)r, run=%(run)d)
run = next(ds.runs())
keys = sorted({k[0] for k in run.detinfo.keys()})
print("HUTCHLIB_JSON:" + json.dumps(keys))
""",
    # psana1: DetNames() gives (full name, alias, user alias); the alias is what a
    # config names (epix100_1, not CxiSc1.0:Epix100a.1), the full name if it has none.
    "lcls1": """
import json, psana
ds = psana.DataSource("exp=%(exp)s:run=%(run)d:smd")
keys = sorted({(t[2] or t[1] or t[0]) for t in psana.DetNames()})
print("HUTCHLIB_JSON:" + json.dumps(keys))
""",
}


def detectors(experiment: str, run: int, *, hutch: str | None = None,
              daq: str | None = None, timeout: int = 300) -> dict:
    """Detector aliases for a run, from psana. Never accept an alias from prose.

    ``daq`` picks psana2 (``lcls2``) or psana1 (``lcls1``); unset, it is resolved
    from the run's files. Neither on disk is a DataError, not a guess.
    """
    hutch = hutch or experiment[:3]
    daq = daq or resolve_daq(hutch, experiment, run)
    if daq not in _DETNAMES_SNIPPET:
        raise DataError(f"no .xtc or .xtc2 for {experiment} run {run}: cannot tell "
                        f"psana1 from psana2 -- pass --daq lcls1|lcls2")
    snippet = _DETNAMES_SNIPPET[daq] % {"exp": experiment, "run": int(run)}
    tool = "psana2" if daq == "lcls2" else "psana1"
    psconda = external_path(PSCONDA_BY_DAQ[daq])
    # shlex.quote, not json.dumps: the latter escapes newlines, which bash does
    # not expand inside double quotes, so python would see a literal backslash-n.
    cmd = f"source {psconda} >/dev/null 2>&1 && python -c {shlex.quote(snippet)}"
    if not shutil.which("bash"):  # pragma: no cover
        raise DataError("bash not available")
    proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, timeout=timeout)
    stdout = proc.stdout.decode("utf-8", "replace")
    marker = "HUTCHLIB_JSON:"
    if marker not in stdout:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise DataError(
            f"{tool} could not open {experiment} run {run} "
            f"(rc={proc.returncode}): {err[-400:] or stdout[-400:]}"
        )
    keys = json.loads(stdout.split(marker, 1)[1].splitlines()[0])
    area = [k for k in keys if not k.startswith(("timing", "control", "epics"))]
    how = "detinfo" if daq == "lcls2" else "DetNames"
    return _record(
        keys,
        f"resolved:{tool} {how} exp={experiment} run={run}",
        area_candidates=area, daq=daq,
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
    raise DataError(f"no CRYST1 record in {pdb_path}")


def xtc(hutch: str, experiment: str, run: int) -> dict:
    """Has this run's data landed, and how much of it? Count, size, and ``daq``.

    Counts LCLS-I ``.xtc`` and LCLS-II ``.xtc2`` alike: counting ``.xtc2`` only
    reported every LCLS-I run as absent (cxi101933627, 2026-11).
    """
    files = xtc_files(hutch, experiment, run)
    total = sum(f.stat().st_size for f in files)
    return _record(
        {"present": bool(files), "count": len(files),
         "total_bytes": total, "total_human": f"{total / 1e9:.1f} GB",
         "files": [f.name for f in files],
         "daq": resolve_daq(hutch, experiment, run)},
        f"resolved:ls {xtc2_dir(hutch, experiment)} run={int(run)}",
    )


#: Old name, kept so existing calls and docs keep working. Counts both suffixes now.
xtc2 = xtc


def daq(hutch: str, experiment: str, run: int | None = None) -> dict:
    """LCLS-I or LCLS-II, from the files the DAQ wrote. None until a run has landed."""
    value = resolve_daq(hutch, experiment, run)
    where = f"{xtc2_dir(hutch, experiment)}" + (f" run={int(run)}" if run is not None else "")
    extra = {} if value else {"note": "no .xtc or .xtc2 yet: ask the operator, never assume"}
    return _record(value, f"resolved:file suffixes in {where} (.xtc2 lcls2, .xtc lcls1)",
                   **extra)


def cds_file(path: str) -> dict:
    """Read any /cds file. Mounted locally on the hutch machines."""
    return _record(_read_local(path), f"resolved:{path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-data: daq | detectors | xtc (alias xtc2) | cell | cat."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-data", _DOC)

    daq_parser = sub.add_parser("daq", help="LCLS-I or LCLS-II, from the XTC files on disk")
    daq_parser.add_argument("--hutch", required=True)
    daq_parser.add_argument("--experiment", required=True)
    daq_parser.add_argument("--run", type=int, help="one run's files; default any in xtc/")

    detectors_parser = sub.add_parser("detectors", help="detector aliases for a run")
    detectors_parser.add_argument("--experiment", required=True)
    detectors_parser.add_argument("--run", required=True, type=int)
    detectors_parser.add_argument("--hutch", help="default: first 3 chars of experiment")
    detectors_parser.add_argument("--daq", choices=("lcls1", "lcls2"),
                                  help="psana1 or psana2; default: from the run's files")

    for name in ("xtc", "xtc2"):
        p = sub.add_parser(name, help="has this run's data landed on S3DF? (.xtc and .xtc2)"
                           + (" -- alias of xtc" if name == "xtc2" else ""))
        p.add_argument("--hutch", required=True)
        p.add_argument("--experiment", required=True)
        p.add_argument("--run", required=True, type=int)

    sub.add_parser("cell", help="unit cell from a PDB CRYST1 record").add_argument("pdb")
    sub.add_parser("cat", help="read a /cds file").add_argument("path")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-data: daq | detectors | xtc (alias xtc2) | cell | cat."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "daq":
            rec = daq(args.hutch, args.experiment, args.run)
        elif args.cmd == "detectors":
            rec = detectors(args.experiment, args.run, hutch=args.hutch, daq=args.daq)
        elif args.cmd in ("xtc", "xtc2"):
            rec = xtc(args.hutch, args.experiment, args.run)
        elif args.cmd == "cell":
            rec = cell(args.pdb)
        elif args.cmd == "cat":
            rec = cds_file(args.path)
        else:
            return USAGE
        return out(rec)
    except (DataError, OSError) as exc:
        return fail(exc, UNREACHABLE)
