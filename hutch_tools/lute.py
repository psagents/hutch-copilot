"""LUTE submission adapter -- Category A, task-agnostic.

Nothing here is CCTBX-specific. Every LUTE managed task needs the same five
things, and LUTE's own launcher gets four of them wrong for any use that is not
"triggered from the eLog with a live Kerberos ticket":

  1. ``--time``. LUTE sets none, so jobs inherit the partition default.
  2. Kerberos. ``launch_slurm`` (= launch_maestro:main) hard-exits when
     KRB5CCNAME is unset -- but a pipeline that does not touch the eLog needs
     no ticket at all.
  3. PYTHONPATH ordering vs psconda.sh, which clobbers it.
  4. A per-job PRRTE session directory for MPI tasks.
  5. Propagating the managed Task's real exit status.

The task itself still runs through LUTE's own ``run_task.py``, i.e. the real
managed Task/Executor path. Only the launcher differs.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess

from ._cli import OK, UNREACHABLE, USAGE, fail, out, parser
from .paths import results_dir, xtc2_files
# Aliased: bare `check`/`registry` would not say what is being checked.
from .state import MissingValue, StaleValue, State
from .traps import check as check_traps
from .traps import registry as trap_registry

PSCONDA = "/sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh"
DEFAULT_PARTITION = "milano"


class LuteError(RuntimeError):
    pass


#: Config keys whose value is a per-run path LUTE expects to already exist.
#: ``in_file`` and ``phil_file`` are the load-bearing pair: LUTE writes both
#: while *parsing the config*, before any Task starts.
_RUN_PATH_KEYS = ("phil_file", "in_file", "output_output_dir", "output_logging_dir")


def ensure_run_dirs(config_path: str, run: int) -> list[str]:
    """Create the per-run directories this config's own paths imply.

    ``IndexCCTBXXFEL.in_file`` is a psana2 locator that LUTE writes during
    pydantic validation of the config -- before the Task launches, and with no
    ``mkdir`` of its own::

        FileNotFoundError: [Errno 2] No such file or directory:
          '.../iters/iter002/r0036/data_mfx101624926_36.loc'

    Every config that puts per-run files under ``{{ work_dir }}/r{{ run:04d }}``
    -- which is the layout that stops parallel runs racing on a shared phil
    file -- therefore fails on a work_dir that has not been used before. The
    directory cannot be created by ``hutch-config``, which never sees a run
    number (``{{ run }}`` is a LUTE-runtime placeholder), so it belongs here:
    ``sbatch`` is the first step that knows the config *and* the run.

    Regex rather than PyYAML on purpose -- this module is stdlib-only so that
    ``status``/``watch``/``kerberos`` keep working without the psana env.
    """
    text = pathlib.Path(config_path).read_text()
    m = re.search(r"^work_dir:\s*(.+?)\s*$", text, re.M)
    if not m:
        raise LuteError(f"{config_path} has no top-level work_dir: key")
    work_dir = m.group(1).strip().strip('"').strip("'")

    def resolve(raw: str) -> str:
        return (raw.replace("{{ work_dir }}", work_dir)
                   .replace("{{ run:04d }}", f"{int(run):04d}")
                   .replace("{{ run }}", str(int(run))))

    created: list[str] = []
    pat = re.compile(rf"^\s*({'|'.join(_RUN_PATH_KEYS)}):\s*(.+?)\s*$", re.M)
    for key, raw in pat.findall(text):
        value = resolve(raw.strip().strip('"').strip("'"))
        if "{{" in value:          # an unresolved placeholder -- not ours to guess
            continue
        # a *_dir key names the directory itself; the rest name a file inside one
        path = pathlib.Path(value)
        target = path if key.endswith("_dir") else path.parent
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(str(target))
    return created


def needs_kerberos(yaml_text: str) -> tuple[bool, str]:
    """Does this config require a Kerberos ticket?

    Only eLog interaction does. ``MergeCCTBXXFEL.phil_parameters.tag`` resolves
    through ``get_elog_runs_by_tag`` -> ``krtc.KerberosTicket``; supplying an
    explicit ``input_path`` short-circuits it entirely. The campaign ran the
    full pipeline with no ticket on the host.

    ``analyze-data``'s setup command makes a valid ticket an unconditional gate
    before anything proceeds, which would have blocked all ten iterations.
    """
    if re.search(r"^\s+tag:\s*\S", yaml_text, re.M):
        return True, "config resolves inputs via an eLog tag (get_elog_runs_by_tag)"
    return False, "config supplies explicit input paths; no eLog interaction"


def have_ticket() -> bool:
    try:
        return subprocess.run(["klist", "-s"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def build_sbatch(
    *,
    task: str,
    config: str,
    log_dir: str,
    lute_path: str,
    experiment: str,
    run: int,
    time_limit: str,
    nodes: int = 1,
    ntasks_per_node: int = 1,
    partition: str = DEFAULT_PARTITION,
    account: str | None = None,
    job_name: str | None = None,
    mpi: bool = True,
) -> str:
    """Render a submission script that survives all five traps above."""
    account = account or f"lcls:{experiment}"
    job_name = job_name or f"{task}_r{run:04d}"
    log = f"{log_dir}/{task}_%j.out"

    prte = ""
    if mpi:
        # Per-job PRRTE session dir, set two ways and created on every node.
        #
        # The LUTE_TENV_ form is what LUTE forwards into the Task environment
        # after stripping the prefix. The bare PRTE_MCA_ form is exported here so
        # mpirun inherits it directly from this script -- belt and braces,
        # because the CCTBX tasks are ThirdPartyTasks and relying on LUTE's env
        # forwarding alone left concurrent jobs colliding.
        #
        # The mkdir has to run on EVERY node in the allocation: /tmp is
        # node-local, so a base created only on the batch node leaves the remote
        # daemons with nowhere to put their session dir, which surfaces as
        # "srun returned non-zero exit status (36096) from launching the
        # per-node daemon" -- the prte-session-collision trap, seen again in
        # jobs 36669529 and 36669546 while sharing nodes with 36669535/36669553.
        prte = (
            'export LUTE_TENV_PRTE_MCA_prte_tmpdir_base="/tmp/prte_${SLURM_JOB_ID}"\n'
            'export PRTE_MCA_prte_tmpdir_base="/tmp/prte_${SLURM_JOB_ID}"\n'
            'srun --ntasks=${SLURM_NNODES} --ntasks-per-node=1 '
            'mkdir -p "/tmp/prte_${SLURM_JOB_ID}" 2>/dev/null || '
            'mkdir -p "/tmp/prte_${SLURM_JOB_ID}"\n'
        )

    text = f"""#!/bin/bash
