"""Safety classification. The point is that misclassification refuses, not proceeds."""

from __future__ import annotations

import pytest

from hutchlib import safety


@pytest.mark.parametrize("code", [
    "daq.state",
    "mfx_dg1_pim.removed",
    "att.position",
    "daq.config_alias_sig.get()",
    "get_run()",
    "print(yag0.inserted)",
])
def test_reads_are_read_only(code):
    assert safety.classify(code)["class"] == safety.READ_ONLY


@pytest.mark.parametrize("code,expected", [
    ("daq.begin(duration=180)", safety.WRITE),
    ("autorun(sample='Lyso', run_length=60, record=True, run_type='DATA')", safety.WRITE),
    ("takepeds()", safety.WRITE),
    ("mfx_target.mv(3.0)", safety.WRITE),
    ("sl1l0.set(0.5)", safety.WRITE),
])
def test_writes_are_writes(code, expected):
    assert safety.classify(code)["class"] == expected


@pytest.mark.parametrize("code", [
    "mfx_stopper_1.remove()",
    "att.set(0.05)",
    "mfx_att_1.mv(0)",
    "pulse_picker.insert()",
    "mr1l4_homs.pitch.mv(-562.035)",
])
def test_beam_critical_wins_over_write(code):
    assert safety.classify(code)["class"] == safety.BEAM_CRITICAL


def test_unrecognised_code_is_not_read_only():
    # Errs upward: anything not positively recognised is at least a write.
    result = safety.classify("weird_thing_we_have_never_seen(42) ** 3")
    assert result["class"] == safety.WRITE
    assert any("not provably read-only" in r for r in result["reasons"])


def test_read_only_needs_no_confirmation():
    assert safety.require_confirmation("daq.state", None)["class"] == safety.READ_ONLY


def test_write_without_confirmation_is_refused():
    with pytest.raises(safety.SafetyRefusal, match="no confirmation was supplied"):
        safety.require_confirmation("daq.begin(duration=180)", None)


def test_understating_the_class_is_refused_not_downgraded():
    # A model that mislabels a stopper move as read-only gets a refusal.
    with pytest.raises(safety.SafetyRefusal, match="but this is 'beam-critical'"):
        safety.require_confirmation("mfx_stopper_1.remove()", safety.READ_ONLY)
    with pytest.raises(safety.SafetyRefusal, match="but this is 'beam-critical'"):
        safety.require_confirmation("mfx_stopper_1.remove()", safety.WRITE)


def test_matching_or_higher_confirmation_passes():
    assert safety.require_confirmation("daq.begin()", safety.WRITE)["class"] == safety.WRITE
    assert safety.require_confirmation("daq.begin()", safety.BEAM_CRITICAL)["class"] == safety.WRITE


def test_reasons_are_quotable_in_a_confirmation_prompt():
    reasons = safety.classify("mfx_att_1.mv(0)")["reasons"]
    assert any("attenuator" in r for r in reasons)
    assert any("device move" in r for r in reasons)
