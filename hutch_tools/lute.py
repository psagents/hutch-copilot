"""hutch-lute: watch for LUTE jobs, report their status, deploy pedestals.

The tool returns raw data -- job states, log paths, command output. It never
interprets whether an analysis succeeded scientifically; that is the skill's job.

LUTE submission itself is not here: the demo path has the eLog ARP trigger the
workflow, so this only observes it.
"""

from __future__ import annotations

import subprocess
import time

from .common import OK, UNREACHABLE, USAGE, fail, out, parser, results_dir, xtc2_files

#: Job-name fragments that mark a job as a LUTE/analysis workflow.
WORKFLOW_HINTS = ("lute", "dag", "sfx", "xes", "xss", "crystfel", "smalldata",
                  "cctbx", "index", "merge", "scale")


class LuteError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Slurm: find the job, then report on it
# --------------------------------------------------------------------------
def watch_for_jobs(
    user: str,
    *,
    since: str | None = None,
    timeout_s: int = 90,
    poll_s: int = 10,
    names: list[str] | None = None,
    hints: tuple[str, ...] = WORKFLOW_HINTS,
) -> dict:
    """Poll squeue for a workflow job appearing after ``since``.

    Reports how long it actually waited, so "no job found" cannot be confused
    with "did not look".
    """
    deadline = time.monotonic() + timeout_s
    seen: dict[str, dict] = {}
    polls = 0
    while True:
        polls += 1
        proc = subprocess.run(
            ["squeue", "-u", user, "-h", "-o", "%i|%j|%T|%V"],
            capture_output=True, timeout=60,
        )
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            jid, name, state, submit = (p.strip() for p in parts)
            if since and submit < since:
                continue
            lowered = name.lower()
            if (names and name in names) or any(h in lowered for h in hints):
                seen[jid] = {"job_id": jid, "job_name": name, "state": state,
                             "submit_time": submit}
        if seen or time.monotonic() >= deadline:
            break
        time.sleep(poll_s)

    return {
        "found": bool(seen),
        "jobs": sorted(seen.values(), key=lambda j: j["job_id"]),
        "polls": polls,
        "waited_s": timeout_s if not seen else None,
        "user": user,
        "since": since,
    }


def job_status(job_id: str) -> dict:
    """sacct summary for one job, with the wall-clock verdict spelled out.

    ``COMPLETED`` is a Slurm verdict, not a scientific one.
    """
    proc = subprocess.run(
        ["sacct", "-j", str(job_id), "--noheader", "--parsable2",
         "--format=JobID,JobName,State,Elapsed,Timelimit,ExitCode"],
        capture_output=True,
        timeout=60,
    )
    rows = [
        line.split("|")
        for line in proc.stdout.decode("utf-8", "replace").splitlines()
        if line and ".batch" not in line.split("|")[0] and ".extern" not in line.split("|")[0]
    ]
    if not rows:
        return {"job_id": str(job_id), "state": "UNKNOWN", "detail": "no sacct record"}
    job_id_s, name, st, elapsed, limit, exit_code = (rows[0] + [""] * 6)[:6]
    return {
        "job_id": job_id_s,
        "job_name": name,
        "state": st,
        "elapsed": elapsed,
        "time_limit": limit,
        "exit_code": exit_code,
        "hit_wall_clock": st.startswith("TIMEOUT"),
    }


# --------------------------------------------------------------------------
# Pedestals (DARK runs)
# --------------------------------------------------------------------------
#: Direct per-detector tools to fall back on when `makepeds` fails.
#:
#: Incident knowledge, not a preference: `makepeds` has known failures for
#: Jungfrau at MFX (April 2025), documented in Confluence "Example Detector
#: Installation -- Jungfrau 4m" (591662237) and "MFX" (419728769).
PEDS_FALLBACKS = {
    "jungfrau": [
        "jungfrau_dark_proc -k exp={experiment},run={run} -d {detector} -o /tmp/peds_{run}",
        "jungfrau_deploy_constants -k exp={experiment},run={run} -d {detector} "
        "-o /tmp/peds_{run} -D",
    ],
    "epix10ka": [
        "epix10ka_pedestals_calibration -k exp={experiment},run={run} -d {detector}",
        "epix10ka_deploy_constants -k exp={experiment},run={run} -d {detector} -D",
    ],
}

#: Environment the fallback tools need on psdev.
PEDS_ENV = "source /cds/group/pcds/pyps/conda/py39env.sh"


