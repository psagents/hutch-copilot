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


def test_exposes_exactly_these_tools():
    names = {t.name for t in asyncio.run(hutch_mcp.mcp.list_tools())}
    assert names == {"happi_list", "happi_device", "happi_pv", "epics_caget", "epics_history",
                     "epics_moved", "lightpath_path", "daq_state", "normal_diff", "data_cat",
                     "ready_beam", "ready_hutch", "ready_daq", "hutch_py_run"}


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


def test_no_tool_inherits_the_mcp_pipe_as_stdin(monkeypatch):
    """stdin is the protocol stream; inherited, it hung hutch-py run past its 300 s timeout."""
    kws = []
    monkeypatch.setattr(hutch_mcp.subprocess, "run", lambda argv, **kw: kws.append(kw) or
                        subprocess.CompletedProcess(argv, 0, "{}", ""))
    hutch_mcp.daq_state("mfx")
    assert kws[0]["stdin"] is subprocess.DEVNULL


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


def test_over_ssh_any_snippet_runs_as_the_caller_with_no_policy(monkeypatch):
    """The approval is Claude Code's prompt, before the call; nothing here judges the code."""
    seen = _fake_run(monkeypatch)
    monkeypatch.delenv("AUTHZ_POLICY_FILE", raising=False)
    hutch_mcp.hutch_py_run("mfx_dg1.mv(3)", hutch="mfx")
    assert seen == [["--hutch", "mfx", "--timeout", "300", "run", "mfx_dg1.mv(3)"]]


POLICY = """
users:
  ytl: {groups: [mfx-operators]}
groups:
  mfx-operators:
    grants: [{hutch: mfx}]
direct_grants:
  - {user: "*", hutch: xpp}
"""


def _over_gateway(monkeypatch, tmp_path, user, policy=POLICY):
    (tmp_path / "policy.yaml").write_text(policy)
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(tmp_path / "policy.yaml"))
    monkeypatch.setattr(hutch_mcp, "_user", lambda: user)


@pytest.mark.parametrize("user,hutch", [
    ("bob", "mfx"),                      # in no group
    ("ytl@slac.stanford.edu", "mfx"),    # the gateway sends bare names
    ("", "xpp"),                         # no name, even for "*"
    ("ytl", "rix"),                      # another hutch
])
def test_a_gateway_call_without_a_grant_runs_nothing(monkeypatch, tmp_path, user, hutch):
    seen = _fake_run(monkeypatch)
    _over_gateway(monkeypatch, tmp_path, user)
    with pytest.raises(ToolError, match="no grant"):
        hutch_mcp.hutch_py_run("print(1)", hutch=hutch)
    assert seen == []


@pytest.mark.parametrize("user,hutch", [("ytl", "mfx"), ("bob", "xpp")])   # a group; a pattern
def test_a_granted_gateway_call_runs(monkeypatch, tmp_path, user, hutch):
    seen = _fake_run(monkeypatch)
    _over_gateway(monkeypatch, tmp_path, user)
    hutch_mcp.hutch_py_run("mfx_dg1.mv(3)", hutch=hutch)
    assert seen == [["--hutch", hutch, "--timeout", "300", "run", "mfx_dg1.mv(3)"]]


@pytest.mark.parametrize("policy", ["users: [", "groups: {}\nusers: {ytl: {groups: [gone]}}", ""])
def test_a_policy_that_cannot_be_evaluated_grants_nothing(monkeypatch, tmp_path, policy):
    _over_gateway(monkeypatch, tmp_path, "ytl", policy)
    assert not hutch_mcp._granted("ytl", "mfx")


def test_no_policy_file_grants_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(tmp_path / "absent.yaml"))
    assert not hutch_mcp._granted("ytl", "mfx")
    monkeypatch.delenv("AUTHZ_POLICY_FILE")
    assert not hutch_mcp._granted("ytl", "mfx")


def test_the_example_policy_is_never_read_and_grants_no_one_openly(monkeypatch):
    """The repo's copy is an example: unread by default, and a model without a "*" grant."""
    example = hutch_mcp.BIN.parent / "authz" / "policy.yaml"
    policy = hutch_mcp.yaml.safe_load(example.read_text())
    assert set(policy) == {"users", "groups", "direct_grants"}
    assert all(g["user"] != "*" for g in policy["direct_grants"])
    monkeypatch.delenv("AUTHZ_POLICY_FILE", raising=False)
    assert not hutch_mcp._granted("alice", "mfx")
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(example))
    assert hutch_mcp._granted("alice", "mfx") and not hutch_mcp._granted("alice", "xpp")


def test_the_model_passes_only_the_code():
    """No approval or class in the schema: the operator's approval is the client's prompt."""
    tools = {t.name: t for t in asyncio.run(hutch_mcp.mcp.list_tools())}
    assert set(tools["hutch_py_run"].inputSchema["properties"]) == {"code", "timeout_s", "hutch"}
