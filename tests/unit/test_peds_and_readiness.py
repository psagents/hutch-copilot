"""Pedestal deployment and the read-only LUTE readiness check.

Both replace prose. `take-run/references/dark-run.md` carried the makepeds chain,
the per-detector fallbacks and a stale hardware table in 151 lines -- including
`/reg/g/pcds` paths this repo's own topology notes call the deprecated alias.
Session-start LUTE checking was proposed for `analyze-data`, which is only read
when someone asks about analysis, so it would never have run at session start.
"""

from __future__ import annotations

import pytest

from hutch_tools import lute, paths, traps


# --------------------------------------------------------------------------
# The ordering trap
# --------------------------------------------------------------------------
def test_missing_xtc2_blocks_deployment():
    v = traps.check("peds", {"xtc2_files": [], "run": 43})
    assert [e["id"] for e in v] == ["peds-before-xtc2"]
    assert "run 43" in v[0]["problem"]


def test_present_xtc2_passes():
    assert traps.check("peds", {"xtc2_files": ["x-r0043-s000.xtc2"], "run": 43}) == []


def test_trap_is_inert_when_the_context_says_nothing_about_xtc2():
    assert traps.check("peds", {}) == []


def test_peds_trap_carries_evidence():
    entry = next(e for e in traps.registry() if e["id"] == "peds-before-xtc2")
    assert "XTC2 files must already be present" in entry["evidence"]


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


def test_refuses_when_no_xtc2_has_landed(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    (tmp_path / "mfx/e1/xtc2").mkdir(parents=True)
    with pytest.raises(lute.LuteError, match="peds-before-xtc2"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm="write")


def test_unknown_detector_has_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    xtc2 = tmp_path / "mfx/e1/xtc2"
    xtc2.mkdir(parents=True)
    (xtc2 / "e1-r0043-s000.xtc2").touch()
    with pytest.raises(lute.LuteError, match="no fallback tools known"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx",
                              detector="epix100a", fallback=True, confirm="write")


# --------------------------------------------------------------------------
# Readiness is read-only, and offline-honest
# --------------------------------------------------------------------------
def test_unconfigured_experiment_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    res = lute.readiness("mfx", "e1")
    assert res["configured"] is False
    assert "/analyze-data" in res["next"]
    assert res["artifacts"]["lute_output_dir"] is False


def test_configured_experiment_is_detected_from_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    out = tmp_path / "mfx/e1/results/lute_output"
    out.mkdir(parents=True)
    (out / "lute.db").touch()
    (out / "mfx_lute.yaml").touch()
    (out / "sfx_cctbx.dag").touch()
    res = lute.readiness("mfx", "e1")
    assert res["configured"] is True
    assert res["next"] is None
    assert res["artifacts"]["config_yaml"] == ["mfx_lute.yaml"]


def test_readiness_declares_what_it_could_not_check(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    res = lute.readiness("mfx", "e1")
    assert any("Kerberos" in item for item in res["not_checked"])


def test_readiness_writes_nothing(tmp_path, monkeypatch):
    """It runs at every session start, so it must not touch the filesystem."""
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    lute.readiness("mfx", "e1")
    assert list(tmp_path.iterdir()) == []


def test_readiness_honours_the_optional_results_subdirectory(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    res = lute.readiness("mfx", "e1", directory="lconreux")
    assert res["lute_output_dir"].endswith("results/lconreux/lute_output")


# --------------------------------------------------------------------------
# XTC2 arrival -- one definition, shared by the resolver and the peds guard
# --------------------------------------------------------------------------
def test_xtc2_resolver_reports_absence_without_guessing(tmp_path, monkeypatch):
    from hutch_tools import resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    rec = resolvers.xtc2("mfx", "e1", 43)
    assert rec["value"]["present"] is False
    assert rec["value"]["count"] == 0
    assert rec["source"].startswith("resolved:ls ")


def test_xtc2_resolver_counts_and_sizes(tmp_path, monkeypatch):
    from hutch_tools import resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    d = tmp_path / "mfx/e1/xtc2"
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
    from hutch_tools import resolvers
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    d = tmp_path / "mfx/e1/xtc2"
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")
    assert resolvers.xtc2("mfx", "e1", 43)["value"]["present"] is True
    # the guard no longer blocks now that a file exists
    assert traps.check("peds", {"xtc2_files": [p.name for p in
                                paths.xtc2_files("mfx", "e1", 43)], "run": 43}) == []
