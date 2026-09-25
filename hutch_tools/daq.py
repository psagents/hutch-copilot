"""Everything that requires talking to the DAQ machine.

One tool owns one class of infrastructure: the hutch-python bridge listener,
DAQ process state, and the per-process log files. All three reach the DAQ
machine by the same ssh path through ``psdev``, which is why they live together
rather than in three tools that each have to get the transport right.

Named ``bridge`` until Sep 2026. The bridge is still the transport; it is no
longer the whole scope, so ``oc_bridge.py`` and ``BridgeError`` keep their
names while the tool does not.

Replaces six verbatim copies of the same SSH+socket+JSON blob that were
embedded in markdown (``SKILL.md`` x3, ``are-we-ready/SKILL.md`` x2,
``bridge-to-cds/SKILL.md`` x1) as Python inside nested-quoted bash inside a
fenced code block. Two of those copies hardcoded
``/sdf/home/f/fpoitevi/.claude/skills/...``, so the bridge setup and both
readiness checks were broken for every user except the original author.

Three transports, matching the three LCLS environments. This table is the
load-bearing part of the retired ``bridge-to-cds`` sub-skill; it lives here,
next to the code it explains, instead of in a 313-line markdown file whose
recipes are now ``hutch-resolve`` subcommands.

  environment          hosts                    filesystems    EPICS CA to IOCs
  -------------------  -----------------------  -------------  ----------------
  S3DF (batch)         s3dflogin, sdf-login     /sdf           no (firewalled)
  CDS (controls-adj.)  psdev, psusr, pslogin    /cds           no (read gateway)
  controls network     mfx-control, cxi-control /cds           yes

  ipython  -> hutch-python session on a DAQ machine (oc_bridge.py on :9999)
  psdev    -> read /cds files; Python 3.9 stdlib only, no conda, no CA
  controls -> hutch control machine; full EPICS CA

Key constraints, each of which has bitten:

  * ``/sdf`` is NOT mounted on psdev and ``/cds`` is NOT mounted on S3DF. The
    only bridge is ssh, so scripts must arrive on stdin and must not name /sdf
    paths. ``psdev_read`` refuses a /sdf path rather than failing obscurely.
  * ``/cds`` and ``/reg/neh`` are the same filesystem; ``/cds`` is canonical and
    ``/reg`` is a deprecated alias. Never probe both.
  * ``caget`` is on PATH on psdev but CA connections to IOCs (172.21.x.x) are
    blocked there, so PV reads must run on ``{hutch}-control``.
  * Beamline names in the happi DB are UPPERCASE; filesystem hutch names are
    lowercase. ``paths`` and ``resolvers`` normalise in opposite directions.
  * psdev is shared: keep remote scripts short and non-blocking.

The remote client is passed to ssh as a single shell-quoted argv element, so
there is no nested-quoting hazard at all -- the reason the markdown version was
fragile.

Everything sent to a live session goes through ``require_confirmation``
first.
"""

from __future__ import annotations

import datetime
import json
import shlex
import sys
import subprocess
from typing import Any

from ._cli import OK, REFUSED, UNREACHABLE, USAGE, fail, out, parser
from .paths import script
from .safety import (
    BEAM_CRITICAL,
    READ_ONLY,
    SEVERITY,
    WRITE,
    SafetyRefusal,
    classify,
    require_confirmation,
)

#: SSH jump host for both /cds access and DAQ machines.
JUMP_HOST = "psdev"

#: Port oc_bridge.py listens on inside the hutch-python session.
BRIDGE_PORT = 9999

#: Timeout classes, in seconds. Named rather than magic numbers at call sites.
T_QUERY = 10
T_DEVICE = 30

# Client that runs *on* the DAQ machine and talks to the local listener.
# TCP forwarding (ssh -L) is disabled on DAQ machines, so the process must run
# there and connect to localhost itself.
_REMOTE_CLIENT = r"""
import socket, json, sys
payload = sys.stdin.buffer.read()
s = socket.socket()
s.settimeout(TIMEOUT)
s.connect(("localhost", PORT))
s.sendall(payload)
s.shutdown(socket.SHUT_WR)
buf = b""
while True:
    chunk = s.recv(65536)
    if not chunk:
        break
    buf += chunk
sys.stdout.write(buf.decode("utf-8", "replace"))
"""


class BridgeError(RuntimeError):
    pass


