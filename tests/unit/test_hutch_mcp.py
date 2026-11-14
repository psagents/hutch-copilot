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


class _Ctx:
    """A client: ``elicitation`` is whether it advertised the capability; ``answer`` its reply."""

    def __init__(self, answer=None, typed="write", elicitation=True):
        caps = type("Caps", (), {"elicitation": object() if elicitation else None})()
        self.session = type("S", (), {"client_params": type("P", (), {"capabilities": caps})()})()
        self.asked = []
        self._answer, self._typed = answer, typed

    async def elicit(self, message, schema):
        self.asked.append(message)
        return type("R", (), {"action": self._answer, "data": schema(confirm=self._typed)})()


def _py_run(code, ctx, monkeypatch):
    seen = _fake_run(monkeypatch)
    return seen, asyncio.run(hutch_mcp.hutch_py_run(code, ctx, hutch="mfx"))


def test_a_read_only_snippet_runs_without_asking(monkeypatch):
    ctx = _Ctx()
    seen, _ = _py_run("print(mfx_dg1.position)", ctx, monkeypatch)
    assert ctx.asked == []
    assert seen == [["--hutch", "mfx", "--timeout", "300", "run", "print(mfx_dg1.position)"]]


def test_a_write_runs_only_with_the_class_the_operator_typed(monkeypatch):
    ctx = _Ctx("accept", typed=" Write ")
    seen, _ = _py_run("mfx_dg1.mv(3)", ctx, monkeypatch)
    assert "mfx_dg1.mv(3)" in ctx.asked[0] and "'write'" in ctx.asked[0]
    assert seen == [["--hutch", "mfx", "--timeout", "300", "--confirm", "write", "run",
                     "mfx_dg1.mv(3)"]]


@pytest.mark.parametrize("ctx,why", [
    (_Ctx("decline"), "did not approve"),
    (_Ctx("cancel"), "did not approve"),
    (_Ctx("accept", typed="yes"), "not an action class"),
    (_Ctx("accept", elicitation=False), "cannot ask the operator"),
])
def test_a_write_without_a_typed_approval_never_runs(monkeypatch, ctx, why):
    seen = _fake_run(monkeypatch)
    with pytest.raises(ToolError, match=why):
        asyncio.run(hutch_mcp.hutch_py_run("mfx_dg1.mv(3)", ctx, hutch="mfx"))
    assert seen == []


POLICY = """
users:
  ytl: {groups: [mfx-operators]}
groups:
  mfx-operators:
    grants: [{hutch: mfx, class: write}]
direct_grants:
  - {user: "*", hutch: xpp, class: beam-critical}
"""


def _over_gateway(monkeypatch, tmp_path, user, policy=POLICY):
    (tmp_path / "policy.yaml").write_text(policy)
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(tmp_path / "policy.yaml"))
    monkeypatch.setattr(hutch_mcp, "_user", lambda: user)


@pytest.mark.parametrize("user,code,hutch", [
    ("bob", "mfx_dg1.mv(3)", "mfx"),                      # in no group
    ("ytl@slac.stanford.edu", "mfx_dg1.mv(3)", "mfx"),    # the gateway sends bare names
    ("", "xpp_stopper.remove()", "xpp"),                  # no name, even for "*"
    ("ytl", "mfx_stopper_1.remove()", "mfx"),             # write < beam-critical
    ("ytl", "mfx_dg1.mv(3)", "rix"),                      # another hutch
])
def test_a_gateway_write_without_a_grant_is_refused_before_asking(monkeypatch, tmp_path,
                                                                   user, code, hutch):
    seen = _fake_run(monkeypatch)
    _over_gateway(monkeypatch, tmp_path, user)
    ctx = _Ctx("accept")
    with pytest.raises(ToolError, match="no grant"):
        asyncio.run(hutch_mcp.hutch_py_run(code, ctx, hutch=hutch))
    assert seen == [] and ctx.asked == []


@pytest.mark.parametrize("user,code,hutch,typed", [
    ("ytl", "mfx_dg1.mv(3)", "mfx", "write"),                 # through a group
    ("bob", "xpp_stopper.remove()", "xpp", "beam-critical"),  # a direct pattern
])
def test_a_granted_gateway_write_still_needs_the_typed_approval(monkeypatch, tmp_path,
                                                                user, code, hutch, typed):
    seen = _fake_run(monkeypatch)
    _over_gateway(monkeypatch, tmp_path, user)
    ctx = _Ctx("accept", typed=typed)
    asyncio.run(hutch_mcp.hutch_py_run(code, ctx, hutch=hutch))
    assert len(ctx.asked) == 1 and seen[0][-4:] == ["--confirm", typed, "run", code]


@pytest.mark.parametrize("policy", ["users: [", "groups: {}\nusers: {ytl: {groups: [gone]}}", ""])
def test_a_policy_that_cannot_be_evaluated_grants_nothing(monkeypatch, tmp_path, policy):
    _over_gateway(monkeypatch, tmp_path, "ytl", policy)
    assert not hutch_mcp._granted("ytl", "mfx", "write")


def test_no_policy_file_grants_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(tmp_path / "absent.yaml"))
    assert not hutch_mcp._granted("ytl", "mfx", "write")
    monkeypatch.delenv("AUTHZ_POLICY_FILE")
    assert not hutch_mcp._granted("ytl", "mfx", "write")


def test_the_example_policy_is_never_read_and_grants_no_one_openly(monkeypatch):
    """The repo's copy is an example: unread by default, and a model without a "*" write grant."""
    example = hutch_mcp.BIN.parent / "authz" / "policy.yaml"
    policy = hutch_mcp.yaml.safe_load(example.read_text())
    assert set(policy) == {"users", "groups", "direct_grants"}
    assert all(g["user"] != "*" for g in policy["direct_grants"])
    monkeypatch.delenv("AUTHZ_POLICY_FILE", raising=False)
    assert not hutch_mcp._granted("alice", "mfx", "write")
    monkeypatch.setenv("AUTHZ_POLICY_FILE", str(example))
    assert hutch_mcp._granted("alice", "mfx", "beam-critical") and not hutch_mcp._granted("bob", "mfx", "beam-critical")


def test_the_model_cannot_pass_a_confirmation():
    """confirm comes only from the operator's form; it is not in the tool's schema."""
    tools = {t.name: t for t in asyncio.run(hutch_mcp.mcp.list_tools())}
    assert set(tools["hutch_py_run"].inputSchema["properties"]) == {"code", "timeout_s", "hutch"}
