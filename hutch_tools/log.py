"""The beamtime log: append-only, machine-timestamped, structure-enforcing.

The last file in the repo that was still mutated by hand. `coordinate-experiment`
wrote it with `cat >>` and carried ~60 lines of prose conventions -- timestamp
format, heading levels, "append, don't restructure", "never insert a `---` inside
an existing section", the `*(reconstructed)*` marker. Conventions in prose are
requests; here they are behaviour:

  * ``append`` can only add at the end of the file. The append-don't-restructure
    rule is not a reminder, it is the only operation available.
  * The timestamp comes from the machine. The old convention was "get the time
    via `date +%H:%M`", which a model could omit, or copy stale from earlier in
    the conversation.
  * ``section`` owns the `---` separator, so it cannot land inside a section.
  * Appending to a log that was never initialised is refused, rather than
    silently producing a headerless file.

Division of labour: ``state`` stores facts, ``log`` records events, and
``coordinate-experiment`` decides what counts as either.
"""

from __future__ import annotations

import argparse

from ._cli import UNREACHABLE, USAGE, experiment_args, fail, out, parser
from ._fs import append_atomic, local_hhmm, lock
from .paths import log_file

#: Marker for entries reconstructed after the fact by /sync-log, so a reader can
#: tell them from real-time notes.
RECONSTRUCTED = "*(reconstructed)*"


class LogError(RuntimeError):
    pass


def path(hutch: str, experiment: str):
    return log_file(hutch, experiment)


def exists(hutch: str, experiment: str) -> bool:
    return path(hutch, experiment).exists()


def init(hutch: str, experiment: str, *, shift_date: str, shift_start: str | None = None,
         header_fields: dict[str, str] | None = None) -> dict:
    """Create the log with its document header. Idempotent — never overwrites."""
    p = path(hutch, experiment)
    if p.exists():
        return {"created": False, "path": str(p), "note": "already exists; left untouched"}

    start = shift_start or local_hhmm()
    lines = [
        f"# {hutch.upper()} Beamtime Log",
        f"**Date:** {shift_date}",
        f"**Experiment:** {experiment}",
        f"**Shift start:** {start}",
        "",
        "---",
        "",
        "## Shift Start",
        "",
        f"- **{start}** Experiment context initialized.",
    ]
    for key, value in (header_fields or {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    with lock(p):
        if not p.exists():                       # re-check under the lock
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines))
    return {"created": True, "path": str(p), "shift_start": start}


def _require(hutch: str, experiment: str):
    p = path(hutch, experiment)
    if not p.exists():
        raise LogError(
            f"no log at {p}. Run `hutch-log init` first — appending would otherwise "
            f"produce a log with no header, date or experiment."
        )
    return p


def append(hutch: str, experiment: str, text: str, *, at: str | None = None,
           reconstructed: bool = False) -> dict:
    """Append one timestamped bullet at the end of the log.

    End-of-file only: that is what makes "append, don't restructure" structural
    rather than advisory.
    """
    if not text.strip():
        raise LogError("refusing to append an empty entry")
    p = _require(hutch, experiment)
    stamp = at or local_hhmm()
    marker = f" {RECONSTRUCTED}" if reconstructed else ""
    entry = f"- **{stamp}**{marker} {text.strip()}\n"

    existing = p.read_text()
    prefix = "" if existing.endswith("\n") else "\n"
    append_atomic(p, prefix + entry)
    return {"appended": entry.rstrip("\n"), "at": stamp,
            "reconstructed": reconstructed, "path": str(p)}


def section(hutch: str, experiment: str, title: str, *, level: int = 2) -> dict:
    """Start a new section. Owns the `---` separator so it cannot land mid-section.

    Level 2 = operational phase, 3 = topic, 4 = specific activity. Only level 2
    gets a horizontal rule before it, matching the document convention.
    """
    if level not in (2, 3, 4):
        raise LogError(f"heading level must be 2, 3 or 4 (got {level})")
    if not title.strip():
        raise LogError("refusing to add an untitled section")
    p = _require(hutch, experiment)

    existing = p.read_text()
    chunks = []
    if not existing.endswith("\n"):
        chunks.append("\n")
    if level == 2:
        # A rule separates phases -- but never two rules in a row.
        if not existing.rstrip().endswith("---"):
            chunks.append("\n---\n")
    chunks.append(f"\n{'#' * level} {title.strip()}\n\n")
    append_atomic(p, "".join(chunks))
    return {"section": title.strip(), "level": level, "path": str(p)}


def tail(hutch: str, experiment: str, n: int = 20) -> dict:
    p = _require(hutch, experiment)
    lines = p.read_text().splitlines()
    return {"path": str(p), "total_lines": len(lines), "tail": lines[-n:]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
_DOC = """hutch-log: path | init | append | section | tail."""


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (separate from main() so it can be introspected)."""
    ap, sub = parser("hutch-log", _DOC)

    for name in ("path", "exists"):
        experiment_args(sub.add_parser(name))

    i = sub.add_parser("init", help="create the log with its header; idempotent")
    experiment_args(i)
    i.add_argument("--shift-date", required=True, help="ISO date of the shift")
    i.add_argument("--shift-start", default=None, help="HH:MM; defaults to now")
    i.add_argument("--field", nargs="*", default=[], metavar="K=V",
                   help="header lines, e.g. Sample='Lyso, 10 mM'")

    a = sub.add_parser("append", help="append one timestamped bullet at end of file")
    experiment_args(a)
    a.add_argument("text")
    a.add_argument("--at", default=None, help="HH:MM; defaults to now (machine clock)")
    a.add_argument("--reconstructed", action="store_true",
                   help="mark as reconstructed after the fact (/sync-log)")

    s = sub.add_parser("section", help="start a new section, separator handled")
    experiment_args(s)
    s.add_argument("title")
    s.add_argument("--level", type=int, default=2, choices=[2, 3, 4])

    t = sub.add_parser("tail")
    experiment_args(t)
    t.add_argument("-n", type=int, default=20)

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-log: path | init | append | section | tail."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "path":
            p = path(args.hutch, args.experiment)
            return out({"log": str(p), "exists": p.exists()})
        if args.cmd == "exists":
            return out({"exists": exists(args.hutch, args.experiment)})
        if args.cmd == "init":
            fields = dict(f.split("=", 1) for f in args.field if "=" in f)
            return out(init(args.hutch, args.experiment, shift_date=args.shift_date,
                            shift_start=args.shift_start, header_fields=fields))
        if args.cmd == "append":
            return out(append(args.hutch, args.experiment, args.text,
                              at=args.at, reconstructed=args.reconstructed))
        if args.cmd == "section":
            return out(section(args.hutch, args.experiment, args.title, level=args.level))
        if args.cmd == "tail":
            return out(tail(args.hutch, args.experiment, args.n))
    except (LogError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
