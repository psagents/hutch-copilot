"""Normal snapshot: judge the hutch against what an operator called normal."""

from __future__ import annotations

import pytest

from hutch_tools import normal

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
