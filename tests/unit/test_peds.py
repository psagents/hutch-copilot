"""Pedestal deployment: the XTC2 ordering guard and per-detector fallbacks."""

from __future__ import annotations

import pytest

from hutch_tools import common as paths, lute


# --------------------------------------------------------------------------
# The ordering guard: makepeds reads the XTC2 files, so they must have landed
# --------------------------------------------------------------------------
def test_missing_xtc2_blocks_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    paths.xtc2_dir("mfx", "e1").mkdir(parents=True)
    with pytest.raises(lute.LuteError, match="no XTC2 files found for run 43"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm="write")


def test_present_xtc2_gets_past_the_guard(tmp_path, monkeypatch):
    """With data on disk the guard passes and the command reaches the transport."""
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")
    calls = []

    def fake_ssh(host, cmd, **kw):
        calls.append((host, cmd))
        return "deployed"

    monkeypatch.setattr(lute, "ssh", fake_ssh)
    rec = lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm="write")
    assert rec["ok"] is True
    assert rec["xtc2_files"] == 1
    assert calls[0][0] == "psdev"
    assert "makepeds -e e1 -r 43" in calls[0][1]


def test_a_failed_step_is_reported_not_raised(tmp_path, monkeypatch):
    """A non-zero remote command becomes ok=False with its output, not a traceback.

    daq._ssh raises on non-zero rather than returning a returncode, so this is
    the case that would regress into an unhandled BridgeUnavailable.
    """
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")

    def fake_ssh(host, cmd, **kw):
        raise lute.BridgeUnavailable("boom", returncode=7, stdout="partial", stderr="no such run")

    monkeypatch.setattr(lute, "ssh", fake_ssh)
    rec = lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx",
                                detector="jungfrau", confirm="write")
    assert rec["ok"] is False
    assert rec["steps"][0]["returncode"] == 7
    assert rec["steps"][0]["stderr"] == "no such run"
    # makepeds failing on a known family must name the fallback route
    assert "--fallback" in rec["known_issue"]


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
def test_refuses_without_a_write_confirmation(monkeypatch):
    """The gate is daq.require_confirmation, and it fires before any transport."""
    def no_ssh(*a, **kw):
        raise AssertionError("a refused command reached the network")

    monkeypatch.setattr(lute, "ssh", no_ssh)
    with pytest.raises(lute.SafetyRefusal, match="makepeds"):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm=None)


def test_the_refusal_does_not_depend_on_what_is_on_disk(tmp_path, monkeypatch):
    """Gate first, XTC2 guard second.

    Deciding "this is a write" must not be contingent on the data having landed:
    with the order reversed, an unconfirmed command on an experiment whose XTC2
    is absent reports the wrong reason and, worse, an unconfirmed command on one
    whose data HAS landed is the only case the gate is exercised on.
    """
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    paths.xtc2_dir("mfx", "e1").mkdir(parents=True)     # no files: guard would trip
    with pytest.raises(lute.SafetyRefusal):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx", confirm=None)


def test_the_cli_maps_a_refusal_to_rc_5(monkeypatch, capsys):
    """rc 5, not a traceback: require_confirmation raises PermissionError, which
    the LuteError/OSError handler does not catch."""
    def no_ssh(*a, **kw):
        raise AssertionError("a refused command reached the network")

    monkeypatch.setattr(lute, "ssh", no_ssh)
    rc = lute.main(["peds", "--hutch", "mfx", "--experiment", "e1", "--run", "43"])
    assert rc == 5
    assert "SafetyRefusal" in capsys.readouterr().err


def test_fallback_commands_are_also_gated(tmp_path, monkeypatch):
    """The per-detector tools match no read-only pattern, so they err upward."""
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))

    def no_ssh(*a, **kw):
        raise AssertionError("a refused command reached the network")

    monkeypatch.setattr(lute, "ssh", no_ssh)
    with pytest.raises(lute.SafetyRefusal):
        lute.deploy_pedestals(experiment="e1", run=43, hutch="mfx",
                              detector="jungfrau", fallback=True, confirm=None)



def test_unknown_detector_has_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
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
    from hutch_tools import data as resolvers
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    rec = resolvers.xtc2("mfx", "e1", 43)
    assert rec["value"]["present"] is False
    assert rec["value"]["count"] == 0
    assert rec["source"].startswith("resolved:ls ")


def test_xtc2_resolver_counts_and_sizes(tmp_path, monkeypatch):
    from hutch_tools import data as resolvers
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
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
    from hutch_tools import data as resolvers
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    d = paths.xtc2_dir("mfx", "e1")
    d.mkdir(parents=True)
    (d / "e1-r0043-s000.xtc2").write_bytes(b"x")
    assert resolvers.xtc2("mfx", "e1", 43)["value"]["present"] is True
    assert paths.xtc2_files("mfx", "e1", 43) != []
