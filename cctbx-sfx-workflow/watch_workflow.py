#!/usr/bin/env python3
"""
cctbx-sfx-workflow / watch_workflow.py

Autonomous watcher that drives the full SFX workflow loop:

  WATCHING
    → poll eLog for new tagged runs
    → --index-run N for each new run
    → INDEXING_SCALING

  INDEXING_SCALING
    → poll lute.db until ScaleCCTBXXFEL COMPLETED
    → render versioned config (vNNN)
    → --merge vNNN_config.yaml
    → MERGING

  MERGING
    → poll lute.db until MergeCCTBXXFEL COMPLETED
    → parse stats + dmin_advisor
    → record iteration in history
    → WATCHING

d_min change flow:
  - dmin_advisor recommendation is written as pending_dmin (advisory).
  - Merges continue at current d_min until user runs:
      iteration_log.py --output-dir DIR approve-dmin --d-min 1.6
  - When approved_dmin is set AND state == WATCHING, the watcher triggers
    --rescale at the new d_min before resuming the normal loop.

Usage:
  python3 watch_workflow.py \\
    --output-dir /path/to/results_dir \\
    --poll-interval 300 \\
    --idle-timeout 30

  --idle-timeout N: exit after N consecutive polls with no state change
                    and no new runs (default: 6; 6 × 300s = 30 min).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Logging — writes to both stdout and {output_dir}/logs/watch_workflow.log
# ---------------------------------------------------------------------------
def setup_logging(output_dir: str) -> logging.Logger:
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "watch_workflow.log")

    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("watch_workflow")
    logger.setLevel(logging.INFO)

    # File handler (append)
    fh = logging.FileHandler(log_path, mode="a")
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(fh)

    # Stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# State helpers (thin wrappers — re-read file on each call so edits take effect)
# ---------------------------------------------------------------------------
_SKILL_DIR = Path(__file__).parent
# setup-cctbx-pipeline is an independent, top-level skill (not bundled alongside
# cctbx-sfx-workflow) — anchor to ~/.claude/skills/ rather than a relative sibling
# path, since cctbx-sfx-workflow's own nesting depth varies by where it's installed
# (standalone top-level skill vs. bundled under hutch-copilot/).
_SETUP_DIR = Path.home() / ".claude" / "skills" / "setup-cctbx-pipeline"


def _run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def load_state(output_dir: str) -> dict:
    import json

    p = os.path.join(output_dir, "workflow_state.json")
    with open(p) as f:
        return json.load(f)


def save_state(output_dir: str, state: dict) -> None:
    import json

    p = os.path.join(output_dir, "workflow_state.json")
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Sub-process wrappers for skill scripts
# ---------------------------------------------------------------------------
def check_runs(output_dir: str, lute_pythonpath: str) -> str:
    """Returns 'NEW_RUNS: [N, ...]' or 'NO_NEW_RUNS'."""
    env = _lute_env(lute_pythonpath)
    r = subprocess.run(
        [
            sys.executable,
            str(_SKILL_DIR / "monitor.py"),
            "--output-dir",
            output_dir,
            "check-runs",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    for line in r.stdout.splitlines():
        if line.startswith("NEW_RUNS:") or line == "NO_NEW_RUNS":
            return line.strip()
    return "NO_NEW_RUNS"


def check_jobs(output_dir: str, logger: Optional[logging.Logger] = None) -> str:
    """Returns PENDING / SCALE_COMPLETE / MERGE_COMPLETE / FAILED:<runs>.

    A FAILED result is preceded by detail lines (which task failed for which
    run) — log every line so the diagnostic detail lands in the watcher log,
    not just the final control-flow line.
    """
    r = subprocess.run(
        [
            sys.executable,
            str(_SKILL_DIR / "monitor.py"),
            "--output-dir",
            output_dir,
            "check-jobs",
        ],
        capture_output=True,
        text=True,
    )
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if logger:
        for line in lines[:-1]:
            logger.info("  %s", line)
    return lines[-1].strip() if lines else "PENDING"


def next_version(output_dir: str) -> str:
    r = _run(
        [
            sys.executable,
            str(_SKILL_DIR / "iteration_log.py"),
            "--output-dir",
            output_dir,
            "next-version",
        ]
    )
    return r.stdout.strip().split("\n")[-1].strip()


def record_iteration(
    output_dir: str, version: str, runs: List[int], d_min: float, trigger: str
) -> None:
    _run(
        [
            sys.executable,
            str(_SKILL_DIR / "iteration_log.py"),
            "--output-dir",
            output_dir,
            "record",
            "--version",
            version,
            "--runs",
        ]
        + [str(r) for r in runs]
        + ["--d-min", str(d_min), "--trigger", trigger]
    )


def render_versioned_config(
    state: dict, version: str, d_min: Optional[float] = None
) -> str:
    """Render a versioned YAML config. Returns path to the generated file."""
    ra = state["render_args"]
    experiment = state["experiment"]
    output_dir = state["output_dir"]
    work_dir = state.get("work_dir", output_dir)
    runs = state.get("known_runs", [])
    tag = state["tag"]
    if d_min is None:
        d_min = state["current_d_min"]

    cmd = (
        [
            sys.executable,
            str(_SETUP_DIR / "render.py"),
            "--output-dir",
            output_dir,
            "--experiment",
            experiment,
            "--runs",
        ]
        + [str(r) for r in runs]
        + [
            "--tag",
            tag,
            "--work-dir",
            work_dir,
            "--pdb",
            ra["pdb"],
            "--geometry",
            ra["geometry"],
            "--mask",
            ra["mask"],
            "--d-min",
            str(d_min),
            "--detector-address",
            ra["detector_address"],
            "--two-theta-low",
            str(ra.get("two_theta_low", 12.5)),
            "--two-theta-high",
            str(ra.get("two_theta_high", 22.8)),
            "--merge-version",
            version,
        ]
    )
    if ra.get("wavelength_offset") is not None:
        cmd += ["--wavelength-offset", str(ra["wavelength_offset"])]
    if ra.get("spectrum_ev_per_pixel") is not None:
        cmd += ["--spectrum-ev-per-pixel", str(ra["spectrum_ev_per_pixel"])]
    if ra.get("spectrum_ev_offset") is not None:
        cmd += ["--spectrum-ev-offset", str(ra["spectrum_ev_offset"])]

    _run(cmd)
    yaml_name = f"{experiment}_cctbx_{version}.yaml"
    return os.path.join(output_dir, yaml_name)


def submit_index_run(state: dict, run_num: int, logger: logging.Logger) -> None:
    submit_script = _submit_script(state)

    # Track attempts separately from known_runs — a retried run is dropped
    # from known_runs so it's re-detected as "new", but the attempt count
    # must survive that drop or a persistently-broken run would retry forever.
    output_dir = state["output_dir"]
    fresh_state = load_state(output_dir)
    attempts = fresh_state.setdefault("run_attempts", {})
    key = str(run_num)
    attempts[key] = attempts.get(key, 0) + 1
    save_state(output_dir, fresh_state)

    logger.info(
        "Submitting --index-run %d (attempt %d)", run_num, attempts[key]
    )
    r = subprocess.run(
        ["bash", submit_script, "--index-run", str(run_num)],
        capture_output=True,
        text=True,
    )
    for line in r.stdout.splitlines():
        logger.info("  %s", line)
    if r.returncode != 0:
        logger.error("--index-run %d failed: %s", run_num, r.stderr.strip())


def handle_failed_runs(
    output_dir: str, failed_run_list: List[int], logger: logging.Logger
) -> None:
    """Drop a failed run from known_runs so it's retried automatically, up to
    max_run_retries. A run that fails again after its retries are exhausted
    is instead kept in known_runs (so it stops being re-submitted forever)
    and recorded in failed_runs for the operator/agent to investigate."""
    state = load_state(output_dir)
    max_retries = state.get("max_run_retries", 1)
    run_attempts = state.get("run_attempts", {})
    failed_runs_record = state.get("failed_runs", {})
    known = set(state.get("known_runs", []))

    retry_runs = []
    give_up_runs = []
    for run_num in failed_run_list:
        attempts = run_attempts.get(str(run_num), 1)
        if attempts <= max_retries:
            retry_runs.append(run_num)
            known.discard(run_num)
        else:
            give_up_runs.append(run_num)
            known.add(run_num)

    if retry_runs:
        logger.warning(
            "Run(s) %s failed — removed from known_runs for automatic retry "
            "(attempt %d of max %d).",
            retry_runs,
            run_attempts.get(str(retry_runs[0]), 1),
            max_retries,
        )

    for run_num in give_up_runs:
        key = str(run_num)
        failed_runs_record[key] = {
            "attempts": run_attempts.get(key, 1),
            "last_failed": datetime.now(timezone.utc).isoformat(),
        }
        work_dir = state.get("work_dir", output_dir)
        logger.error(
            "Run %d failed %d time(s) (retries exhausted) — will NOT be "
            "retried automatically. To diagnose: check %s/r%04d/index/logs/ "
            "and %s/r%04d/scale/ for the underlying cctbx.xfel error. Common "
            "causes: wrong --detector-address, a bad/misaligned geometry file, "
            "or a genuinely bad run (detector glitch, no crystal hits). Once "
            "fixed, resubmit manually with: bash submit_cctbx_%s.sh "
            "--index-run %d — or exclude the run from the tag if it's bad "
            "data. If the cause isn't obvious from the logs, ask an agent "
            "(e.g. Claude via hutch-copilot) to read those logs and diagnose.",
            run_num,
            run_attempts.get(key, 1),
            work_dir,
            run_num,
            work_dir,
            run_num,
            state["experiment"],
            run_num,
        )

    state["known_runs"] = sorted(known)
    state["failed_runs"] = failed_runs_record
    remaining = [
        r
        for r in state.get("indexing_scale_runs", [])
        if r not in failed_run_list
    ]
    state["indexing_scale_runs"] = remaining
    if not remaining:
        state["state"] = "WATCHING"
        logger.info("State → WATCHING (nothing left to wait on in this batch)")
    save_state(output_dir, state)


def submit_merge(state: dict, config_path: str, logger: logging.Logger) -> None:
    submit_script = _submit_script(state)
    logger.info("Submitting --merge %s", os.path.basename(config_path))
    r = subprocess.run(
        ["bash", submit_script, "--merge", config_path], capture_output=True, text=True
    )
    for line in r.stdout.splitlines():
        logger.info("  %s", line)
    if r.returncode != 0:
        logger.error("--merge failed: %s", r.stderr.strip())


def submit_rescale(state: dict, config_path: str, logger: logging.Logger) -> None:
    submit_script = _submit_script(state)
    logger.info("Submitting --rescale %s", os.path.basename(config_path))
    r = subprocess.run(
        ["bash", submit_script, "--rescale", config_path],
        capture_output=True,
        text=True,
    )
    for line in r.stdout.splitlines():
        logger.info("  %s", line)
    if r.returncode != 0:
        logger.error("--rescale failed: %s", r.stderr.strip())


def parse_stats(log_path: str, logger: logging.Logger) -> Optional[object]:
    if not os.path.exists(log_path):
        logger.warning("Merge log not found: %s", log_path)
        return None
    r = _run(
        [sys.executable, str(_SKILL_DIR / "stats_parser.py"), "--log", log_path],
        check=False,
    )
    for line in r.stdout.splitlines():
        logger.info("  %s", line)
    # Return the parsed stats object for dmin advisor
    sys.path.insert(0, str(_SKILL_DIR))
    from stats_parser import parse_log

    return parse_log(log_path)


def run_dmin_advisor(
    log_path: str, current_dmin: float, logger: logging.Logger
) -> Optional[float]:
    """Returns recommended d_min float or None if no change."""
    r = _run(
        [
            sys.executable,
            str(_SKILL_DIR / "dmin_advisor.py"),
            "--log",
            log_path,
            "--current-dmin",
            str(current_dmin),
        ],
        check=False,
    )
    output = r.stdout.strip()
    for line in output.splitlines():
        logger.info("  dmin_advisor: %s", line)
    first_line = output.splitlines()[0] if output else ""
    if first_line.startswith("RECOMMEND_DMIN:"):
        return float(first_line.split(":")[1].strip())
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _submit_script(state: dict) -> str:
    experiment = state["experiment"]
    output_dir = state["output_dir"]
    return os.path.join(output_dir, f"submit_cctbx_{experiment}.sh")


def _lute_env(lute_pythonpath: str) -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{lute_pythonpath}:{existing}" if existing else lute_pythonpath
    return env


def _merge_log_path(state: dict, version: str) -> str:
    work_dir = state.get("work_dir", state["output_dir"])
    tag = state["tag"]
    return os.path.join(work_dir, f"merge_{tag}", version, f"merge_{tag}_main.log")


def _parse_new_runs(line: str) -> List[int]:
    """Parse 'NEW_RUNS: [38, 39]' → [38, 39]."""
    import ast

    return ast.literal_eval(line.split(":", 1)[1].strip())


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_watcher(
    output_dir: str,
    poll_interval: int,
    idle_timeout: int,
    lute_pythonpath: str,
    submit_stagger: int = 60,
) -> None:
    logger = setup_logging(output_dir)
    logger.info("=" * 60)
    logger.info("watch_workflow starting")
    logger.info("  output_dir      : %s", output_dir)
    logger.info("  poll_interval   : %ds", poll_interval)
    logger.info(
        "  idle_timeout    : %d polls (%.0f min)",
        idle_timeout,
        idle_timeout * poll_interval / 60,
    )
    logger.info("  submit_stagger  : %ds between per-run indexer submissions", submit_stagger)
    logger.info("=" * 60)

    idle_count = 0

    while True:
        state = load_state(output_dir)
        current_state = state.get("state", "WATCHING")
        acted = False

        # ------------------------------------------------------------------
        # Priority check: approved d_min change, only when WATCHING
        # ------------------------------------------------------------------
        approved_dmin = state.get("approved_dmin")
        if (
            approved_dmin is not None
            and approved_dmin != state["current_d_min"]
            and current_state == "WATCHING"
        ):
            logger.info(
                "Approved d_min change: %.2f → %.2f Å",
                state["current_d_min"],
                approved_dmin,
            )
            ver = next_version(output_dir)
            logger.info(
                "Rendering versioned config %s at d_min=%.2f", ver, approved_dmin
            )
            config_path = render_versioned_config(state, ver, d_min=approved_dmin)
            submit_rescale(state, config_path, logger)
            # Update state
            state = load_state(output_dir)
            state["state"] = "MERGING"
            state["current_version"] = ver
            state["current_d_min"] = approved_dmin
            state["last_merge_submission_time"] = datetime.now(timezone.utc).isoformat()
            state.pop("approved_dmin", None)
            state.pop("pending_dmin", None)
            save_state(output_dir, state)
            logger.info("State → MERGING (dmin_change, %s)", ver)
            acted = True

        # ------------------------------------------------------------------
        elif current_state == "WATCHING":
            result = check_runs(output_dir, lute_pythonpath)
            logger.info("check-runs: %s", result)
            if result.startswith("NEW_RUNS:"):
                new_runs = _parse_new_runs(result)
                logger.info("New runs detected: %s", new_runs)
                for i, run_num in enumerate(new_runs):
                    if i > 0:
                        # Stagger heavy (4-node/240-task) indexer submissions —
                        # two landing on the same node at the same instant can
                        # collide launching MPI's per-node daemon (srun exit
                        # 36096). Not a guaranteed fix (SLURM still owns node
                        # placement), just a cheap way to cut how often two
                        # submissions land at literally the same moment.
                        logger.info(
                            "Waiting %ds before submitting next run "
                            "(stagger to avoid node-launch collisions)...",
                            submit_stagger,
                        )
                        time.sleep(submit_stagger)
                    submit_index_run(state, run_num, logger)
                # Update state
                state = load_state(output_dir)
                state["state"] = "INDEXING_SCALING"
                state["indexing_scale_runs"] = sorted(new_runs)
                known = set(state.get("known_runs", []))
                known.update(new_runs)
                state["known_runs"] = sorted(known)
                save_state(output_dir, state)
                logger.info("State → INDEXING_SCALING (runs %s)", new_runs)
                acted = True

        # ------------------------------------------------------------------
        elif current_state == "INDEXING_SCALING":
            result = check_jobs(output_dir, logger)
            logger.info("check-jobs: %s", result)
            if result == "SCALE_COMPLETE":
                state = load_state(output_dir)
                ver = next_version(output_dir)
                logger.info(
                    "Scale complete. Rendering versioned config %s at d_min=%.2f",
                    ver,
                    state["current_d_min"],
                )
                config_path = render_versioned_config(state, ver)
                submit_merge(state, config_path, logger)
                state = load_state(output_dir)
                state["state"] = "MERGING"
                state["current_version"] = ver
                state["last_merge_submission_time"] = datetime.now(
                    timezone.utc
                ).isoformat()
                state.pop("indexing_scale_runs", None)
                save_state(output_dir, state)
                logger.info("State → MERGING (%s)", ver)
                acted = True
            elif result.startswith("FAILED:"):
                run_list_str = result.split(":", 1)[1].strip()
                failed_run_list = [int(x) for x in run_list_str.split(",") if x]
                handle_failed_runs(output_dir, failed_run_list, logger)
                acted = True

        # ------------------------------------------------------------------
        elif current_state == "MERGING":
            result = check_jobs(output_dir, logger)
            logger.info("check-jobs: %s", result)
            if result == "MERGE_COMPLETE":
                state = load_state(output_dir)
                ver = state["current_version"]
                log_path = _merge_log_path(state, ver)
                logger.info("Merge complete (%s). Waiting 30s for log flush...", ver)
                time.sleep(30)
                logger.info("Parsing stats...")
                parse_stats(log_path, logger)

                # d_min advisor
                rec_dmin = run_dmin_advisor(log_path, state["current_d_min"], logger)
                if rec_dmin is not None and rec_dmin != state.get("pending_dmin"):
                    logger.info(
                        "d_min change recommended: %.2f → %.2f Å  "
                        "(merges continue at current d_min until approved)",
                        state["current_d_min"],
                        rec_dmin,
                    )
                    logger.info(
                        "  To approve, run: python3 %s/iteration_log.py "
                        "--output-dir %s approve-dmin --d-min %.2f",
                        _SKILL_DIR,
                        output_dir,
                        rec_dmin,
                    )
                    state["pending_dmin"] = rec_dmin

                # Record iteration
                trigger = (
                    "dmin_change" if state.get("_dmin_change_trigger") else "new_run"
                )
                state.pop("_dmin_change_trigger", None)
                record_iteration(
                    output_dir,
                    ver,
                    state["known_runs"],
                    state["current_d_min"],
                    trigger,
                )

                # Reload after record (it saves state)
                state = load_state(output_dir)
                if rec_dmin is not None:
                    state["pending_dmin"] = rec_dmin
                state["state"] = "WATCHING"
                save_state(output_dir, state)
                logger.info("State → WATCHING")
                acted = True

        # ------------------------------------------------------------------
        if acted:
            idle_count = 0
        else:
            idle_count += 1
            logger.info("No action taken (idle %d/%d)", idle_count, idle_timeout)

        if idle_count >= idle_timeout:
            logger.info(
                "Idle timeout reached (%d polls, %.0f min). Exiting.",
                idle_timeout,
                idle_timeout * poll_interval / 60,
            )
            break

        logger.info("Sleeping %ds...", poll_interval)
        time.sleep(poll_interval)

    logger.info("watch_workflow exiting cleanly.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(
        description="Autonomous watcher for the cctbx-sfx-workflow."
    )
    p.add_argument(
        "--output-dir",
        required=True,
        help="Experiment output directory containing workflow_state.json.",
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=300,
        help="Seconds between polls (default: 300).",
    )
    p.add_argument(
        "--idle-timeout",
        type=int,
        default=30,
        help="Exit after this many consecutive idle polls (default: 30).",
    )
    p.add_argument(
        "--lute-pythonpath",
        default=None,
        help="Path to LUTE install lib/pythonX.Y/site-packages. "
        "Auto-detected from {output_dir}/lute/ if not given.",
    )
    p.add_argument(
        "--submit-stagger",
        type=int,
        default=60,
        help="Seconds to wait between submitting each new run's indexer "
        "when multiple runs are detected at once (default: 60).",
    )
    args = p.parse_args()

    # Auto-detect LUTE pythonpath from work_dir/lute/install/
    lute_pythonpath = args.lute_pythonpath
    if lute_pythonpath is None:
        state = __import__("json").load(
            open(os.path.join(args.output_dir, "workflow_state.json"))
        )
        work_dir = state.get("work_dir", args.output_dir)
        lute_install = os.path.join(
            work_dir, "lute", "install", "lib", "python3.9", "site-packages"
        )
        if os.path.isdir(lute_install):
            lute_pythonpath = lute_install
        else:
            lute_pythonpath = ""

    run_watcher(
        output_dir=args.output_dir,
        poll_interval=args.poll_interval,
        idle_timeout=args.idle_timeout,
        lute_pythonpath=lute_pythonpath,
        submit_stagger=args.submit_stagger,
    )


if __name__ == "__main__":
    main()