#SBATCH --partition={partition}
#SBATCH --account={account}
#SBATCH --time={time_limit}
#SBATCH --nodes={nodes}
#SBATCH --ntasks-per-node={ntasks_per_node}
#SBATCH --cpus-per-task=1
#SBATCH --job-name={job_name}
#SBATCH --output={log}
#SBATCH --error={log}

# psconda FIRST -- it clobbers PYTHONPATH -- then prepend the LUTE checkout.
source {PSCONDA}
export PYTHONPATH="{lute_path}:${{PYTHONPATH}}"
export LUTE_PATH="{lute_path}"
export EXPERIMENT="{experiment}"
export RUN_NUM="{run}"
export RUN="{run}"
export LUTE_USE_TCP=1
{prte}
cd {shlex.quote(log_dir)}
echo "=== $(date -Is) {task} job ${{SLURM_JOB_ID}} on ${{SLURM_JOB_NODELIST}} ==="
scontrol show job ${{SLURM_JOB_ID}} | grep -E 'TimeLimit|NumNodes|NumCPUs' || true
python -OB {shlex.quote(lute_path)}/run_task.py -c {shlex.quote(config)} -t {task}
RC=$?
echo "=== $(date -Is) {task} exit=${{RC}} ==="
# run_task.py exits 0 even when the managed Task failed, so inspect the log too.
if grep -q "Task failed with return code" "{log_dir}/{task}_${{SLURM_JOB_ID}}.out" 2>/dev/null; then
    echo "=== {task}: LUTE reported Task failure -> failing the Slurm job ==="
    exit 1
