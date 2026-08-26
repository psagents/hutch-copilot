#!/usr/bin/env python3
"""
cctbx-sfx-workflow / stats_parser.py

Parses a cctbx.xfel.merge main.log file and extracts the key
statistics needed by the workflow skill.

Usage:
  python stats_parser.py --log /path/to/merge_Lyso_main.log [--json]

Output (default): human-readable summary.
Output (--json): machine-readable JSON for agent consumption.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


@dataclass
class BinStats:
    bin_num: int
    d_max: float
    d_min: float
    completeness_pct: float
    asu_multi: float  # <asu multi>
    obs_multi: float  # <obs multi>
    n_meas: int
    mean_I_over_sigI: float


@dataclass
class ImageBinStats:
    bin_num: int
    d_max: float
    d_min: float
    n_lattices: int
    accepted_pct: float


@dataclass
class MergeStats:
    # Image statistics
    image_bins: List[ImageBinStats] = field(default_factory=list)
    first_bin_accepted_pct: float = 0.0

    # Intensity statistics (all accepted experiments)
    intensity_bins: List[BinStats] = field(default_factory=list)
    overall_completeness_pct: float = 0.0

    # CC½ from cxi-xmerge table
    cc_half: float = 0.0

    # Targets met
    @property
    def targets_met(self) -> bool:
        return (
            self.first_bin_accepted_pct == 100.0
            and self.overall_completeness_pct >= 99.0
            and self.cc_half >= 95.0
        )

    def format_summary(self) -> str:
        lines = ["=" * 60, "MERGE STATISTICS SUMMARY", "=" * 60]
        lines.append(
            f"  First-bin acceptance : {self.first_bin_accepted_pct:.1f}%  "
            f"{'✓' if self.first_bin_accepted_pct == 100.0 else '✗ (target: 100%)'}"
        )
        lines.append(
            f"  Overall completeness : {self.overall_completeness_pct:.1f}%  "
            f"{'✓' if self.overall_completeness_pct >= 99.0 else '✗ (target: ≥99%)'}"
        )
        lines.append(
            f"  CC½ (CCint)          : {self.cc_half:.1f}%  "
            f"{'✓' if self.cc_half >= 95.0 else '✗ (target: ≥95%)'}"
        )
        lines.append("")
        lines.append(f"  ALL TARGETS MET: {'YES ✓' if self.targets_met else 'NO'}")
        lines.append("")

        if self.intensity_bins:
            lines.append("  Intensity statistics (all accepted):")
            lines.append(
                f"  {'Bin':>3}  {'d_min':>6}  {'Compl%':>7}  {'<asu m>':>8}  {'<obs m>':>8}"
            )
            lines.append("  " + "-" * 44)
            for b in self.intensity_bins:
                lines.append(
                    f"  {b.bin_num:>3}  {b.d_min:>6.3f}  {b.completeness_pct:>7.1f}  "
                    f"{b.asu_multi:>8.2f}  {b.obs_multi:>8.2f}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def parse_log(log_path: str) -> MergeStats:
    stats = MergeStats()
    text = Path(log_path).read_text()
    log_size = len(text)

    # ---- Image statistics -----------------------------------------------
    # Matches rows like:  1 -1.0000 -  4.8859      694    97.06
    for m in re.finditer(
        r"^\s+(\d+)\s+(-?[\d.]+)\s+-\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s*$",
        text,
        re.MULTILINE,
    ):
        # Only add if this looks like an image-stats row (no brackets like [508/668])
        b = ImageBinStats(
            bin_num=int(m.group(1)),
            d_max=float(m.group(2)),
            d_min=float(m.group(3)),
            n_lattices=int(m.group(4)),
            accepted_pct=float(m.group(5)),
        )
        stats.image_bins.append(b)
    if stats.image_bins:
        stats.first_bin_accepted_pct = stats.image_bins[0].accepted_pct
    elif log_size > 0:
        print(
            f"WARNING: image_bins is empty after parsing a non-empty log "
            f"({log_size} bytes). The image statistics table format may have "
            f"changed. Check the log manually and update the regex in "
            f"stats_parser.py if needed.",
            file=sys.stderr,
        )

    # ---- Intensity statistics (all accepted experiments) ----------------
    # Matches rows like:
    #   1 -1.0000 -  4.8859     [627/668] 93.86   6.49   6.92   4337  627  ...
    # Columns: bin d_max d_min [n/total] compl% asu_multi obs_multi n_meas asu_m_meas
    all_marker = "Intensity Statistics (all accepted experiments)"
    all_start = text.find(all_marker)
    if all_start != -1:
        all_text = text[all_start:]
        for m in re.finditer(
            r"^\s+(\d+)\s+(-?[\d.]+)\s+-\s+([\d.]+)\s+\[\d+/\d+\]\s+([\d.]+)"
            r"\s+([\d.]+)\s+([\d.]+)",
            all_text,
            re.MULTILINE,
        ):
            b = BinStats(
                bin_num=int(m.group(1)),
                d_max=float(m.group(2)),
                d_min=float(m.group(3)),
                completeness_pct=float(m.group(4)),  # already a percentage
                asu_multi=float(m.group(5)),
                obs_multi=float(m.group(6)),
                n_meas=0,
                mean_I_over_sigI=0.0,
            )
            stats.intensity_bins.append(b)

        if not stats.intensity_bins:
            print(
                f"WARNING: intensity_bins is empty despite finding the "
                f"'Intensity Statistics' section header. The bin row format "
                f"may have changed. Check the log manually and update the "
                f"regex in stats_parser.py if needed.",
                file=sys.stderr,
            )

        # "All" row: All                    [4456/11741] 37.95   1.09   2.87 ...
        all_row = re.search(r"^All\s+\[\d+/\d+\]\s+([\d.]+)", all_text, re.MULTILINE)
        if all_row:
            stats.overall_completeness_pct = float(all_row.group(1))
    elif log_size > 0:
        print(
            f"WARNING: 'Intensity Statistics (all accepted experiments)' section "
            f"not found in log ({log_size} bytes). The merge may not have "
            f"completed, or the log format has changed.",
            file=sys.stderr,
        )

    # ---- CC½ from cxi-xmerge table --------------------------------------
    # "C.C. int is XX.X% on NNNN indices"
    cc_match = re.search(r"C\.C\. int is ([\d.]+)%", text)
    if cc_match:
        stats.cc_half = float(cc_match.group(1))

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(
        description="Parse cctbx.xfel.merge log and extract statistics."
    )
    p.add_argument("--log", required=True, help="Path to merge_*_main.log.")
    p.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human summary.",
    )
    args = p.parse_args()

    if not Path(args.log).exists():
        print(f"ERROR: log file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    stats = parse_log(args.log)

    if args.json:
        out = {
            "first_bin_accepted_pct": stats.first_bin_accepted_pct,
            "overall_completeness_pct": stats.overall_completeness_pct,
            "cc_half": stats.cc_half,
            "targets_met": stats.targets_met,
            "intensity_bins": [asdict(b) for b in stats.intensity_bins],
            "image_bins": [asdict(b) for b in stats.image_bins],
        }
        print(json.dumps(out, indent=2))
    else:
        print(stats.format_summary())


if __name__ == "__main__":
    main()
