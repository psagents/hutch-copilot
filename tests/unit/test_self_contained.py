"""Nothing load-bearing may live outside this repo.

Three sibling skills that this skill *depended* on were removed -- including the
one that owned the CCTBX jinja template, which left `hutch-config` with nothing to
render. These tests keep that from coming back.

They deliberately check only whether an advisor reference is **declared**, never
whether it is installed, so they behave the same on any machine. Which skills
exist is a question for `hutch-resolve skills`, not for a test -- and not for
prose either: an earlier revision asserted eleven advisors were absent after
checking a single skill root, and deleted working references on that basis.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from hutch_tools import config, paths

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
        if ".git" not in p.parts and p.name != "MIGRATION.md"
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


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
def test_default_template_ships_with_the_repo():
    """hutch-config must be usable with no arguments beyond its context.

    When the template lived in `setup-cctbx-pipeline` and that skill was removed,
    `hutch-config` had nothing to render at all.
    """
    assert config.DEFAULT_TEMPLATE in config.available_templates()
    resolved = config.resolve_template(config.DEFAULT_TEMPLATE)
    assert resolved.is_file()
    assert ROOT in resolved.parents


def test_unknown_template_name_lists_what_is_available():
    with pytest.raises(config.ConfigError, match="Available: cctbx_pipeline"):
        config.resolve_template("no_such_template")


def test_template_dir_is_inside_the_repo():
    assert config.template_dir() == ROOT / "templates"


def test_every_shipped_template_renders_and_parses():
    """A template that cannot render is worse than an absent one."""
    minimal = {
        "generator": "test", "experiment": "e1", "tag": "T", "work_dir": "/w",
        "pdb_file": "/p.pdb", "geometry_file": "/g.expt", "mask_file": "/m.mask",
        "d_min": 2.0, "detector_address": "det", "unit_cell": "1 1 1 90 90 90",
        "space_group": "P 1", "date": "2026/01/01", "merge_version": "v001",
    }
    for name in config.available_templates():
        text = config.render_template(config.resolve_template(name), minimal)
        docs = config.parse_documents(text)
        assert docs, name


def test_shipped_template_passes_its_own_trap_register():
    minimal = {
        "generator": "test", "experiment": "e1", "tag": "T", "work_dir": "/w",
        "pdb_file": "/p.pdb", "geometry_file": "/g.expt", "mask_file": "/m.mask",
        "d_min": 2.0, "detector_address": "det", "unit_cell": "1 1 1 90 90 90",
        "space_group": "P 1", "date": "2026/01/01", "merge_version": "v001",
    }
    report = config.build(template=config.DEFAULT_TEMPLATE, context=minimal,
                          overrides={}, whitelist_dir=None)
    assert report["documents"] == 2
    assert report["tasks"] == ["IndexCCTBXXFEL", "MergeCCTBXXFEL", "ScaleCCTBXXFEL"]


def test_template_records_its_own_provenance():
    """The reconstruction and its unverified blocks must stay documented."""
    text = config.resolve_template(config.DEFAULT_TEMPLATE).read_text()
    assert "PROVENANCE" in text
    assert "UNVERIFIED" in text
