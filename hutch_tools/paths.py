"""Canonical path resolution for hutch-copilot.

THE ONLY PLACE any hutch-copilot path is constructed.

Before this module the state-file path was written out in prose in six
SKILL.md files and three of them were wrong -- they said
``{exp}/{exp}_state.json`` instead of ``{exp}/results/psagents/{exp}_state.json``.
Those three sub-skills therefore read a file nothing ever wrote, so their
"use any non-null field, do not re-ask the user" rule silently degraded into
re-asking the user for everything.

Skills must call ``hutch-state path`` rather than interpolating a path
themselves.
"""

from __future__ import annotations

import os
import pathlib

# Root of the LCLS data filesystem. Overridable so the unit tests can run
# against a fixture tree instead of /sdf/data/lcls/ds.
DS_ROOT = pathlib.Path(os.environ.get("HUTCH_DS_ROOT", "/sdf/data/lcls/ds"))

# Where hutch-copilot keeps its own artifacts inside an experiment directory.
# Named 'psagents' since commit 499e147.
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
    return agent_dir(hutch, experiment) / f"{experiment}_state.json"


def log_file(hutch: str, experiment: str) -> pathlib.Path:
    return agent_dir(hutch, experiment) / f"{experiment}_logs.md"


def xtc2_dir(hutch: str, experiment: str) -> pathlib.Path:
    """Directory the DAQ writes ``.xtc2`` files into.

    It is ``xtc``, not ``xtc2`` -- the ``2`` is the file extension, not part of
    the directory name. This returned ``xtc2`` until Sep 2026, and all 175 mfx
    experiment directories on disk use ``xtc/`` (none anywhere under
    /sdf/data/lcls/ds uses ``xtc2/``), so `hutch-resolve xtc2` reported
    ``present: false`` for data that had landed and the peds trap refused every
    DARK run.
    """
    return experiment_dir(hutch, experiment) / "xtc"


def xtc2_files(hutch: str, experiment: str, run: int) -> list[pathlib.Path]:
    """XTC2 files for one run. Shared by the resolver and the pedestal guard.

    Lives here rather than in either caller so there is one definition of what
    "the data has landed" means -- take-run used to check it with an inline
    `ls | grep`, which cannot be tested and reports nothing about size.

    The suffix filter is load-bearing: ``xtc/`` also holds ``index/``, ``md5/``
    and ``smalldata/`` subdirectories, and a bare ``*r0004*`` glob would count
    anything of theirs that happened to carry the run number.
    """
    d = xtc2_dir(hutch, experiment)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"*r{int(run):04d}*") if p.suffix == ".xtc2")


def results_dir(hutch: str, experiment: str, subdir: str | None = None) -> pathlib.Path:
    base = experiment_dir(hutch, experiment) / "results"
    return base / subdir if subdir else base


def skill_root() -> pathlib.Path:
    """Install directory of this skill, resolved from this file's location.

    Never hardcode it. ``SKILL.md`` and ``are-we-ready/SKILL.md`` shipped
    ``/sdf/home/f/fpoitevi/.claude/skills/hutch-copilot/...`` in three places,
    which made the bridge setup and both readiness checks fail for every user
    except the original author.
    """
    return pathlib.Path(__file__).resolve().parent.parent


def script(*parts: str) -> pathlib.Path:
    """Path to a script shipped with this skill, e.g. script('scripts', 'oc_bridge.py')."""
    p = skill_root().joinpath(*parts)
    if not p.exists():
        raise FileNotFoundError(f"skill script not found: {p}")
    return p
