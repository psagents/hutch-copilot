#!/usr/bin/env python3
"""
cctbx-sfx-workflow / iteration_log.py

Manages the workflow state file and versioned merge output directories.

Subcommands:
  init          Create workflow_state.json for a new experiment.
  next-version  Print the next vXXX version string.
  record        Record a completed merge iteration in history.
  status        Print the current workflow state summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


STATE_FILE = "workflow_state.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def state_path(output_dir: str) -> str:
    return os.path.join(output_dir, STATE_FILE)


def load_state(output_dir: str) -> dict:
    p = state_path(output_dir)
    if not os.path.exists(p):
        print(
            f"ERROR: {STATE_FILE} not found in {output_dir}. Run 'init' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(p) as f:
        return json.load(f)


def save_state(output_dir: str, state: dict) -> None:
    p = state_path(output_dir)
    with open(p, "w") as f:
        json.dump(state, f, indent=2)
    print(f"State saved: {p}")


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------
def _existing_versions(output_dir: str, tag: str) -> List[int]:
    """Return sorted list of existing vXXX version numbers."""
    merge_dir = os.path.join(output_dir, f"merge_{tag}")
    if not os.path.isdir(merge_dir):
        return []
    versions = []
    for entry in os.listdir(merge_dir):
        m = re.fullmatch(r"v(\d{3})", entry)
        if m and os.path.isdir(os.path.join(merge_dir, entry)):
            versions.append(int(m.group(1)))
    return sorted(versions)


def next_version_str(output_dir: str, tag: str) -> str:
    existing = _existing_versions(output_dir, tag)
    n = (max(existing) + 1) if existing else 1
    return f"v{n:03d}"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> None:
    """Create a fresh workflow_state.json."""
    p = state_path(args.output_dir)
    if os.path.exists(p) and not args.force:
        print(
            f"ERROR: {STATE_FILE} already exists at {args.output_dir}. "
            f"Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    state = {
        "experiment": args.experiment,
        "tag": args.tag,
        "current_d_min": args.d_min,
        "work_dir": args.work_dir or args.output_dir,
        "output_dir": os.path.abspath(args.output_dir),
        "known_runs": [],
        "current_version": None,
        "state": "WATCHING",
        "last_check": datetime.now(timezone.utc).isoformat(),
        "history": [],
        "max_run_retries": 1,
        "run_attempts": {},
        "failed_runs": {},
        # Store original render args so re-renders are reproducible.
        # Calibration values are optional (None when not used); stored here so
        # re-renders in the workflow skill reproduce the original invocation exactly.
        "render_args": {
            "pdb": args.pdb or "",
            "geometry": args.geometry or "",
            "mask": args.mask or "",
            "detector_address": args.detector_address or "",
            "wavelength_offset": args.wavelength_offset,
            "spectrum_ev_per_pixel": args.spectrum_ev_per_pixel,
            "spectrum_ev_offset": args.spectrum_ev_offset,
            "two_theta_low": args.two_theta_low,
            "two_theta_high": args.two_theta_high,
        },
    }
    os.makedirs(args.output_dir, exist_ok=True)
    save_state(args.output_dir, state)
    print(
        f"Initialized workflow state for {args.experiment}/{args.tag} "
        f"at d_min={args.d_min} Å"
    )


def cmd_next_version(args: argparse.Namespace) -> None:
    """Print the next available vXXX string."""
    state = load_state(args.output_dir)
    tag = state["tag"]
    ver = next_version_str(args.output_dir, tag)
    print(ver)


def cmd_record(args: argparse.Namespace) -> None:
    """Record a completed merge iteration and create versioned directory + manifest."""
    state = load_state(args.output_dir)
    tag = state["tag"]
    work_dir = state.get("work_dir", args.output_dir)

    version = args.version
    version_dir = os.path.join(work_dir, f"merge_{tag}", version)
    os.makedirs(version_dir, exist_ok=True)

    manifest = {
        "version": version,
        "experiment": state["experiment"],
        "tag": tag,
        "runs": args.runs,
        "d_min": args.d_min,
        "trigger": args.trigger,
        "approved_by": args.approved_by,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = os.path.join(version_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Update state
    state["current_version"] = version
    state["current_d_min"] = args.d_min
    state["known_runs"] = sorted(set(state.get("known_runs", [])) | set(args.runs))
    state["state"] = "WATCHING"
    state["history"].append(manifest)
    save_state(args.output_dir, state)
    print(
        f"Recorded iteration {version}: runs={args.runs}, d_min={args.d_min}, "
        f"trigger={args.trigger}"
    )


def cmd_status(args: argparse.Namespace) -> None:
    """Print a human-readable status summary."""
    state = load_state(args.output_dir)
    print(f"Experiment    : {state['experiment']}")
    print(f"Tag           : {state['tag']}")
    print(f"State         : {state['state']}")
    print(f"Current d_min : {state['current_d_min']} Å")
    print(f"Known runs    : {state.get('known_runs', [])}")
    print(f"Current ver.  : {state.get('current_version', 'none')}")
    print(f"Last check    : {state.get('last_check', 'never')}")
    print(f"Iterations    : {len(state.get('history', []))}")
    if state.get("pending_dmin") is not None:
        print(f"Pending d_min : {state['pending_dmin']} Å  (awaiting approval)")
    if state.get("approved_dmin") is not None:
        print(
            f"Approved d_min: {state['approved_dmin']} Å  (will apply next WATCHING poll)"
        )
    if state.get("history"):
        print("\nHistory:")
        for h in state["history"]:
            print(
                f"  {h['version']}  runs={h['runs']}  d_min={h['d_min']}  "
                f"trigger={h['trigger']}  {h['timestamp'][:10]}"
            )


def cmd_approve_dmin(args: argparse.Namespace) -> None:
    """Set approved_dmin in state to trigger a d_min change on next watcher poll."""
    state = load_state(args.output_dir)
    current = state["current_d_min"]
    pending = state.get("pending_dmin")

    if args.d_min == current:
        print(
            f"ERROR: approved d_min ({args.d_min}) is the same as current d_min "
            f"({current}). No change needed.",
            file=sys.stderr,
        )
        sys.exit(1)

    state["approved_dmin"] = args.d_min
    save_state(args.output_dir, state)
    print(f"Approved d_min change: {current} → {args.d_min} Å")
    if pending is not None and pending != args.d_min:
        print(
            f"Note: watcher recommended {pending} Å but you approved {args.d_min} Å. "
            f"Your value will be used."
        )
    print("The watcher will apply this change on the next WATCHING poll.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(
        description="cctbx-sfx-workflow: manage workflow state and versioned iterations."
    )
    p.add_argument("--output-dir", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    pi = sub.add_parser("init", help="Create workflow_state.json.")
    pi.add_argument("--experiment", required=True)
    pi.add_argument("--tag", required=True)
    pi.add_argument("--d-min", required=True, type=float)
    pi.add_argument("--work-dir", default=None)
    pi.add_argument("--pdb", default=None)
    pi.add_argument("--geometry", default=None)
    pi.add_argument("--mask", default=None)
    pi.add_argument("--detector-address", default=None)
    # Optional calibration values — must be stored so re-renders reproduce them
    pi.add_argument("--wavelength-offset", type=float, default=None)
    pi.add_argument("--spectrum-ev-per-pixel", type=float, default=None)
    pi.add_argument("--spectrum-ev-offset", type=float, default=None)
    # Radial average 2theta range — must match the value used in the original
    # setup-cctbx-pipeline render.py call, so re-renders stay consistent.
    pi.add_argument("--two-theta-low", type=float, default=12.5)
    pi.add_argument("--two-theta-high", type=float, default=22.8)
    pi.add_argument("--force", action="store_true")

    # next-version
    sub.add_parser("next-version", help="Print next vXXX version string.")

    # record
    pr = sub.add_parser("record", help="Record a completed merge iteration.")
    pr.add_argument("--version", required=True, help="Version string, e.g. v003.")
    pr.add_argument("--runs", required=True, nargs="+", type=int)
    pr.add_argument("--d-min", required=True, type=float)
    pr.add_argument(
        "--trigger",
        required=True,
        choices=["new_run", "dmin_change"],
        help="What triggered this iteration.",
    )
    pr.add_argument(
        "--approved-by",
        default=None,
        help="Username who approved (for dmin_change triggers).",
    )

    # status
    sub.add_parser("status", help="Print current workflow state summary.")

    # approve-dmin
    pad = sub.add_parser(
        "approve-dmin", help="Approve a d_min change for the watcher to apply."
    )
    pad.add_argument(
        "--d-min",
        required=True,
        type=float,
        help="New d_min value to approve (e.g. 1.6).",
    )

    args = p.parse_args()

    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "next-version":
        cmd_next_version(args)
    elif args.cmd == "record":
        cmd_record(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "approve-dmin":
        cmd_approve_dmin(args)


if __name__ == "__main__":
    main()
