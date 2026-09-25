"""Nothing load-bearing may live outside this repo.

These check only whether an advisor reference is **declared**, never whether it
is installed, so they behave the same on any machine. Which skills exist is a
question for `hutch-resolve skills`, not for a test.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from hutch_tools import common as paths

ROOT = paths.skill_root()

#: Skills that may be referenced as `@name` in the docs. They are consultants,
#: never dependencies: the skill must work with none of them installed. Adding a
#: name here is a deliberate act -- see the Optional advisors table in SKILL.md.
DECLARED_ADVISORS = {
    "ask-lute", "ask-cctbx-xfel", "ask-happi",          # ~/.claude/skills
    "ask-smalldata", "ask-lcls2", "ask-ami", "ask-epics",
    "ask-slurm-s3df", "ask-s3df", "lcls-catalog",
    "elog-copilot", "elog-search", "confluence-search",
    "experimental-hutch-python", "xpm-seq",             # opencode root
}

CODE_DIRS = ("hutch_tools", "bin")


def _docs():
    return [
        p for p in sorted(ROOT.rglob("*.md"))
        if ".git" not in p.parts
    ]


def _code():
    """Source files only -- no __pycache__, which embeds build-time paths."""
    files = []
    for d in CODE_DIRS:
        files += [
            p for p in sorted((ROOT / d).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in ("", ".py")
        ]
    return files


def _executable_source(path: pathlib.Path) -> str:
    """Source with docstrings and comments removed.

    Prose that documents a historical bug -- `bridge.py` and `paths.py` both
    explain the `/sdf/home/f/fpoitevi` paths they exist to prevent -- must not
    trip a check aimed at real code. Stripping via the AST is exact; guessing
    from line prefixes is not, because these live inside multi-line docstrings.
    """
    import ast

    text = path.read_text(errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                text = text.replace(doc, "")
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_docs_reference_only_declared_advisors():
    undeclared = {}
    for p in _docs():
        found = set(re.findall(r"@([a-z][a-z0-9-]*)", p.read_text()))
        extra = found - DECLARED_ADVISORS
        if extra:
            undeclared[str(p.relative_to(ROOT))] = sorted(extra)
    assert not undeclared, (
        f"undeclared advisor references: {undeclared}. Either remove the pointer "
        f"and give in-repo guidance instead, or add the skill to DECLARED_ADVISORS "
        f"and to the Optional advisors table in SKILL.md."
    )


def test_no_code_path_points_at_another_skill():
    """`paths.skill_root()` resolves itself; nothing should name a sibling skill.

    Three files once hardcoded `/sdf/home/f/fpoitevi/.claude/skills/hutch-copilot`,
    which broke the skill for every other user.
    """
    offenders = {}
    pattern = re.compile(r"\.claude/skills/(?!hutch-copilot\b)([a-z0-9-]+)")
    for p in _code():
        hits = set(pattern.findall(_executable_source(p)))
        if hits:
            offenders[str(p.relative_to(ROOT))] = sorted(hits)
    assert not offenders, f"code references another skill directory: {offenders}"


def test_no_code_path_hardcodes_a_home_directory():
    offenders = {}
    for p in _code():
        hits = set(re.findall(r"/sdf/home/[a-z]/[a-z0-9_]+", _executable_source(p)))
        if hits:
            offenders[str(p.relative_to(ROOT))] = sorted(hits)
    assert not offenders, f"hardcoded home directory: {offenders}"








def test_skills_resolver_finds_skills_across_multiple_roots(tmp_path, monkeypatch):
    from hutch_tools import resolve as resolvers

    a, b = tmp_path / "local", tmp_path / "shared"
    for root, names in ((a, ["ask-lute", "hutch-copilot"]), (b, ["elog-copilot", "xpm-seq"])):
        for name in names:
            (root / name).mkdir(parents=True)
            (root / name / "SKILL.md").write_text("---\nname: x\n---\n")
    monkeypatch.setenv("HUTCH_SKILL_ROOTS", f"{a}:{b}")

    rec = resolvers.skills()
    assert set(rec["value"]) == {"ask-lute", "hutch-copilot", "elog-copilot", "xpm-seq"}
    assert rec["count"] == 4
    assert rec["value"]["elog-copilot"] == str(b)


def test_a_directory_without_skill_md_is_not_a_skill(tmp_path, monkeypatch):
    from hutch_tools import resolve as resolvers

    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text("x")
    monkeypatch.setenv("HUTCH_SKILL_ROOTS", str(tmp_path))
    assert set(resolvers.skills()["value"]) == {"real"}


def test_earlier_roots_win(tmp_path, monkeypatch):
    """Local overrides shared, matching how the skill loader resolves names."""
    from hutch_tools import resolve as resolvers

    a, b = tmp_path / "local", tmp_path / "shared"
    for root in (a, b):
        (root / "dup").mkdir(parents=True)
        (root / "dup" / "SKILL.md").write_text("x")
    monkeypatch.setenv("HUTCH_SKILL_ROOTS", f"{a}:{b}")
    assert resolvers.skills()["value"]["dup"] == str(a)


def test_querying_one_skill_reports_the_roots_it_searched(tmp_path, monkeypatch):
    """The distinction that was missed: 'not installed' vs 'looked in the wrong place'."""
    from hutch_tools import resolve as resolvers

    monkeypatch.setenv("HUTCH_SKILL_ROOTS", str(tmp_path))
    rec = resolvers.skills("elog-copilot")
    assert rec["value"]["installed"] is False
    assert rec["value"]["root"] is None
    assert rec["roots_searched"] == [str(tmp_path)]


def test_a_missing_root_is_skipped_not_fatal(tmp_path, monkeypatch):
    from hutch_tools import resolve as resolvers

    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text("x")
    monkeypatch.setenv("HUTCH_SKILL_ROOTS", f"{tmp_path}:{tmp_path / 'nope'}")
    assert set(resolvers.skills()["value"]) == {"real"}


def test_every_declared_advisor_is_a_plausible_skill_name():
    """Catches a typo in DECLARED_ADVISORS, which no other test would."""
    for name in DECLARED_ADVISORS:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", name), name
