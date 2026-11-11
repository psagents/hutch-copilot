"""hutch_copilot.server: the MCP server that fronts the hutch-only tools. Needs the MCP SDK."""

import asyncio
import subprocess

import pytest

pytest.importorskip("mcp")
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from hutch_copilot import server as hutch_mcp  # noqa: E402


def _fake_run(monkeypatch, rc=0, stdout='{"value": 1}'):
    seen = []

    def run(argv, **kw):
        seen.append(argv[2:])  # drop the interpreter and the bin path
        return subprocess.CompletedProcess(argv, rc, stdout, "")
    monkeypatch.setattr(hutch_mcp.subprocess, "run", run)
    return seen


def test_exposes_only_the_read_only_hutch_tools():
    names = {t.name for t in asyncio.run(hutch_mcp.mcp.list_tools())}
    assert names == {"happi_list", "happi_device", "happi_pv", "epics_caget", "epics_history",
                     "epics_moved", "lightpath_path", "daq_state", "normal_diff", "data_cat",
                     "ready_beam", "ready_hutch", "ready_daq"}


def test_schemas_come_from_the_signatures():
    tools = {t.name: t for t in asyncio.run(hutch_mcp.mcp.list_tools())}
    assert tools["epics_history"].inputSchema["required"] == ["pv", "start"]
    assert tools["epics_caget"].inputSchema["properties"]["pv"]["type"] == "array"


def test_argv_follows_each_cli(monkeypatch):
    seen = _fake_run(monkeypatch)
    hutch_mcp.daq_state("mfx")
    hutch_mcp.epics_moved("MFX:DG1:PIC:01", "2026-11-05T00:00")
    hutch_mcp.epics_history("A:B", "2026-11-05T00:00", "2026-11-06T00:00")
    hutch_mcp.epics_caget(["A:B", "C:D"])
    hutch_mcp.normal_diff("mfx1", taking_data=True, hutch="mfx")
    hutch_mcp.normal_diff("mfx1", hutch="mfx")
    hutch_mcp.ready_beam(hutch="mfx")
    hutch_mcp.ready_beam(30, hutch="mfx")
    hutch_mcp.ready_hutch("mfx")
    hutch_mcp.ready_daq("mfx1", hutch="mfx")
    hutch_mcp.ready_daq(hutch="mfx")
    assert seen == [
        ["--hutch", "mfx", "state"],
        ["moved", "MFX:DG1:PIC:01", "--from", "2026-11-05T00:00"],
        ["history", "A:B", "--from", "2026-11-05T00:00", "--to", "2026-11-06T00:00"],
        ["caget", "A:B", "C:D"],
        ["diff", "--hutch", "mfx", "--experiment", "mfx1", "--taking-data"],
        ["diff", "--hutch", "mfx", "--experiment", "mfx1"],
        ["beam", "--hutch", "mfx"],
        ["beam", "--hutch", "mfx", "--settle-s", "30"],
        ["hutch", "--hutch", "mfx"],
        ["daq", "--hutch", "mfx", "--experiment", "mfx1"],
        ["daq", "--hutch", "mfx"],
    ]


def test_hutch_defaults_to_the_servers_own(monkeypatch):
    seen = _fake_run(monkeypatch)
    monkeypatch.setenv("HUTCH", "mfx")
    hutch_mcp.happi_device("dg1_pim")
    hutch_mcp.ready_daq()
    assert seen == [["device", "--hutch", "mfx", "dg1_pim"], ["daq", "--hutch", "mfx"]]


def test_no_hutch_anywhere_is_refused(monkeypatch):
    seen = _fake_run(monkeypatch)
    monkeypatch.delenv("HUTCH", raising=False)
    with pytest.raises(ToolError, match="no hutch"):
        hutch_mcp.daq_state()
    assert seen == []


def test_a_failing_tool_raises_with_its_rc(monkeypatch):
    _fake_run(monkeypatch, rc=4, stdout='{"error": "DaqError"}')
    with pytest.raises(ToolError, match="^rc 4"):
        hutch_mcp.daq_state("mfx")


def test_a_value_that_looks_like_a_flag_is_refused(monkeypatch):
    seen = _fake_run(monkeypatch)
    with pytest.raises(ToolError, match="looks like a flag"):
        hutch_mcp.data_cat("--help")
    assert seen == []


def test_the_caller_outside_a_request_is_the_stdio_account(monkeypatch):
    monkeypatch.setenv("LOGNAME", "someone")
    assert hutch_mcp._user() == "stdio:someone"
