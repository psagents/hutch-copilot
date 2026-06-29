"""Set up the LUTE workspace and register workflows in the LCLS eLog.

This is the single script to run after the skill has written DAG files and the
YAML configuration. It handles everything end-to-end:

  1. Optional fresh LUTE install (git clone + build) if -f is passed
  2. Workspace setup: create lute_output_dir and lute.db
  3. Patch slurm_params in each pre-written DAG file
  4. Register each workflow in the LCLS eLog (requires Kerberos ticket)

Usage
-----
The skill writes {lute_output_dir}/{wf_name}.dag and
{lute_output_dir}/{hutch}_lute.yaml, then calls:

    python install_lute.py \\
        -e {experiment} \\
        -v {version}    \\
        -W {wf1} [{wf2} ...] \\
        [--partition {partition}] \\
        [--account   {account}]  \\
        [--nodes     {N}]        \\
        [--ntasks-per-node {N}]  \\
        [-f]   # fresh install   \\
        [-D {subdirectory}]

Notes
-----
- Does NOT write or overwrite the YAML config — the skill owns that file.
- Does NOT copy DAG files from workflows/common/ — DAGs must already exist.
- Re-run safe: will not fail if the workspace directory or database already exist.
"""

__author__ = "Gabriel Dorlhiac"

import argparse
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

import requests
from krtc import KerberosTicket  # type: ignore


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task-specific SLURM resource overrides.
# Tasks listed here receive fixed node/task counts regardless of user defaults.
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "SmallDataProducer": {"nodes": 4, "ntasks_per_node": 50, "exclusive": True},
    "SmallDataProducer2": {"nodes": 4, "ntasks_per_node": 50, "exclusive": True},
    "BayFAIOptimizer": {"nodes": 1, "ntasks_per_node": 120},
    "BayFAIOptimizer2": {"nodes": 1, "ntasks_per_node": 120},
}

# ---------------------------------------------------------------------------
# Trigger spec parsing.
# Triggers are determined by the skill during analysis planning (Phase 3) and
# passed explicitly via --trigger.  No name-based lookup is performed here.
#
# Accepted --trigger formats (one per -W workflow, in matching order):
#   START_OF_RUN                                — fires at the start of every DAQ run
#   END_OF_RUN                                  — fires after every DAQ run
#   MANUAL                                      — fires only when user triggers from eLog
#   RUN_PARAM_IS_VALUE:<param_name>:<value>     — fires when a run param reaches a value
#       e.g.  RUN_PARAM_IS_VALUE:SmallData:done
# ---------------------------------------------------------------------------


