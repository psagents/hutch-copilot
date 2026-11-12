"""Normal snapshot: judge the hutch against what an operator called normal."""

from __future__ import annotations

import pytest

from hutch_copilot import normal
from hutch_copilot.tools import state

SNAP = {
    "taken_at": "2026-11-01T23:39:57+00:00",
    "beam_path": {"d1_ipm": "Inserted", "mirror": "Inserted", "stop": "Inserted",
                  "slit": "Inserted", "valve": "Removed"},
    "rules": [
        {"match": "*_ipm", "kind": "ignore"},
        {"match": "mirror", "kind": "required_in"},
        {"match": "stop", "kind": "idle_closed"},
        {"match": "slit", "kind": "min_gap", "min_mm": 0.1,
         "pvs": {"x": "S:X", "y": "S:Y", "blocked": "S:B"}},
    ],
}
NOW = {"d1_ipm": "Inserted", "mirror": "Inserted", "stop": "Inserted",
       "slit": "Inserted", "valve": "Removed"}
OPEN = {"S:X": "0.5", "S:Y": "0.5", "S:B": "NO"}


def verdicts(res):
    return {f["device"]: f["verdict"] for f in res["findings"]}


def test_benign_blockers_drop_out_while_idle():
    res = normal.diff(SNAP, ["d1_ipm", "mirror", "stop", "slit"], NOW, OPEN, taking_data=False)
    assert verdicts(res) == {"d1_ipm": "ignored", "mirror": "ok",
                             "stop": "expected_idle", "slit": "ok"}
    assert res["blocking"] == []


def test_idle_closed_stopper_blocks_when_taking_data():
    res = normal.diff(SNAP, ["stop"], NOW, OPEN, taking_data=True)
    assert verdicts(res)["stop"] == "blocker"
    assert res["blocking"] == ["stop"]


def test_required_mirror_out_is_a_blocker():
    res = normal.diff(SNAP, [], {**NOW, "mirror": "Removed"}, OPEN, taking_data=False)
    assert verdicts(res)["mirror"] == "blocker"


@pytest.mark.parametrize("pvs", [
    {"S:X": "2.8e-17", "S:Y": "0.3", "S:B": "NO"},   # 2026-11-04 mfx_dg2_midstream_slits
    {"S:X": "0.5", "S:Y": "0.5", "S:B": "YES"},
])
def test_a_closed_slit_is_a_warning(pvs):
    res = normal.diff(SNAP, ["slit"], NOW, pvs, taking_data=False)
    assert verdicts(res)["slit"] == "warning"


def test_an_unreadable_slit_is_unknown_not_ok():
    res = normal.diff(SNAP, ["slit"], NOW, {}, taking_data=False)
    assert verdicts(res)["slit"] == "unknown"


def test_an_unruled_blocker_stays_a_blocker():
    res = normal.diff(SNAP, ["valve"], {**NOW, "valve": "Inserted"}, OPEN, taking_data=False)
    assert verdicts(res)["valve"] == "blocker"
    assert res["changed"] == [{"device": "valve", "was": "Removed", "now": "Inserted"}]


def test_a_device_new_or_gone_since_the_snapshot_is_a_change():
    now = {k: v for k, v in NOW.items() if k != "valve"} | {"newdev": "Removed"}
    res = normal.diff(SNAP, [], now, OPEN, taking_data=False)
    assert {"device": "valve", "was": "Removed", "now": None} in res["changed"]
    assert {"device": "newdev", "was": None, "now": "Removed"} in res["changed"]


@pytest.mark.parametrize("device,kind,state", [
    ("mirror", "required_in", "Disconnected"),
    ("mirror", "required_in", "Unknown"),
    ("stop", "idle_closed", "Disconnected"),
    ("stop", "idle_closed", "Error"),
])
def test_unreadable_ruled_device_is_unknown(device, kind, state):
    """Unreadable states (Disconnected, Unknown, Error) → unknown verdict, not blocking."""
    snap = {
        "beam_path": {"mirror": "Inserted", "stop": "Inserted"},
        "rules": [
            {"match": "mirror", "kind": "required_in"},
            {"match": "stop", "kind": "idle_closed"},
        ]
    }
    now = {**NOW, device: state}

    # For idle_closed, include in blocking to test it still becomes unknown
    blocking = [device] if device == "stop" else []

    res = normal.diff(snap, blocking, now, OPEN, taking_data=True)
    verdicts_dict = verdicts(res)
    assert verdicts_dict[device] == "unknown"
    assert device not in res["blocking"]


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    return tmp_path


