"""The package's shape: one module per binary, stdlib only.

The stdlib-only check is the load-bearing one. The tools have to run from a bare
checkout under psconda with no install step, so a third-party import anywhere in
`hutch_tools` breaks every command on a machine that happens not to have it.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import subprocess
import sys

import pytest

import hutch_tools

#: module -> the bin/hutch-* that fronts it.
TOOLS = {
    "state": "hutch-state",
    "daq": "hutch-daq",
    "resolve": "hutch-resolve",
    "lute": "hutch-lute",
    "log": "hutch-log",
}

PKG = pathlib.Path(hutch_tools.__file__).resolve().parent
ROOT = PKG.parent

#: Everything `hutch_tools` is allowed to import. Standard library only.
STDLIB_ALLOWED = set(getattr(sys, "stdlib_module_names", ())) | {
    "hutch_tools", "__future__",
}


def test_version_is_exposed():
    assert hutch_tools.__version__


@pytest.mark.parametrize("module", sorted(TOOLS))
def test_each_tool_module_has_a_cli(module):
    mod = importlib.import_module(f"hutch_tools.{module}")
    assert callable(mod.main)
    assert callable(mod.build_parser)


@pytest.mark.parametrize("module,binary", sorted(TOOLS.items()))
def test_each_binary_points_at_its_module(module, binary):
    """bin/hutch-x must import hutch_tools.x -- a stale shim is a broken tool."""
    assert f"from hutch_tools.{module} import main" in (ROOT / "bin" / binary).read_text()


def test_every_module_is_either_a_tool_or_common():
    found = {p.stem for p in PKG.glob("*.py")} - {"__init__"}
    assert found == set(TOOLS) | {"common"}


@pytest.mark.parametrize("source", sorted(PKG.glob("*.py")), ids=lambda p: p.name)
def test_no_module_imports_a_third_party_package(source):
    """jinja2, PyYAML, requests and krtc left with config.py and install.py.

    Nothing may bring one back: `pyproject.toml` declares no dependencies, so an
    import here is a command that fails on arrival rather than at install time.
    """
    tree = ast.parse(source.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported - STDLIB_ALLOWED)


def test_a_tool_runs_from_a_bare_checkout():
    """No install step, no PYTHONPATH: the bin/ shim has to be enough."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "hutch-state"), "--help"],
        capture_output=True, cwd="/", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"audit" in proc.stdout