def _detector_family(detector: str) -> str | None:
    for family in PEDS_FALLBACKS:
        if detector.lower().startswith(family):
            return family
    return None


def deploy_pedestals(
    *,
    experiment: str,
    run: int,
    hutch: str,
    detector: str = "",
    operator: str | None = None,
    fallback: bool = False,
    confirm: str | None = None,
) -> dict:
    """Deploy pedestal constants for a DARK run.

    Writes to the calibration store, so it refuses without an explicit write
    confirmation. ``takepeds()`` is not done here -- that runs inside the
    hutch-python session, via ``hutch-daq``.
    """
    if confirm != "write":
        raise LuteError(
            "deploying pedestals writes to the calibration store. Show the command "
            "to the operator, then re-run with --confirm write."
        )

    # Ordering hazard: makepeds reads the XTC2 files, so they must have landed.
    # Deploying before arrival produces pedestals from absent or partial data,
    # and the calibration store then serves them to every later run.
    present = [p.name for p in xtc2_files(hutch, experiment, int(run))]
    if not present:
        raise LuteError(
            f"refusing to deploy pedestals: no XTC2 files found for run {int(run)} "
            f"-- verify the DARK run recorded first."
        )

    operator = operator or f"{hutch.strip().lower()}opr"
    family = _detector_family(detector)

    if fallback:
        if family is None:
            raise LuteError(
                f"no fallback tools known for detector {detector!r}. "
                f"Known families: {', '.join(sorted(PEDS_FALLBACKS))}"
            )
        commands = [
            f"{PEDS_ENV} && " + c.format(experiment=experiment, run=int(run), detector=detector)
            for c in PEDS_FALLBACKS[family]
        ]
    else:
        commands = [f"makepeds -e {experiment} -r {int(run)} -u {operator}"]

    results = []
    for cmd in commands:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "psdev", cmd],
            capture_output=True, timeout=1800,
        )
        results.append({
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace")[-2000:],
            "stderr": proc.stderr.decode("utf-8", "replace")[-2000:],
        })
        if proc.returncode != 0:
            break

    ok = all(r["returncode"] == 0 for r in results)
    out_rec = {
        "ok": ok,
        "route": "fallback" if fallback else "makepeds",
        "detector_family": family,
        "xtc2_files": len(present),
        "steps": results,
        "calib_dir": str(results_dir(hutch, experiment).parent / "calib"),
    }
    if not ok and not fallback and family:
        out_rec["known_issue"] = (
            f"makepeds has documented failures for {family} at MFX (April 2025). "
            f"Re-run with --fallback --detector {detector} to use the direct tools."
        )
    return out_rec


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-lute: watch | status | peds."""


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-lute", _DOC)

    watch_parser = sub.add_parser("watch", help="poll squeue for a workflow job to appear")
    watch_parser.add_argument("--user", required=True)
    watch_parser.add_argument("--since", default=None,
                              help="ignore jobs submitted before this timestamp")
    watch_parser.add_argument("--timeout", type=int, default=90)
    watch_parser.add_argument("--poll", type=int, default=10)
    watch_parser.add_argument("--name", nargs="*", default=None,
                              help="exact job names, in addition to the name hints")

    status_parser = sub.add_parser("status", help="sacct summary for one job")
    status_parser.add_argument("job_id")

    peds_parser = sub.add_parser("peds", help="deploy pedestal constants for a DARK run")
    peds_parser.add_argument("--hutch", required=True)
    peds_parser.add_argument("--experiment", required=True)
    peds_parser.add_argument("--run", required=True, type=int)
    peds_parser.add_argument("--detector", default="",
                             help="needed for --fallback, which is per-detector")
    peds_parser.add_argument("--operator", default=None, help="defaults to <hutch>opr")
    peds_parser.add_argument("--fallback", action="store_true",
                             help="use the direct per-detector tools instead of makepeds")
    peds_parser.add_argument("--confirm", default=None, choices=["write"])

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-lute: watch | status | peds."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "watch":
            res = watch_for_jobs(args.user, since=args.since, timeout_s=args.timeout,
                                 poll_s=args.poll, names=args.name)
            out(res)
            return OK if res["found"] else UNREACHABLE
        if args.cmd == "status":
            return out(job_status(args.job_id))
        if args.cmd == "peds":
            res = deploy_pedestals(
                experiment=args.experiment, run=args.run, hutch=args.hutch,
                detector=args.detector, operator=args.operator,
                fallback=args.fallback, confirm=args.confirm,
            )
            out(res)
            return OK if res["ok"] else UNREACHABLE
    except (LuteError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
