"""hutch-skills: which Claude Code skills are installed, and where.

Install truth, not machine truth -- which is why it is its own tool rather than
living beside the resolvers that read happi, CA or the DAQ.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from typing import Any

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, skill_root, utcnow

#: Skills a doc here MAY cite as `@name`. Being listed is permission, not a requirement: the
#: tier is derived, never stored. One a SKILL.md cites is *required* (a judgment routes through
#: it, and this skill holds no science knowledge to fall back on); the rest are available.
ADVISORS = {
    "ask-lute", "ask-cctbx-xfel", "ask-happi",
    "ask-smalldata", "ask-lcls2", "ask-ami", "ask-epics",
    "ask-slurm-s3df", "ask-s3df", "lcls-catalog",
    "elog-copilot", "elog-search", "confluence-search",
    "experimental-hutch-python", "xpm-seq",
    "psana-daq", "psana-daq-control", "psana-daq-monitor",
    "psana-daq-logs", "psana-daq-history", "psana-configdb",
}


def required() -> dict[str, list[str]]:
    """Each advisor a SKILL.md here cites as `@name`, with the skills that cite it."""
    root = skill_root()
    cited: dict[str, list[str]] = {}
    for doc in [root / "SKILL.md", *sorted((root / "skills").glob("*/SKILL.md"))]:
        where = "hutch-copilot" if doc.parent == root else doc.parent.name
        for name in sorted(set(re.findall(r"@([a-z][a-z0-9-]*)", doc.read_text())) & ADVISORS):
            cited.setdefault(name, []).append(where)
    return cited


def _record(value: Any, source: str, **extra: Any) -> dict:
    rec = {"value": value, "source": source, "retrieved_at": utcnow()}
    rec.update(extra)
    return rec


def skill_roots() -> list[pathlib.Path]:
    """Roots holding Claude Code skills, in precedence order. Colon-separated."""
    return [pathlib.Path(r).expanduser()
            for r in external_path("SKILL_ROOTS").split(":") if r]


def skills(name: str | None = None) -> dict:
    """Installed skills across every known root.

    A skill is a directory containing SKILL.md. The roots searched are returned
    even when nothing is found, so "not installed" and "looked in the wrong
    place" stay distinguishable.
    """
    roots = skill_roots()
    found: dict[str, str] = {}
    unreadable: list[str] = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            entries = sorted(root.iterdir())
        except PermissionError:
            # A root other accounts own (the shared opencode tree) must not hide the readable ones.
            unreadable.append(str(root))
            continue
        for entry in entries:
            if (entry / "SKILL.md").is_file() and entry.name not in found:
                found[entry.name] = str(root)

    source = "resolved:skill roots " + ", ".join(str(r) for r in roots)
    searched = {"roots_searched": [str(r) for r in roots], "unreadable_roots": unreadable}
    if name is not None:
        return _record(
            {"name": name, "installed": name in found, "root": found.get(name)},
            source + f" name={name}", **searched,
        )
    return _record(found, source, count=len(found), **searched)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-skills: list."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-skills", _DOC)
    list_parser = sub.add_parser("list", help="which skills are installed, and where")
    list_parser.add_argument("name", nargs="?", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-skills: list."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "list":
            return out(skills(args.name))
        return USAGE
    except OSError as exc:
        return fail(exc, UNREACHABLE)
