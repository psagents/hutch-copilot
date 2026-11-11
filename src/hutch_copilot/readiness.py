"""hutch-ready: the three readiness checks, judgements over tools/ -- beam | hutch | daq.

``beam`` is thin over ``hutch-lightpath path``: `impediment` is the one to act on,
and `settled: false` means undetermined, not a fault. ``hutch`` composes the
device database with Channel Access and defers beam-path devices to lightpath.
``daq`` adds the verdict the DAQ's own signals do not carry.
"""

from __future__ import annotations

import argparse
import re
import subprocess

from .tools import daq, happi, lightpath
from .tools.common import UNREACHABLE, fail, out, parser, utcnow
from .tools.epics import T_QUERY, EpicsError, caget


def check_hutch(hutch: str, *, inventory_only: bool = False,
                timeout: int = T_QUERY) -> dict:
    """The hutch's own devices and where they are. Beam-path devices defer to ``beam``.

    Returns one row per device carrying exactly one of ``value`` (a live
    readback), ``on_lightpath`` (ask ``beam``, which is authoritative for
    it) or ``needs_session`` (a composite device whose component suffixes live
    in its class, not its prefix).
    """
    db = happi.db_path()
    rows = happi.inventory(hutch)
    source = f"resolved:happi {db} beamline={hutch.upper()}"
    if inventory_only:
        return {"value": rows, "source": source, "retrieved_at": utcnow(),
                "count": len(rows)}

    lightpath_names = happi.lightpath_names(db, hutch)
    readable = {r["name"]: r["prefix"] + ".RBV"
                for r in rows
                if r["name"] not in lightpath_names and happi.has_motor_record(r)}
    # One caget per PV, not one for the whole hutch: caget exits non-zero and
    # prints nothing on stdout when ANY name in the batch is missing -- so a
    # single absent record would blank every other reading.
    values: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    for name, pvname in readable.items():
        try:
            values[name] = caget([pvname], timeout=timeout)[pvname]
        except EpicsError as exc:
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

    return {
        "value": out_rows,
        "source": source + f" + caget on {len(readable)} motor record(s)",
        "retrieved_at": utcnow(),
        "count": len(out_rows),
        "on_lightpath": len(lightpath_names & {r["name"] for r in rows}),
    }


def check_daq(hutch: str, *, experiment: str | None = None,
              timeout: int = daq.T_CONTROL_MS) -> dict:
    """The DAQ's signals with a readiness verdict over them.

    ``can_record`` is None when the state cannot be read, because an unreachable
    DAQ is unknown, never a confident no. ``run_number`` and ``config`` are
    context, not part of the verdict.
    """
    payload = dict(daq.state(hutch, timeout=timeout)["value"])

    state = (payload.get("state") or "").strip().lower()
    payload["can_record"] = (
        state in daq.DAQ_READY_STATES if state in daq.DAQ_STATES else None
    )
    if experiment and payload.get("experiment") not in (None, "", experiment):
        payload["experiment_mismatch"] = {
            "daq": payload["experiment"], "expected": experiment,
        }
    # Informational, and deliberately after can_record: an unparseable config
    # is worth reporting but is not a readiness failure, so it must not be able
    # to change the verdict.
    try:
        payload["config"] = daq.config_summary(hutch)
    except (daq.DaqError, OSError, re.error) as exc:
        payload["config"] = {"error": str(exc)}
    return {
        "value": payload,
        "source": "resolved:daq signals off the DAQ control port",
        "retrieved_at": utcnow(),
    }


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-ready", __doc__.splitlines()[0])
    b = sub.add_parser("beam", help="is beam reaching the hutch, and what blocks it")
    b.add_argument("--hutch", required=True)
    b.add_argument("--settle-s", type=float, default=10.0,
                   help="seconds to wait for Channel Access to settle")
    h = sub.add_parser("hutch", help="are the hutch's own devices placed")
    h.add_argument("--hutch", required=True)
    h.add_argument("--inventory-only", action="store_true",
                   help="happi listing only, no live readback")
    d = sub.add_parser("daq", help="is the DAQ able to record")
    d.add_argument("--hutch", required=True)
    d.add_argument("--experiment", default=None,
                   help="cross-check against the DAQ's own experiment")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-ready: beam | hutch | daq."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "beam":
            return out(lightpath.beam_path(args.hutch, settle_s=args.settle_s))
        if args.cmd == "hutch":
            return out(check_hutch(args.hutch, inventory_only=args.inventory_only))
        return out(check_daq(args.hutch, experiment=args.experiment))
    except (lightpath.LightpathError, happi.HappiError, EpicsError, daq.DaqError,
            OSError, subprocess.SubprocessError) as exc:
        return fail(exc, UNREACHABLE)
