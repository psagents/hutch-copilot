"""The package surface, and the laziness that keeps the commands decoupled."""

from __future__ import annotations

import subprocess
import sys

import pytest

import hutch_tools


def test_public_names_resolve():
    assert hutch_tools.State.__name__ == "State"
    assert hutch_tools.classify("daq.state")["class"] == hutch_tools.READ_ONLY
    assert hutch_tools.RESOLVED == "resolved"
    assert issubclass(hutch_tools.ConfigError, RuntimeError)


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        hutch_tools.nope


def test_submodules_are_reachable_as_attributes():
    assert hutch_tools.paths.AGENT_SUBDIR == "results/psagents"
    assert callable(hutch_tools.state.main)


def test_dir_lists_the_public_surface():
    listed = dir(hutch_tools)
    assert "State" in listed and "paths" in listed and "build" in listed
    assert listed == sorted(listed)


def test_every_export_actually_exists():
    for name in hutch_tools._EXPORTS:
        assert getattr(hutch_tools, name) is not None, name


def test_every_module_imports():
    for name in hutch_tools.MODULES:
        assert getattr(hutch_tools, name) is not None, name


# --------------------------------------------------------------------------
# The decoupling this layout exists to preserve
# --------------------------------------------------------------------------
def _imported_after(target: str) -> set[str]:
    """Which hutch_tools submodules end up in sys.modules after importing `target`?"""
    code = (
        "import sys; sys.path.insert(0, '.');"
        f"import {target};"
        "print(' '.join(sorted(m for m in sys.modules if m.startswith('hutch_tools.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    return set(out.stdout.decode().split())


def test_importing_state_does_not_drag_in_config_or_its_deps():
    """__init__ must stay lazy.

    Importing a submodule executes __init__.py, so an eager re-export there would
    pull config -- and jinja2/PyYAML -- into every command, and a syntax error in
    config.py would break hutch-state again.
    """
    loaded = _imported_after("hutch_tools.state")
    assert "hutch_tools.config" not in loaded
    assert "hutch_tools.resolvers" not in loaded
    assert "hutch_tools.bridge" not in loaded
    assert loaded == {"hutch_tools._cli", "hutch_tools.paths", "hutch_tools.state"}


def test_importing_lute_does_not_drag_in_config():
    loaded = _imported_after("hutch_tools.lute")
    assert "hutch_tools.config" not in loaded


def test_no_module_object_relative_imports_remain():
    """Internal imports name what they use.

    `from . import traps` then `traps.check(...)` hides the dependency in the call
    site; `from .traps import check as check_traps` puts it in the import block,
    which is also what makes the build_sbatch trap test a plain monkeypatch.
    """
    import pathlib
    import re

    root = pathlib.Path(hutch_tools.__file__).parent
    offenders = {
        p.name: re.findall(r"^\s*from \. import .*$", p.read_text(), re.M)
        for p in sorted(root.glob("*.py"))
    }
    assert not any(offenders.values()), {k: v for k, v in offenders.items() if v}
