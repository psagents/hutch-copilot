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

from hutch_tools import log, paths


@pytest.fixture()
def logged(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    log.init("mfx", "e1", shift_date="2026-08-31", shift_start="08:00")
    return log.path("mfx", "e1")


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------
def test_init_writes_the_document_header(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
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
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    log.init("mfx", "e1", shift_date="2026-08-31",
             header_fields={"Sample": "Lyso, 10 mM", "Photon energy": "9500 eV"})
    text = log.path("mfx", "e1").read_text()
    assert "- Sample: Lyso, 10 mM" in text
    assert "- Photon energy: 9500 eV" in text


def test_appending_without_init_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
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
    log.section("mfx", "e1", "Data Collection")
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
# section
# --------------------------------------------------------------------------
def test_phase_section_gets_a_separator(logged):
    log.append("mfx", "e1", "an entry", at="09:00")
    log.section("mfx", "e1", "Data Collection")
    text = logged.read_text()
    assert "\n---\n\n## Data Collection\n" in text


def test_no_double_separator(logged):
    """The convention: never two rules in a row."""
    log.section("mfx", "e1", "First")
    log.section("mfx", "e1", "Second")
    assert "---\n\n---" not in logged.read_text()


@pytest.mark.parametrize("level,prefix", [(2, "## "), (3, "### "), (4, "#### ")])
def test_heading_levels(logged, level, prefix):
    log.section("mfx", "e1", "Topic", level=level)
    assert f"{prefix}Topic" in logged.read_text()


def test_sub_sections_get_no_separator(logged):
    log.append("mfx", "e1", "x", at="09:00")
    log.section("mfx", "e1", "A topic", level=3)
    text = logged.read_text()
    assert "### A topic" in text
    assert text.count("---") == 1          # only the one from init


def test_bad_heading_level_is_refused(logged):
    with pytest.raises(log.LogError, match="level must be 2, 3 or 4"):
        log.section("mfx", "e1", "Nope", level=5)


def test_untitled_section_is_refused(logged):
    with pytest.raises(log.LogError, match="untitled"):
        log.section("mfx", "e1", "  ")


# --------------------------------------------------------------------------
# tail
# --------------------------------------------------------------------------
def test_tail_reports_totals_and_the_last_lines(logged):
    for i in range(5):
        log.append("mfx", "e1", f"entry {i}", at="10:00")
    res = log.tail("mfx", "e1", n=3)
    assert res["total_lines"] > 5
    assert res["tail"][-1].endswith("entry 4")
    assert len(res["tail"]) == 3


def test_tail_on_a_missing_log_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    with pytest.raises(log.LogError, match="no log at"):
        log.tail("mfx", "e1")


# --------------------------------------------------------------------------
# concurrency -- several agents plus the operator write during a shift
# --------------------------------------------------------------------------
def _writer(args):
    root, text = args
    os.environ["HUTCH_DS_ROOT"] = str(root)
    import importlib
    from hutch_tools import log as lg
    from hutch_tools import paths as p
    importlib.reload(p)
    importlib.reload(lg)
    lg.append("mfx", "e1", text, at="12:00")


def test_concurrent_appends_lose_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
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
