"""State store: provenance, TTL, atomicity, concurrency."""

from __future__ import annotations

import json
import os

import pytest

from hutch_tools import common as paths, state


@pytest.fixture()
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    return state.State("mfx", "mfxl1013621")


def test_path_is_the_canonical_one(st, tmp_path):
    # The bug this refactor exists to kill: three SKILL.md files said
    # {exp}/{exp}_state.json instead of {exp}/results/psagents/{exp}_state.json.
    assert st.path == tmp_path / "mfx/mfxl1013621/results/psagents/mfxl1013621_state.json"


def test_hutch_case_does_not_change_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    assert state.State("MFX", "e1").path == state.State("mfx", "e1").path


def test_set_requires_a_source(st):
    with pytest.raises(state.StateError, match="without a source"):
        st.set("sample_name", "Lyso", source="")


def test_set_rejects_unknown_kind(st):
    with pytest.raises(state.StateError, match="kind must be"):
        st.set("sample_name", "Lyso", source="operator said so", kind="vibes")


def test_roundtrip_carries_provenance(st):
    st.set("sample_name", "Lyso", source="operator at 14:32", kind=state.OPERATOR)
    entry = st.get_entry("sample_name")
    assert entry["value"] == "Lyso"
    assert entry["source"] == "operator at 14:32"
    assert entry["kind"] == state.OPERATOR
    assert entry["retrieved_at"]


def test_resolved_and_operator_values_are_distinguishable(st):
    st.set("photon_energy_eV", 9500, source="operator typed it", kind=state.OPERATOR)
    st.set("detectors", ["jungfrau"],
           source="psana2 detinfo exp=mfxl1013621 run=36", kind=state.RESOLVED)
    kinds = {r["field"]: r["kind"] for r in st.audit()}
    assert kinds["photon_energy_eV"] == state.OPERATOR
    assert kinds["detectors"] == state.RESOLVED


def test_missing_field_raises_rather_than_returning_none(st):
    with pytest.raises(state.MissingValue):
        st.get("sample_name")


def test_null_field_is_missing_not_a_value(st):
    st.set("sample_name", "Lyso", source="op")
    st.unset("sample_name")
    with pytest.raises(state.MissingValue):
        st.get("sample_name")


def test_ttl_lookup_prefers_the_longest_prefix():
    assert state.ttl_for("machine_state.daq_status") == 600.0
    assert state.ttl_for("beampath") == 30.0
    assert state.ttl_for("sample_name") is None
    # unknown fields do not expire
    assert state.ttl_for("something_new") is None


def test_stale_value_raises_and_stale_ok_returns_it(st):
    st.set("beampath", {"blocking": []}, source="lightpath", ttl_s=0.0)
    with pytest.raises(state.StaleValue) as exc:
        st.get("beampath")
    assert "stale" in str(exc.value)
    assert st.get("beampath", stale_ok=True) == {"blocking": []}


def test_operator_declared_values_do_not_expire(st):
    st.set("sample_name", "Lyso", source="operator")
    assert st.get_entry("sample_name")["stale"] is False


def test_dotted_fields_nest(st):
    st.set("machine_state.daq_status", "Ready", source="daq.state via mfx-daq")
    assert json.loads(st.path.read_text())["machine_state"]["daq_status"] == "Ready"


def test_timestamps_mirror_keeps_prerefactor_readers_working(st):
    st.set("sample_name", "Lyso", source="operator")
    assert "sample_name" in json.loads(st.path.read_text())["_timestamps"]


def test_write_is_atomic_and_leaves_no_temp_files(st):
    st.set("sample_name", "Lyso", source="operator")
    st.set("concentration", "10 mM", source="operator")
    leftovers = [p.name for p in st.path.parent.iterdir()
                 if p.name.startswith(".state-") or p.name.endswith(".lock")]
    assert leftovers == []


