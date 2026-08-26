#!/usr/bin/env python3
"""
cctbx-sfx-workflow / dmin_advisor.py

Recommends a new d_min based on the multiplicity and completeness rule:
  Find the lowest d_min (highest resolution bin) where:
    (obs_multi >= 10 OR asu_multi >= 10) AND completeness >= 99%
  Recommendation is only made if it is lower (higher resolution) than current d_min.

Usage:
  python dmin_advisor.py --log /path/to/merge_main.log --current-dmin 2.5

Output:
  RECOMMEND_DMIN: 1.8   (new d_min recommended)
  NO_CHANGE             (current d_min is already at or beyond the rule)
  INSUFFICIENT_DATA     (not enough bins pass the criteria to recommend a change)
  CC_HALF_BELOW_TARGET  (a bin would otherwise qualify, but overall CC½ < 95% —
                         the documented merge target from cctbx-sfx-workflow/SKILL.md.
                         Held at current d_min until CC½ improves; pushing to higher
                         resolution while overall data quality is already below
                         target would compound the problem, not resolve it.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Import stats_parser from the same directory
_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from stats_parser import parse_log, BinStats


def recommend_dmin(log_path: str, current_dmin: float) -> tuple[str, list[BinStats]]:
    """
    Returns (recommendation_string, qualifying_bins).

    recommendation_string is one of:
      "RECOMMEND_DMIN: X.XX"
      "NO_CHANGE"
      "INSUFFICIENT_DATA"
    """
    stats = parse_log(log_path)

    if not stats.intensity_bins:
        return "INSUFFICIENT_DATA", []

    # Bins satisfying both multiplicity AND completeness criteria
    qualifying = [
        b
        for b in stats.intensity_bins
        if (b.obs_multi >= 10.0 or b.asu_multi >= 10.0) and b.completeness_pct >= 99.0
    ]

    if not qualifying:
        return "INSUFFICIENT_DATA", []

    # Lowest d_min (highest resolution) among qualifying bins
    best = min(qualifying, key=lambda b: b.d_min)

    if best.d_min >= current_dmin:
        return "NO_CHANGE", qualifying

    # Overall CC½ target (matches cctbx-sfx-workflow/SKILL.md's documented
    # merge targets). Per-bin CC½ isn't parsed from the log — only the
    # overall value is — so this gates the recommendation as a whole rather
    # than filtering individual bins.
    if stats.cc_half < 95.0:
        return (
            f"CC_HALF_BELOW_TARGET: overall CC½={stats.cc_half:.1f}% (<95% target) "
            f"— would otherwise recommend {best.d_min:.2f} Å, holding at "
            f"{current_dmin:.2f} Å until CC½ improves",
            qualifying,
        )

    return f"RECOMMEND_DMIN: {best.d_min:.2f}", qualifying


def main() -> None:
    p = argparse.ArgumentParser(
        description="Recommend d_min from multiplicity and completeness statistics."
    )
    p.add_argument("--log", required=True, help="Path to merge_*_main.log.")
    p.add_argument(
        "--current-dmin", required=True, type=float, help="Current d_min in Å."
    )
    args = p.parse_args()

    if not Path(args.log).exists():
        print(f"ERROR: log file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    recommendation, qualifying_bins = recommend_dmin(args.log, args.current_dmin)
    print(recommendation)

    # Print the qualifying bins for agent context (to show user)
    if qualifying_bins:
        print()
        print("Qualifying bins (obs_multi or asu_multi ≥ 10 AND completeness ≥ 99%):")
        print(
            f"  {'Bin':>3}  {'d_min':>6}  {'Compl%':>7}  {'<asu m>':>8}  {'<obs m>':>8}"
        )
        print("  " + "-" * 44)
        for b in qualifying_bins:
            marker = (
                " ← recommended"
                if (
                    recommendation.startswith("RECOMMEND_DMIN")
                    and abs(b.d_min - float(recommendation.split(": ")[1])) < 0.001
                )
                else ""
            )
            print(
                f"  {b.bin_num:>3}  {b.d_min:>6.3f}  {b.completeness_pct:>7.1f}  "
                f"{b.asu_multi:>8.2f}  {b.obs_multi:>8.2f}{marker}"
            )


if __name__ == "__main__":
    main()
