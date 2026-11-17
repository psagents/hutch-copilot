"""hutch-py: python in a fresh load of the hutch's own hutch-python, and its conf.yml.

``run`` launches the hutch's ``{hutch}python`` wrapper on a staged script: ``load()`` runs before
hutch_python's interactive/script branch, so the script gets the identical namespace a session
would, with no session and no listener. Nothing here judges the code: the operator approves each
run in Claude Code's permission prompt (an `ask` rule bin/hutch-setup installs), and through the
gateway the server's policy says who may run at all. The DAQ's state, logs and config are
``hutch-daq``.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

from .common import (
    OK,
    UNREACHABLE,
    USAGE,
    external_path,
    fail,
    out,
    parser,
    utcnow,
)

# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
#: Timeout classes, in seconds. Named rather than magic numbers at call sites.
T_QUERY = 10
T_DEVICE = 30
#: A fresh load, not a live session: the import alone measured 24.5s cold on
#: mfx-daq off NFS, before any device instantiation. See PLAN.md Q1.
T_LOAD = 180


class BridgeError(RuntimeError):
    pass


class BridgeUnavailable(BridgeError):
    """The bridge could not be reached. Never fall back to a hardcoded default.

    Carries ``returncode``, ``stdout`` and ``stderr`` when the failure was a
    remote command exiting non-zero, so a caller reporting per-step results does
    not have to re-run it to find out what it printed. Both are ``""`` and the
    code ``None`` when the failure was the connection itself.
    """

    def __init__(self, message: str, *, returncode: int | None = None,
                 stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ssh(
    host: str,
    remote_cmd: str,
    *,
    stdin: bytes | None = None,
    timeout: int = T_QUERY,
    run_timeout: int | None = None,
) -> str:
    """Run one command over ssh and return its stdout.

    ``timeout`` bounds the connection; ``run_timeout`` bounds the whole call and
    defaults to ``timeout + 30``. They are separate because a long-running remote
    command (``makepeds`` takes minutes) still has to fail fast on an unreachable
    host -- a single value would make a dead host hang for the command's budget.

    No jump host: the tools run at the hutch, where the hosts they still reach
    are directly reachable. ``psdev`` was the jump host and is neither needed
    nor reachable from here.
    """
    run_timeout = timeout + 30 if run_timeout is None else run_timeout
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, remote_cmd]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=run_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeUnavailable(f"ssh to {host} timed out after {run_timeout}s") from exc
    except FileNotFoundError as exc:  # pragma: no cover - ssh always present at LCLS
        raise BridgeUnavailable("ssh not found on PATH") from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise BridgeUnavailable(
            f"ssh to {host} failed (rc={proc.returncode}): {err}",
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", "replace"),
            stderr=err,
        )
    return proc.stdout.decode("utf-8", "replace")

#: Prepended to every staged script. The happi load sits inside safe_load(), so a
#: db it cannot read leaves an empty namespace and rc=0 -- silently reporting
#: nothing rather than failing. Keyed off size alone: daq is absent at MEC/CXI/XCS
#: (lcls1) and `las` (nodaq).
#:
#: hutch-python builds elog only from ~/.web.cfg, which personal accounts lack;
#: HutchELog with no user/pw authenticates with the caller's Kerberos ticket instead.
#: It must also land in {hutch}.db: autorun & co. `from mfx.db import elog` at call time.
_GUARD = (
    "assert len(globals()) > 50, (\n"
    "    'hutch-python loaded a near-empty namespace -- check that conf.yml db "
    "is readable by this user (the /reg/g spelling is not)')\n"
    "if 'elog' not in globals():\n"
    "    try:\n"
    "        from elog import HutchELog as _HE; elog = _HE({hutch!r})\n"
    "        __import__('sys').modules[{db!r}].elog = elog\n"
    "    except Exception as _e:\n"
    "        __import__('sys').stderr.write(f'warning: no elog object ({{_e}}) "
    "-- elog posting unavailable; kinit?\\n')\n"
)

#: Printed last so the caller can see the namespace it actually got.
_SIZE_MARKER = "HUTCH_NS_SIZE:"


def run_script(hutch: str, code: str, *, timeout: int = T_LOAD) -> dict:
    """Run python in a fresh hutch-python load, with no live session; ``timeout`` in seconds."""
    h = hutch.strip().lower()
    # The hutch's own wrapper, not hutch-python directly: it sources {hutch}env,
    # which sets the conda env and the PYTHONPATH that `load: mfx.beamline` and
    # `from mfx.db import elog` need. Reimplementing that here would drift.
    launcher = f"{external_path('PYTHON_APPS')}/{h}/{h}python"
    guard = _GUARD.format(hutch=h.upper(), db=f"{h}.db")
    staged = f"{guard}{code}\nprint({_SIZE_MARKER!r}, len(globals()))\n"

    # /dev/stdin, not a temp file: cli.py reads the script to EOF before exec, so
    # an input() prompt in the snippet raises EOFError instead of hanging here.
    # Without it takepeds silently takes its LCLS-I branch and records nothing.
    scripts = f"{external_path('ENG_TOOLS')}/{h}/scripts"
    env = {**os.environ, "PATH": f"{scripts}:{os.environ.get('PATH', '')}"}
    try:
        proc = subprocess.run(
            [launcher, "/dev/stdin"],
            input=staged.encode(),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except OSError as exc:
        raise SessionError(f"cannot run {launcher}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SessionError(f"{launcher} did not finish within {timeout}s") from exc

    text = proc.stdout.decode("utf-8", "replace")
    size = None
    if _SIZE_MARKER in text:
        tail = text.split(_SIZE_MARKER, 1)[1].split()
        size = int(tail[0]) if tail and tail[0].isdigit() else None
        text = text.split(_SIZE_MARKER, 1)[0]
    return {
        "ok": proc.returncode == 0,
        "output": text,
        "error": proc.stderr.decode("utf-8", "replace").strip() or None,
        "returncode": proc.returncode,
        "namespace_size": size,
    }


# --------------------------------------------------------------------------
# hutch-python config: DAQ host, load list
# --------------------------------------------------------------------------
class SessionError(RuntimeError):
    """A session-side source was reachable but could not answer."""


def hutch_conf(hutch: str) -> dict:
    """The hutch's conf.yml, source of the DAQ host and platform."""
    path = f"{external_path('PYTHON_APPS')}/{hutch.strip().lower()}/conf.yml"
    try:
        text = pathlib.Path(path).read_text(errors="replace")
    except OSError as exc:
        raise SessionError(f"cannot read {path}: {exc}") from exc
    parsed: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2).strip():
            parsed[m.group(1)] = m.group(2).strip()
    return {"value": parsed, "source": f"resolved:{path}",
            "retrieved_at": utcnow(), "raw": text}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-py: run | conf."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-py", _DOC)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--timeout", type=int, default=T_LOAD, help="seconds, load included")
    run_parser = sub.add_parser("run", help="run python in a fresh load, no session")
    run_parser.add_argument("code", help="python source, or - to read stdin")
    sub.add_parser("conf", help="the hutch's conf.yml")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-py: run | conf."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "conf":
            return out(hutch_conf(args.hutch))
        if args.cmd == "run":
            code = sys.stdin.read() if args.code == "-" else args.code
            res = run_script(args.hutch, code, timeout=args.timeout)
            out(res)
            # A snippet that raised is not an unreachable source: collapsing the
            # two would leave a caller unable to tell "no launcher" from
            # "the run failed".
            return OK if res["ok"] else 1
        return USAGE
    except (SessionError, OSError) as exc:
        return fail(exc, UNREACHABLE)
