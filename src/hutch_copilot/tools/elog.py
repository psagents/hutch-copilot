"""hutch-elog: post an entry to the experiment's eLog. Reading the eLog is the elog-search skill's."""

from __future__ import annotations

import argparse
import json
import subprocess

from .common import OK, REFUSED, UNREACHABLE, USAGE, fail, out, parser, utcnow
from .lute import elog_ticket

#: The endpoint elog.pswww.PHPWebService.post uses.
POST_URL = "https://pswww.slac.stanford.edu/ws-kerb/lgbk/lgbk/{experiment}/ws/new_elog_entry"


class ElogError(RuntimeError):
    pass


class ElogRefused(ElogError):
    """The eLog answered and said no."""


def post(experiment: str, text: str, *, tags: list[str] | None = None,
         run: int | None = None, attach: list[str] | None = None) -> dict:
    """The new entry's id, after posting ``text`` (and files) as the ticket's owner."""
    ok, ticket = elog_ticket()
    if not ok:
        raise ElogError(f"no valid Kerberos ticket at {ticket}: run kinit -c {ticket} "
                        f"<user>@SLAC.STANFORD.EDU in a terminal")
    # curl, not requests+krtc: SPNEGO is built into curl, and krtc is absent from the tool python.
    # --form-string, not -F: a text starting with @ or < would otherwise be read as a file.
    argv = ["curl", "-sS", "--max-time", "60", "--negotiate", "-u", ":",
            "--form-string", f"log_text={text}"]
    if tags:
        argv += ["--form-string", f"log_tags={' '.join(tags)}"]
    if run is not None:
        argv += ["--form-string", f"run_num={run}"]
    for path in attach or []:
        argv += ["-F", f"files=@{path}"]
    argv.append(POST_URL.format(experiment=experiment))
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=90,
                              env={"KRB5CCNAME": ticket, "PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ElogError(f"curl to the eLog failed: {exc}") from exc
    body = proc.stdout.decode("utf-8", "replace")
    try:
        reply = json.loads(body)
    except json.JSONDecodeError:
        raise ElogError(f"eLog did not answer with JSON (curl rc={proc.returncode}): "
                        f"{(proc.stderr.decode('utf-8', 'replace') or body)[:300]}") from None
    if not reply.get("success"):
        raise ElogRefused(f"eLog refused the post: {reply.get('error_msg')}")
    return {"value": reply["value"]["_id"], "source": f"resolved:{argv[-1]}",
            "retrieved_at": utcnow()}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-elog: post."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-elog", _DOC)
    post_parser = sub.add_parser("post", help="post one entry; a write, approved at the prompt")
    post_parser.add_argument("--experiment", required=True)
    post_parser.add_argument("--text", required=True)
    post_parser.add_argument("--tag", action="append", default=[])
    post_parser.add_argument("--run", type=int, default=None)
    post_parser.add_argument("--attach", action="append", default=[], help="a file; repeatable")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-elog: post."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "post":
            out(post(args.experiment, args.text, tags=args.tag, run=args.run, attach=args.attach))
            return OK
        return USAGE
    except ElogRefused as exc:
        return fail(exc, REFUSED)
    except ElogError as exc:
        return fail(exc, UNREACHABLE)
