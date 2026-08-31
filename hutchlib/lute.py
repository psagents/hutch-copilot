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

from . import traps

PSCONDA = "/sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh"
DEFAULT_PARTITION = "milano"


class LuteError(RuntimeError):
    pass


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
        # LUTE strips the LUTE_TENV_ prefix and passes the rest into the Task
        # environment; its own MCA dict does not set prte_tmpdir_base, so this
        # value survives into mpirun.
        prte = (
            'export LUTE_TENV_PRTE_MCA_prte_tmpdir_base="/tmp/prte_${SLURM_JOB_ID}"\n'
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
    violations = traps.check("sbatch", {"sbatch_text": text, "mpi": mpi})
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
