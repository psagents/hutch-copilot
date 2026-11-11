"""The package's shape: one module per binary, stdlib only at module scope.

The stdlib-only check is the load-bearing one. The tools have to run from a bare
checkout under psconda with no install step, so a third-party import anywhere in
`hutch_copilot` (bar `server`, the MCP SDK's one home) breaks every command on a machine that happens not to have it.

`hutch-lute render` genuinely needs jinja2 and PyYAML, and `register` needs
requests and krtc. Those four are imported *inside* the functions that use them,
so the AST check below -- which only walks module-scope imports -- still holds
for every module, and `test_building_the_lute_parser_does_not_import_jinja2`
pins the part the AST cannot see.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import subprocess
import sys

import pytest

import hutch_copilot

#: module, under hutch_copilot -> the bin/hutch-* that fronts it.
TOOLS = {
    "tools.state": "hutch-state",
    "tools.daq": "hutch-daq",
    "tools.happi": "hutch-happi",
    "tools.epics": "hutch-epics",
    "tools.lightpath": "hutch-lightpath",
    "tools.session": "hutch-py",
    "tools.data": "hutch-data",
    "tools.skills": "hutch-skills",
    "tools.lute": "hutch-lute",
    "tools.log": "hutch-log",
    "normal": "hutch-normal",
}

PKG = pathlib.Path(hutch_copilot.__file__).resolve().parent
ROOT = PKG.parents[1]
#: Every module but server, the MCP layer and the only one that may import the MCP SDK.
STDLIB_ONLY = sorted(p for p in PKG.rglob("*.py") if p.name != "server.py")

#: Everything those modules are allowed to import. Standard library only.
STDLIB_ALLOWED = set(getattr(sys, "stdlib_module_names", ())) | {
    "hutch_copilot", "__future__",
}


def _dotted(p: pathlib.Path) -> str:
    return ".".join(p.relative_to(PKG).with_suffix("").parts)


def test_version_is_exposed():
    assert hutch_copilot.__version__


@pytest.mark.parametrize("module", sorted(TOOLS))
def test_each_tool_module_has_a_cli(module):
    mod = importlib.import_module(f"hutch_copilot.{module}")
    assert callable(mod.main)
    assert callable(mod.build_parser)


@pytest.mark.parametrize("module,binary", sorted(TOOLS.items()))
def test_each_binary_points_at_its_module(module, binary):
    """bin/hutch-x must import hutch_copilot.<module> -- a stale shim is a broken tool."""
    assert f"from hutch_copilot.{module} import main" in (ROOT / "bin" / binary).read_text()


def test_every_module_is_a_tool_common_or_the_server():
    found = {_dotted(p) for p in PKG.rglob("*.py") if p.stem != "__init__"}
    assert found == set(TOOLS) | {"tools.common", "server"}


@pytest.mark.parametrize("source", sorted((PKG / "tools").glob("*.py")), ids=lambda p: p.name)
def test_tools_never_import_the_layers_above_them(source):
    """tools/ is the bottom layer: normal and server build on it, never the reverse."""
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.ImportFrom):
            assert node.level < 2, f"{source.name}: from {'.' * node.level}{node.module or ''}"
            if node.level == 0 and node.module:
                assert not node.module.startswith("hutch_copilot.") or \
                    node.module.startswith("hutch_copilot.tools"), f"{source.name}: {node.module}"


#: Third-party packages a function-local import may name, and what needs them.
LAZY_ALLOWED = {
    "jinja2": "hutch-lute render",
    "yaml": "hutch-lute render",
    "requests": "hutch-lute register",
    "krtc": "hutch-lute register",
}


@pytest.mark.parametrize("source", STDLIB_ONLY, ids=_dotted)
def test_no_module_imports_a_third_party_package_at_module_scope(source):
    """`pyproject.toml` declares no dependencies, so a module-scope third-party
    import is a command that fails on arrival rather than at install time.

    Module scope specifically: `import jinja2` at the top of lute.py breaks
    `hutch-lute watch` on a machine without it, while the same import inside
    `render_template` costs nothing until someone renders a config. That is the
    whole discipline, so the walk stops at function and class bodies rather than
    using ast.walk, which would descend into them and see no difference.
    """
    tree = ast.parse(source.read_text())
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported - STDLIB_ALLOWED)


@pytest.mark.parametrize("source", STDLIB_ONLY, ids=_dotted)
def test_a_lazy_third_party_import_is_one_we_sanctioned(source):
    """Deferring an import makes it invisible to the check above, so name them.

    Without this, "import it inside the function" becomes a way to add any
    dependency silently.
    """
    tree = ast.parse(source.read_text())
    top = set(tree.body)
    nested = set()
    for node in ast.walk(tree):
        if node in top:
            continue
        if isinstance(node, ast.Import):
            nested |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            nested.add(node.module.split(".")[0])
    unsanctioned = sorted(nested - STDLIB_ALLOWED - set(LAZY_ALLOWED))
    assert not unsanctioned, (
        f"{source.name} lazily imports {unsanctioned}, which is not in "
        f"LAZY_ALLOWED. Add it there with the subcommand that needs it, or "
        f"remove the dependency."
    )


def test_building_the_lute_parser_does_not_import_jinja2():
    """`hutch-lute watch` must work without the psana env.

    The parser is built for every invocation, so an import pulled in while
    constructing it defeats the lazy-import discipline for every subcommand at
    once -- including the ones that need no dependency.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import hutch_copilot.tools.lute as l; l.build_parser();"
         " import sys; assert 'jinja2' not in sys.modules, 'jinja2';"
         " assert 'yaml' not in sys.modules, 'yaml';"
         " assert 'requests' not in sys.modules, 'requests'"],
        capture_output=True, cwd=str(PKG.parent), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode()


def test_a_tool_runs_from_a_bare_checkout():
    """No install step, no PYTHONPATH: the bin/ shim has to be enough."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "hutch-state"), "--help"],
        capture_output=True, cwd="/", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"audit" in proc.stdout
