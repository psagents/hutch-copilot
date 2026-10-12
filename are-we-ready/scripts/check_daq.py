#!/usr/bin/env python3
"""Is the DAQ able to record? The DAQ's own signals, plus the verdict over them.

``hutch-daq state`` reports the signals and judges nothing. The judgment is
here: ``can_record`` is None when the state cannot be read, because an
unreachable DAQ is unknown, never a confident no. ``run_number`` and ``config``
are context, not part of the verdict -- whether data has already landed is not a
readiness question, and a config we could not parse does not make the DAQ
unready.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from hutch_tools import daq
from hutch_tools.common import UNREACHABLE, fail, out, utcnow


def check_daq(hutch: str, *, experiment: str | None = None,
              timeout: int = daq.T_CONTROL_MS) -> dict:
    """The DAQ's signals with a readiness verdict over them."""
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
        "source": "resolved:daq signals via the hutch-python session",
        "retrieved_at": utcnow(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_daq.py", description=__doc__)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--experiment", default=None,
                    help="cross-check against the DAQ's own experiment")
    args = ap.parse_args(argv)
    try:
        return out(check_daq(args.hutch, experiment=args.experiment))
    except (daq.DaqError, OSError, subprocess.SubprocessError) as exc:
        return fail(exc, UNREACHABLE)


if __name__ == "__main__":
    raise SystemExit(main())
