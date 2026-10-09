#!/usr/bin/env python3
"""Is beam reaching the hutch, and what blocks it?

Thin over ``hutch-lightpath path``: the library already computes the blocking
set and the impediment. Kept as a readiness script anyway so the three checks
are invoked the same way, and so the interpretation notes stay next to the
skill that reads them -- `impediment` is the one to act on, and `settled:
false` means undetermined, not a fault.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from hutch_tools import lightpath
from hutch_tools.common import UNREACHABLE, fail, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_beam.py", description=__doc__)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--settle-s", type=float, default=10.0,
                    help="seconds to wait for Channel Access to settle")
    args = ap.parse_args(argv)
    try:
        return out(lightpath.beam_path(args.hutch, settle_s=args.settle_s))
    except (lightpath.LightpathError, OSError) as exc:
        return fail(exc, UNREACHABLE)


if __name__ == "__main__":
    raise SystemExit(main())
