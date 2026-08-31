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

# Root of the LCLS data filesystem. Overridable for tests and for the
# scenario harness, which runs against a fixture tree.
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
    return experiment_dir(hutch, experiment) / "xtc2"


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
