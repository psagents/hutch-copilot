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
import datetime
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
# Archived history
#
# caget answers "what is this PV now". "What changed overnight" is a different
# backend -- the EPICS Archiver Appliance -- and it is reachable only from the
# hutch network, so this raises off-hutch rather than returning an empty series.
# --------------------------------------------------------------------------
#: Bit 11 (2048) of a motor's MSTA. Clear on a record that never connected, so
#: it separates a real sample from an IOC restart replaying the last value.
MSTA_PRESENT = 2048
#: Bit 1 (2) of MSTA, DONE_MOVING. Archived on change, unlike .RBV which is
#: archived with a deadband -- so this is what catches a sub-deadband move.
MSTA_DONE_MOVING = 2


def history(pv: str, start: str, end: str, *, timeout: int = T_QUERY) -> dict:
    """Archived samples for one PV between two times.

    Parameters
    ----------
    start, end : str
        ISO 8601. A naive value is read as this host's local time, which at a
        hutch is Pacific -- the operator's wall clock, and what the appliance
        wants.
    """
    import requests

    url = external_path("ARCHIVER_URL") + "/data/getData.json"
    params = {"pv": pv, "from": _iso(start), "to": _iso(end)}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise EpicsError(
            f"archiver unreachable at {url}: {exc}. It serves the hutch network "
            f"only -- this fails from S3DF."
        ) from exc
    # The appliance 404s a PV it does not archive; there is no endpoint that
    # lists what it holds, so a 404 is the only way to learn that.
    if resp.status_code == 404:
        raise EpicsError(f"{pv} is not archived (404 from {url})")
    if not resp.ok:
        raise EpicsError(f"archiver returned {resp.status_code} for {pv}: {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise EpicsError(f"archiver returned non-JSON for {pv}: {resp.text[:200]!r}") from exc
    samples = [
        {
            "at": datetime.datetime.fromtimestamp(
                s["secs"], datetime.timezone.utc
            ).isoformat(),
            "value": s.get("val"),
            # Present only when the appliance saw a gap; surfaced so a
            # reconnect is labelled rather than silently plotted as a change.
            **({"connection_lost_s": s["cnxlostepsecs"]} if "cnxlostepsecs" in s else {}),
            **({"connection_regained_s": s["cnxregainedepsecs"]}
               if "cnxregainedepsecs" in s else {}),
        }
        for block in body for s in block.get("data", [])
    ]
    return {
        "value": {"pv": pv, "from": params["from"], "to": params["to"],
                  "count": len(samples), "samples": samples},
        "source": f"resolved:archiver getData.json pv={pv}",
        "retrieved_at": utcnow(),
    }


def _iso(when: str) -> str:
    """Local-time ISO 8601 with an explicit offset, which the appliance requires."""
    try:
        parsed = datetime.datetime.fromisoformat(when)
    except ValueError as exc:
        raise EpicsError(f"not an ISO 8601 time: {when!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat()


def moved(pv: str, start: str, end: str, *, timeout: int = T_QUERY) -> dict:
    """Whether a motor was commanded to move, from its MSTA rather than its RBV.

    ``.RBV`` is archived with a deadband, so a move smaller than it leaves no
    sample at all and reads as "did not move". ``.MSTA`` is archived on change,
    so every commanded move appears. ``.MOV``/``.DMOV`` are not archived.
    """
    base = pv.split(".", 1)[0]
    series = history(f"{base}.MSTA", start, end, timeout=timeout)["value"]
    transitions = []
    for sample in series["samples"]:
        raw = sample["value"]
        if not isinstance(raw, (int, float)):
            continue
        msta = int(raw)
        if not msta & MSTA_PRESENT:
            # An IOC reconnect re-posts the unchanged value with the encoder and
            # gains bits clear -- a state a working motor record never holds.
            # Counting it as motion is the failure mode this check exists for.
            continue
        transitions.append({**sample, "done_moving": bool(msta & MSTA_DONE_MOVING)})
    done = [t["done_moving"] for t in transitions]
    return {
        "value": {
            "pv": base,
            "from": series["from"],
            "to": series["to"],
            # No usable sample is unknown, never a confident "did not move".
            "moved": None if not transitions else any(
                a != b for a, b in zip(done, done[1:])
            ),
            "samples_considered": len(transitions),
            "samples_discarded": series["count"] - len(transitions),
            "transitions": transitions,
        },
        "source": f"resolved:archiver getData.json pv={base}.MSTA",
        "retrieved_at": utcnow(),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-epics: caget | history | moved."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-epics", _DOC)
    # No --hutch: CA is read locally and reaches the IOCs from any hutch
    # machine, so the hutch was never part of the question.
    caget_parser = sub.add_parser("caget", help="read PVs over Channel Access")
    caget_parser.add_argument("pv", nargs="+")

    for name, helptext in (
        ("history", "archived samples for one PV between two times"),
        ("moved", "whether a motor moved, from its MSTA (deadband-proof)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("pv")
        p.add_argument("--from", dest="start", required=True,
                       help="ISO 8601, e.g. 2026-10-02T18:00. Naive means local time")
        p.add_argument("--to", dest="end", default=None,
                       help="ISO 8601; defaults to now")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-epics: caget | history | moved."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "caget":
            return out(pv_values(args.pv))
        if args.cmd in ("history", "moved"):
            end = args.end or datetime.datetime.now().astimezone().isoformat()
            fn = history if args.cmd == "history" else moved
            return out(fn(args.pv, args.start, end))
        return USAGE
    except (EpicsError, OSError) as exc:
        return fail(exc, UNREACHABLE)
