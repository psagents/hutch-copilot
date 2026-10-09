"""hutch-python: the live session on this machine, and the safety gate in front of it.

The bridge is a loopback socket into a running hutch-python IPython session:
oc_bridge.py exec()s in the session's own globals(), so every loaded device
object is in scope. Nothing else reaches another process's namespace.

Everything sent to a live session passes ``require_confirmation`` first. That
gate, the transport, and the session's own conf.yml live here; the DAQ's state,
logs and config file are ``hutch-daq``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import sys
from typing import Any

from .common import (
    OK,
    REFUSED,
    UNREACHABLE,
    USAGE,
    external_path,
    fail,
    out,
    parser,
    script,
    utcnow,
)

# --------------------------------------------------------------------------
# Safety classification -- consulted before anything is transmitted
# --------------------------------------------------------------------------
READ_ONLY = "read-only"
WRITE = "write"
BEAM_CRITICAL = "beam-critical"

#: Increasing severity. A snippet is classified at its highest match.
SEVERITY = {READ_ONLY: 0, WRITE: 1, BEAM_CRITICAL: 2}

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------
# Positive evidence that a snippet only reads. Everything must match one of
# these for the snippet to be considered read-only.
_READ_ONLY_PATTERNS = [
    r"^\s*$",
    r"^\s*#",
    r"\.position\b",
    r"\.inserted\b",
    r"\.removed\b",
    r"\.get\(\s*\)",
    r"\.read\(\s*\)",
    r"\.describe\(\s*\)",
    r"\bdaq\.state\b",
    r"\bdaq\.status\(\s*\)",
    r"\bdaq\.config_info\(\s*\)",
    r"\bget_run\(\s*\)",
    r"\bcaget\b",
    r"\bcamonitor\s+-n\s*1\b",
    r"\bhappi\b",
    r"\blightpath\b",
    r"\bprint\(",
    r"^\s*(import|from)\s",
    r"^\s*(for|if|else|elif|try|except|finally|with|def|return|pass)\b",
    r"^\s*[A-Za-z_][\w.]*\s*$",  # bare attribute read, e.g. `daq.state`
    r"^\s*\w+\s*=\s*[^=]",  # local assignment (evaluated below on its RHS)
    r"\bTrue\b|\bFalse\b|\bNone\b",
]

# Operations that change the machine.
_WRITE_PATTERNS = {
    r"\.mv\(": "device move (.mv)",
    r"\.mvr\(": "relative device move (.mvr)",
    r"\.move\(": "device move (.move)",
    r"\.umv\(": "device move (.umv)",
    r"\.set\(": "setpoint write (.set)",
    r"\.put\(": "PV write (.put)",
    r"\bcaput\b": "PV write (caput)",
    r"\.insert\(": "device insert",
    r"\.remove\(": "device remove",
    r"\bdaq\.begin\(": "DAQ begin",
    r"\bdaq\.configure\(": "DAQ configure",
    r"\bdaq\.end_run\(": "DAQ end_run",
    r"\bdaq\.connect\(": "DAQ connect",
    r"\bdaq\.disconnect\(": "DAQ disconnect",
    r"\bautorun\(": "DATA run (autorun)",
    r"\bgeomrun\(": "GEOM run (geomrun)",
    r"\btakepeds\(": "DARK run (takepeds)",
    r"\bmakepeds\b": "pedestal deployment (makepeds)",
    r"\bRE\(": "Bluesky plan execution",
    r"\.stage\(": "device stage",
    r"\bopen\([^)]*['\"][wa]": "file write",
}

# Devices whose motion changes delivered beam. Matched on the whole snippet,
# so `mfx_att_1.set(...)` and `att1.mv(...)` both trip it.
#
# `\b` cannot be used: device names are underscore-joined and `_` is a word
# character, so `\bstopper\b` never matches `mfx_stopper_1`.
_TOK_START = r"(?<![A-Za-z0-9])"
_TOK_END = r"(?![A-Za-z0-9])"


def _tok(core: str) -> str:
    return _TOK_START + core + _TOK_END


_BEAM_CRITICAL_DEVICES = {
    _tok(r"stopper\w*"): "stopper",
    _tok(r"pps\w*"): "PPS stopper",
    _tok(r"mps\w*"): "MPS device",
    _tok(r"att(en\w*)?\d*"): "attenuator",
    _tok(r"apert\w*"): "aperture",
    _tok(r"shutter\w*"): "shutter",
    _tok(r"gatt\d*"): "gas attenuator",
    _tok(r"lodcm\w*"): "LODCM",
    _tok(r"mr\d+[a-z]\d+\w*"): "mirror (lightpath component)",
    _tok(r"picker\w*"): "pulse picker",
}


def _lines(code: str) -> list[str]:
    return [ln for ln in code.splitlines() if ln.strip()]


def classify(code: str) -> dict:
    """Classify a snippet. Returns {class, reasons, unmatched_lines}.

    Errs upward: any line that is not positively recognised as read-only makes
    the snippet at least WRITE.
    """
    reasons: list[str] = []
    verdict = READ_ONLY

    for pattern, label in _WRITE_PATTERNS.items():
        if re.search(pattern, code):
            reasons.append(label)
            verdict = WRITE

    if verdict == WRITE:
        for pattern, label in _BEAM_CRITICAL_DEVICES.items():
            if re.search(pattern, code, re.IGNORECASE):
                reasons.append(f"beam-critical target: {label}")
                verdict = BEAM_CRITICAL

    unmatched = [
        ln
        for ln in _lines(code)
        if not any(re.search(p, ln) for p in _READ_ONLY_PATTERNS)
    ]
    if verdict == READ_ONLY and unmatched:
        # Not provably read-only -> treat as a write and say which lines.
        verdict = WRITE
        reasons.append(
            "not provably read-only: "
            + "; ".join(ln.strip()[:60] for ln in unmatched[:3])
        )

    return {"class": verdict, "reasons": reasons, "unmatched_lines": unmatched}


class SafetyRefusal(PermissionError):
    """Raised when a snippet's class outranks the confirmation offered."""


