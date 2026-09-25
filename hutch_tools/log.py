"""hutch-log: the beamtime log -- append-only, timestamped by the machine.

``append`` adds at end-of-file only, and refuses if ``init`` never ran.
``state`` stores facts; ``log`` records events.
"""

from __future__ import annotations

import argparse

from .common import (
    UNREACHABLE,
    USAGE,
    experiment_args,
    fail,
    local_hhmm,
    log_file,
    out,
    parser,
)

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
    """Append one timestamped bullet at the end of the log. End-of-file only."""
    if not text.strip():
        raise LogError("refusing to append an empty entry")
    p = _require(hutch, experiment)
    stamp = at or local_hhmm()
    marker = f" {RECONSTRUCTED}" if reconstructed else ""
    entry = f"- **{stamp}**{marker} {text.strip()}\n"

    prefix = "" if p.read_text().endswith("\n") else "\n"
    with p.open("a") as fh:
        fh.write(prefix + entry)
    return {"appended": entry.rstrip("\n"), "at": stamp,
            "reconstructed": reconstructed, "path": str(p)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
_DOC = """hutch-log: init | append."""


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-log", _DOC)

    init_parser = sub.add_parser("init", help="create the log with its header; idempotent")
    experiment_args(init_parser)
    init_parser.add_argument("--shift-date", required=True, help="ISO date of the shift")
    init_parser.add_argument("--shift-start", default=None, help="HH:MM; defaults to now")
    init_parser.add_argument("--field", nargs="*", default=[], metavar="K=V",
                             help="header lines, e.g. Sample='Lyso, 10 mM'")

    append_parser = sub.add_parser("append", help="append one timestamped bullet at end of file")
    experiment_args(append_parser)
    append_parser.add_argument("text")
    append_parser.add_argument("--at", default=None,
                               help="HH:MM; defaults to now (machine clock)")
    append_parser.add_argument("--reconstructed", action="store_true",
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