fi
exit ${{RC}}
"""
    violations = check_traps("sbatch", {"sbatch_text": text, "mpi": mpi})
    if violations:  # pragma: no cover - guards against future edits to this template
        detail = "\n".join(f"  [{v['id']}] {v['problem']}" for v in violations)
        raise LuteError(f"generated sbatch violates known traps:\n{detail}")
    return text


def submit(
    sbatch_text: str,
    *,
    script_path: str,
    dependency: str | None = None,
    dry_run: bool = False,
) -> str:
    """Write the script and sbatch it. Returns the job id (or the path if dry-run)."""
    p = pathlib.Path(script_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sbatch_text)
    p.chmod(0o755)
    if dry_run:
        return f"DRY-RUN wrote {p}"

    argv = ["sbatch", "--parsable"]
    if dependency:
        # afterany, not afterok: a task killed at its wall-clock limit still
        # leaves usable per-image output on disk.
        argv.append(f"--dependency=afterany:{dependency}")
    argv.append(str(p))
    proc = subprocess.run(argv, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise LuteError(f"sbatch failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode().strip().splitlines()[0]


#: Job-name fragments that indicate a LUTE-triggered workflow job.
WORKFLOW_HINTS = ("lute", "dag", "sfx", "xes", "xss", "crystfel", "smalldata",
                  "geom", "dark", "peds", "cctbx", "bayfai")


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

    Replaces the prose polling loop that ``take-run`` Phase 5 described and
    analysis monitoring described again, differently. Reports how long it
    actually waited, so "no job found" cannot be confused with "did not look".
    """
    import time

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

    Used by both ``take-run`` Phase 5 and analysis monitoring, which previously
    each described squeue/sacct interpretation in their own prose.
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
#: This is incident knowledge, not a preference: `makepeds` has known failures
#: for Jungfrau at MFX (April 2025), documented in Confluence "Example Detector
#: Installation — Jungfrau 4m" (591662237) and "MFX" (419728769). It used to live
#: in a 151-line prose reference that also carried deprecated `/reg/g/pcds` paths;
#: encoding it here means the tool hands you the fallback instead of you having to
#: remember that the reference exists.
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

