"""The package surface, and the laziness that keeps the commands decoupled."""

from __future__ import annotations

import subprocess
import sys

import pytest

import hutchlib


def test_public_names_resolve():
    assert hutchlib.State.__name__ == "State"
    assert hutchlib.classify("daq.state")["class"] == hutchlib.READ_ONLY
    assert hutchlib.RESOLVED == "resolved"
    assert issubclass(hutchlib.ConfigError, RuntimeError)


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        hutchlib.nope


def test_submodules_are_reachable_as_attributes():
    assert hutchlib.paths.AGENT_SUBDIR == "results/psagents"
    assert callable(hutchlib.state.main)


def test_dir_lists_the_public_surface():
    listed = dir(hutchlib)
    assert "State" in listed and "paths" in listed and "build" in listed
    assert listed == sorted(listed)


def test_every_export_actually_exists():
    for name in hutchlib._EXPORTS:
        assert getattr(hutchlib, name) is not None, name


def test_every_module_imports():
    for name in hutchlib.MODULES:
        assert getattr(hutchlib, name) is not None, name


# --------------------------------------------------------------------------
# The decoupling this layout exists to preserve
# --------------------------------------------------------------------------
def _imported_after(target: str) -> set[str]:
    """Which hutchlib submodules end up in sys.modules after importing `target`?"""
    code = (
        "import sys; sys.path.insert(0, '.');"
        f"import {target};"
        "print(' '.join(sorted(m for m in sys.modules if m.startswith('hutchlib.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    return set(out.stdout.decode().split())


def test_importing_state_does_not_drag_in_config_or_its_deps():
    """__init__ must stay lazy.

    Importing a submodule executes __init__.py, so an eager re-export there would
    pull config -- and jinja2/PyYAML -- into every command, and a syntax error in
    config.py would break hutch-state again.
    """
    loaded = _imported_after("hutchlib.state")
    assert "hutchlib.config" not in loaded
    assert "hutchlib.resolvers" not in loaded
    assert "hutchlib.bridge" not in loaded
    assert loaded == {"hutchlib._cli", "hutchlib.paths", "hutchlib.state"}


def test_importing_lute_does_not_drag_in_config():
    loaded = _imported_after("hutchlib.lute")
    assert "hutchlib.config" not in loaded


def test_no_module_object_relative_imports_remain():
    """Internal imports name what they use.

    `from . import traps` then `traps.check(...)` hides the dependency in the call
    site; `from .traps import check as check_traps` puts it in the import block,
    which is also what makes the build_sbatch trap test a plain monkeypatch.
    """
    import pathlib
    import re

    root = pathlib.Path(hutchlib.__file__).parent
    offenders = {
        p.name: re.findall(r"^\s*from \. import .*$", p.read_text(), re.M)
        for p in sorted(root.glob("*.py"))
    }
    assert not any(offenders.values()), {k: v for k, v in offenders.items() if v}
