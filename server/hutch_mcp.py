"""hutch-mcp: the hutch-only hutch-copilot tools as an MCP server, run on the hutch machine.

stdio by default (a client over ssh); streamable HTTP on the Unix socket ``MCP_UDS`` when
``MCP_TRANSPORT=streamable-http``, for the SAGE tunnel, as epics-mcp does. Every tool runs an
existing ``bin/hutch-*`` with a fixed argv and returns its JSON; none of them writes.
"""

from __future__ import annotations

import getpass
import logging
import os
import pathlib
import subprocess
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
#: lightpath settles for ~40 s; anything far past that is hung.
T_TOOL = 300
LOG = logging.getLogger("hutch-mcp")

mcp = FastMCP(
    "hutch-mcp",
    instructions="Read-only hutch tools (happi, Channel Access, archiver, lightpath, DAQ state) "
                 "that only the hutch machine can reach. Each returns the hutch-copilot tool's JSON.",
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


def _run(binary: str, *args: str | None) -> str:
    argv = [a for a in args if a is not None]
    for a in argv:
        if a.startswith("-") and a not in _FLAGS:
            raise ToolError(f"{a!r} looks like a flag, not a value")
    LOG.info("user=%s tool=%s argv=%s", _user(), binary, argv)
    try:
        p = subprocess.run([sys.executable, str(BIN / binary), *argv],
                           capture_output=True, text=True, timeout=T_TOOL)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{binary} timed out after {T_TOOL} s") from None
    text = p.stdout.strip() or p.stderr.strip()
    if p.returncode:
        raise ToolError(f"rc {p.returncode}\n{text}")
    return text


#: The only dash-led argv entries a tool may send; any argument value starting with "-" is refused.
_FLAGS = {"--hutch", "--suffix", "--from", "--to", "--experiment", "--taking-data"}


def _opt(flag: str, value: str | None) -> tuple[str | None, str | None]:
    return (flag, value) if value else (None, None)


@mcp.tool()
def happi_list(hutch: str) -> str:
    """Every device happi records for this hutch, with its PV prefix."""
    return _run("hutch-happi", "list", "--hutch", hutch)


@mcp.tool()
def happi_device(hutch: str, name: str) -> str:
    """One happi device entry by name."""
    return _run("hutch-happi", "device", "--hutch", hutch, name)


@mcp.tool()
def happi_pv(hutch: str, name: str, suffix: str | None = None) -> str:
    """The PV prefix of a happi device, optionally with a suffix such as :RBV appended."""
    return _run("hutch-happi", "pv", "--hutch", hutch, name, *_opt("--suffix", suffix))


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
def lightpath_path(hutch: str) -> str:
    """The beam path to the hutch and every device blocking it. Takes ~40 s."""
    return _run("hutch-lightpath", "path", "--hutch", hutch)


@mcp.tool()
def daq_state(hutch: str) -> str:
    """The DAQ's own state (transition, recording, config alias, experiment), off its control port."""
    return _run("hutch-daq", "--hutch", hutch, "state")


@mcp.tool()
def normal_diff(hutch: str, experiment: str, taking_data: bool = False) -> str:
    """Judge the current beam path and DAQ against the operator's normal snapshot."""
    return _run("hutch-normal", "diff", "--hutch", hutch, "--experiment", experiment,
                "--taking-data" if taking_data else None)


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
