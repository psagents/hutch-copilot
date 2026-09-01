"""LUTE workspace setup and eLog workflow registration.

Ported from ``analyze-data/scripts/install_lute.py`` by Gabriel Dorlhiac, which
was the largest file in the repo and had no tests. The logic is his; what changed
is the shape, and two bugs fell out of the reshaping.

**Bug 1 — DAG patching was not idempotent.** It appended ``--account`` and
``--partition`` to every ``slurm_params`` line, so a second run produced
``... --account=X --partition=Y --account=X --partition=Y``. Its own docstring
told you to *"re-run with the same -W to update registration after a DAG or
trigger change"*. ``patch_slurm_params`` here strips any account/partition tokens
already present before appending, so it is idempotent **and repairs a DAG a
previous run doubled**.

**Bug 2 — preconditions were checked last.** The Kerberos check was step 4, after
``create_virtual_envs`` had already ``rmtree``-d the venv directory and after the
DAGs were patched. A missing ticket therefore destroyed both environments and
doubled the DAG parameters before failing. Here every precondition is evaluated
by ``plan()`` before anything mutates.

Two further changes follow the repo's rules rather than the original's:

  * The results path came from an f-string against ``/sdf/data/lcls/ds/...``.
    It now comes from ``paths.results_dir`` -- the same reason no skill spells
    out the state file path.
  * Recreating the virtual environments deletes a directory, so it requires
    ``--confirm write``. Nothing here removes anything without being told to.

``requests`` and ``krtc`` are imported inside ``register`` so that planning,
patching and workspace setup work without them.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

from ._cli import OK, UNREACHABLE, USAGE, fail, out, parser
from .paths import results_dir

#: Interpreters used for the two virtual environments LUTE needs.
PYTHON39 = "/sdf/group/lcls/ds/ana/sw/conda2/inst/bin/python3.9"
PYTHON311 = "/sdf/group/lcls/ds/ana/sw/conda2-v3/inst/bin/python3.11"
ENVS = (("lute_env_py39", PYTHON39), ("lute_env_py311", PYTHON311))

ELOG_URL = ("https://pswww.slac.stanford.edu/ws-kerb/lgbk/lgbk/"
            "{experiment}/ws/create_update_workflow_def")

SIMPLE_TRIGGERS = ("START_OF_RUN", "END_OF_RUN", "MANUAL")
DEFAULT_TRIGGER = "END_OF_RUN"


class InstallError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Pure functions -- the parts worth testing
# --------------------------------------------------------------------------
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


def have_ticket() -> tuple[bool, str]:
    """Is there a usable Kerberos ticket at the path the eLog path expects?"""
    ticket = f"FILE:{os.environ.get('HOME', '')}/krb5cc.ticket"
    try:
        rc = subprocess.run(["klist", "-c", ticket], capture_output=True,
                            timeout=10).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"klist failed: {exc}"
    return rc == 0, ticket


# --------------------------------------------------------------------------
# plan -- read-only, and evaluated before anything mutates
# --------------------------------------------------------------------------
def plan(
    *, experiment: str, hutch: str | None = None, directory: str | None = None,
    workflows: list[str] | None = None, triggers: list[str] | None = None,
    partition: str = "milano", account: str | None = None,
    version: str = "dev", debug: bool = False,
) -> dict:
    """Everything the install would do, with every precondition, touching nothing.

    The original checked its preconditions after the destructive steps. Separating
    the plan is what makes ordering a property of the design instead of a comment.
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
    ticket_ok, ticket = have_ticket()

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


# --------------------------------------------------------------------------
# Side effects
# --------------------------------------------------------------------------
def _run(cmd: list[str]) -> None:
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
        _run([interpreter, "-m", "venv", str(env)])
        pip = str(env / "bin" / "pip")
        _run([pip, "install", "--upgrade", "pip"])
        _run([pip, "install", spec])
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
    ticket_ok, ticket = have_ticket()
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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
_DOC = """hutch-install: plan | envs | workspace | dags | register | all."""


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--experiment", "-e", required=True)
    p.add_argument("--hutch", default=None, help="defaults to the first 3 chars of experiment")
    p.add_argument("--directory", "-D", default=None, help="subdirectory under results/")
    p.add_argument("--workflow", "-W", nargs="*", default=[], help="DAG basenames")
    p.add_argument("--trigger", nargs="*", default=[],
                   help=f"per workflow, in order; default {DEFAULT_TRIGGER}")
    p.add_argument("--partition", default="milano")
    p.add_argument("--account", default=None, help="defaults to lcls:<experiment>")
    p.add_argument("--version", "-v", default="dev", help="lute-lcls version, or 'dev'")
    p.add_argument("--debug", action="store_true")


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (separate from main() so it can be introspected)."""
    ap, sub = parser("hutch-install", _DOC)
    for name, helptext in (
        ("plan", "print every path, payload and precondition; touches nothing"),
        ("envs", "recreate both virtual environments (destructive)"),
        ("workspace", "create lute_output and lute.db (idempotent)"),
        ("dags", "set account/partition in each DAG (idempotent)"),
        ("register", "POST workflow definitions to the eLog"),
        ("all", "workspace, dags, envs, register -- in that order, after checks"),
    ):
        p = sub.add_parser(name, help=helptext)
        _common(p)
        if name in ("envs", "register", "all"):
            p.add_argument("--confirm", default=None, choices=["write"])
    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-install: plan | envs | workspace | dags | register | all."""
    args = build_parser().parse_args(argv)
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
                    + ". Run `hutch-install plan` for detail. Nothing was changed."
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