def require_confirmation(code: str, confirmed_as: str | None) -> dict:
    """Gate a snippet. Raise unless ``confirmed_as`` matches its class or higher.

    ``confirmed_as`` is the class a human has actually approved. Passing a
    weaker class than the snippet requires is a refusal, not a downgrade --
    that is the whole point.
    """
    result = classify(code)
    needed = result["class"]
    if SEVERITY[needed] == 0:
        return result

    if confirmed_as is None:
        raise SafetyRefusal(
            f"this is a {needed} operation and no confirmation was supplied.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  show the command to the operator, then re-send with "
            f"--confirm {needed}"
        )
    if confirmed_as not in SEVERITY:
        raise SafetyRefusal(f"unknown confirmation class {confirmed_as!r}")
    if SEVERITY[confirmed_as] < SEVERITY[needed]:
        raise SafetyRefusal(
            f"confirmed as {confirmed_as!r} but this is {needed!r}.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  a {needed} operation needs an explicit {needed} confirmation, "
            f"including a beam-risk warning to the operator."
        )
    return result

# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
#: Port oc_bridge.py listens on inside the hutch-python session.
BRIDGE_PORT = 9999

#: Timeout classes, in seconds. Named rather than magic numbers at call sites.
T_QUERY = 10
T_DEVICE = 30


def _start_hint() -> str:
    """How the operator starts the listener. One spelling, used by every error."""
    return (
        f"in the hutch-python session, run: "
        f"exec(open({str(script('scripts', 'oc_bridge.py'))!r}).read())"
    )


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

# --------------------------------------------------------------------------
# IPython bridge (live hutch-python session)
#
# A loopback socket, not an ssh hop: the session runs on this machine, and the
# socket is the only way into its interpreter -- oc_bridge.py exec()s in the
# session's own globals(), so every loaded device object is in scope. Being on
# the same host does not substitute for it; nothing else reaches another
# process's namespace.
# --------------------------------------------------------------------------
def _bridge_request(payload: bytes, timeout: int) -> str:
    """One request/response against the listener. Send, half-close, read to EOF."""
    try:
        with socket.create_connection(("localhost", BRIDGE_PORT), timeout=timeout) as sock:
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except (ConnectionRefusedError, FileNotFoundError) as exc:
        raise BridgeUnavailable(
            f"nothing is listening on localhost:{BRIDGE_PORT} -- "
            f"no hutch-python session with the bridge started.\n"
            f"  {_start_hint()}"
        ) from exc
    except OSError as exc:
        raise BridgeUnavailable(f"bridge socket failed: {exc}") from exc
    return b"".join(chunks).decode("utf-8", "replace")


