"""hutch-lightpath: the beam path to an endstation, and what blocks it.

Runs the lightpath library under the pcds interpreter, since that is where it
and happi are installed. Needs no hutch-python session: lightpath builds the
path from the happi db and reads device state over Channel Access directly.
"""

from __future__ import annotations

import argparse
import json
import subprocess

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow


class LightpathError(RuntimeError):
    """lightpath was reachable but could not answer. Never silently defaulted."""


#: Runs under the pcds interpreter, not the tools' own. %(…)s-formatted rather
#: than str.format: the body is full of dict literals.
_PATH_SNIPPET = '''
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


def beam_path(hutch: str, *, settle_s: float = 10.0, timeout: int = 180) -> dict:
    """Devices between the source and this endstation, which block beam, and the first impediment."""
    interpreter = external_path("PCDS_PYTHON")
    snippet = _PATH_SNIPPET % {
        "db": external_path("HAPPI_DB"),
        "end": hutch.strip().upper(),
        "settle": float(settle_s),
    }
    try:
        proc = subprocess.run([interpreter, "-c", snippet],
                              capture_output=True, timeout=timeout)
    except OSError as exc:
        raise LightpathError(f"cannot run {interpreter}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LightpathError(f"lightpath did not answer within {timeout}s") from exc

    text = proc.stdout.decode("utf-8", "replace")
    marker = "HUTCHLIB_JSON:"
    if marker not in text:
        # No fallback: the obvious one (lc.devices) is every lightpath-active
        # device in the facility rather than this path, and quietly reporting
        # another beamline's devices is worse than failing.
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise LightpathError(
            f"lightpath could not compute the path to {hutch.upper()} "
            f"(rc={proc.returncode}): {err[-400:] or text[-400:]}"
        )
    # Split on the marker: lightpath logs to stdout, so the payload is not
    # necessarily the last line.
    report = json.loads(text.split(marker, 1)[1].splitlines()[0])
    return {
        "value": report,
        "source": f"resolved:lightpath via {interpreter} ({report['method']})",
        "retrieved_at": utcnow(),
        "blocking": report["blocking"],
        "settled": report["settled"],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-lightpath: path."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-lightpath", _DOC)
    path_parser = sub.add_parser("path", help="the beam path to this hutch, and what blocks it")
    path_parser.add_argument("--hutch", required=True)
    path_parser.add_argument("--settle-s", type=float, default=10.0,
                             help="seconds to wait for Channel Access to settle")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-lightpath: path."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "path":
            return out(beam_path(args.hutch, settle_s=args.settle_s))
        return USAGE
    except (LightpathError, OSError) as exc:
        return fail(exc, UNREACHABLE)