class BridgeUnavailable(BridgeError):
    """The bridge could not be reached.

    Callers must NOT fall back to a hardcoded default on this error. Report it
    and either degrade to explicitly-labelled stale state or ask the operator.
    Silent fallback to a literal is the bug class this refactor exists to kill.
    """


def _ssh(
    host: str,
    remote_cmd: str,
    *,
    stdin: bytes | None = None,
    timeout: int = T_QUERY,
    jump: str | None = JUMP_HOST,
) -> str:
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}"]
    if jump and host != jump:
        argv += ["-J", jump]
    argv += [host, remote_cmd]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeUnavailable(f"ssh to {host} timed out after {timeout + 30}s") from exc
    except FileNotFoundError as exc:  # pragma: no cover - ssh always present at LCLS
        raise BridgeUnavailable("ssh not found on PATH") from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise BridgeUnavailable(f"ssh to {host} failed (rc={proc.returncode}): {err}")
    return proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# IPython bridge (live hutch-python session)
# --------------------------------------------------------------------------
def daq_host(hutch: str) -> str:
    """DAQ machine for a hutch, e.g. mfx -> mfx-daq.

    Resolve from the hutch-python conf.yml when it matters -- see
    ``resolvers.daq``. This convention is only the fallback spelling.
    """
    return f"{hutch.strip().lower()}-daq"


def exec_code(
    code: str,
    *,
    hutch: str,
    confirm: str | None = None,
    timeout: int = T_QUERY,
    host: str | None = None,
) -> dict:
    """Run Python in the live hutch-python session.

    ``confirm`` is the action class a human has approved (see safety.py).
    Read-only snippets need no confirmation; anything else is refused without
    a matching class.
    """
    verdict = require_confirmation(code, confirm)

    host = host or daq_host(hutch)
    client = (
        _REMOTE_CLIENT.replace("TIMEOUT", str(timeout + 20)).replace("PORT", str(BRIDGE_PORT))
    )
    remote_cmd = "python3 -c " + shlex.quote(client)
    raw = _ssh(
        host,
        remote_cmd,
        stdin=json.dumps({"code": code}).encode(),
        timeout=timeout,
    )
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        raise BridgeError(
            f"bridge returned non-JSON -- is the listener running?\n"
            f"  start it in the hutch-python session with:\n"
            f"    exec(open('/tmp/oc_bridge.py').read())\n"
            f"  got: {raw[:300]!r}"
        ) from None

    return {
        "ok": resp.get("status") == "ok",
        "output": resp.get("output", ""),
        "error": resp.get("error"),
        "safety": verdict,
        "host": host,
    }


def install_listener(hutch: str, host: str | None = None) -> str:
    """Copy oc_bridge.py to /tmp on the DAQ machine. Operator still starts it."""
    host = host or daq_host(hutch)
    src = script("scripts", "oc_bridge.py")
    _ssh(host, "cat > /tmp/oc_bridge.py", stdin=src.read_bytes(), timeout=T_QUERY)
    return (
        f"installed {src.name} on {host}:/tmp/oc_bridge.py\n"
        f"ask the operator to run, in the hutch-python session:\n"
        f"    exec(open('/tmp/oc_bridge.py').read())"
    )


def ping(hutch: str, host: str | None = None) -> dict:
    """Is the live session reachable? Read-only, so never needs confirmation."""
    try:
        res = exec_code("True", hutch=hutch, host=host, timeout=T_QUERY)
    except (BridgeError, BridgeUnavailable) as exc:
        return {"ok": False, "detail": str(exc), "mode": "offline"}
    return {
        "ok": res["ok"] and res["output"].strip() == "True",
        "detail": res["output"].strip() or res.get("error"),
        "mode": "live",
        "host": res["host"],
    }


