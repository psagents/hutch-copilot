"""hutch-data: what has landed on disk, and what is in it.

psana2 for detector aliases, the filesystem for XTC2 arrival, and two file
readers whose contents are authoritative in themselves (a PDB CRYST1 record, a
``/cds`` file). Nothing here touches a live session or Channel Access.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
from typing import Any

from .common import (
    UNREACHABLE,
    USAGE,
    external_path,
    fail,
    out,
    parser,
    utcnow,
    xtc2_dir,
    xtc2_files,
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
    psconda = external_path("PSCONDA")
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
            f"psana2 could not open {experiment} run {run} "
            f"(rc={proc.returncode}): {err[-400:] or stdout[-400:]}"
        )
    keys = json.loads(stdout.split(marker, 1)[1].splitlines()[0])
    area = [k for k in keys if not k.startswith(("timing", "control", "epics"))]
    return _record(
        keys,
        f"resolved:psana2 detinfo exp={experiment} run={run}",
        area_candidates=area,
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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-data: detectors | xtc2 | cell | cat."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-data", _DOC)

    detectors_parser = sub.add_parser("detectors", help="detector aliases for a run")
    detectors_parser.add_argument("--experiment", required=True)
    detectors_parser.add_argument("--run", required=True, type=int)

    xtc2_parser = sub.add_parser("xtc2", help="has this run's data landed on S3DF?")
    xtc2_parser.add_argument("--hutch", required=True)
    xtc2_parser.add_argument("--experiment", required=True)
    xtc2_parser.add_argument("--run", required=True, type=int)

    sub.add_parser("cell", help="unit cell from a PDB CRYST1 record").add_argument("pdb")
    sub.add_parser("cat", help="read a /cds file").add_argument("path")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-data: detectors | xtc2 | cell | cat."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "detectors":
            rec = detectors(args.experiment, args.run)
        elif args.cmd == "xtc2":
            rec = xtc2(args.hutch, args.experiment, args.run)
        elif args.cmd == "cell":
            rec = cell(args.pdb)
        elif args.cmd == "cat":
            rec = cds_file(args.path)
        else:
            return USAGE
        return out(rec)
    except (DataError, OSError) as exc:
        return fail(exc, UNREACHABLE)
