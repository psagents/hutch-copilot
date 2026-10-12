"""hutch-lute: everything LUTE -- submission, monitoring, pedestals, config, workspace.

The tool returns raw data -- job states, log paths, command output, parsed
reports. It never interprets whether an analysis succeeded scientifically; that
is the skill's job.

Submission is here but is not the demo path: the eLog ARP triggers the workflow,
so ``watch``/``status`` only observe it. Checked against LUTE v0.3.0/dev at
``/sdf/group/lcls/ds/tools/lute`` on Sep 25 -- most of what this module used to
claim as a gap has since been fixed upstream, and what remains is listed here so
the next reader does not have to re-audit it.

``sbatch`` exists for three things ``launch_scripts/submit_slurm.py`` still does
not do:

  1. ``--time``. LUTE sets none -- it injects only ``--output``/``--error`` and
     passes every other ``#SBATCH`` through, so a job inherits the partition
     default (24 h on milano). LUTE's own ``task_timeout`` is application-level,
     not a wall clock.
  2. Kerberos is not required. A pipeline with explicit input paths touches no
     eLog, so it needs no ticket; ``needs_kerberos`` answers that positively
     rather than warning and degrading.
  3. A per-job PRRTE session directory. LUTE sets ``PRTE_MCA_*`` and a hostfile
     (tasks/task.py), which fixes resource *discovery*; it sets no
     ``prte_tmpdir_base``, so two MPI jobs sharing a node still collide in the
     default session dir.

**Not** a reason any more, and removed Sep 25: the exit-status workaround.
``executor.py`` exits -1 on FAILED/TIMEDOUT/CANCELLED/UNRESPONSIVE, so RC already
reflects Task failure and the generated script no longer greps the log for
"Task failed with return code".

The task itself still runs through LUTE's own ``run_task.py``, i.e. the real
managed Task/Executor path. Only the launcher differs.

``hutch-config`` and ``hutch-install`` were separate binaries until Sep 2026.
They are the same infrastructure, so they are the same tool: ``render`` is the
old ``hutch-config`` (named ``render`` because ``sbatch --config <path>`` already
means "the config to submit", a different thing from producing one), and
``plan``/``workspace``/``dags``/``envs``/``register``/``all`` are the old
``hutch-install``.

Those six overlap LUTE's own ``utilities/setup/setup_lute.py``, which is ahead of
them on DAG patching (it applies per-task nodes/ntasks from a table) and is
non-destructive where ``envs`` is. They are kept for one reason: **setup_lute can
only register a DAG it already has.** It resolves ``<name>.dag`` against
``{lute_output}`` then ``workflows/common/`` -- a catalogue of five -- and on a
miss logs "Workflow definition not found ... Skipping workflow" and continues.
Its trigger is hardcoded by substring: ``START_OF_RUN`` if ``"smd"`` is in the
name, else ``MANUAL``. A DAG ``analyze-data`` composes for the experiment at hand
is in neither the catalogue nor those two triggers, and the demo needs
``END_OF_RUN``. ``parse_trigger`` here accepts all four, including
``RUN_PARAM_IS_VALUE:<name>:<value>``.

jinja2 and PyYAML are imported inside the functions that need them. Everything
else in the package is stdlib-only, and ``hutch-lute watch``/``status``/
``sbatch`` must keep working without the psana env; ``build_parser()`` must not
pull them in either. test_package_api.py enforces both.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import time
from typing import Any

from .common import (
    OK,
    REFUSED,
    UNREACHABLE,
    USAGE,
    external_path,
    fail,
    out,
    parser,
    results_dir,
    skill_root,
    xtc2_files,
)
from .session import BridgeUnavailable, SafetyRefusal, require_confirmation
from .session import _ssh as ssh
from .state import MissingValue, StaleValue, State

PSCONDA = external_path("PSCONDA")
DEFAULT_PARTITION = "milano"

#: Job-name fragments that mark a job as a LUTE/analysis workflow.
WORKFLOW_HINTS = ("lute", "dag", "sfx", "xes", "xss", "crystfel", "smalldata",
                  "cctbx", "index", "merge", "scale")


class LuteError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Slurm: submit, then find the job and report on it
# --------------------------------------------------------------------------
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
    -- the layout that stops parallel runs racing on a shared phil file --
    therefore fails on a work_dir that has not been used before. ``render`` never
    sees a run number, so it belongs here: ``sbatch`` is the first step that
    knows the config *and* the run.

    Regex rather than PyYAML on purpose -- ``sbatch`` stays usable without the
    psana env.
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
    explicit ``input_path`` short-circuits it entirely. Making a valid ticket an
    unconditional gate would block a pipeline that never touches the eLog.
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
    """Render a submission script that survives the four launcher traps above."""
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
        # per-node daemon".
        prte = (
            'export LUTE_TENV_PRTE_MCA_prte_tmpdir_base="/tmp/prte_${SLURM_JOB_ID}"\n'
            'export PRTE_MCA_prte_tmpdir_base="/tmp/prte_${SLURM_JOB_ID}"\n'
            'srun --ntasks=${SLURM_NNODES} --ntasks-per-node=1 '
            'mkdir -p "/tmp/prte_${SLURM_JOB_ID}" 2>/dev/null || '
            'mkdir -p "/tmp/prte_${SLURM_JOB_ID}"\n'
        )

    return f"""#!/bin/bash
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
exit ${{RC}}
"""


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
            jid, name, state, submit_time = (p.strip() for p in parts)
            if since and submit_time < since:
                continue
            lowered = name.lower()
            if (names and name in names) or any(h in lowered for h in hints):
                seen[jid] = {"job_id": jid, "job_name": name, "state": state,
                             "submit_time": submit_time}
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
         "--format=JobID,JobName,State,Elapsed,Timelimit,ExitCode,StdOut,StdErr"],
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
    job_id_s, name, st, elapsed, limit, exit_code, out, err = (rows[0] + [""] * 8)[:8]
    return {
        "job_id": job_id_s,
        "job_name": name,
        "state": st,
        "elapsed": elapsed,
        "time_limit": limit,
        "exit_code": exit_code,
        "hit_wall_clock": st.startswith("TIMEOUT"),
        "stdout": out,
        "stderr": err,
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

#: makepeds takes minutes on a full DARK run, so the remote command gets its own
#: budget -- but the connection still has to fail fast on an unreachable psdev.
PEDS_RUN_TIMEOUT = 1800


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
    """Deploy pedestal constants for a DARK run, by running makepeds on psdev.

    Writes to the calibration store, so every command goes through the same
    confirmation gate as anything sent to a live session. ``takepeds()`` is not
    done here -- that runs *inside* the hutch-python session, and is just
    ``hutch-daq exec 'takepeds()'``.

    Raises
    ------
    SafetyRefusal
        Declared confirmation weaker than the command's class. Nothing runs.
    LuteError
        XTC2 has not landed, or no fallback tools exist for this detector.
    """
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

    # Gate before the XTC2 guard, and gate every command before any of them
    # runs: a refusal must not depend on what is on disk, and a refusal on the
    # second command must not leave the first already deployed.
    for cmd in commands:
        require_confirmation(cmd, confirm)

    # Ordering hazard: makepeds reads the XTC2 files, so they must have landed.
    # Deploying before arrival produces pedestals from absent or partial data,
    # and the calibration store then serves them to every later run.
    present = [p.name for p in xtc2_files(hutch, experiment, int(run))]
    if not present:
        raise LuteError(
            f"refusing to deploy pedestals: no XTC2 files found for run {int(run)} "
            f"-- verify the DARK run recorded first."
        )

    results = []
    for cmd in commands:
        try:
            stdout = ssh("psdev", cmd, timeout=30, run_timeout=PEDS_RUN_TIMEOUT, jump=None)
        except BridgeUnavailable as exc:
            results.append({"command": cmd, "returncode": exc.returncode or 255,
                            "stdout": exc.stdout[-2000:], "stderr": exc.stderr[-2000:]})
            break
        results.append({"command": cmd, "returncode": 0,
                        "stdout": stdout[-2000:], "stderr": ""})

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
# Readiness -- read-only
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
# render -- was `hutch-config`
#
# Comments in the rendered YAML are load-bearing (they record why step_list
# excludes 'modify', why the radial-average two-theta range is what it is), so
# overrides are applied as line edits rather than by round-tripping through a
# YAML parser, which would drop them.
# --------------------------------------------------------------------------
#: Sentinel: remove the key entirely rather than setting a value.
DELETE = "__DELETE__"


class ConfigError(RuntimeError):
    pass


def _task_span(lines: list[str], task: str) -> tuple[int, int]:
    """[start, end) of a top-level ``task:`` block."""
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{task}:")), None)
    if start is None:
        top = {m.group(1) for m in (re.match(r"^([A-Za-z_]\w*):", ln) for ln in lines) if m}
        raise ConfigError(
            f"task block {task!r} not found in the rendered YAML. "
            f"Present at top level: {sorted(top)}"
        )
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t")):
            return start, i
    return start, len(lines)


def _sub_span(lines: list[str], lo: int, hi: int, sub: str) -> tuple[int, int, int]:
    """[start, end) of a nested block, plus the indent its children use."""
    start = next((i for i in range(lo, hi) if lines[i].strip() == f"{sub}:"), None)
    if start is None:
        raise ConfigError(f"sub-block {sub!r} not found in lines {lo}..{hi}")
    parent_indent = len(lines[start]) - len(lines[start].lstrip())
    child_indent = parent_indent + 2
    for i in range(start + 1, hi):
        if not lines[i].strip():
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= parent_indent:
            return start, i, child_indent
        child_indent = min(child_indent, indent) if i == start + 1 else child_indent
    return start, hi, child_indent


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _find_key(lines: list[str], lo: int, hi: int, key: str, indent: int) -> tuple[int, int] | None:
    """Locate ``key:`` at ``indent``, swallowing any block-list continuation."""
    pat = re.compile(rf"^{' ' * indent}{re.escape(key)}:")
    for i in range(lo, hi):
        if pat.match(lines[i]):
            end = i + 1
            while end < hi and lines[end].strip():
                if len(lines[end]) - len(lines[end].lstrip()) > indent:
                    end += 1
                else:
                    break
            return i, end
    return None


def apply_override(lines: list[str], dotted: str, value: Any) -> tuple[list[str], str]:
    """Apply ``TASK.SUBBLOCK.key = value``. Returns (lines, action).

    ``action`` is ``replaced``, ``inserted``, ``deleted`` or ``absent``. Callers
    must surface it: an ``inserted`` where you expected ``replaced`` is how a
    misspelled key announces itself.
    """
    try:
        task, sub, key = dotted.split(".", 2)
    except ValueError:
        raise ConfigError(
            f"override {dotted!r} must be TASK.SUBBLOCK.key (e.g. "
            f"IndexCCTBXXFEL.phil_parameters.dispatch_index)"
        ) from None

    t_lo, t_hi = _task_span(lines, task)
    s_lo, s_hi, indent = _sub_span(lines, t_lo, t_hi, sub)
    found = _find_key(lines, s_lo + 1, s_hi, key, indent)

    if value == DELETE:
        if found is None:
            return lines, "absent"
        a, b = found
        return lines[:a] + lines[b:], "deleted"

    pad = " " * indent
    if isinstance(value, list):
        new = [f"{pad}{key}:\n"] + [f"{pad}  - {v}\n" for v in value]
    else:
        new = [f"{pad}{key}: {_fmt(value)}\n"]

    if found is not None:
        a, b = found
        return lines[:a] + new + lines[b:], "replaced"

    ins = s_hi
    while ins > s_lo + 1 and not lines[ins - 1].strip():
        ins -= 1
    return lines[:ins] + new + lines[ins:], "inserted"


#: Jinja builtins and keywords, which are not template variables.
_JINJA_WORDS = frozenset(
    "if else elif endif for endfor in is not and or none true false defined "
    "undefined default length join lower upper trim int float string list "
    "set endset with endwith block endblock macro endmacro raw endraw".split()
)


def template_whitelist(template_text: str) -> set[str]:
    """Variable names a jinja phil template actually references.

    ``phil_parameters`` is pydantic ``extra='allow'``, so any key is accepted --
    and the fixed template then silently drops anything it does not reference. A
    typo, or a knob that does not exist, is an invisible no-op.

    Scans both ``{{ ... }}`` and ``{% ... %}``: the real LUTE phil templates
    guard most values with ``{% if x is defined and x is not none %}``, so a
    ``{{ }}``-only scan would under-report the legal key set and produce false
    "unknown key" errors.
    """
    names: set[str] = set()
    for block in re.findall(r"\{\{(.*?)\}\}|\{%(.*?)%\}", template_text, re.S):
        for chunk in block:
            if not chunk:
                continue
            for ident in re.findall(r"[A-Za-z_][\w]*", chunk):
                if ident not in _JINJA_WORDS:
                    names.add(ident)
    return names


def phil_templates(yaml_text: str) -> dict[str, str]:
    """Map each task block to the phil template basename it names.

    Read from the config's own ``phil_file``, so there is no hardcoded
    task -> template table to go stale.
    """
    found: dict[str, str] = {}
    for m in re.finditer(r"^([A-Za-z_]\w*):$", yaml_text, re.M):
        task = m.group(1)
        rest = yaml_text[m.end():]
        nxt = re.search(r"^[A-Za-z_]\w*:$", rest, re.M)
        block = rest[: nxt.start()] if nxt else rest
        pf = re.search(r"phil_file:\s*\"([^\"]+)\"", block)
        if pf:
            found[task] = pathlib.PurePosixPath(pf.group(1)).name
    return found


def whitelist_for(yaml_text: str, phil_dir: str | pathlib.Path) -> dict[str, set[str]]:
    """Legal phil keys per task, from the LUTE templates on disk."""
    phil_dir = pathlib.Path(phil_dir)
    if not phil_dir.is_dir():
        raise ConfigError(
            f"phil template directory not found: {phil_dir}. "
            f"Point --whitelist-dir at {{LUTE_PATH}}/config/templates, or pass "
            f"--no-whitelist to skip the check and accept that an unknown key "
            f"will be silently ignored."
        )
    allowed: dict[str, set[str]] = {}
    for task, basename in phil_templates(yaml_text).items():
        path = phil_dir / basename
        if not path.exists():
            raise ConfigError(f"{task} names phil_file {basename!r}, absent from {phil_dir}")
        allowed[task] = template_whitelist(path.read_text())
    return allowed


#: Delimiter schemes.
#:
#: ``lute`` uses ``${{ }}`` for variables and ``$% %`` for blocks, so that
#: LUTE's own native ``{{ work_dir }}`` and ``{{ run:04d }}`` placeholders pass
#: through the render untouched and are substituted later at task runtime. This
#: is the default: rendering a LUTE config with standard delimiters fails on
#: ``{{ run:04d }}``, which is not valid jinja.
DELIMITERS = {
    "lute": {
        "variable_start_string": "${{",
        "variable_end_string": "}}",
        "block_start_string": "$%",
        "block_end_string": "%",
        "comment_start_string": "{##",
        "comment_end_string": "##}",
    },
    "standard": {},
}

def resolve_template(name_or_path: str | pathlib.Path) -> pathlib.Path:
    """Locate a template by path. This repo ships none -- the caller names one."""
    p = pathlib.Path(name_or_path)
    if not p.exists():
        raise ConfigError(
            f"template not found: {p}. Pass a path to a LUTE config template; "
            f"this repo vendors no pipeline templates."
        )
    return p


def render_template(
    template_path: str | pathlib.Path,
    context: dict,
    delims: str = "lute",
) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "jinja2 is required to render a template; source psconda.sh first"
        ) from exc
    if delims not in DELIMITERS:
        raise ConfigError(f"delims must be one of {sorted(DELIMITERS)}, got {delims!r}")
    path = pathlib.Path(template_path)
    if not path.exists():
        raise ConfigError(f"template not found: {path}")
    env = Environment(
        loader=FileSystemLoader(str(path.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        **DELIMITERS[delims],
    )
    try:
        return env.get_template(path.name).render(**context)
    except Exception as exc:
        hint = ""
        if delims == "lute" and "{{" in path.read_text():
            hint = (
                "\n  hint: with --delims lute, template variables are written ${{ x }}; "
                "plain {{ x }} is passed through for LUTE to substitute at runtime."
            )
        raise ConfigError(f"template render failed: {exc}{hint}") from None


def parse_documents(yaml_text: str) -> list[dict]:
    """Parse the rendered config, tolerating the %YAML directive.

    LUTE's runtime placeholders (``{{ work_dir }}``, ``{{ run:04d }}``) are
    inside quotes, so they survive a parse unharmed.
    """
    try:
        import yaml as _yaml
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("PyYAML is required; source psconda.sh first") from exc
    stripped = re.sub(r"^%YAML [\d.]+\n", "", yaml_text)
    try:
        return [d for d in _yaml.safe_load_all(stripped) if d is not None]
    except Exception as exc:
        raise ConfigError(f"rendered config is not valid YAML: {exc}") from None


def build(
    *,
    template: str | pathlib.Path,
    context: dict,
    overrides: dict[str, Any] | None = None,
    whitelist_dir: str | pathlib.Path | None = None,
    delims: str = "lute",
) -> dict:
    """Render, override, validate. Returns a report; raises on any violation.

    The report lists every override with what it did, so a misspelled key that
    quietly became a new entry is visible instead of looking like a null result.
    """
    overrides = overrides or {}
    text = render_template(resolve_template(template), context, delims=delims)

    lines = text.splitlines(keepends=True)
    actions: dict[str, str] = {}
    for dotted, value in overrides.items():
        lines, action = apply_override(lines, dotted, value)
        actions[dotted] = action
    text = "".join(lines)

    docs = parse_documents(text)

    violations: list[str] = []
    if whitelist_dir is not None:
        allowed = whitelist_for(text, whitelist_dir)
        for task, keys in allowed.items():
            # Our own overrides, and then what the template itself emitted: a
            # context key no template references is equally a silent no-op.
            scoped = [
                k for k, v in overrides.items()
                if k.startswith(f"{task}.") and v != DELETE
                and k.rsplit(".", 1)[-1] not in keys
            ]
            if scoped:
                violations.append(
                    f"{task}: these override keys are not referenced by the phil "
                    f"template and would be silently ignored: "
                    f"{', '.join(sorted(k.rsplit('.', 1)[-1] for k in scoped))}"
                )
            block = next((d[task] for d in docs if isinstance(d, dict) and task in d), None)
            if isinstance(block, dict) and isinstance(block.get("phil_parameters"), dict):
                unknown = sorted(set(block["phil_parameters"]) - keys)
                if unknown:
                    violations.append(
                        f"{task}.phil_parameters contains keys the template does "
                        f"not reference: {', '.join(unknown)}"
                    )

    if violations:
        raise ConfigError(
            "config failed validation:\n" + "\n".join(f"  {v}" for v in violations)
        )

    return {
        "text": text,
        "documents": len(docs),
        "overrides": actions,
        "inserted_keys": [k for k, a in actions.items() if a == "inserted"],
        "tasks": sorted(phil_templates(text)),
        "whitelist_checked": whitelist_dir is not None,
        "delims": delims,
    }


def parse_set(pairs: list[str]) -> dict[str, Any]:
    """Parse ``--set K=V`` pairs. V is JSON when parseable, else a string."""
    parsed: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ConfigError(f"--set expects KEY=VALUE, got {pair!r}")
        key, _, raw = pair.partition("=")
        if raw == DELETE:
            parsed[key.strip()] = DELETE
            continue
        try:
            parsed[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            parsed[key.strip()] = raw
    return parsed


def _render(args) -> int:
    """Execute `hutch-lute render` from an already-parsed namespace."""
    try:
        ctx = json.load(open(args.context)) if args.context else {}
        ctx.update(parse_set(args.set_context))

        ovr = json.load(open(args.overrides)) if args.overrides else {}
        if isinstance(ovr, dict) and "overrides" in ovr:
            ovr = ovr["overrides"]      # a top-level 'overrides' key is unwrapped
        ovr.update(parse_set(args.sets))

        if not args.no_whitelist and not args.whitelist_dir:
            raise ConfigError(
                "pass --whitelist-dir {LUTE_PATH}/config/templates so unknown phil keys "
                "are caught, or --no-whitelist to accept that they are dropped silently."
            )

        report = build(
            template=args.template, context=ctx, overrides=ovr,
            whitelist_dir=None if args.no_whitelist else args.whitelist_dir,
            delims=args.delims,
        )
        if not args.out:
            return out(report["text"], plain=True)
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report["text"])
        summary = {k: v for k, v in report.items() if k != "text"}
        summary["written"] = str(path)
        return out(summary)
    except (ConfigError, OSError) as exc:
        return fail(exc, UNREACHABLE)


# --------------------------------------------------------------------------
# plan | workspace | dags | envs | register | all -- was `hutch-install`
#
# Why this exists at all, given LUTE ships utilities/setup/setup_lute.py: that
# script can only register a DAG it already has, from a catalogue of five, with a
# trigger hardcoded to START_OF_RUN or MANUAL. See the module docstring. The point
# of keeping these is registering a DAG composed for the experiment at hand, with
# the trigger the workflow actually needs.
#
# Two bugs were fixed when this was ported from analyze-data/scripts/install_lute.py.
# Both have since been fixed upstream too, so neither is a reason to prefer this
# code -- kept as notes on what to not regress:
#
# 1. DAG patching was not idempotent -- it appended --account/--partition to
#    every slurm_params line, so a second run doubled them. patch_slurm_params
#    strips what it owns before appending, so it is idempotent AND repairs a DAG
#    a previous run doubled. Upstream update_dag_params rebuilds the whole line,
#    which is also idempotent, and additionally applies per-task nodes/ntasks
#    from its own DEFAULT_CONFIG table. This implementation does not.
# 2. Preconditions were checked last, after the venv directory had been rmtree'd,
#    so a missing ticket destroyed both environments before failing. plan()
#    evaluates every precondition before anything mutates. Upstream sidesteps it
#    entirely by never deleting: it reuses an existing venv. `envs` here still
#    rmtrees, which is why it requires --confirm write -- it is the more
#    destructive of the two.
#
# requests and krtc are imported inside register() so planning, patching and
# workspace setup work without them.
# --------------------------------------------------------------------------
PYTHON39 = external_path("LUTE_PY39")
PYTHON311 = external_path("LUTE_PY311")
ENVS = (("lute_env_py39", PYTHON39), ("lute_env_py311", PYTHON311))

ELOG_URL = ("https://pswww.slac.stanford.edu/ws-kerb/lgbk/lgbk/"
            "{experiment}/ws/create_update_workflow_def")

SIMPLE_TRIGGERS = ("START_OF_RUN", "END_OF_RUN", "MANUAL")
DEFAULT_TRIGGER = "END_OF_RUN"

#: Subcommand -> help text.
INSTALL_SUBCOMMANDS = (
    ("plan", "print every path, payload and precondition; touches nothing"),
    ("envs", "recreate both virtual environments (destructive)"),
    ("workspace", "create lute_output and lute.db (idempotent)"),
    ("dags", "set account/partition in each DAG (idempotent)"),
    ("register", "POST workflow definitions to the eLog"),
    ("all", "workspace, dags, envs, register -- in that order, after checks"),
)

#: Of those, the ones that change something and so require --confirm write.
NEEDS_CONFIRM = ("envs", "register", "all")


class InstallError(RuntimeError):
    pass


def parse_trigger(spec: str) -> dict[str, str]:
    """Parse an eLog trigger spec.

    ``START_OF_RUN`` | ``END_OF_RUN`` | ``MANUAL`` |
    ``RUN_PARAM_IS_VALUE:<name>:<value>``
    """
    parts = spec.split(":")
    kind = parts[0].upper()

    if kind in SIMPLE_TRIGGERS:
        if len(parts) != 1:
            raise InstallError(f"unexpected fields after trigger type {kind!r}: {spec}")
        return {"trigger": kind}

    if kind == "RUN_PARAM_IS_VALUE":
        if len(parts) != 3:
            raise InstallError(
                "RUN_PARAM_IS_VALUE requires RUN_PARAM_IS_VALUE:<name>:<value>, "
                f"got: {spec}"
            )
        return {"trigger": kind, "run_param_name": parts[1], "run_param_value": parts[2]}

    raise InstallError(
        f"unknown trigger type {kind!r}. Expected one of "
        f"{', '.join(SIMPLE_TRIGGERS)}, or RUN_PARAM_IS_VALUE:<name>:<value>."
    )


#: Tokens this tool owns and therefore may rewrite. Anything else in
#: slurm_params was put there deliberately by whoever authored the DAG.
_OWNED = re.compile(r"\s*--(?:account|partition)=\S+")


def patch_slurm_params(text: str, *, partition: str, account: str) -> tuple[str, int]:
    """Set ``--account``/``--partition`` on every ``slurm_params`` line.

    Idempotent: existing account/partition tokens are removed before the current
    ones are appended, so running twice is a no-op and running over a DAG that a
    previous version doubled repairs it. Returns (patched_text, lines_changed).
    """
    patched: list[str] = []
    changed = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if not stripped.startswith("slurm_params:"):
            patched.append(line)
            continue
        indent = line[: len(line) - len(stripped)]
        existing = stripped[len("slurm_params:"):].strip().strip("'\"")
        base = _OWNED.sub("", existing).strip()
        params = f"{base} --account={account} --partition={partition}".strip()
        patched.append(f"{indent}slurm_params: '{params}'\n")
        changed += 1
    return "".join(patched), changed


def elog_payload(
    *, workflow: str, trigger: dict[str, str], arp_executable: str,
    launch_executable: str, config_path: str, dag_path: str,
    partition: str, account: str, debug: bool = False,
) -> dict[str, Any]:
    """Assemble one workflow definition for the eLog. Pure."""
    params = (f"{launch_executable} -c {config_path} -W {dag_path}"
              f" --partition={partition} --account={account}")
    if debug:
        params += " --debug"
    return {
        "name": f"lute_{workflow}",
        "executable": arp_executable,
        "location": "S3DF",
        "parameters": params,
        **trigger,
    }


def elog_ticket() -> tuple[bool, str]:
    """Is there a usable Kerberos ticket at the path the eLog path expects?

    Distinct from ``have_ticket()``, which asks the ambient cache whether *any*
    ticket exists. eLog registration needs one at ``FILE:$HOME/krb5cc.ticket``
    specifically, because that is what it sets KRB5CCNAME to.
    """
    ticket = f"FILE:{os.environ.get('HOME', '')}/krb5cc.ticket"
    try:
        rc = subprocess.run(["klist", "-c", ticket], capture_output=True,
                            timeout=10).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"klist failed: {exc}"
    return rc == 0, ticket


def plan(
    *, experiment: str, hutch: str | None = None, directory: str | None = None,
    workflows: list[str] | None = None, triggers: list[str] | None = None,
    partition: str = DEFAULT_PARTITION, account: str | None = None,
    version: str = "dev", debug: bool = False,
) -> dict:
    """Everything the install would do, with every precondition, touching nothing.

    Separating the plan is what makes ordering a property of the design instead
    of a comment -- see bug 2 in the section note above.
    """
    hutch = (hutch or experiment[:3]).lower()
    account = account or f"lcls:{experiment}"
    base = results_dir(hutch, experiment, directory)
    out_dir = base / "lute_output"
    envs_dir = base / "lute_envs"
    config_path = out_dir / f"{hutch}_lute.yaml"
    venv39 = envs_dir / "lute_env_py39"

    workflows = list(workflows or [])
    specs = list(triggers or [])
    resolved: list[dict] = []
    for i, wf in enumerate(workflows):
        spec = specs[i] if i < len(specs) else DEFAULT_TRIGGER
        resolved.append({"workflow": wf, "spec": spec, **parse_trigger(spec)})

    dags = {wf: out_dir / f"{wf}.dag" for wf in workflows}
    ticket_ok, ticket = elog_ticket()

    checks = [
        {"check": "config exists", "ok": config_path.exists(), "detail": str(config_path)},
        {"check": "python3.9 interpreter", "ok": pathlib.Path(PYTHON39).is_file(),
         "detail": PYTHON39},
        {"check": "python3.11 interpreter", "ok": pathlib.Path(PYTHON311).is_file(),
         "detail": PYTHON311},
        {"check": "at least one workflow named", "ok": bool(workflows),
         "detail": ", ".join(workflows) or "(none)"},
        {"check": "kerberos ticket (registration only)", "ok": ticket_ok,
         "detail": ticket if ticket_ok else
                   f"missing; run: kinit -c {ticket} <username>@SLAC.STANFORD.EDU"},
    ]
    for wf, dag in dags.items():
        checks.append({"check": f"dag exists: {wf}", "ok": dag.exists(), "detail": str(dag)})

    arp = str(venv39 / "bin" / "submit_launch_slurm.sh")
    launch = str(venv39 / "bin" / "launch_slurm")
    payloads = [
        elog_payload(
            workflow=r["workflow"],
            trigger={k: v for k, v in r.items() if k not in ("workflow", "spec")},
            arp_executable=arp, launch_executable=launch,
            config_path=str(config_path), dag_path=str(dags[r["workflow"]]),
            partition=partition, account=account, debug=debug,
        )
        for r in resolved
    ]

    return {
        "experiment": experiment, "hutch": hutch, "account": account,
        "partition": partition, "version": version,
        "results_dir": str(base), "lute_output_dir": str(out_dir),
        "lute_envs_dir": str(envs_dir), "config_path": str(config_path),
        "workflows": resolved,
        "dags": {wf: str(p) for wf, p in dags.items()},
        "arp_executable": arp, "launch_executable": launch,
        "elog_payloads": payloads,
        "checks": checks,
        "blocking": [c["check"] for c in checks
                     if not c["ok"] and "registration only" not in c["check"]],
        "envs_dir_exists": envs_dir.exists(),
        "destructive": (
            f"`envs` deletes {envs_dir} and recreates both virtual environments"
            if envs_dir.exists() else None
        ),
    }


def _install_run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise InstallError(
            f"command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}"
        )


def create_venvs(plan_: dict, *, confirm: str | None = None) -> dict:
    """Recreate both virtual environments. Destructive, so it must be confirmed."""
    envs_dir = pathlib.Path(plan_["lute_envs_dir"])
    if confirm != "write":
        raise InstallError(
            f"this deletes {envs_dir} and reinstalls both environments. "
            f"Show it to the operator, then re-run with --confirm write."
        )
    for name, interpreter in ENVS:
        if not pathlib.Path(interpreter).is_file():
            raise InstallError(
                f"interpreter not found: {interpreter}. Is the LCLS conda stack present?"
            )

    if envs_dir.exists():
        shutil.rmtree(envs_dir)
    envs_dir.mkdir(parents=True, mode=0o775)

    spec = "lute-lcls" if plan_["version"] == "dev" else f"lute-lcls=={plan_['version']}"
    created = []
    for name, interpreter in ENVS:
        env = envs_dir / name
        _install_run([interpreter, "-m", "venv", str(env)])
        pip = str(env / "bin" / "pip")
        _install_run([pip, "install", "--upgrade", "pip"])
        _install_run([pip, "install", spec])
        created.append(str(env))
    return {"created": created, "package": spec}


def setup_workspace(plan_: dict) -> dict:
    """Create the output directory and lute.db. Idempotent."""
    out_dir = pathlib.Path(plan_["lute_output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o777)
    db = out_dir / "lute.db"
    created = not db.exists()
    if created:
        db.touch()
        os.chmod(db, 0o664)
    return {"lute_output_dir": str(out_dir), "db": str(db), "db_created": created}


def patch_dags(plan_: dict) -> dict:
    """Set account/partition in every named DAG. Idempotent; repairs doubling."""
    results = {}
    for wf, dag in plan_["dags"].items():
        p = pathlib.Path(dag)
        if not p.exists():
            raise InstallError(f"DAG not found: {p}. Write it before installing.")
        before = p.read_text()
        after, changed = patch_slurm_params(
            before, partition=plan_["partition"], account=plan_["account"])
        if changed == 0:
            raise InstallError(
                f"no slurm_params lines in {p} -- the DAG is missing its resource "
                f"specifications, which are the caller's responsibility."
            )
        if after != before:
            p.write_text(after)
            os.chmod(p, 0o666)
        results[wf] = {"slurm_params_lines": changed, "modified": after != before}
    return results


def register(plan_: dict, *, confirm: str | None = None) -> dict:
    """POST each workflow definition to the eLog. Needs a ticket; writes remotely."""
    if confirm != "write":
        raise InstallError(
            "registering workflows writes to the eLog. Show the payloads to the "
            "operator, then re-run with --confirm write."
        )
    ticket_ok, ticket = elog_ticket()
    if not ticket_ok:
        raise InstallError(
            f"no valid Kerberos ticket at {ticket}. Run:\n"
            f"  kinit -c {ticket} <username>@SLAC.STANFORD.EDU\n"
            f"The FILE: prefix is required."
        )
    try:
        import requests
        from krtc import KerberosTicket
    except ImportError as exc:                       # pragma: no cover
        raise InstallError(
            f"eLog registration needs requests and krtc ({exc}). "
            f"Source psconda.sh, or install the 'elog' extra."
        ) from exc

    os.environ["KRB5CCNAME"] = ticket
    krb = KerberosTicket("HTTP@pswww.slac.stanford.edu")
    url = ELOG_URL.format(experiment=plan_["experiment"])

    registered, failed = [], []
    for payload in plan_["elog_payloads"]:
        try:
            resp = requests.post(url=url, headers=krb.getAuthHeaders(), json=payload)
            resp.raise_for_status()
            registered.append(payload["name"])
        except Exception as exc:                     # noqa: BLE001 - report, don't abort
            failed.append({"name": payload["name"], "error": str(exc)})
    return {
        "registered": registered, "failed": failed,
        "verify_at": f"https://pswww.slac.stanford.edu/lgbk/lgbk/"
                     f"{plan_['experiment']}/ (Workflow Definitions tab)",
    }


def _install(args) -> int:
    """Execute one of plan|workspace|dags|envs|register|all."""
    try:
        plan_ = plan(
            experiment=args.experiment, hutch=args.hutch, directory=args.directory,
            workflows=args.workflow, triggers=args.trigger, partition=args.partition,
            account=args.account, version=args.version, debug=args.debug,
        )
        if args.cmd == "plan":
            out(plan_)
            return OK if not plan_["blocking"] else UNREACHABLE
        if args.cmd == "workspace":
            return out(setup_workspace(plan_))
        if args.cmd == "dags":
            return out(patch_dags(plan_))
        if args.cmd == "envs":
            return out(create_venvs(plan_, confirm=args.confirm))
        if args.cmd == "register":
            res = register(plan_, confirm=args.confirm)
            out(res)
            return OK if not res["failed"] else UNREACHABLE

        if args.cmd == "all":
            if plan_["blocking"]:
                raise InstallError(
                    "preconditions not met: " + ", ".join(plan_["blocking"])
                    + ". Run `hutch-lute plan` for detail. Nothing was changed."
                )
            steps = {
                "workspace": setup_workspace(plan_),
                "dags": patch_dags(plan_),
                "envs": create_venvs(plan_, confirm=args.confirm),
            }
            steps["register"] = register(plan_, confirm=args.confirm)
            out({"plan": plan_, "steps": steps})
            return OK if not steps["register"]["failed"] else UNREACHABLE
    except (InstallError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-lute: sbatch | status | watch | peds | readiness | kerberos
    | render | plan | workspace | dags | envs | register | all."""