# --------------------------------------------------------------------------
# DAQ processes and their logs
#
# NOTE: neither `status` nor `logs` has a behavioural unit test, and that is
# deliberate rather than an oversight. Both are pure remote-command wrappers
# whose correctness is entirely in the shape of the output `daqmgr` and the
# opr log directory actually produce, and there is no live DAQ machine to check
# that against yet (PLAN.md M0.2/M0.6 are the gate). A mocked test would only
# assert that our guess matches our guess. Their CLI surface is still covered
# by tests/unit/test_doc_commands.py, and the parsing below is written to
# degrade to raw text rather than raise when the format surprises it.
# --------------------------------------------------------------------------
def process_status(hutch: str, host: str | None = None, timeout: int = T_DEVICE) -> dict:
    """``daqmgr status <hutch>.py`` on the DAQ machine: process name -> state.

    Read-only, so it never needs confirmation. Equivalent to reading the
    daqstat GUI, and it works when the GUI is not up.
    """
    hutch = hutch.strip().lower()
    host = host or daq_host(hutch)
    raw = _ssh(host, f"daqmgr status {shlex.quote(hutch + '.py')}", timeout=timeout)

    processes: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split()
        # daqmgr prints `<name> <state> ...`; anything else is a banner or a
        # blank. Keep the raw text so an unparsed format is still readable.
        if len(parts) >= 2 and not line.startswith(" "):
            processes[parts[0]] = parts[1]
    return {
        "host": host,
        "hutch": hutch,
        "processes": processes,
        "raw": raw,
    }


def _log_dir(hutch: str, when: "datetime.date | None" = None) -> str:
    """Per-process DAQ log directory for a hutch, on the DAQ machine.

    Under ``/cds``, which is NOT mounted on S3DF -- every command touching this
    path has to run remotely.
    """
    d = when or datetime.date.today()
    return f"/cds/home/opr/{hutch.strip().lower()}opr/{d.year:04d}/{d.month:02d}"


def logs(
    hutch: str,
    *,
    process: str | None = None,
    lines: int = 0,
    count: int = 20,
    host: str | None = None,
    when: "datetime.date | None" = None,
    timeout: int = T_DEVICE,
) -> dict:
    """List the most recent DAQ log files, or tail one of them.

    Files are named ``<timestamp>_<host>:<process>.log``. With ``lines`` > 0 the
    newest match is tailed; otherwise the newest ``count`` names are listed.

    TBD (PLAN.md TBD-1): which process names are worth exposing as filter
    values, whether this layout is stable across DAQ versions, and how long
    logs are retained. All three need a live experiment to answer, so this
    ships with the smallest surface that cannot be wrong.
    """
    hutch = hutch.strip().lower()
    host = host or daq_host(hutch)
    d = _log_dir(hutch, when)
    # A process filter matches the `:<process>.log` field, not the whole name,
    # so it cannot accidentally match the host or the timestamp.
    pattern = f"*:{process}.log" if process else "*.log"
    listing = (
        f"ls -1t {shlex.quote(d)}/{pattern} 2>/dev/null | head -n {int(count)}"
    )

    if lines > 0:
        newest = f"$({listing} | head -n 1)"
        raw = _ssh(
            host,
            f'f={newest}; [ -n "$f" ] && echo "==> $f" && tail -n {int(lines)} "$f"',
            timeout=timeout,
        )
        return {"host": host, "directory": d, "process": process,
                "tail_lines": lines, "content": raw}

    raw = _ssh(host, listing, timeout=timeout)
    files = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return {
        "host": host,
        "directory": d,
        "process": process,
        "count": len(files),
        "files": files,
    }


# --------------------------------------------------------------------------
# psdev (read /cds from S3DF)
# --------------------------------------------------------------------------
def psdev_python(script: str, params: dict[str, Any] | None = None, timeout: int = T_QUERY) -> str:
    """Run a stdlib-only Python script on psdev, with params injected as JSON.

    ``/sdf`` is not mounted on psdev, so the script must arrive on stdin and
    must not reference /sdf paths. Params are embedded as a JSON literal rather
    than string-formatted, so values cannot break out of the script.
    """
    prologue = f"import json\nP = json.loads({json.dumps(json.dumps(params or {}))})\n"
    return _ssh("psdev", "python3 -", stdin=(prologue + script).encode(), timeout=timeout, jump=None)


def psdev_read(path: str, timeout: int = T_QUERY) -> str:
    """cat a /cds file."""
    if path.startswith("/sdf"):
        raise BridgeError(f"/sdf is not mounted on psdev: {path}")
    return _ssh("psdev", f"cat {shlex.quote(path)}", timeout=timeout, jump=None)


# --------------------------------------------------------------------------
# controls machine (EPICS CA)
# --------------------------------------------------------------------------
CAGET = "/cds/group/pcds/epics/extensions/bin/linux-x86_64/caget"