#: Environment the fallback tools need on psdev. /cds is canonical; /reg is the
#: deprecated alias the old reference file still used.
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
    confirmation -- same rule the bridge applies to a device move.

    ``takepeds()`` is NOT done here: it runs inside the hutch-python session and
    goes through ``hutch-bridge``. This is only the analysis-side half.
    """
    if confirm != "write":
        raise LuteError(
            "deploying pedestals writes to the calibration store. Show the command "
            "to the operator, then re-run with --confirm write."
        )

    # Ordering hazard: makepeds reads the XTC2 files, so they must have landed.
    present = [p.name for p in xtc2_files(hutch, experiment, int(run))]
    violations = check_traps("peds", {"xtc2_files": present, "run": int(run)})
    if violations:
        detail = "\n".join(f"  [{v['id']}] {v['problem']}" for v in violations)
        raise LuteError(f"refusing to deploy pedestals:\n{detail}")

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
# Is LUTE configured for this experiment?
# --------------------------------------------------------------------------
def readiness(hutch: str, experiment: str, directory: str | None = None) -> dict:
    """Read-only check of whether LUTE is configured. Never installs anything.

    Session start needs to *report* this, not fix it: installing LUTE creates a
    venv, patches a DAG and registers eLog workflows -- all writes, none of which
    should happen as a side effect of someone asking whether we are ready.

    Deliberately offline-capable: it inspects artifacts on disk plus recorded
    state, and does not try to confirm eLog registration, which would need a
    ticket. It reports what it could and could not check.
    """
    base = results_dir(hutch, experiment, directory)
    out_dir = base / "lute_output"
    artifacts = {
        "lute_output_dir": out_dir.is_dir(),
        "lute_db": (out_dir / "lute.db").exists(),
        "config_yaml": sorted(p.name for p in out_dir.glob("*_lute.yaml")) if out_dir.is_dir() else [],
        "dags": sorted(p.name for p in out_dir.glob("*.dag")) if out_dir.is_dir() else [],
    }

    recorded: dict = {"lute_config": None, "note": None}
    try:
        recorded["lute_config"] = State(hutch, experiment).get("lute_config", stale_ok=True)
    except StaleValue as exc:      # pragma: no cover - lute_config has no TTL
        recorded["note"] = str(exc)
    except MissingValue:
        recorded["note"] = "lute_config not recorded in state"

    configured = bool(artifacts["config_yaml"] and artifacts["dags"] and artifacts["lute_db"])
    return {
        "configured": configured,
        "artifacts": artifacts,
        "recorded": recorded,
        "not_checked": ["eLog workflow registration (needs a Kerberos ticket)"],
        "next": None if configured else "run /analyze-data to configure LUTE for this experiment",
        "lute_output_dir": str(out_dir),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-lute: sbatch | submit | status | watch | peds | readiness | traps | kerberos.

    Config rendering is a separate binary (``hutch-config``): it is a different
    lifecycle phase, and it is the only part that needs jinja2 and PyYAML, so
    keeping it out leaves this command stdlib-only.
    """


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser.

    Separate from main() so the CLI surface can be introspected -- the
    doc-drift test walks every invocation in the markdown and checks its
    subcommand and flags against these parsers.
    """

    ap, sub = parser("hutch-lute", _DOC)

    sb = sub.add_parser("sbatch", help="render a submission script")
    sb.add_argument("--task", required=True)
    sb.add_argument("--config", required=True)
    sb.add_argument("--log-dir", required=True)
    sb.add_argument("--lute-path", required=True)
    sb.add_argument("--experiment", required=True)
    sb.add_argument("--run", required=True, type=int)
    sb.add_argument("--time", dest="time_limit", required=True,
                    help="Slurm walltime, e.g. 00:10:00 -- mandatory, LUTE sets none")
    sb.add_argument("--nodes", type=int, default=1)
    sb.add_argument("--ntasks-per-node", type=int, default=1)
    sb.add_argument("--no-mpi", action="store_true")
    sb.add_argument("--submit", action="store_true")
    sb.add_argument("--dependency", default=None)
    sb.add_argument("--script-path", default=None)

    sub.add_parser("status").add_argument("job_id")

    w = sub.add_parser("watch", help="poll squeue for a workflow job to appear")
    w.add_argument("--user", required=True, help="e.g. mfxopr")
    w.add_argument("--since", default=None, help="ISO timestamp; ignore older jobs")
    w.add_argument("--timeout", type=int, default=90)
    w.add_argument("--names", nargs="*", default=None, help="exact workflow names to match")

    tr = sub.add_parser("traps", help="list the incident register, or check a file")
    tr.add_argument("--scope", default=None,
                    choices=["sbatch", "lute-yaml", "run-params", "phil-overrides"])
    tr.add_argument("--yaml", default=None)

    kb = sub.add_parser("kerberos", help="does this config actually need a ticket?")
    kb.add_argument("--yaml", required=True)

    pd = sub.add_parser("peds", help="deploy pedestal constants for a DARK run")
    pd.add_argument("--experiment", required=True)
    pd.add_argument("--run", required=True, type=int)
    pd.add_argument("--hutch", required=True)
    pd.add_argument("--detector", default="", help="e.g. jungfrau -- selects fallback tools")
    pd.add_argument("--operator", default=None, help="defaults to {hutch}opr")
    pd.add_argument("--fallback", action="store_true",
                    help="use the direct per-detector tools instead of makepeds")
    pd.add_argument("--confirm", default=None, choices=["write"],
                    help="required: this writes to the calibration store")

    rd = sub.add_parser("readiness", help="is LUTE configured for this experiment? read-only")
    rd.add_argument("--hutch", required=True)
    rd.add_argument("--experiment", required=True)
    rd.add_argument("--directory", default=None, help="optional subdir under results/")

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-lute: sbatch | submit | status | watch | traps | kerberos.

    Config rendering is a separate binary (``hutch-config``): it is a different
    lifecycle phase, and it is the only part that needs jinja2 and PyYAML, so
    keeping it out leaves this command stdlib-only.
    """
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "sbatch":
            text = build_sbatch(
                task=args.task, config=args.config, log_dir=args.log_dir,
                lute_path=args.lute_path, experiment=args.experiment, run=args.run,
                time_limit=args.time_limit, nodes=args.nodes,
                ntasks_per_node=args.ntasks_per_node, mpi=not args.no_mpi,
            )
            if not args.submit:
                return out(text, plain=True)
            # LUTE writes in_file/phil_file while parsing the config, so their
            # per-run directory has to exist before the job starts, not when
            # the Task does. Reported, not silent: a created list that is
            # unexpectedly long means the work_dir was not what you thought.
            made = ensure_run_dirs(args.config, args.run)
            path = args.script_path or f"{args.log_dir}/sbatch_{args.task}.sh"
            return out({"job_id": submit(text, script_path=path,
                                              dependency=args.dependency),
                        "created_dirs": made})
        if args.cmd == "status":
            return out(job_status(args.job_id))
        if args.cmd == "watch":
            res = watch_for_jobs(args.user, since=args.since,
                                 timeout_s=args.timeout, names=args.names)
            out(res)
            return OK if res["found"] else UNREACHABLE
        if args.cmd == "traps":
            if args.yaml:
                return out(check_traps(args.scope or "lute-yaml",
                                            {"yaml_text": open(args.yaml).read()}))
            reg = trap_registry()
            return out([e for e in reg
                             if not args.scope or e["applies_to"] == args.scope])
        if args.cmd == "peds":
            res = deploy_pedestals(
                experiment=args.experiment, run=args.run, hutch=args.hutch,
                detector=args.detector, operator=args.operator,
                fallback=args.fallback, confirm=args.confirm,
            )
            out(res)
            return OK if res["ok"] else UNREACHABLE
        if args.cmd == "readiness":
            res = readiness(args.hutch, args.experiment, args.directory)
            out(res)
            return OK if res["configured"] else UNREACHABLE
        if args.cmd == "kerberos":
            needed, why = needs_kerberos(open(args.yaml).read())
            return out({"needs_kerberos": needed, "why": why,
                             "have_ticket": have_ticket()})
    except (LuteError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
