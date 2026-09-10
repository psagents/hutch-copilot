#!/usr/bin/env python3
"""
cctbx-sfx-workflow / monitor.py

Two subcommands:
  check-runs   Poll eLog for new runs with the workflow tag.
  check-jobs   Check lute.db for recently completed Scale/Merge tasks.

Output is machine-readable single-line stdout for easy agent parsing.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------
def load_state(output_dir: str) -> dict:
    state_path = os.path.join(output_dir, "workflow_state.json")
    if not os.path.exists(state_path):
        print(
            f"ERROR: workflow_state.json not found in {output_dir}. "
            f"Run iteration_log.py init first.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(state_path) as f:
        return json.load(f)


def save_state(output_dir: str, state: dict) -> None:
    state_path = os.path.join(output_dir, "workflow_state.json")
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# eLog run query
# ---------------------------------------------------------------------------
def get_elog_runs_by_tag(experiment: str, tag: str) -> List[int]:
    """Return sorted list of run numbers tagged with `tag` in the LCLS eLog.

    Uses lute.io.elog if available (requires psana2 env); falls back to
    empty list with a warning so the caller can ask the user.
    """
    try:
        from lute.io.elog import get_elog_runs_by_tag as _get

        runs = _get(experiment, tag)
        return sorted(int(r) for r in runs)
    except Exception as exc:
        print(
            f"WARN: eLog query failed ({exc}). Ensure psana2 conda env is active.",
            file=sys.stderr,
        )
        return []


# ---------------------------------------------------------------------------
# lute.db completion query
# ---------------------------------------------------------------------------
def find_lute_db(work_dir: str) -> Optional[str]:
    candidate = os.path.join(work_dir, "lute.db")
    return candidate if os.path.exists(candidate) else None


def scale_complete_for_runs(db_path: str, run_nums: List[int]) -> bool:
    """Return True only when ALL run_nums have a ScaleCCTBXXFEL COMPLETED row.

    lute.db config.run is stored as TEXT (e.g. '38'), not an integer.
    """
    if not run_nums:
        return False
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    completed_runs = set()
    for run_num in run_nums:
        cur.execute(
            """
            SELECT e.id
            FROM executions e
            JOIN tasks t        ON e.task_id   = t.id
            JOIN config c       ON e.config_id = c.id
            LEFT JOIN results r ON e.result_id = r.id
            WHERE t.name = 'ScaleCCTBXXFEL'
              AND CAST(c.run AS INTEGER) = ?
              AND r.status = 'TaskStatus.COMPLETED'
            LIMIT 1
        """,
            (run_num,),
        )
        if cur.fetchone() is not None:
            completed_runs.add(run_num)
    conn.close()
    return completed_runs == set(run_nums)


def latest_task_status(db_path: str, task_name: str, run_num: int) -> Optional[str]:
    """Return the status of the most recent `task_name` execution for run_num,
    or None if it has never been attempted. Only the latest attempt matters —
    a run that failed once and later succeeded on retry must not still read
    as failed."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.status
        FROM executions e
        JOIN tasks t        ON e.task_id   = t.id
        JOIN config c       ON e.config_id = c.id
        LEFT JOIN results r ON e.result_id = r.id
        WHERE t.name = ? AND CAST(c.run AS INTEGER) = ?
        ORDER BY e.timestamp DESC
        LIMIT 1
        """,
        (task_name, run_num),
    )
    row = cur.fetchone()
    conn.close()
    return row["status"] if row else None


def failed_runs_for(db_path: str, run_nums: List[int]) -> dict:
    """Return {run_num: task_name} for runs whose most recent Index or Scale
    attempt ended in TaskStatus.FAILED."""
    failed = {}
    for run_num in run_nums:
        idx_status = latest_task_status(db_path, "IndexCCTBXXFEL", run_num)
        if idx_status == "TaskStatus.FAILED":
            failed[run_num] = "IndexCCTBXXFEL"
            continue
        scale_status = latest_task_status(db_path, "ScaleCCTBXXFEL", run_num)
        if scale_status == "TaskStatus.FAILED":
            failed[run_num] = "ScaleCCTBXXFEL"
    return failed


def merge_complete_since(db_path: str, since_iso: str) -> bool:
    """Return True if a MergeCCTBXXFEL COMPLETED after since_iso.

    since_iso is an ISO timestamp (UTC) stored when --merge was submitted.
    lute.db timestamps are YYYY-MM-DD HH:MM:SS UTC (no timezone).
    """
    try:
        since_dt = datetime.fromisoformat(since_iso).replace(tzinfo=None)
        since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        since_str = "1970-01-01 00:00:00"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.id
        FROM executions e
        JOIN tasks t        ON e.task_id   = t.id
        LEFT JOIN results r ON e.result_id = r.id
        WHERE t.name = 'MergeCCTBXXFEL'
          AND r.status = 'TaskStatus.COMPLETED'
          AND e.timestamp > ?
        LIMIT 1
    """,
        (since_str,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_check_runs(args: argparse.Namespace) -> None:
    """Check eLog for runs not yet in known_runs. Print NEW_RUNS or NO_NEW_RUNS."""
    state = load_state(args.output_dir)
    experiment = state["experiment"]
    tag = state["tag"]
    known = set(state.get("known_runs", []))

    current = get_elog_runs_by_tag(experiment, tag)
    if not current:
        print("NO_NEW_RUNS")
        return

    new_runs = sorted(set(current) - known)
    # Update last_check timestamp in state
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(args.output_dir, state)

    if new_runs:
        print(f"NEW_RUNS: {new_runs}")
    else:
        print("NO_NEW_RUNS")


def cmd_check_jobs(args: argparse.Namespace) -> None:
    """Check lute.db for job completion. Print PENDING, SCALE_COMPLETE,
    MERGE_COMPLETE, or FAILED.

    INDEXING_SCALING: requires ScaleCCTBXXFEL COMPLETED for every run in
      state["indexing_scale_runs"]. Guards against false positives from old
      completions in lute.db.

    MERGING: requires MergeCCTBXXFEL COMPLETED with timestamp after
      state["last_merge_submission_time"]. Guards against false positives
      from prior merge completions.
    """
    state = load_state(args.output_dir)
    work_dir = state.get("work_dir", args.output_dir)
    db_path = find_lute_db(work_dir)

    if db_path is None:
        print("PENDING")
        return

    current_state = state.get("state", "WATCHING")

    if current_state == "INDEXING_SCALING":
        pending_runs = state.get("indexing_scale_runs", [])
        if not pending_runs:
            # No runs tracked — fall back to PENDING to avoid false trigger
            print("PENDING")
            return
        failed = failed_runs_for(db_path, pending_runs)
        if failed:
            for run_num, task in sorted(failed.items()):
                print(f"  {task} FAILED for run {run_num}")
            run_list = ",".join(str(r) for r in sorted(failed))
            print(f"FAILED: {run_list}")
            return
        if scale_complete_for_runs(db_path, pending_runs):
            print("SCALE_COMPLETE")
        else:
            print("PENDING")

    elif current_state == "MERGING":
        since = state.get("last_merge_submission_time", "1970-01-01T00:00:00+00:00")
        if merge_complete_since(db_path, since):
            print("MERGE_COMPLETE")
        else:
            print("PENDING")

    else:
        print("PENDING")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(
        description="cctbx-sfx-workflow monitor: eLog and job status checks."
    )
    p.add_argument(
        "--output-dir",
        required=True,
        help="Experiment output directory containing workflow_state.json.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-runs", help="Poll eLog for new tagged runs.")
    sub.add_parser("check-jobs", help="Check lute.db for Scale/Merge task completion.")

    args = p.parse_args()

    if args.cmd == "check-runs":
        cmd_check_runs(args)
    elif args.cmd == "check-jobs":
        cmd_check_jobs(args)


if __name__ == "__main__":
    main()
