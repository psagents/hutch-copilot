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
# Paths -- the only place a hutch-copilot path is constructed
# --------------------------------------------------------------------------
#: Root of the LCLS data filesystem. Overridable so tests can use a fixture tree.
DS_ROOT = pathlib.Path(os.environ.get("HUTCH_DS_ROOT", "/sdf/data/lcls/ds"))

#: Where hutch-copilot keeps its own artifacts inside an experiment directory.
AGENT_SUBDIR = "results/psagents"


def _norm_hutch(hutch: str) -> str:
    """Hutch names are lowercase on the filesystem, uppercase in happi."""
    return hutch.strip().lower()


def experiment_dir(hutch: str, experiment: str) -> pathlib.Path:
    """Top-level experiment directory, pre-provisioned by LCLS DM."""
    return DS_ROOT / _norm_hutch(hutch) / experiment


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
