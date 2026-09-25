"""hutch_tools -- the tool layer for hutch-copilot.

Mechanism lives here so that skills can carry only judgment. Every command
returns JSON on stdout and a non-zero exit status with a structured error on
stderr.

Layering (nothing above depends on anything below it, and there are no cycles):

    _cli                       output contract only: out, fail, exit codes
    paths  safety  traps       leaves; no intra-package dependencies
    _fs                        lock + atomic append, shared by state and log
    state     -> paths
    log       -> paths
    install   -> paths
    daq       -> paths, safety
    lute      -> traps
    config    -> traps
    resolvers -> daq, state

Public API, for consumers that want names rather than modules::

    from hutch_tools import State, classify, build, ConfigError

WHY THIS IS LAZY. Importing any submodule executes this file, so an eager
``from .config import build`` here would pull ``config`` -- and therefore jinja2
and PyYAML -- into ``import hutch_tools.state``. That would undo the decoupling the
per-module CLI split bought: a syntax error in ``config.py`` would once again
break ``hutch-state``. PEP 562 ``__getattr__`` resolves each name only when it
is actually asked for, so ``bin/hutch-state`` still touches nothing but
``paths``, ``state`` and ``_cli``.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.1.0"

#: Submodules, in dependency order. ``from hutch_tools import state`` is resolved by
#: the import system; ``__getattr__`` below only covers the attribute form.
MODULES = (
    "_cli",
    "_fs",
    "paths",
    "safety",
    "traps",
    "state",
    "daq",
    "lute",
    "config",
    "resolvers",
    "log",
    "install",
)

#: Curated public names -> the module that defines each. Anything absent is
#: internal; import the submodule explicitly if you need it.
_EXPORTS = {
    # paths
    "agent_dir": "paths",
    "experiment_dir": "paths",
    "log_file": "paths",
    "skill_root": "paths",
    "state_file": "paths",
    "xtc2_dir": "paths",
    # state
    "INFERRED": "state",
    "MissingValue": "state",
    "OPERATOR": "state",
    "RESOLVED": "state",
    "StaleValue": "state",
    "State": "state",
    "StateError": "state",
    # safety
    "BEAM_CRITICAL": "safety",
    "READ_ONLY": "safety",
    "SafetyRefusal": "safety",
    "WRITE": "safety",
    "classify": "safety",
    "require_confirmation": "safety",
    # daq
    "BridgeError": "daq",
    "BridgeUnavailable": "daq",
    "exec_code": "daq",
    "ping": "daq",
    # resolvers
    "ResolveError": "resolvers",
    # traps
    "template_whitelist": "traps",
    # lute
    "LuteError": "lute",
    "build_sbatch": "lute",
    "job_status": "lute",
    "needs_kerberos": "lute",
    # log
    "LogError": "log",
    # install
    "InstallError": "install",
    "parse_trigger": "install",
    # config
    "ConfigError": "config",
    "DELETE": "config",
    "build": "config",
}

__all__ = [*MODULES, *sorted(_EXPORTS), "MODULES", "__version__"]


def __getattr__(name: str) -> Any:
    """Resolve a public name (or a submodule) on first access. See module docstring."""
    if name in _EXPORTS:
        return getattr(importlib.import_module(f".{_EXPORTS[name]}", __name__), name)
    if name in MODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
