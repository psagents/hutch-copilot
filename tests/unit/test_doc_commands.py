"""Every `bin/hutch-*` invocation in the markdown must exist in the CLI.

This guard exists because writing the docs drifted from the code twice within one
session: `hutch-resolve cell --pdb F` when the CLI takes a positional path, and
`hutch-lute config` after config moved to its own binary. A documented command
that does not parse is the same silent-failure class as a phil key the template
drops -- it looks fine until someone at a beamline runs it.

Values are not checked (they are placeholders like `$H` or `<PV>`); subcommand
and flag *names* are, which is where drift actually happens.
"""

from __future__ import annotations

import re

import pytest

from hutch_copilot.tools.common import skill_root

SKILL = skill_root()

#: binary suffix -> module, under hutch_copilot, that owns it.
#: MUST list every bin/hutch-*; test_every_binary_is_covered enforces that.
#: `log` and `install` were missing for their whole existence, so 13 documented
#: invocations were skipped by the `suffix not in BINARIES` guard below -- a
#: drift check with a silent hole is the bug class this repo exists to remove.
BINARIES = {
    "state": "tools.state",
    "daq": "tools.daq",
    "happi": "tools.happi",
    "epics": "tools.epics",
    "lightpath": "tools.lightpath",
    "py": "tools.session",
    "data": "tools.data",
    "skills": "tools.skills",
    "lute": "tools.lute",
    "log": "tools.log",
    "normal": "normal",
    "ready": "readiness",
    "setup": "install",
}

INVOCATION = re.compile(r"bin/hutch-([a-z]+)((?:[ \t]+[^\n`|#]*)?)")


def _parsers():
    import importlib

    out = {}
    for suffix, module in BINARIES.items():
        mod = importlib.import_module(f"hutch_copilot.{module}")
        out[suffix] = mod.build_parser()
    return out


def _subcommands(parser) -> dict:
    """Map subcommand name -> its parser, or {} for a single-command binary."""
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _flags(parser) -> set[str]:
    return {opt for action in parser._actions for opt in action.option_strings}


def _documented():
    """(source, binary, argv) for every invocation found in the markdown."""
    found = []
    for md in sorted(SKILL.rglob("*.md")):
        if ".git" in md.parts:
            continue
        for line in md.read_text().splitlines():
            for m in INVOCATION.finditer(line):
                suffix, tail = m.group(1), m.group(2) or ""
                if suffix not in BINARIES:      # e.g. the `bin/hutch-*` glob
                    continue
                # strip a trailing line-continuation and inline comment
                tail = tail.split("#")[0].rstrip().rstrip("\\").strip()
                argv = [tok for tok in tail.split() if tok]
                found.append((f"{md.relative_to(SKILL)}", suffix, argv))
    return found


def test_markdown_contains_invocations_to_check():
    docs = _documented()
    assert len(docs) > 20, f"only found {len(docs)} -- is the regex still right?"


#: Not argparse CLIs, so there is no parser to check a documented invocation
#: against. `env` is sourced into a shell rather than run; `session-start` and `stop`
#: are hooks the harness calls with no arguments, the latter reading JSON on stdin.
#: test_env.py covers the first two, test_stop_hook.py the third. `mcp` is the
#: apptainer launcher for the server image, a shell script taking a sif path.
NOT_CLIS = {"env", "session-start", "stop", "mcp"}


def test_every_binary_is_covered():
    """Every bin/hutch-* must be in BINARIES, or its docs go unchecked.

    Without this, adding a binary silently opts its documentation out of the
    drift check: `_documented` skips any suffix it does not know. That is how
    `hutch-log` went unchecked from the day it landed.
    """
    shipped = {p.name.split("hutch-", 1)[1] for p in (SKILL / "bin").glob("hutch-*")}
    missing = sorted(shipped - set(BINARIES) - NOT_CLIS)
    assert not missing, (
        f"bin/hutch-{{{','.join(missing)}}} ship but are not in BINARIES, so every "
        f"documented invocation of them is silently skipped. Add them."
    )


@pytest.mark.parametrize("source,suffix,argv", _documented(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_documented_invocation_matches_the_cli(source, suffix, argv):
    parsers = _parsers()
    parser = parsers[suffix]
    subs = _subcommands(parser)

    positionals = [a for a in argv if not a.startswith("-")]
    sub_parser = parser
    if subs:
        named = next((a for a in positionals if a in subs), None)
        if named is None:
            # No subcommand present: acceptable only for a bare mention such as
            # `bin/hutch-lute` in a prose sentence.
            assert not positionals or all(
                p.startswith(("$", "<", "{")) for p in positionals
            ), f"{source}: `hutch-{suffix} {' '.join(argv)}` names no known subcommand of {sorted(subs)}"
            return
        sub_parser = subs[named]

    known = _flags(sub_parser) | _flags(parser)
    used = {a.split("=")[0] for a in argv if a.startswith("--")}
    unknown = sorted(used - known)
    assert not unknown, (
        f"{source}: `hutch-{suffix} {' '.join(argv)}` uses {unknown}, "
        f"not in {sorted(known)}"
    )


MCP_NAME = re.compile(r"\b(?:happi|epics|lightpath|daq|normal|data|ready|hutch_py)_[a-z_]+")


def test_every_mcp_tool_a_skill_names_exists():
    """A SKILL.md naming a tool the server lacks sends the model looking for it, then improvising."""
    import ast

    tree = ast.parse((SKILL / "src" / "hutch_copilot" / "server.py").read_text())
    served = {f.name for f in tree.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
              and any(ast.unparse(d) == "mcp.tool()" for d in f.decorator_list)}
    named = {(md.relative_to(SKILL).as_posix(), n) for md in SKILL.rglob("SKILL.md")
             if ".git" not in md.parts for n in MCP_NAME.findall(md.read_text())}
    assert served and named
    assert not {(md, n) for md, n in named if n not in served}
