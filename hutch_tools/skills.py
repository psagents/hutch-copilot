"""hutch-skills: which Claude Code skills are installed, and where.

Install truth, not machine truth -- which is why it is its own tool rather than
living beside the resolvers that read happi, CA or the DAQ.
"""

from __future__ import annotations

import argparse
import pathlib
from typing import Any

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow


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
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if (entry / "SKILL.md").is_file() and entry.name not in found:
                found[entry.name] = str(root)

    source = "resolved:skill roots " + ", ".join(str(r) for r in roots)
    if name is not None:
        return _record(
            {"name": name, "installed": name in found, "root": found.get(name)},
            source + f" name={name}",
            roots_searched=[str(r) for r in roots],
        )
    return _record(found, source, count=len(found),
                   roots_searched=[str(r) for r in roots])


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
