"""Executable incident register.

This is the one category of knowledge that is *not* retrievable from an
authoritative source: empirical facts about broken software versions. The only
authority is the incident that produced them.

So it lives here as assertions rather than as a prose table -- version-pinned,
testable, and impossible to read past. ``setup-cctbx-pipeline/render.py``
already worked this way with seven assertions and it is the best-designed piece
of the skill family: it caught real traps at config time rather than after a
ten-minute job died.

Every entry carries the evidence that created it -- an error string, a Slurm
job id, or a source location. An entry without evidence is a rumour and does
not belong here.
"""

from __future__ import annotations

import re
from typing import Callable

Check = Callable[[dict], list[str]]

_REGISTRY: list[dict] = []


def trap(
    trap_id: str,
    *,
    applies_to: str,
    what: str,
    evidence: str,
) -> Callable[[Check], Check]:
    """Register one incident-derived assertion.

    ``applies_to`` selects which contexts run it: 'sbatch', 'lute-yaml',
    'run-params', 'phil-overrides'.
    """

    def wrap(fn: Check) -> Check:
        _REGISTRY.append(
            {
                "id": trap_id,
                "applies_to": applies_to,
                "what": what,
                "evidence": evidence,
                "check": fn,
            }
        )
        return fn

    return wrap


def check(scope: str, ctx: dict) -> list[dict]:
    """Run every trap for ``scope``. Returns a list of violations."""
    out = []
    for entry in _REGISTRY:
        if entry["applies_to"] != scope:
            continue
        for problem in entry["check"](ctx):
            out.append({"id": entry["id"], "problem": problem, "evidence": entry["evidence"]})
    return out


def registry() -> list[dict]:
    return [{k: v for k, v in e.items() if k != "check"} for e in _REGISTRY]


# --------------------------------------------------------------------------
# Slurm submission
# --------------------------------------------------------------------------
@trap(
    "no-time-limit",
    applies_to="sbatch",
    what="LUTE sets no --time anywhere, so jobs silently inherit the partition default.",
    evidence=(
        "scontrol show partition milano -> DefaultTime=1-00:00:00 MaxTime=10-00:00:00; "
        "repo-wide grep for --time/time_limit/walltime across launch_scripts/, lute/ "
        "and workflows/ returns no Slurm-time hits."
    ),
)
def _no_time_limit(ctx: dict) -> list[str]:
    text = ctx.get("sbatch_text", "")
    if not re.search(r"^#SBATCH\s+--time=", text, re.M):
        return [
            "no '#SBATCH --time=' directive: this job would inherit the partition "
            "default (24 h on milano), not the intended limit."
        ]
    return []


@trap(
    "pythonpath-after-psconda",
    applies_to="sbatch",
    what="psconda.sh overwrites PYTHONPATH, so a repo-checkout LUTE cannot be imported.",
    evidence=(
        "LUTE's own submit_slurm.py generates a script that sources psconda AFTER "
        "setting PYTHONPATH; `import lute` then resolves to the installed package "
        "or fails."
    ),
)
def _pythonpath_order(ctx: dict) -> list[str]:
    text = ctx.get("sbatch_text", "")
    src = text.find("psconda.sh")
    pp = text.find("PYTHONPATH=")
    if src != -1 and pp != -1 and pp < src:
        return [
            "PYTHONPATH is set before psconda.sh is sourced; psconda will clobber it. "
            "Source psconda first, then prepend."
        ]
    return []


@trap(
    "prte-session-collision",
    applies_to="sbatch",
    what="Two MPI CCTBX jobs sharing any node collide in PRRTE's default session dir.",
    evidence=(
        "jobs 36372545 and 36372548 both died within 10 s with 'srun returned "
        "non-zero exit status (36096) from launching the per-node daemon' while "
        "sharing 3 of 4 nodes; after setting a per-job prte_tmpdir_base, jobs "
        "36376600 and 36376603 ran concurrently on the identical 4 nodes."
    ),
)
def _prte_tmpdir(ctx: dict) -> list[str]:
    text = ctx.get("sbatch_text", "")
    if not ctx.get("mpi", True):
        return []
    if "prte_tmpdir_base" not in text:
        return [
            "no per-job PRRTE session directory: set "
            "LUTE_TENV_PRTE_MCA_prte_tmpdir_base=/tmp/prte_$SLURM_JOB_ID or a "
            "concurrent job on a shared node will fail at daemon launch."
        ]
    return []


@trap(
    "exit-code-swallowed",
    applies_to="sbatch",
    what="run_task.py exits 0 even when the managed Task failed, so sacct State lies.",
    evidence=(
        "iteration-2/3 jobs showed State=COMPLETED while the Executor log contained "
        "'Task failed with return code'; downstream steps then ran on absent input."
    ),
)
def _exit_code(ctx: dict) -> list[str]:
    text = ctx.get("sbatch_text", "")
    if "run_task.py" in text and "Task failed with return code" not in text:
        return [
            "job does not inspect the Executor log for 'Task failed with return code'; "
            "a failed Task will be reported as COMPLETED."
        ]
    return []


