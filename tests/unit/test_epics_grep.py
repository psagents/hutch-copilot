"""`grep()` matches the PV name alone, so an anchored pattern finds the bare motor record."""

from __future__ import annotations

from hutch_copilot.tools import epics


def test_grep_matches_pv_names_and_names_the_ioc(monkeypatch, tmp_path):
    info = tmp_path / "ioc-mfx-motion" / "iocInfo"
    info.mkdir(parents=True)
    (info / "IOC.pvlist").write_text('MFX:LJH:JET:Y, "motor"\nMFX:LJH:JET:Y:PLC:bEnable, "bo"\n')
    monkeypatch.setenv("HUTCH_IOC_DATA", str(tmp_path))
    rec = epics.grep("JET:Y$")
    assert rec["value"] == [{"pv": "MFX:LJH:JET:Y", "ioc": "ioc-mfx-motion", "rtyp": "motor"}]
    assert epics.grep("JET", limit=1)["truncated"] is True
