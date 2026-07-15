"""Set up the LUTE workspace and register workflows in the LCLS eLog.

This is the single script to run after the skill has written DAG files and the
YAML configuration. It handles everything end-to-end:

  1. LUTE installation: create two isolated Python virtual environments and
     pip-install lute-lcls into each (always recreates if already present)
  2. Workspace setup: create lute_output_dir and lute.db
  3. Inject --account and --partition into each pre-written DAG file
  4. Register each workflow in the LCLS eLog (requires Kerberos ticket)

Virtual environments created:
    {results_dir}/lute_envs/lute_env_py39/    (Python 3.9)
    {results_dir}/lute_envs/lute_env_py311/   (Python 3.11)

Python interpreters used:
    Python 3.9:  /sdf/group/lcls/ds/ana/sw/conda2/inst/bin/python3.9
    Python 3.11: /sdf/group/lcls/ds/ana/sw/conda2-v3/inst/bin/python3.11

Entry points (arp_executable, launch_executable) come from lute_env_py39/bin/.

Usage
-----
The skill writes {lute_output_dir}/{wf_name}.dag (with correct per-task
slurm_params already set) and {lute_output_dir}/{hutch}_lute.yaml, then calls:

    python install_lute.py \\
        -e {experiment} \\
        -v {version}    \\
        -W {wf1} [{wf2} ...] \\
        --trigger {spec1} [{spec2} ...] \\
        [--partition {partition}]   \\
        [--account   {account}]     \\
        [-D {subdirectory}]

Notes
-----
- Does NOT write or overwrite the YAML config — the skill owns that file.
- Does NOT copy DAG files from workflows/common/ — DAGs must already exist.
- Workspace creation is idempotent: will not fail if the directory or database
  already exist.
- Per-task resource requirements (--nodes, --ntasks-per-node, --exclusive, etc.)
  are the skill's responsibility and must be written into the DAG at creation time.
  To change resources for a specific task, edit the .dag file directly.
"""

__author__ = "Gabriel Dorlhiac"

import argparse
import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

import requests
from krtc import KerberosTicket  # type: ignore


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Python interpreter paths for virtual env creation (-fi mode)
# ---------------------------------------------------------------------------

_PYTHON39_PATH = "/sdf/group/lcls/ds/ana/sw/conda2/inst/bin/python3.9"
_PYTHON311_PATH = "/sdf/group/lcls/ds/ana/sw/conda2-v3/inst/bin/python3.11"

# ---------------------------------------------------------------------------
# Trigger spec parsing
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
# Subprocess helper
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
# Mode 1: fresh virtual env install (-fi)
# ---------------------------------------------------------------------------


def create_virtual_envs(lute_envs_dir: str, version: str = "dev") -> None:
    """Create (or recreate) lute_env_py39 and lute_env_py311 under lute_envs_dir.

    If lute_envs_dir already exists, it is deleted entirely before recreation
    so the caller always ends up with a clean state.

    Args:
        lute_envs_dir: Absolute path to the directory that will hold both
                       virtual environments, e.g.
                       /sdf/data/lcls/ds/mfx/mfxl1013621/results/lute_envs
        version:       LUTE version to pip-install. Use "dev" (default) for the
                       latest published release; any other value is passed as a
                       version pin (e.g. "0.2.0" → pip install lute-lcls==0.2.0).
    """
    if os.path.exists(lute_envs_dir):
        logger.info(
            f"lute_envs/ already exists at {lute_envs_dir} — removing for clean install."
        )
        shutil.rmtree(lute_envs_dir)

    os.makedirs(lute_envs_dir, mode=0o775)
    logger.info(f"Created lute_envs directory: {lute_envs_dir}")

    package_spec = "lute-lcls" if version == "dev" else f"lute-lcls=={version}"

    envs = [
        ("lute_env_py39", _PYTHON39_PATH),
        ("lute_env_py311", _PYTHON311_PATH),
    ]

    for env_name, python_exe in envs:
        env_path = os.path.join(lute_envs_dir, env_name)
        logger.info(f"Creating virtual env {env_name} using {python_exe} …")

        if not os.path.isfile(python_exe):
            logger.error(
                f"Python interpreter not found: {python_exe}\n"
                "  Ensure the LCLS conda stack is present on this system."
            )
            sys.exit(1)

        _run([python_exe, "-m", "venv", env_path])
        pip = os.path.join(env_path, "bin", "pip")
        logger.info(f"  pip install {package_spec} into {env_name} …")
        _run([pip, "install", "--upgrade", "pip"])
        _run([pip, "install", package_spec])
        logger.info(f"  {env_name}: done.")


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


def patch_dag_slurm_params(dag_path: str, partition: str, account: str) -> None:
    """Append --account and --partition to every slurm_params field in a DAG file.

    Per-task resource requirements (--nodes, --ntasks-per-node, --exclusive, ...)
    are the skill's responsibility and must already be present in the DAG when
    this function is called.  This function only injects the two environment-
    specific values that are not known at DAG-creation time.

    Args:
        dag_path:  Path to the .dag YAML file.
        partition: SLURM partition name (e.g. "milano").
        account:   SLURM account (e.g. "lcls:mfxl1013621").
    """
    with open(dag_path) as fh:
        lines = fh.readlines()

    patched: List[str] = []
    found: int = 0

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("slurm_params:"):
            indent = line[: len(line) - len(stripped)]
            existing = stripped[len("slurm_params:") :].strip().strip("'\"")
            new_params = f"{existing} --account={account} --partition={partition}"
            patched.append(f"{indent}slurm_params: '{new_params}'\n")
            found += 1
        else:
            patched.append(line)

    if found == 0:
        logger.warning(
            f"No slurm_params lines found in {dag_path}. "
            "The DAG may be missing resource specifications — check the file."
        )

    with open(dag_path, "w") as fh:
        fh.writelines(patched)

    os.chmod(dag_path, 0o666)
    logger.info(
        f"Injected account/partition into {found} slurm_params block(s): {dag_path}"
    )


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
        help="LUTE version to install. 'dev' (default) installs the latest published lute-lcls; any other value is used as a version pin (e.g. '0.2.0' → pip install lute-lcls==0.2.0).",
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

    # --- 1. Install LUTE (always virtual envs) ---
    lute_envs_dir = os.path.join(results_dir, "lute_envs")
    create_virtual_envs(lute_envs_dir, version=args.version)
    venv_py39 = os.path.join(lute_envs_dir, "lute_env_py39")
    arp_executable = os.path.join(venv_py39, "bin", "submit_launch_slurm.sh")
    launch_executable = os.path.join(venv_py39, "bin", "launch_slurm")

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