# --------------------------------------------------------------------------
# LUTE / CCTBX configuration
# --------------------------------------------------------------------------
@trap(
    "parallel-a2a",
    applies_to="lute-yaml",
    what="parallel_a2a triggers a TypeError in cctbx group_reflections.",
    evidence="TypeError: distribute_reflection_table() missing 1 required positional argument, group_reflections.py:123.",
)
def _parallel_a2a(ctx: dict) -> list[str]:
    return (
        ["'parallel_a2a' must not appear in the config"]
        if re.search(r"^\s*[^#\n]*parallel_a2a", ctx.get("yaml_text", ""), re.M)
        else []
    )


@trap(
    "merge-modify-step",
    applies_to="lute-yaml",
    what="'modify' in the merge step_list needs the s1 column, which scale output lacks.",
    evidence="KeyError: \"Unknown column 's1'\" during merge; scale already applied the polarization correction.",
)
def _merge_modify(ctx: dict) -> list[str]:
    text = ctx.get("yaml_text", "")
    m = re.search(r"dispatch_step_list:\s*(?:>-|\|)?\s*((?:\n\s+\S.*)+|.*)", text)
    if m and re.search(r"\bmodify\b", m.group(1)):
        return ["'modify' must not appear in MergeCCTBXXFEL dispatch_step_list"]
    return []


@trap(
    "merge-suffix",
    applies_to="lute-yaml",
    what="cctbx.xfel.merge defaults to scanning *_integrated.expt and finds nothing.",
    evidence="merge reported 0 experiments until input_experiments_suffix was set to '.expt' (scale writes scale_r*.expt).",
)
def _merge_suffix(ctx: dict) -> list[str]:
    text = ctx.get("yaml_text", "")
    if "MergeCCTBXXFEL" not in text:
        return []
    missing = [
        key
        for key in ('input_experiments_suffix: ".expt"', 'input_reflections_suffix: ".refl"')
        if key not in text
    ]
    return [f"MergeCCTBXXFEL is missing {k}" for k in missing]


@trap(
    "phil-file-race",
    applies_to="lute-yaml",
    what="Parallel runs sharing a phil_file path overwrite each other.",
    evidence="phil_file must contain the {{ run }} template variable; two runs in one work_dir otherwise race.",
)
def _phil_race(ctx: dict) -> list[str]:
    out = []
    for task in ("IndexCCTBXXFEL", "ScaleCCTBXXFEL"):
        m = re.search(rf"{task}:.*?phil_file:\s*\"([^\"]+)\"", ctx.get("yaml_text", ""), re.S)
        if m and "{{ run" not in m.group(1):
            out.append(f"{task}.phil_file lacks '{{{{ run }}}}' -- parallel runs will race")
    return out


@trap(
    "merge-version-padding",
    applies_to="lute-yaml",
    what="The merge output directory must be zero-padded to three digits.",
    evidence="CCTBXMerger died with FileNotFoundError on cctbx_merge.phil when the directory was v03 rather than v003.",
)
def _merge_version(ctx: dict) -> list[str]:
    for m in re.finditer(r"merge_[^/\s\"]*/v(\d+)", ctx.get("yaml_text", "")):
        if len(m.group(1)) != 3:
            return [f"merge version directory 'v{m.group(1)}' must be zero-padded to 3 digits"]
    return []


# --------------------------------------------------------------------------
# Silent-drop guard (Category C)
# --------------------------------------------------------------------------
def template_whitelist(template_text: str) -> set[str]:
    """Variable names a jinja phil template actually references.

    ``phil_parameters`` is pydantic ``extra='allow'``, so any key is accepted --
    and the fixed template then silently drops anything it does not reference.
    A typo, or a knob that does not exist, is an invisible no-op. This is what
    made the campaign's search for a ``max_events`` parameter a dead end.
    """
    return set(re.findall(r"\{\{-?\s*([A-Za-z_][\w]*)", template_text))


@trap(
    "phil-key-silently-dropped",
    applies_to="phil-overrides",
    what="Unknown phil_parameters keys are accepted by pydantic and dropped by the template.",
    evidence=(
        "IndexCCTBXXFELParameters.PhilParameters sets extra='allow' (sfx_index.py:559-560); "
        "lute/tasks/task.py:458-466 renders with a FileSystemLoader pinned to "
        "{LUTE_PATH}/config/templates, which cannot be redirected."
    ),
)
def _phil_keys(ctx: dict) -> list[str]:
    allowed = ctx.get("whitelist")
    if not allowed:
        return []
    unknown = sorted(k.rsplit(".", 1)[-1] for k in ctx.get("overrides", {}) if k.rsplit(".", 1)[-1] not in allowed)
    if unknown:
        return [
            "these keys are not referenced by the phil template and would be "
            f"silently ignored: {', '.join(unknown)}"
        ]
    return []


# --------------------------------------------------------------------------
# Run parameters
# --------------------------------------------------------------------------
@trap(
    "run-type-case",
    applies_to="run-params",
    what="LUTE branches on run_type; lowercase silently takes no branch.",
    evidence="take-run/SKILL.md:110 states run_type must always be uppercase because LUTE's !run_type DAG keys are DARK/GEOM/DATA.",
)
def _run_type(ctx: dict) -> list[str]:
    rt = ctx.get("run_type")
    if rt is None:
        return []
    if rt not in ("DARK", "GEOM", "DATA"):
        return [f"run_type={rt!r} must be exactly one of DARK, GEOM, DATA (uppercase)"]
    return []
