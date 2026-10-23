"""Output contract, exit codes, and canonical paths -- shared by all five tools.

Every command prints JSON on stdout and a structured error on stderr, and exits
with one of the codes below so a caller can branch without parsing text.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any

# --------------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------------
OK = 0
USAGE = 2
STATE_ERROR = 3
UNREACHABLE = 4
REFUSED = 5


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------
def out(obj: Any, plain: bool = False) -> int:
    """Print a result. JSON unless ``plain`` and the object is already a string."""
    if plain and isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    return OK


def fail(exc: BaseException, code: int = 1) -> int:
    """Print a structured error to stderr and return an exit code."""
    print(
        json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2),
        file=sys.stderr,
    )
    return code


def parser(prog: str, doc: str | None) -> tuple[argparse.ArgumentParser, Any]:
    """An argument parser and its subparsers, with the shared conventions applied."""
    ap = argparse.ArgumentParser(prog=prog, description=doc)
    return ap, ap.add_subparsers(dest="cmd", required=True)


def experiment_args(p: argparse.ArgumentParser) -> None:
    """Add the --hutch/--experiment pair that most subcommands need."""
    p.add_argument("--hutch", required=True)
    p.add_argument("--experiment", required=True)


def json_or_str(raw: str) -> Any:
    """Parse a CLI value as JSON when possible, else keep it a string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------
def utcnow() -> str:
    """UTC timestamp, second resolution. Used for provenance."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def local_hhmm() -> str:
    """Wall-clock HH:MM from the machine clock. The log is read by humans on shift."""
    return datetime.datetime.now().strftime("%H:%M")


# --------------------------------------------------------------------------
# External paths -- declared by bin/hutch-env, never by this package
#
# This package holds no path outside its own checkout. bin/hutch-env is the
# single source of truth; a hardcoded literal here is how the EPICS bin dir
# came to be written down twice, in two languages, free to drift apart.
# --------------------------------------------------------------------------
#: Matches either declaration shape bin/hutch-env uses:
#:   export HUTCH_X="${HUTCH_X:-/value}"
#:   export HUTCH_X='/value'            (inside an `if [ -z ... ]` guard)
#: Greedy to the last brace on purpose -- HUTCH_DAQ_CONFIG_DIR's value contains
#: a literal `{hutch}`, which a `[^}]+` pattern would truncate.
_DECLARATION = (
    r"""^\s*export HUTCH_%(n)s="\$\{HUTCH_%(n)s:-(?P<expanded>.*)\}"\s*$"""
    r"""|^\s*export HUTCH_%(n)s='(?P<quoted>[^']*)'\s*$"""
)


def external_path(name: str) -> str:
    """A path outside this repo: ``$HUTCH_<name>``, else what bin/hutch-env declares.

    Reading the file when the variable is unset is deliberate: the five
    ``bin/hutch-*`` shims are plain Python and nothing sources hutch-env, so
    requiring the export would break every invocation that does not.
    """
    env = os.environ.get("HUTCH_" + name)
    if env:
        return env
    # Resolved from this file rather than via script(), which is defined below
    # and would be a forward reference at import time.
    env_file = pathlib.Path(__file__).resolve().parent.parent / "bin" / "hutch-env"
    try:
        text = env_file.read_text(errors="replace")
    except OSError as exc:
        raise FileNotFoundError(f"cannot read {env_file}: {exc}") from exc
    match = re.search(_DECLARATION % {"n": name}, text, re.M)
    if match is None:
        raise FileNotFoundError(
            f"bin/hutch-env does not declare HUTCH_{name}. It is the single "
            f"source of truth for external paths -- add it there, not here."
        )
    found = match.group("expanded")
    return found if found is not None else match.group("quoted")


# --------------------------------------------------------------------------
# Paths -- the only place a hutch-copilot path is constructed
# --------------------------------------------------------------------------
def ds_root() -> pathlib.Path:
    """Root of the LCLS data filesystem."""
    return pathlib.Path(external_path("DS_ROOT"))

#: Where hutch-copilot keeps its own artifacts inside an experiment directory.
AGENT_SUBDIR = "results/psagents"


def _norm_hutch(hutch: str) -> str:
    """Hutch names are lowercase on the filesystem, uppercase in happi."""
    return hutch.strip().lower()


def experiment_dir(hutch: str, experiment: str) -> pathlib.Path:
    """Top-level experiment directory, pre-provisioned by LCLS DM."""
    return ds_root() / _norm_hutch(hutch) / experiment