def parse_trigger(spec: str) -> Dict[str, str]:
    """Parse a trigger spec string into an eLog trigger dict.

    Args:
        spec: One of "START_OF_RUN", "END_OF_RUN", "MANUAL", or
              "RUN_PARAM_IS_VALUE:<param_name>:<param_value>".

    Returns:
        Dict with at least the key "trigger", plus "run_param_name" and
        "run_param_value" when the trigger type is RUN_PARAM_IS_VALUE.

    Raises:
        ValueError: If the spec cannot be parsed.
    """
    parts = spec.split(":")
    trigger_type = parts[0].upper()

    if trigger_type in ("START_OF_RUN", "END_OF_RUN", "MANUAL"):
        if len(parts) != 1:
            raise ValueError(
                f"Unexpected fields after trigger type '{trigger_type}': {spec}"
            )
        return {"trigger": trigger_type}

    if trigger_type == "RUN_PARAM_IS_VALUE":
        if len(parts) != 3:
            raise ValueError(
                f"RUN_PARAM_IS_VALUE requires format "
                f"'RUN_PARAM_IS_VALUE:<param_name>:<param_value>', got: {spec}"
            )
        return {
            "trigger": trigger_type,
            "run_param_name": parts[1],
            "run_param_value": parts[2],
        }

    raise ValueError(
        f"Unknown trigger type '{trigger_type}'. "
        "Expected START_OF_RUN, END_OF_RUN, MANUAL, or RUN_PARAM_IS_VALUE:<name>:<value>."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: List[str], cwd: Optional[str] = None) -> None:
    """Run a subprocess, streaming stdout/stderr to the logger."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        cwd=cwd,
    )
    if result.stdout:
        logger.info(result.stdout.rstrip())
    if result.stderr:
        logger.warning(result.stderr.rstrip())
    if result.returncode != 0:
        logger.error(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Fresh install
# ---------------------------------------------------------------------------


def git_clone(repo: str, location: str, tag: str) -> None:
    """Clone a GitHub repository to `location` and check out `tag`.

    Skips if `location` already exists (idempotent).

    Args:
        repo:     Repository slug, e.g. "slac-lcls/lute".
        location: Absolute path to clone into.
        tag:      Branch or tag to check out after cloning.
    """
    if os.path.exists(location):
        logger.info(f"Directory already exists at {location} — skipping clone.")
        return
    logger.info(f"Cloning {repo} → {location} (tag: {tag}) …")
    _run(["git", "clone", f"https://github.com/{repo}.git", location])
    _run(["git", "checkout", tag], cwd=location)


def run_build_script(lute_path: str) -> None:
    """Run LUTE's build.sh -e inside `lute_path`.

    Args:
        lute_path: Root of the cloned LUTE repository.
    """
    logger.info(f"Building LUTE at {lute_path} — this may take a few minutes …")
    _run(["./build.sh", "-e"], cwd=lute_path)


def set_permissions(path: str, mode: int = 0o765) -> None:
    """Recursively apply `mode` to `path` and everything under it."""
    os.chmod(path, mode)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chmod(os.path.join(root, d), mode)
        for f in files:
            os.chmod(os.path.join(root, f), mode)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


def setup_workspace(lute_output_dir: str) -> None:
    """Create the LUTE output directory and database file.

    Idempotent — safe to call on an existing workspace.

    Args:
        lute_output_dir: Absolute path to the lute_output directory.
    """
    os.makedirs(lute_output_dir, mode=0o777, exist_ok=True)
    os.chmod(lute_output_dir, 0o777)
    logger.info(f"Workspace: {lute_output_dir}")

    db_path = os.path.join(lute_output_dir, "lute.db")
    if os.path.exists(db_path):
        logger.info(f"Database already exists at {db_path} — skipping.")
    else:
        open(db_path, "a").close()
        os.chmod(db_path, 0o664)
        logger.info(f"Created database: {db_path}")


# ---------------------------------------------------------------------------
# DAG patching
# ---------------------------------------------------------------------------


def patch_dag_slurm_params(
    dag_path: str,
    partition: str,
    account: str,
    default_nodes: int,
    default_ntasks: int,
) -> None:
    """Patch every slurm_params field in a DAG file in-place.

    Tasks in DEFAULT_CONFIG receive their fixed resource counts.
    All other tasks receive the user-supplied defaults.

    Args:
        dag_path:      Path to the .dag YAML file.
        partition:     SLURM partition name (e.g. "milano").
        account:       SLURM account (e.g. "lcls:mfxl1013621").
        default_nodes: Node count for tasks not in DEFAULT_CONFIG.
        default_ntasks: ntasks-per-node for tasks not in DEFAULT_CONFIG.
    """
    with open(dag_path) as fh:
        lines = fh.readlines()

    patched: List[str] = []
    current_task: Optional[str] = None

    for line in lines:
        stripped = line.lstrip()

        # Track the most recently declared task_name so we know which task
        # the following slurm_params line belongs to.
        raw: Optional[str] = None
        if stripped.startswith("- task_name:"):
            raw = stripped[2:]  # strip leading "- "
        elif stripped.startswith("task_name:"):
            raw = stripped
        if raw is not None:
            current_task = raw.split(":", 1)[1].strip().strip("\"'")

        if stripped.startswith("slurm_params:"):
            indent = line[: len(line) - len(stripped)]
            cfg = DEFAULT_CONFIG.get(current_task or "")
            if cfg:
                exclusive = " --exclusive" if cfg.get("exclusive") else ""
                params = (
                    f"--account={account} --partition={partition}"
                    f" --nodes={cfg['nodes']}"
                    f" --ntasks-per-node={cfg['ntasks_per_node']}"
                    f"{exclusive}"
                )
            else:
                params = (
                    f"--account={account} --partition={partition}"
                    f" --nodes={default_nodes}"
                    f" --ntasks-per-node={default_ntasks}"
                )
            patched.append(f"{indent}slurm_params: '{params}'\n")
        else:
            patched.append(line)

    with open(dag_path, "w") as fh:
        fh.writelines(patched)

    os.chmod(dag_path, 0o666)
    logger.info(f"Patched slurm_params: {dag_path}")


# ---------------------------------------------------------------------------
# eLog registration
# ---------------------------------------------------------------------------


def check_kerberos_ticket() -> bool:
    """Return True if a valid Kerberos ticket exists at $HOME/krb5cc.ticket."""
    ticket_path = f"FILE:{os.environ['HOME']}/krb5cc.ticket"
    result = subprocess.run(
        ["klist", "-c", ticket_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode == 0


def post_workflow_to_elog(experiment: str, workflow: Dict[str, Any]) -> None:
    """POST a single workflow definition to the LCLS eLog.

    Requires KRB5CCNAME=FILE:$HOME/krb5cc.ticket in the environment.

    Args:
        experiment: LCLS experiment name.
        workflow:   Workflow definition dict with keys: name, executable,
                    location, parameters, trigger (and optionally
                    run_param_name, run_param_value).
    """
    krbticket = KerberosTicket("HTTP@pswww.slac.stanford.edu")
    url = (
        f"https://pswww.slac.stanford.edu/ws-kerb/lgbk/lgbk/{experiment}/ws"
        "/create_update_workflow_def"
    )
    resp = requests.post(url=url, headers=krbticket.getAuthHeaders(), json=workflow)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="install_lute",
        description=(
            "Set up the LUTE workspace and register workflows in the LCLS eLog. "
            "The skill must write DAG files and the YAML config before calling this script."
        ),
        epilog="See https://github.com/slac-lcls/lute for more information.",
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Verbose logging.")
    parser.add_argument(
        "-e",
        "--experiment",
        required=True,
        type=str,
        help="LCLS experiment name (e.g. mfx123456789).",
    )
    parser.add_argument(
        "-v",
        "--version",
        type=str,
        default="dev",
        help="LUTE version tag or 'dev'. Default: dev.",
    )
    parser.add_argument(
        "-f",
        "--fresh_install",
        action="store_true",
        help=(
            "Clone and build LUTE locally in the experiment results folder. "
            "Use this for local code modifications; otherwise the central "
            "installation at /sdf/group/lcls/ds/tools/lute/ is used."
        ),
    )
    parser.add_argument(
        "-D",
        "--directory",
        type=str,
        default="",
        help="Optional subdirectory under results/ for LUTE output.",
    )
    parser.add_argument(
        "-W",
        "--workflow",
        type=str,
        nargs="+",
        action="extend",
        help=(
            "Workflow name(s) to patch and register. Each must correspond to a "
            ".dag file already present at {lute_output_dir}/{name}.dag."
        ),
    )
    parser.add_argument(
        "--trigger",
        type=str,
        nargs="+",
        action="extend",
        help=(
            "eLog trigger spec for each workflow, in the same order as -W. "
            "Formats: START_OF_RUN | END_OF_RUN | MANUAL | "
            "RUN_PARAM_IS_VALUE:<param_name>:<param_value> "
            "(e.g. RUN_PARAM_IS_VALUE:SmallData:done). "
            "Defaults to END_OF_RUN if omitted or fewer specs than workflows."
        ),
    )
    parser.add_argument(
        "--partition",
        type=str,
        default="milano",
        help="SLURM partition. Default: milano.",
    )
    parser.add_argument(
        "--account",
        type=str,
        default="",
        help="SLURM account. Default: lcls:<experiment>.",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=1,
        help="Default node count for tasks not in DEFAULT_CONFIG. Default: 1.",
    )
    parser.add_argument(
        "--ntasks-per-node",
        type=int,
        default=1,
        dest="ntasks_per_node",
        help="Default ntasks-per-node for tasks not in DEFAULT_CONFIG. Default: 1.",
    )
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    hutch: str = args.experiment[:3]
    account: str = args.account or f"lcls:{args.experiment}"

    results_dir: str = f"/sdf/data/lcls/ds/{hutch}/{args.experiment}/results"
    if args.directory:
        results_dir = f"{results_dir}/{args.directory}"

    lute_output_dir: str = os.path.join(results_dir, "lute_output")
    config_path: str = os.path.join(lute_output_dir, f"{hutch}_lute.yaml")

    # --- 1. Fresh install (optional) ---
    if args.fresh_install:
        lute_path = os.path.join(results_dir, "lute")
        git_clone("slac-lcls/lute", lute_path, args.version)
        run_build_script(lute_path)
        set_permissions(lute_path)
        arp_executable = f"{lute_path}/install/bin/submit_launch_slurm.sh"
        launch_executable = f"{lute_path}/install/bin/launch_slurm"
    else:
        lute_path = f"/sdf/group/lcls/ds/tools/lute/{args.version}/lute"
        arp_executable = f"{lute_path}/install/bin/submit_launch_slurm.sh"
        launch_executable = f"{lute_path}/install/bin/launch_slurm"

    # --- 2. Workspace setup ---
    setup_workspace(lute_output_dir)

    # --- 3. DAG patching + eLog payload assembly ---
    workflow_names: List[str] = args.workflow or []
    if not workflow_names:
        logger.warning(
            "No -W workflows specified. Workspace created; nothing to patch or register."
        )
        return

    if not os.path.exists(config_path):
        logger.error(
            f"YAML config not found: {config_path}\n"
            "  Write the config file before calling this script."
        )
        sys.exit(1)

    # Parse trigger specs, aligned positionally with workflow_names.
    # Any workflow without a matching spec defaults to END_OF_RUN.
    trigger_specs: List[str] = args.trigger or []
    triggers: List[Dict[str, str]] = []
    for i, wf_name in enumerate(workflow_names):
        raw_spec = trigger_specs[i] if i < len(trigger_specs) else "END_OF_RUN"
        try:
            triggers.append(parse_trigger(raw_spec))
        except ValueError as exc:
            logger.error(f"Invalid --trigger spec for workflow '{wf_name}': {exc}")
            sys.exit(1)

    failed: List[str] = []
    elog_payloads: List[Dict[str, Any]] = []

    for wf_name, trigger in zip(workflow_names, triggers):
        dag_path = os.path.join(lute_output_dir, f"{wf_name}.dag")
        if not os.path.exists(dag_path):
            logger.error(
                f"DAG not found: {dag_path}\n"
                "  Write the DAG file to lute_output_dir before calling this script."
            )
            failed.append(wf_name)
            continue

        patch_dag_slurm_params(
            dag_path=dag_path,
            partition=args.partition,
            account=account,
            default_nodes=args.nodes,
            default_ntasks=args.ntasks_per_node,
        )

        param_string = (
            f"{launch_executable} -c {config_path} -W {dag_path}"
            f" --partition={args.partition} --account={account}"
        )
        if args.debug:
            param_string += " --debug"

        elog_payloads.append(
            {
                "name": f"lute_{wf_name}",
                "executable": arp_executable,
                "location": "S3DF",
                "parameters": param_string,
                **trigger,
            }
        )

    if failed:
        logger.error(
            f"Skipped (DAG not found): {failed}\n  Aborting before eLog registration."
        )
        sys.exit(1)

    # --- 4. Kerberos check ---
    if not check_kerberos_ticket():
        logger.error(
            "No valid Kerberos ticket found at $HOME/krb5cc.ticket.\n"
            "Run the following in your terminal, then retry:\n\n"
            "  kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU\n\n"
            "The FILE: prefix is required."
        )
        sys.exit(1)

    os.environ["KRB5CCNAME"] = f"FILE:{os.environ['HOME']}/krb5cc.ticket"
    logger.info("Kerberos ticket validated.")

    # --- 5. eLog registration ---
    elog_failed: List[str] = []
    for payload in elog_payloads:
        logger.info(f"Registering: {payload['name']}  (trigger: {payload['trigger']})")
        try:
            post_workflow_to_elog(args.experiment, payload)
            logger.info(f"  ✓  {payload['name']}")
        except requests.exceptions.HTTPError as exc:
            logger.error(f"  ✗  {payload['name']}: {exc}")
            elog_failed.append(payload["name"])

    if elog_failed:
        logger.error(f"eLog registration failed for: {elog_failed}")
        sys.exit(1)

    logger.info(
        f"\nAll done.\n"
        f"  Workspace : {lute_output_dir}\n"
        f"  Config    : {config_path}\n"
        f"  Workflows : {', '.join(f'lute_{n}' for n in workflow_names)}\n"
        f"  Verify at : https://pswww.slac.stanford.edu/lgbk/lgbk/{args.experiment}/"
        f"  (Workflow Definitions tab)"
    )


if __name__ == "__main__":
    main()
