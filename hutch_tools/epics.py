"""hutch-epics: read PVs over Channel Access.

Local: CA reaches the IOCs from the hutch machines, so there is no hop. Do not
set EPICS_CA_ADDR_LIST -- it is unset on the DAQ machine and CA works by
broadcast; setting it would be a guess that could break a working path.

Lived in daq.py until Oct 2026, which is how the EPICS bin dir came to be
written down twice. Channel Access has nothing to do with the DAQ or the live
session: the hutch is reachable over CA with no session running at all.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow

#: Timeout class, in seconds. Named rather than a magic number at call sites.
T_QUERY = 10


class EpicsError(RuntimeError):
    """Channel Access could not answer. Never falls back to a remembered value."""


def caget_binary() -> str:
    """``caget`` from the EPICS bin dir bin/hutch-env declares, else PATH.

    The declared directory outranks PATH: it is the deliberate, operator-owned
    value, where PATH is ambient. A host with no such directory still works off
    PATH, so a control machine shipping its own caget is unaffected.
    """
    candidate = pathlib.Path(external_path("EPICS_BIN")) / "caget"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("caget")
    if found:
        return found
    raise EpicsError(
        f"no caget at {candidate} and none on PATH -- is this a hutch machine?"
    )


def caget(pvs: list[str], *, timeout: int = T_QUERY) -> dict[str, str]:
    """Read PVs locally over Channel Access."""
    if not pvs:
        return {}
    try:
        proc = subprocess.run(
            [caget_binary(), "-w", str(timeout), *pvs],
            capture_output=True,
            timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired as exc:
        raise EpicsError(f"caget timed out after {timeout + 10}s") from exc
    stdout = proc.stdout.decode("utf-8", "replace")
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1].strip()
    missing = [p for p in pvs if p not in values]
    if missing:
        # caget exits non-zero and prints the reason here -- a PV that does not
        # exist and an IOC that is down look different, so quote it.
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise EpicsError(f"no value returned for {missing}" + (f": {err}" if err else ""))
    return values


def pv_values(pvs: list[str], *, timeout: int = T_QUERY) -> dict:
    """Live PV values over Channel Access. Compose the name with ``hutch-happi pv``."""
    return {
        "value": caget(pvs, timeout=timeout),
        "source": f"resolved:caget {' '.join(pvs)}",
        "retrieved_at": utcnow(),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-epics: caget."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-epics", _DOC)
    # No --hutch: CA is read locally and reaches the IOCs from any hutch
    # machine, so the hutch was never part of the question.
    caget_parser = sub.add_parser("caget", help="read PVs over Channel Access")
    caget_parser.add_argument("pv", nargs="+")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-epics: caget."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "caget":
            return out(pv_values(args.pv))
        return USAGE
    except (EpicsError, OSError) as exc:
        return fail(exc, UNREACHABLE)