def agent_dir(hutch: str, experiment: str) -> pathlib.Path:
    """Directory holding the state JSON and the beamtime log."""
    return experiment_dir(hutch, experiment) / AGENT_SUBDIR


def state_file(hutch: str, experiment: str) -> pathlib.Path:
    """Canonical path to the experiment state file."""
    return agent_dir(hutch, experiment) / f"{experiment}_state.json"


def log_file(hutch: str, experiment: str) -> pathlib.Path:
    """Canonical path to the beamtime log."""
    return agent_dir(hutch, experiment) / f"{experiment}_logs.md"


def results_dir(hutch: str, experiment: str, subdir: str | None = None) -> pathlib.Path:
    """The experiment's results directory, or a named subdirectory of it."""
    base = experiment_dir(hutch, experiment) / "results"
    return base / subdir if subdir else base


def xtc2_dir(hutch: str, experiment: str) -> pathlib.Path:
    """Directory the DAQ writes .xtc2 files into.

    Named ``xtc``, not ``xtc2`` -- the 2 is the file extension.
    """
    return experiment_dir(hutch, experiment) / "xtc"


def xtc2_files(hutch: str, experiment: str, run: int) -> list[pathlib.Path]:
    """XTC2 files for one run. One definition of what "the data has landed" means.

    The suffix filter matters: ``xtc/`` also holds ``index/``, ``md5/`` and
    ``smalldata/``, whose contents can carry the run number too.
    """
    d = xtc2_dir(hutch, experiment)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"*r{int(run):04d}*") if p.suffix == ".xtc2")


def skill_root() -> pathlib.Path:
    """Install directory of this skill, resolved from this file's location.

    Never hardcode it -- a literal home directory breaks the skill for every
    user but its author.
    """
    return pathlib.Path(__file__).resolve().parent.parent


def script(*parts: str) -> pathlib.Path:
    """Path to a script shipped with this skill, e.g. script('scripts', 'oc_bridge.py')."""
    p = skill_root().joinpath(*parts)
    if not p.exists():
        raise FileNotFoundError(f"skill script not found: {p}")
    return p


def _acl(argv: list[str], remote: bool) -> subprocess.CompletedProcess:
    if remote:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "psana", shlex.join(argv)]
    return subprocess.run(argv, capture_output=True)


def grant_group(group: str, *, rw: list[pathlib.Path] = (), rx: list[pathlib.Path] = ()) -> dict:
    """Give ``group`` access to these trees (access + default ACLs), then check it took.

    An eLog workflow runs as whoever fires it, so every experiment member needs
    the registered workspace -- not just ``ps-data``. World permissions are
    never the fix: any ``o+w`` found is removed. Paths this user does not own
    cannot take an ACL and are reported, not failed.
    """
    me = os.getuid()
    skipped, failed = [], []
    for perm, roots in (("rwX", rw), ("rX", rx)):
        want = "rw" if perm == "rwX" else "r"
        for root in map(pathlib.Path, roots):
            if not root.exists():
                continue
            owned = []
            for p in [root, *root.rglob("*")]:
                if p.is_symlink():
                    continue
                st = p.lstat()
                if st.st_uid != me:
                    skipped.append(str(p))
                    continue
                owned.append(str(p))
                if st.st_mode & 0o002:
                    os.chmod(p, st.st_mode & ~0o002)
            # One recursive call per tree; it reports, and skips, what it cannot change.
            # The hutch hosts mount /sdf/data over NFSv4 without POSIX ACLs ("Operation
            # not supported", and getfacl shows none that S3DF has), so set and check on psana.
            setfacl = ["setfacl", "-R", "-m", f"g:{group}:{perm},d:g:{group}:{perm}", str(root)]
            remote = b"Operation not supported" in _acl(setfacl, False).stderr
            if remote:
                _acl(setfacl, True)
            acls = _acl(["getfacl", "-Rp", str(root)], remote).stdout.decode("utf-8", "replace")
            granted = set()
            for block in acls.split("\n\n"):
                name = re.search(r"^# file: (.+)$", block, re.M)
                if name and re.search(rf"^group:{re.escape(group)}:{want}", block, re.M):
                    granted.add(name.group(1))
            failed += [p for p in owned if p not in granted]
    return {"group": group, "ok": not failed, "failed": failed, "skipped_not_owned": skipped}
