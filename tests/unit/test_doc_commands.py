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

import pathlib
import re

import pytest

import hutch_tools

SKILL = pathlib.Path(hutch_tools.__file__).resolve().parent.parent

#: binary suffix -> module that owns it.
#: MUST list every bin/hutch-*; test_every_binary_is_covered enforces that.
#: `log` and `install` were missing for their whole existence, so 13 documented
#: invocations were skipped by the `suffix not in BINARIES` guard below -- a
#: drift check with a silent hole is the bug class this repo exists to remove.
BINARIES = {
    "state": "state",
    "daq": "daq",
    "happi": "happi",
    "epics": "epics",
    "lightpath": "lightpath",
    "python": "session",
    "data": "data",
    "skills": "skills",
    "lute": "lute",
    "log": "log",
}

INVOCATION = re.compile(r"bin/hutch-([a-z]+)((?:[ \t]+[^\n`|#]*)?)")


def _parsers():
    import importlib

    out = {}
    for suffix, module in BINARIES.items():
        mod = importlib.import_module(f"hutch_tools.{module}")
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
#: against. `env` is sourced into a shell rather than run and `session-start` is a
#: hook the harness calls with no arguments; test_env.py covers both.
NOT_CLIS = {"env", "session-start"}


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
