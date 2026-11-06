"""hutch-mcp: the stdio MCP server that fronts the hutch-only tools."""

import io
import json
import subprocess

from hutch_tools import mcp_server as mcp


def _call(name, arguments):
    return mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})["result"]


def _fake_run(monkeypatch, rc=0, stdout='{"value": 1}', stderr=""):
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)
    monkeypatch.setattr(mcp.subprocess, "run", run)
    return seen


def test_lists_only_read_only_hutch_tools():
    tools = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"daq_state", "lightpath_path", "epics_caget", "epics_moved", "happi_pv", "normal_diff"} <= names
    assert not any(n.startswith(("hutch_py", "state_set", "normal_take", "lute")) for n in names)
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)


def test_required_args_come_from_the_template():
    tools = {t["name"]: t for t in mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]}
    assert tools["epics_history"]["inputSchema"]["required"] == ["pv", "start"]


def test_argv_order_follows_each_cli(monkeypatch):
    seen = _fake_run(monkeypatch)
    _call("daq_state", {"hutch": "mfx"})
    _call("epics_moved", {"pv": "MFX:DG1:PIC:01", "start": "2026-11-05T00:00"})
    _call("epics_caget", {"pv": ["A:B", "C:D"]})
    _call("normal_diff", {"hutch": "mfx", "experiment": "mfx1", "taking_data": True})
    _call("normal_diff", {"hutch": "mfx", "experiment": "mfx1", "taking_data": False})
    assert [a[1:] for a in seen] == [
        ["--hutch", "mfx", "state"],
        ["moved", "MFX:DG1:PIC:01", "--from", "2026-11-05T00:00"],
        ["caget", "A:B", "C:D"],
        ["diff", "--hutch", "mfx", "--experiment", "mfx1", "--taking-data"],
        ["diff", "--hutch", "mfx", "--experiment", "mfx1"],
    ]
    assert seen[0][0].endswith("bin/hutch-daq")


def test_a_failing_tool_is_an_error_result_with_its_rc(monkeypatch):
    _fake_run(monkeypatch, rc=4, stdout='{"error": "DaqError"}')
    res = _call("daq_state", {"hutch": "mfx"})
    assert res["isError"] is True
    assert res["content"][0]["text"].startswith("rc 4")


def test_an_argument_that_looks_like_a_flag_is_refused(monkeypatch):
    seen = _fake_run(monkeypatch)
    res = _call("data_cat", {"path": "--help"})
    assert res["isError"] is True and seen == []


def test_missing_required_argument_is_refused(monkeypatch):
    seen = _fake_run(monkeypatch)
    assert _call("lightpath_path", {})["isError"] is True and seen == []


def test_protocol_basics():
    init = mcp.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})["result"]
    assert init["capabilities"] == {"tools": {}} and init["protocolVersion"] == "2025-06-18"
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})["error"]["code"] == -32601


def test_serve_speaks_newline_delimited_json():
    out = io.StringIO()
    mcp.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
                          '{"jsonrpc":"2.0","method":"notifications/initialized"}\nnot json\n'), out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert lines[1]["error"]["code"] == -32700 and len(lines) == 2