def caget(pvs: list[str], *, hutch: str, timeout: int = T_QUERY) -> dict[str, str]:
    """Read PVs from a hutch control machine.

    CA is firewalled from both S3DF and psdev, so this must run on
    ``{hutch}-control``.
    """
    if not pvs:
        return {}
    host = f"{hutch.strip().lower()}-control"
    cmd = f"{shlex.quote(CAGET)} " + " ".join(shlex.quote(p) for p in pvs)
    out = _ssh(host, cmd, timeout=timeout)
    values: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1].strip()
    missing = [p for p in pvs if p not in values]
    if missing:
        raise BridgeUnavailable(f"no value returned for {missing}")
    return values


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-daq: ping | install | exec | status | logs."""


def _confirm_and_timeout(p, post: bool = False) -> None:
    """Add --confirm/--timeout, either before or after the subcommand.

    ``post=True`` stores into shadow dests so the subparser cannot clobber the
    value given before the subcommand; ``_resolve`` picks the winner.
    """
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


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser.

    Separate from main() so the CLI surface can be introspected -- the
    doc-drift test walks every invocation in the markdown and checks its
    subcommand and flags against these parsers.

    ``--confirm`` and ``--timeout`` are accepted on **both** sides of the
    subcommand, and that is deliberate. They were subcommand-only until Sep
    2026, which made the form take-run/SKILL.md documents -- ``--hutch H
    --timeout 300 --confirm write exec '...'`` -- die with ``invalid choice:
    '300'``. The doc-drift test could not see it, because it checks flags as an
    unordered union of the subparser's and the parent's, so a flag in the wrong
    position still matches. Moving them to the top level alone would have broken
    the opposite form, ``exec '...' --confirm write``, which is what PLAN.md's
    standing safety test uses -- and turning that test's expected refusal (rc 5)
    into an argparse usage error (rc 2) is exactly the kind of silent weakening
    the gate exists to catch. So: both, with the trailing one winning.
    """

    ap, sub = parser("hutch-daq", _DOC)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--host", default=None, help="override the DAQ host")
    _confirm_and_timeout(ap)

    sub.add_parser("ping")
    sub.add_parser("install", help="copy oc_bridge.py to the DAQ machine")

    e = sub.add_parser("exec", help="run python in the live hutch-python session")
    e.add_argument("code", help="python source, or - to read stdin")
    # Post-subcommand spelling. A distinct dest is required: argparse resolves
    # the subparser into the same namespace, so a same-dest option would reset
    # the parent's value to its default whenever it is not passed again.
    _confirm_and_timeout(e, post=True)

    sub.add_parser("status", help="daqmgr process states on the DAQ machine")

    lg = sub.add_parser("logs", help="list or tail per-process DAQ log files")
    lg.add_argument("--process", default=None,
                    help="filter the :<process>.log field, e.g. drp, teb, control")
    lg.add_argument("-n", "--lines", type=int, default=0,
                    help="tail this many lines of the newest match; 0 lists instead")
    lg.add_argument("--count", type=int, default=20, help="how many names to list")

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-daq: ping | install | exec | status | logs."""
    args = build_parser().parse_args(argv)
    confirm, timeout = _resolve(args)
    try:
        if args.cmd == "ping":
            res = ping(args.hutch, host=args.host)
            out(res)
            return OK if res["ok"] else UNREACHABLE
        if args.cmd == "install":
            return out(install_listener(args.hutch, host=args.host), plain=True)
        if args.cmd == "status":
            return out(process_status(args.hutch, host=args.host, timeout=timeout))
        if args.cmd == "logs":
            return out(logs(args.hutch, process=args.process, lines=args.lines,
                            count=args.count, host=args.host, timeout=timeout))
        if args.cmd == "exec":
            code = sys.stdin.read() if args.code == "-" else args.code
            # `classify` used to be a subcommand of its own. Folded in here so
            # that asking "what is this?" and sending it are the same call on
            # the same text -- two entry points let the answer drift from the
            # code it was about.
            verdict = classify(code)
            if confirm is None and SEVERITY[verdict["class"]] > 0:
                out(verdict)
                # Nothing was transmitted: exec_code is not reached, and its
                # require_confirmation would run before any ssh regardless.
                return REFUSED
            res = exec_code(code, hutch=args.hutch, confirm=confirm,
                            timeout=timeout, host=args.host)
            out(res)
            # A remote traceback used to exit 0, so rc could not distinguish a
            # failed call from a successful one.
            return OK if res["ok"] else UNREACHABLE
    except SafetyRefusal as exc:
        return fail(exc, REFUSED)
    except BridgeError as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
