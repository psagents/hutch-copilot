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
  * Appending to a log that was never initialised is refused, rather than
    silently producing a headerless file.

Two subcommands, ``init`` and ``append``, and that is the whole surface. It also
had ``path``, ``exists``, ``section`` and ``tail`` until Sep 2026. ``path`` and
``exists`` were never invoked from any skill file; ``tail`` re-read a file the
agent can read directly; and ``section`` let a caller impose structure on an
append-only log, which is the one thing an append-only log is for not having.
Phase changes are now an ordinary entry.

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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
_DOC = """hutch-log: init | append."""


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (separate from main() so it can be introspected)."""
    ap, sub = parser("hutch-log", _DOC)

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

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-log: init | append."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            fields = dict(f.split("=", 1) for f in args.field if "=" in f)
            return out(init(args.hutch, args.experiment, shift_date=args.shift_date,
                            shift_start=args.shift_start, header_fields=fields))
        if args.cmd == "append":
            return out(append(args.hutch, args.experiment, args.text,
                              at=args.at, reconstructed=args.reconstructed))
    except (LogError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
