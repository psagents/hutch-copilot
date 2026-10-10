"""`moved()` reads MSTA, and the bit filtering is the whole point of it.

Two ways to get this wrong, both of which look like a working answer at a
beamline: trust `.RBV` (archived with a deadband, so a small move reads as no
move) or count an IOC reconnect as motion. The reconnect case is what these
pin -- a stuck motor reported as moving sends someone to the wrong rack.
"""

from __future__ import annotations

import pytest

from hutch_tools import epics

#: MSTA on a connected motor: bit 11 set, so a sample is real.
PRESENT = epics.MSTA_PRESENT
MOVING = PRESENT                               # bit 1 clear -> moving
IDLE = PRESENT | epics.MSTA_DONE_MOVING        # bit 1 set   -> done


def _series(monkeypatch, values):
    """Stub history() with MSTA values, one sample a minute."""
    samples = [{"at": f"2026-10-02T18:{i:02d}:00+00:00", "value": v}
               for i, v in enumerate(values)]
    monkeypatch.setattr(epics, "history", lambda *a, **k: {
        "value": {"from": "f", "to": "t", "count": len(samples), "samples": samples},
    })


def test_a_done_moving_transition_is_a_move(monkeypatch):
    _series(monkeypatch, [IDLE, MOVING, IDLE])
    v = epics.moved("MFX:DG1:PIC:01", "f", "t")["value"]
    assert v["moved"] is True
    assert v["samples_considered"] == 3


def test_a_motor_that_sat_still_did_not_move(monkeypatch):
    _series(monkeypatch, [IDLE, IDLE, IDLE])
    assert epics.moved("MFX:DG1:PIC:01", "f", "t")["value"]["moved"] is False


def test_an_ioc_reconnect_is_not_motion(monkeypatch):
    """The failure this function exists to avoid. On reconnect the appliance
    re-posts the value with the encoder/gains bits clear; that state differs
    from the surrounding IDLE samples, so a naive diff calls it a move."""
    _series(monkeypatch, [IDLE, 0, IDLE])
    v = epics.moved("MFX:DG1:PIC:01", "f", "t")["value"]
    assert v["moved"] is False, "a dropped connection was counted as a move"
    assert v["samples_discarded"] == 1


def test_no_usable_sample_is_unknown_not_no(monkeypatch):
    """Nothing archived means the question was not answered. Reporting False
    would be a confident no built on an empty series."""
    _series(monkeypatch, [])
    assert epics.moved("MFX:DG1:PIC:01", "f", "t")["value"]["moved"] is None
    _series(monkeypatch, [0, 0])
    assert epics.moved("MFX:DG1:PIC:01", "f", "t")["value"]["moved"] is None


def test_the_rbv_suffix_is_replaced_with_msta(monkeypatch):
    """A caller passing .RBV must still get the deadband-proof field."""
    seen = {}

    def fake(pv, *a, **k):
        seen["pv"] = pv
        return {"value": {"from": "f", "to": "t", "count": 0, "samples": []}}

    monkeypatch.setattr(epics, "history", fake)
    epics.moved("MFX:DG1:PIC:01.RBV", "f", "t")
    assert seen["pv"] == "MFX:DG1:PIC:01.MSTA"


def test_a_naive_time_gets_a_local_offset():
    """The appliance needs an explicit offset; a naive string would be read as
    UTC and silently shift the window by the Pacific offset."""
    assert epics._iso("2026-10-02T18:00")[-6] in "+-"
    with pytest.raises(epics.EpicsError):
        epics._iso("yesterday evening")