def _install_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--experiment", "-e", required=True)
    p.add_argument("--hutch", default=None, help="defaults to the first 3 chars of experiment")
    p.add_argument("--directory", "-D", default=None, help="subdirectory under results/")
    p.add_argument("--workflow", "-W", nargs="*", default=[], help="DAG basenames")
    p.add_argument("--trigger", nargs="*", default=[],
                   help=f"per workflow, in order; default {DEFAULT_TRIGGER}")
    p.add_argument("--partition", default=DEFAULT_PARTITION)
    p.add_argument("--account", default=None, help="defaults to lcls:<experiment>")
    p.add_argument("--version", "-v", default="dev", help="lute-lcls version, or 'dev'")
    p.add_argument("--debug", action="store_true")


def _render_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--template", required=True,
                   help="path to a LUTE config template; this repo vendors none")
    p.add_argument("--context", default=None, help="JSON file of template context")
    # action="extend", not the default "store": with plain nargs="*" a second
    # --set silently REPLACED the first, so `--set a=1 --set b=2` applied only
    # b=2 and dropped a=1 with no diagnostic. That is the exact silent-drop
    # failure --whitelist-dir exists to catch, occurring inside the tool meant
    # to catch it. extend accumulates every occurrence instead.
    p.add_argument("--set-context", nargs="*", action="extend", default=[], metavar="K=V")
    p.add_argument("--set", nargs="*", action="extend", default=[], dest="sets",
                   metavar="TASK.BLOCK.KEY=V",
                   help=f"override; value {DELETE} removes the key")
    p.add_argument("--overrides", default=None,
                   help="JSON file of overrides; a top-level 'overrides' key is unwrapped")
    p.add_argument("--whitelist-dir", default=None,
                   help="{LUTE_PATH}/config/templates -- enables the silent-drop check")
    p.add_argument("--no-whitelist", action="store_true",
                   help="skip the silent-drop check (an unknown key will be ignored)")
    p.add_argument("--delims", default="lute", choices=sorted(DELIMITERS),
                   help="lute: ${{ }} for template vars so LUTE's own {{ }} survives")
    p.add_argument("--out", default=None, help="write here; omit to print the config")


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-lute", _DOC)

    sbatch_parser = sub.add_parser("sbatch", help="render a submission script")
    sbatch_parser.add_argument("--task", required=True)
    sbatch_parser.add_argument("--config", required=True)
    sbatch_parser.add_argument("--log-dir", required=True)
    sbatch_parser.add_argument("--lute-path", required=True)
    sbatch_parser.add_argument("--experiment", required=True)
    sbatch_parser.add_argument("--run", required=True, type=int)
    sbatch_parser.add_argument("--time", dest="time_limit", required=True,
                               help="Slurm walltime, e.g. 00:10:00 -- mandatory, LUTE sets none")
    sbatch_parser.add_argument("--nodes", type=int, default=1)
    sbatch_parser.add_argument("--ntasks-per-node", type=int, default=1)
    sbatch_parser.add_argument("--no-mpi", action="store_true")
    sbatch_parser.add_argument("--submit", action="store_true")
    sbatch_parser.add_argument("--dependency", default=None)
    sbatch_parser.add_argument("--script-path", default=None)

    status_parser = sub.add_parser("status", help="sacct summary for one job")
    status_parser.add_argument("job_id")

    watch_parser = sub.add_parser("watch", help="poll squeue for a workflow job to appear")
    watch_parser.add_argument("--user", required=True)
    watch_parser.add_argument("--since", default=None,
                              help="ignore jobs submitted before this timestamp")
    watch_parser.add_argument("--timeout", type=int, default=90)
    watch_parser.add_argument("--poll", type=int, default=10)
    watch_parser.add_argument("--name", nargs="*", default=None,
                              help="exact job names, in addition to the name hints")

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

    readiness_parser = sub.add_parser("readiness",
                                      help="is LUTE configured for this experiment? read-only")
    readiness_parser.add_argument("--hutch", required=True)
    readiness_parser.add_argument("--experiment", required=True)
    readiness_parser.add_argument("--directory", default=None,
                                  help="optional subdir under results/")

    kerberos_parser = sub.add_parser("kerberos", help="does this config actually need a ticket?")
    kerberos_parser.add_argument("--yaml", required=True)

    _render_args(sub.add_parser("render", help="render a LUTE config YAML, validated"))

    for name, helptext in INSTALL_SUBCOMMANDS:
        install_parser = sub.add_parser(name, help=helptext)
        _install_common(install_parser)
        if name in NEEDS_CONFIRM:
            install_parser.add_argument("--confirm", default=None, choices=["write"])

    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-lute: submission, monitoring, pedestals, config rendering, workspace setup."""
    args = build_parser().parse_args(argv)

    # render and the install subcommands own their own exit codes, so they are
    # dispatched before the handlers below rather than inside them.
    if args.cmd == "render":
        return _render(args)
    if args.cmd in {name for name, _ in INSTALL_SUBCOMMANDS}:
        return _install(args)

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
            # per-run directory has to exist before the job starts, not when the
            # Task does. Reported, not silent: a created list that is
            # unexpectedly long means the work_dir was not what you thought.
            made = ensure_run_dirs(args.config, args.run)
            path = args.script_path or f"{args.log_dir}/sbatch_{args.task}.sh"
            return out({"job_id": submit(text, script_path=path,
                                         dependency=args.dependency),
                        "created_dirs": made})
        if args.cmd == "status":
            return out(job_status(args.job_id))
        if args.cmd == "watch":
            res = watch_for_jobs(args.user, since=args.since, timeout_s=args.timeout,
                                 poll_s=args.poll, names=args.name)
            out(res)
            return OK if res["found"] else UNREACHABLE
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
    except SafetyRefusal as exc:
        return fail(exc, REFUSED)
    except (LuteError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
