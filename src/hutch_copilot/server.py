"""hutch-mcp: the hutch-only hutch-copilot tools as an MCP server, run on the hutch machine.

stdio by default (a client over ssh); streamable HTTP on the Unix socket ``MCP_UDS`` when
``MCP_TRANSPORT=streamable-http``, for the SAGE tunnel, as epics-mcp does. Every tool runs an
existing ``bin/hutch-*`` with a fixed argv and returns its JSON. Only ``hutch_py_run`` can write,
and only once the operator has typed the action class into an MCP elicitation form.
"""

from __future__ import annotations

import getpass
import logging
import os
import pathlib
import subprocess
import sys

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from .tools import session

BIN = pathlib.Path(__file__).resolve().parents[2] / "bin"
#: lightpath settles for ~40 s; anything far past that is hung.
T_TOOL = 300
LOG = logging.getLogger("hutch-mcp")

mcp = FastMCP(
    "hutch-mcp",
    instructions="Hutch tools (happi, Channel Access, archiver, lightpath, DAQ state, the three "
                 "readiness checks over them, and hutch_py_run) that only the hutch machine can "
                 "reach. Each returns the hutch-copilot tool's JSON. `hutch` defaults to this "
                 "server's own. hutch_py_run asks the operator directly before any write.",
    # Only agentgateway's certificate reaches the socket (ghostunnel), and it sends its own Host.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _user() -> str:
    try:
        request = mcp.get_context().request_context.request
    except (LookupError, ValueError):
        request = None
    if request is None:
        return f"stdio:{getpass.getuser()}"
    return request.headers.get("x-authenticated-user", "?")


def _run(binary: str, *args: str | None, timeout: int = T_TOOL) -> str:
    argv = [a for a in args if a is not None]
    for a in argv:
        if a.startswith("-") and a not in _FLAGS:
            raise ToolError(f"{a!r} looks like a flag, not a value")
    LOG.info("user=%s tool=%s argv=%s", _user(), binary, argv)
    try:
        # stdin is the MCP pipe: a child must never inherit it.
        p = subprocess.run([sys.executable, str(BIN / binary), *argv], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{binary} timed out after {timeout} s") from None
    text = p.stdout.strip() or p.stderr.strip()
    if p.returncode:
        raise ToolError(f"rc {p.returncode}\n{text}")
    return text


#: The only dash-led argv entries a tool may send; any argument value starting with "-" is refused.
_FLAGS = {"--hutch", "--suffix", "--from", "--to", "--experiment", "--taking-data", "--settle-s",
          "--confirm", "--timeout"}


def _opt(flag: str, value: str | None) -> tuple[str | None, str | None]:
    return (flag, value) if value else (None, None)


def _hutch(hutch: str | None) -> str:
    """The hutch asked for, else this server's own: one server serves one hutch."""
    hutch = hutch or os.environ.get("HUTCH")
    if not hutch:
        raise ToolError("no hutch: pass one, or start the server with HUTCH set (bin/hutch-mcp does)")
    return hutch


@mcp.tool()
def happi_list(hutch: str | None = None) -> str:
    """Every device happi records for this hutch, with its PV prefix."""
    return _run("hutch-happi", "list", "--hutch", _hutch(hutch))


@mcp.tool()
def happi_device(name: str, hutch: str | None = None) -> str:
    """One happi device entry by name."""
    return _run("hutch-happi", "device", "--hutch", _hutch(hutch), name)


@mcp.tool()
def happi_pv(name: str, suffix: str | None = None, hutch: str | None = None) -> str:
    """The PV prefix of a happi device, optionally with a suffix such as :RBV appended."""
    return _run("hutch-happi", "pv", "--hutch", _hutch(hutch), name, *_opt("--suffix", suffix))


@mcp.tool()
def epics_caget(pv: list[str]) -> str:
    """Live Channel Access values of one or more PVs, read now."""
    return _run("hutch-epics", "caget", *pv)


@mcp.tool()
def epics_history(pv: str, start: str, end: str | None = None) -> str:
    """Archived samples of one PV between two ISO times (end defaults to now)."""
    return _run("hutch-epics", "history", pv, "--from", start, *_opt("--to", end))


@mcp.tool()
def epics_moved(pv: str, start: str, end: str | None = None) -> str:
    """Did a motor move between two ISO times? Reads .MSTA from the archiver; null means unknown."""
    return _run("hutch-epics", "moved", pv, "--from", start, *_opt("--to", end))


@mcp.tool()
def lightpath_path(hutch: str | None = None) -> str:
    """The beam path to the hutch and every device blocking it. Takes ~40 s."""
    return _run("hutch-lightpath", "path", "--hutch", _hutch(hutch))


@mcp.tool()
def daq_state(hutch: str | None = None) -> str:
    """The DAQ's own state (transition, recording, config alias, experiment), off its control port."""
    return _run("hutch-daq", "--hutch", _hutch(hutch), "state")


@mcp.tool()
def normal_diff(experiment: str, taking_data: bool = False, hutch: str | None = None) -> str:
    """Judge the current beam path and DAQ against the operator's normal snapshot."""
    return _run("hutch-normal", "diff", "--hutch", _hutch(hutch), "--experiment", experiment,
                "--taking-data" if taking_data else None)


@mcp.tool()
def ready_beam(settle_s: float | None = None, hutch: str | None = None) -> str:
    """Readiness: is beam reaching the hutch, and what blocks it? `impediment` is the one to act on.

    `settled: false` means Channel Access had not connected yet: call again with a longer
    settle_s (default 10 s) before reporting anything as blocked.
    """
    return _run("hutch-ready", "beam", "--hutch", _hutch(hutch),
                *_opt("--settle-s", None if settle_s is None else str(settle_s)))


@mcp.tool()
def ready_hutch(hutch: str | None = None) -> str:
    """Readiness: are the hutch's own devices placed? happi inventory plus a live readback each."""
    return _run("hutch-ready", "hutch", "--hutch", _hutch(hutch))


@mcp.tool()
def ready_daq(experiment: str | None = None, hutch: str | None = None) -> str:
    """Readiness: can the DAQ record? Its state plus a `can_record` verdict (null means unknown)."""
    return _run("hutch-ready", "daq", "--hutch", _hutch(hutch), *_opt("--experiment", experiment))


class _Approval(BaseModel):
    confirm: str


@mcp.tool()
async def hutch_py_run(code: str, ctx: Context, timeout_s: int = 300,
                       hutch: str | None = None) -> str:
    """Run python in a fresh hutch-python load on the hutch (hutch-py run).

    Read-only snippets run at once. Anything else is shown to the operator, who must type its
    action class (write, beam-critical) to approve: you cannot approve it, only show it first.
    """
    h = _hutch(hutch)
    verdict = session.classify(code)
    confirm = None
    if verdict["class"] != session.READ_ONLY:
        # Over HTTP the server runs as whoever started it, so a write would run as them.
        if not _user().startswith("stdio:"):
            raise ToolError("writes are refused over HTTP until a per-user policy exists; "
                            "use a stdio session over your own ssh")
        params = ctx.session.client_params
        if not (params and params.capabilities.elicitation):
            raise ToolError("this client cannot ask the operator (no MCP elicitation); nothing ran")
        cls = verdict["class"]
        answer = await ctx.elicit(
            f"hutch-py on {h}, as {_user()}: a {cls} operation.\n"
            f"why: {'; '.join(verdict['reasons'])}\n\n{code}\n\n"
            f"Type {cls!r} to run it, or decline.", schema=_Approval)
        if answer.action != "accept":
            raise ToolError(f"the operator did not approve ({answer.action}); nothing ran")
        confirm = answer.data.confirm.strip().lower()
        if confirm not in session.SEVERITY:
            raise ToolError(f"approval {confirm!r} is not an action class; nothing ran")
    LOG.info("user=%s hutch_py_run class=%s confirm=%s", _user(), verdict["class"], confirm)
    # hutch-py's own gate still decides: a confirmation weaker than the class is refused (rc 5).
    return _run("hutch-py", "--hutch", h, "--timeout", str(timeout_s),
                *_opt("--confirm", confirm), "run", code, timeout=timeout_s + 60)


@mcp.tool()
def data_cat(path: str) -> str:
    """Read a file under /cds, which only the hutch machines mount."""
    return _run("hutch-data", "cat", path)


def main() -> None:
    """Serve on stdio, or on the Unix socket MCP_UDS with MCP_TRANSPORT=streamable-http."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if os.environ.get("MCP_TRANSPORT", "stdio") == "stdio":
        mcp.run("stdio")
        return
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), uds=os.environ["MCP_UDS"], log_level="warning")


if __name__ == "__main__":
    main()