def exec_code(
    code: str,
    *,
    confirm: str | None = None,
    timeout: int = T_QUERY,
) -> dict:
    """Run Python in the live hutch-python session on this machine.

    ``confirm`` is the action class a human has approved. Read-only snippets
    need none; anything else is refused without a matching class.
    """
    verdict = require_confirmation(code, confirm)

    raw = _bridge_request(json.dumps({"code": code}).encode(), timeout)
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        raise BridgeError(
            f"bridge returned non-JSON: {raw[:300]!r}"
        ) from None

    return {
        "ok": resp.get("status") == "ok",
        "output": resp.get("output", ""),
        "error": resp.get("error"),
        "safety": verdict,
    }

def install_listener() -> str:
    """Nothing to copy -- the script is local. Report the path to exec()."""
    return f"the operator starts the bridge on this machine:\n    {_start_hint()}"


def ping() -> dict:
    """Is the live session reachable? Read-only, so never needs confirmation."""
    try:
        res = exec_code("True", timeout=T_QUERY)
    except (BridgeError, BridgeUnavailable) as exc:
        return {"ok": False, "detail": str(exc), "mode": "offline"}
    return {
        "ok": res["ok"] and res["output"].strip() == "True",
        "detail": res["output"].strip() or res.get("error"),
        "mode": "live",
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
_DOC = """hutch-python: ping | install | exec | conf."""


def _confirm_and_timeout(p, post: bool = False) -> None:
    """Add --confirm/--timeout. ``post=True`` uses shadow dests so the subparser
    cannot clobber a value given before the subcommand; ``_resolve`` picks."""
    suffix = "_post" if post else ""
    p.add_argument("--confirm", dest="confirm" + suffix,
                   choices=[READ_ONLY, WRITE, BEAM_CRITICAL], default=None,
                   help="action class a human has approved")
    p.add_argument("--timeout", dest="timeout" + suffix, type=int,
                   default=None if post else T_QUERY)


def _resolve(args) -> tuple:
    """(confirm, timeout), taking the post-subcommand spelling when both appear."""
    confirm = getattr(args, "confirm_post", None) or args.confirm
    timeout = getattr(args, "timeout_post", None) or args.timeout
    return confirm, timeout


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected).

    ``--confirm`` and ``--timeout`` are accepted on both sides of the
    subcommand; the trailing one wins.
    """
    ap, sub = parser("hutch-python", _DOC)
    ap.add_argument("--hutch", required=True)
    _confirm_and_timeout(ap)

    sub.add_parser("ping", help="is the live session reachable?")
    sub.add_parser("install", help="where to find oc_bridge.py for the operator to start")

    exec_parser = sub.add_parser("exec", help="run python in the live hutch-python session")
    exec_parser.add_argument("code", help="python source, or - to read stdin")
    # A distinct dest is required: argparse resolves the subparser into the same
    # namespace, so a same-dest option would reset the parent's value.
    _confirm_and_timeout(exec_parser, post=True)

    sub.add_parser("conf", help="the hutch's conf.yml")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-python: ping | install | exec | conf."""
    args = build_parser().parse_args(argv)
    confirm, timeout = _resolve(args)
    try:
        if args.cmd == "ping":
            res = ping()
            out(res)
            return OK if res["ok"] else UNREACHABLE
        if args.cmd == "install":
            return out(install_listener(), plain=True)
        if args.cmd == "conf":
            return out(hutch_conf(args.hutch))
        if args.cmd == "exec":
            code = sys.stdin.read() if args.code == "-" else args.code
            verdict = classify(code)
            if confirm is None and SEVERITY[verdict["class"]] > 0:
                # Nothing is transmitted: exec_code is never reached.
                out(verdict)
                return REFUSED
            res = exec_code(code, confirm=confirm, timeout=timeout)
            out(res)
            return OK if res["ok"] else UNREACHABLE
        return USAGE
    except SafetyRefusal as exc:
        return fail(exc, REFUSED)
    except (BridgeError, SessionError, OSError) as exc:
        return fail(exc, UNREACHABLE)