def _seed(exp, taken_at):
    state.State("mfx", exp).set("normal", {**SNAP, "taken_at": taken_at},
                                source="test", kind="operator")


def test_own_snapshot_wins(root):
    _seed("mfxA", "2026-10-01T00:00:00+00:00")
    _seed("mfxB", "2026-11-01T00:00:00+00:00")
    got = normal.latest("mfx", "mfxA")
    assert (got["from"], got["borrowed"]) == ("mfxA", False)


def test_fallback_is_newest_by_time_not_name(root):
    _seed("mfx999", "2026-09-01T00:00:00+00:00")
    _seed("mfx100", "2026-11-01T00:00:00+00:00")
    got = normal.latest("mfx", "mfxNEW")
    assert (got["from"], got["borrowed"]) == ("mfx100", True)


def test_unreadable_experiments_are_reported(root):
    _seed("mfx100", "2026-11-01T00:00:00+00:00")
    locked = root / "mfx" / "mfxLOCK" / "results" / "psagents"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        got = normal.latest("mfx", "mfxNEW")
    finally:
        locked.chmod(0o755)
    assert any("mfxLOCK" in u for u in got["unreadable"])


def test_no_snapshot_anywhere_is_none_not_an_error(root):
    got = normal.latest("mfx", "mfxNEW")
    assert got["snapshot"] is None and got["from"] is None


def test_own_snapshot_unreadable_is_reported(root):
    _seed("mfxOther", "2026-11-01T00:00:00+00:00")
    own_locked = root / "mfx" / "mfxOWN" / "results" / "psagents"
    own_locked.mkdir(parents=True)
    own_locked.chmod(0)
    try:
        got = normal.latest("mfx", "mfxOWN")
    finally:
        own_locked.chmod(0o755)
    assert (got["from"], got["borrowed"]) == ("mfxOther", True)
    assert any("mfxOWN" in u for u in got["unreadable"])


def test_malformed_normal_is_reported(root):
    _seed("mfxValid", "2026-11-01T00:00:00+00:00")
    path = root / "mfx" / "mfxBad" / "results" / "psagents"
    path.mkdir(parents=True)
    (path / "mfxBad_state.json").write_text('{"normal": {"rules": []}}')
    got = normal.latest("mfx", "mfxNEW")
    assert (got["from"], got["borrowed"]) == ("mfxValid", True)
    assert any("mfxBad" in u for u in got["unreadable"])


from hutch_copilot.tools import daq, epics, lightpath


@pytest.fixture()
def machine(monkeypatch):
    monkeypatch.setattr(lightpath, "beam_path", lambda hutch, **kw: {"value": {
        "blocking": ["stop"], "devices": [{"name": k, "state": v} for k, v in NOW.items()]}})
    monkeypatch.setattr(daq, "state", lambda hutch, **kw: {"value": {
        "state": "running", "recording": False, "config_alias": "BEAM"}})
    monkeypatch.setattr(epics, "caget", lambda pvs, **kw: {p: OPEN[p] for p in pvs})


def test_take_keeps_borrowed_rules_and_records_the_operator(root, machine, capsys):
    _seed("mfxOLD", "2026-11-01T00:00:00+00:00")
    assert normal.main(["take", "--hutch", "mfx", "--experiment", "mfxNEW",
                        "--source", "operator lconreux: this is normal"]) == 0
    entry = state.State("mfx", "mfxNEW").get_entry("normal")
    assert entry["kind"] == "operator"
    assert entry["value"]["rules"] == SNAP["rules"]
    assert entry["value"]["daq_idle"]["state"] == "running"


def test_diff_reports_where_the_snapshot_came_from(root, machine, capsys):
    import json as _j
    _seed("mfxOLD", "2026-11-01T00:00:00+00:00")
    assert normal.main(["diff", "--hutch", "mfx", "--experiment", "mfxNEW"]) == 0
    res = _j.loads(capsys.readouterr().out)
    assert res["borrowed"] and res["from"] == "mfxOLD"
    assert res["blocking"] == []


def test_diff_with_no_snapshot_says_so(root, machine, capsys):
    import json as _j
    assert normal.main(["diff", "--hutch", "mfx", "--experiment", "mfxNEW"]) == 0
    assert _j.loads(capsys.readouterr().out)["snapshot"] is None
