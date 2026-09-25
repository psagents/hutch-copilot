"""Pedestal deployment: the XTC2 ordering guard and per-detector fallbacks."""

from __future__ import annotations

import pytest

from hutch_tools import common as paths, lute


# --------------------------------------------------------------------------
# The ordering guard: makepeds reads the XTC2 files, so they must have landed
# --------------------------------------------------------------------------
def test_missing_xtc2_blocks_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    paths.xtc2_dir("mfx", "e1").mkdir(parents=True)
    with pytest.raises(lute.LuteError, match="no XTC2 files found for run 43"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm="write")


def test_present_xtc2_gets_past_the_guard(tmp_path, monkeypatch):
    """With data on disk the guard passes, so the failure has to come from ssh."""
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        class R:
            returncode = 0
            stdout = b"deployed"
            stderr = b""
        return R()

    monkeypatch.setattr(lute.subprocess, "run", fake_run)
    rec = lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm="write")
    assert rec["ok"] is True
    assert rec["xtc2_files"] == 1
    assert "makepeds -e e1 -r 43" in " ".join(calls[0])


# --------------------------------------------------------------------------
# Fallback selection -- incident knowledge, now data
# --------------------------------------------------------------------------
@pytest.mark.parametrize("detector,family", [
    ("jungfrau", "jungfrau"), ("jungfrau4M", "jungfrau"), ("Jungfrau16M", "jungfrau"),
    ("epix10ka", "epix10ka"), ("epix10ka2M", "epix10ka"),
    ("epix100a", None), ("", None),
])
def test_detector_family_detection(detector, family):
    assert lute._detector_family(detector) == family


def test_fallbacks_use_the_canonical_cds_path_not_the_deprecated_alias():
    assert "/cds/group/pcds" in lute.PEDS_ENV
    assert "/reg/" not in lute.PEDS_ENV


def test_every_fallback_family_has_a_process_and_a_deploy_step():
    for family, commands in lute.PEDS_FALLBACKS.items():
        assert len(commands) == 2, family
        assert any(tok in commands[1] for tok in ("deploy", "-D")), family


# --------------------------------------------------------------------------
# Deployment is a write
# --------------------------------------------------------------------------
def test_refuses_without_a_write_confirmation():
    with pytest.raises(lute.LuteError, match="re-run with --confirm write"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm=None)



def test_unknown_detector_has_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    xtc2 = paths.xtc2_dir("mfx", "e1")
    xtc2.mkdir(parents=True)
    (xtc2 / "e1-r0043-s000.xtc2").touch()
    with pytest.raises(lute.LuteError, match="no fallback tools known"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx",
                              detector="epix100a", fallback=True, confirm="write")


# --------------------------------------------------------------------------
# Readiness is read-only, and offline-honest
# --------------------------------------------------------------------------





def test_xtc2_resolver_reports_absence_without_guessing(tmp_path, monkeypatch):
    from hutch_tools import resolve as resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    rec = resolvers.xtc2("mfx", "e1", 43)
    assert rec["value"]["present"] is False
    assert rec["value"]["count"] == 0
    assert rec["source"].startswith("resolved:ls ")


def test_xtc2_resolver_counts_and_sizes(tmp_path, monkeypatch):
    from hutch_tools import resolve as resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    # Built through the resolver's own path function, not a literal, so a wrong
    # directory name cannot be asserted true by both sides at once -- this test
    # passed for months against `xtc2/`, which no experiment on disk has.
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    for s in range(3):
        (d / f"e1-r0043-s{s:03d}.xtc2").write_bytes(b"x" * 1000)
    (d / "e1-r0044-s000.xtc2").write_bytes(b"x")        # a different run
    rec = resolvers.xtc2("mfx", "e1", 43)
    assert rec["value"]["count"] == 3
    assert rec["value"]["total_bytes"] == 3000
    assert all("r0043" in n for n in rec["value"]["files"])


def test_the_resolver_and_the_peds_guard_agree(tmp_path, monkeypatch):
    """Both read paths.xtc2_files, so 'the data has landed' has one definition."""
    from hutch_tools import resolve as resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")
    assert resolvers.xtc2("mfx", "e1", 43)["value"]["present"] is True
    assert paths.xtc2_files("mfx", "e1", 43) != []
