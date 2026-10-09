"""The beamtime log: the prose conventions, now enforced.

`coordinate-experiment` carried ~60 lines of rules for a file it wrote by hand
with `cat >>`: the timestamp format, heading levels, "append, don't restructure",
"never insert a `---` inside an existing section", the `*(reconstructed)*` marker.
Each of those is a test here.
"""

from __future__ import annotations

import multiprocessing
import os

import pytest

from hutch_tools import common as paths, log


@pytest.fixture()
def logged(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    log.init("mfx", "e1", shift_date="2026-08-31", shift_start="08:00")
    return log.path("mfx", "e1")


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------
def test_init_writes_the_document_header(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    res = log.init("mfx", "e1", shift_date="2026-08-31", shift_start="08:00")
    text = log.path("mfx", "e1").read_text()
    assert res["created"] is True
    assert text.startswith("# MFX Beamtime Log")
    assert "**Experiment:** e1" in text
    assert "**Shift start:** 08:00" in text
    assert "## Shift Start" in text


def test_init_is_idempotent_and_never_overwrites(logged):
    before = logged.read_text()
    res = log.init("mfx", "e1", shift_date="2099-01-01")
    assert res["created"] is False
    assert logged.read_text() == before


def test_init_carries_optional_header_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    log.init("mfx", "e1", shift_date="2026-08-31",
             header_fields={"Sample": "Lyso, 10 mM", "Photon energy": "9500 eV"})
    text = log.path("mfx", "e1").read_text()
    assert "- Sample: Lyso, 10 mM" in text
    assert "- Photon energy: 9500 eV" in text


def test_appending_without_init_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    with pytest.raises(log.LogError, match="hutch-log init"):
        log.append("mfx", "e1", "something happened")
    assert not log.path("mfx", "e1").exists()


# --------------------------------------------------------------------------
# append
# --------------------------------------------------------------------------
def test_append_stamps_the_time_from_the_machine(logged):
    res = log.append("mfx", "e1", "Run 43 (DARK) complete")
    assert res["at"] and len(res["at"]) == 5 and res["at"][2] == ":"
    assert logged.read_text().rstrip().endswith("Run 43 (DARK) complete")


def test_append_uses_the_documented_bullet_format(logged):
    log.append("mfx", "e1", "Sample changed to FeNO6", at="14:32")
    assert "- **14:32** Sample changed to FeNO6" in logged.read_text()


def test_reconstructed_entries_are_marked(logged):
    log.append("mfx", "e1", "LUTE configured", at="09:15", reconstructed=True)
    assert f"- **09:15** {log.RECONSTRUCTED} LUTE configured" in logged.read_text()


def test_append_only_ever_lands_at_the_end(logged):
    """This is what makes 'append, don't restructure' structural, not advisory."""
    log.append("mfx", "e1", "Data Collection begins", at="09:59")
    log.append("mfx", "e1", "first", at="10:00")
    log.append("mfx", "e1", "second", at="10:05")
    lines = [ln for ln in logged.read_text().splitlines() if ln.strip()]
    assert lines[-2].endswith("first")
    assert lines[-1].endswith("second")


def test_empty_entry_is_refused(logged):
    with pytest.raises(log.LogError, match="empty entry"):
        log.append("mfx", "e1", "   ")


def test_append_repairs_a_missing_trailing_newline(logged):
    logged.write_text(logged.read_text().rstrip("\n"))
    log.append("mfx", "e1", "after a truncated file", at="11:00")
    text = logged.read_text()
    assert "\n- **11:00** after a truncated file" in text


# --------------------------------------------------------------------------
# Removed surface
#
# `section` and `tail` were subcommands until Sep 2026, with eight tests between
# them. `section` let a caller impose headings on an append-only log -- the one
# structure an append-only log exists not to have -- and `tail` re-read a file
# the agent can read directly. `path` and `exists` were never invoked from any
# skill file. The `path()` *function* stays: _require, init and append all use it.
# --------------------------------------------------------------------------
def test_only_init_and_append_are_exposed():
    import argparse
    for action in log.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            assert set(action.choices) == {"init", "append"}
            return
    raise AssertionError("no subparsers found")


# --------------------------------------------------------------------------
# concurrency -- several agents plus the operator write during a shift
# --------------------------------------------------------------------------
def _writer(args):
    """One pool worker appending to the shared log.

    Sets HUTCH_DS_ROOT rather than patching an attribute: a pool worker is a
    fresh process, so monkeypatch does not reach it. No reload needed --
    common.ds_root() reads the environment per call.
    """
    root, text = args
    os.environ["HUTCH_DS_ROOT"] = str(root)
    from hutch_tools import log as lg
    lg.append("mfx", "e1", text, at="12:00")


def test_concurrent_appends_lose_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    log.init("mfx", "e1", shift_date="2026-08-31")
    texts = [f"concurrent entry {i}" for i in range(8)]
    with multiprocessing.Pool(4) as pool:
        pool.map(_writer, [(tmp_path, t) for t in texts])
    text = log.path("mfx", "e1").read_text()
    missing = [t for t in texts if t not in text]
    assert not missing, missing


def test_no_lockfile_is_left_behind(logged):
    log.append("mfx", "e1", "x", at="10:00")
    leftovers = [p.name for p in logged.parent.iterdir() if p.name.endswith(".lock")]
    assert leftovers == []
