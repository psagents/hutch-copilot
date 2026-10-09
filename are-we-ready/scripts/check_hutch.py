#!/usr/bin/env python3
"""Are the hutch's own devices placed? happi inventory plus a live readback.

A readiness question, not a backend read: it composes the device database
(``hutch-happi``) with Channel Access (``hutch-epics``) and defers beam-path
devices to lightpath. That composition is judgment, which is why it lives with
the skill that asks it rather than in the tools.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from hutch_tools import happi
from hutch_tools.common import UNREACHABLE, fail, out, utcnow
from hutch_tools.epics import T_QUERY, EpicsError, caget


def check_hutch(hutch: str, *, inventory_only: bool = False,
                timeout: int = T_QUERY) -> dict:
    """The hutch's own devices and where they are. Beam-path devices defer to check_beam.

    Returns one row per device carrying exactly one of ``value`` (a live
    readback), ``on_lightpath`` (ask ``check_beam``, which is authoritative for
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_hutch.py", description=__doc__)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--inventory-only", action="store_true",
                    help="happi listing only, no live readback")
    args = ap.parse_args(argv)
    try:
        return out(check_hutch(args.hutch, inventory_only=args.inventory_only))
    except (happi.HappiError, EpicsError, OSError) as exc:
        return fail(exc, UNREACHABLE)


if __name__ == "__main__":
    raise SystemExit(main())
