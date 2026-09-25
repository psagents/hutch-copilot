"""Path resolution and the Kerberos-scoping fix."""

from __future__ import annotations

import pathlib

from hutch_tools import lute, paths


def test_skill_root_is_self_resolving():
    # SKILL.md and are-we-ready/SKILL.md hardcoded
    # /sdf/home/f/fpoitevi/.claude/skills/hutch-copilot in three places, which
    # broke the bridge setup and both readiness checks for every other user.
    root = paths.skill_root()
    assert (root / "hutch_tools" / "paths.py").exists()
    assert (root / "SKILL.md").exists()


def test_script_lookup_resolves_a_shipped_script():
    assert paths.script("scripts", "oc_bridge.py").exists()


def test_script_lookup_fails_loudly():
    try:
        paths.script("scripts", "does_not_exist.py")
    except FileNotFoundError as exc:
        assert "does_not_exist" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_ds_root_is_overridable(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    assert paths.xtc2_dir("mfx", "e1") == tmp_path / "mfx/e1/xtc"


def test_xtc2_lives_in_xtc_not_xtc2(tmp_path, monkeypatch):
    """The directory is `xtc`; the `2` belongs to the file extension.

    This assertion said `xtc2` until Sep 2026, which locked in a path no
    experiment on disk has -- 175 mfx directories use `xtc/` and none use
    `xtc2/`. So `hutch-resolve xtc2` answered `present: false` for data that
    had landed, and the peds trap refused every DARK run on that evidence.
    """
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    assert paths.xtc2_dir("mfx", "e1").name == "xtc"


def test_only_xtc2_files_count_as_landed_data(tmp_path, monkeypatch):
    """`xtc/` also holds index/, md5/ and smalldata/ entries carrying the run
    number, so a bare `*r0004*` glob over-counts."""
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    d = tmp_path / "mfx" / "e1" / "xtc"
    d.mkdir(parents=True)
    (d / "e1-r0004-s001-c000.xtc2").write_bytes(b"x")
    (d / "e1-r0004-s001-c000.xtc2.md5").write_bytes(b"x")   # sibling, not data
    (d / "e1-r0005-s001-c000.xtc2").write_bytes(b"x")       # wrong run

    found = paths.xtc2_files("mfx", "e1", 4)

    assert [p.name for p in found] == ["e1-r0004-s001-c000.xtc2"]


TAGGED = """
MergeCCTBXXFEL:
  phil_parameters:
    tag: "Lyso"
"""

PATHED = """
MergeCCTBXXFEL:
  phil_parameters:
    input_path:
      - /some/scale/dir
"""


def test_elog_tag_needs_kerberos():
    needed, why = lute.needs_kerberos(TAGGED)
    assert needed is True
    assert "eLog tag" in why


def test_explicit_input_path_does_not_need_kerberos():
    # analyze-data/commands/setup.md makes a ticket an unconditional gate before
    # anything proceeds. It is only needed for eLog interaction -- the campaign
    # ran the full pipeline with no ticket on the host.
    needed, why = lute.needs_kerberos(PATHED)
    assert needed is False
    assert "no eLog interaction" in why


def test_cell_string_matches_the_format_in_production_use(tmp_path):
    """Pin the unit_cell spelling.

    setup-cctbx-pipeline's validation greps for this string verbatim in the
    rendered YAML, so '90.0' vs '90' is the difference between passing and a
    spurious failure. Its read_cryst1 emits str(float); match that.
    """
    from hutch_tools import resolvers
    pdb = tmp_path / "x.pdb"
    pdb.write_text(
        "CRYST1   77.050   77.050   37.210  90.00  90.00  90.00 P 43 21 2     8\n"
    )
    rec = resolvers.cell(str(pdb))
    assert rec["value"]["unit_cell"] == "77.05 77.05 37.21 90.0 90.0 90.0"
    assert rec["value"]["space_group"] == "P 43 21 2"
    assert rec["source"].startswith("resolved:CRYST1 ")


RUN_PATHS = """%YAML 1.3
---
work_dir: {WD}
...
---
IndexCCTBXXFEL:
  phil_file: "{{ work_dir }}/r{{ run:04d }}/cctbx_index.phil"
  in_file: "{{ work_dir }}/r{{ run:04d }}/data_e_{{ run }}.loc"
  phil_parameters:
    output_output_dir: "{{ work_dir }}/r{{ run:04d }}/index"
    output_logging_dir: "{{ work_dir }}/r{{ run:04d }}/index/logs"
"""


def test_ensure_run_dirs_creates_the_dirs_lute_writes_into(tmp_path):
    """LUTE writes in_file/phil_file while *parsing* the config, not when the
    Task runs, and never mkdirs their parent. Iteration 2 of the campaign died
    with FileNotFoundError on r0036/data_mfx101624926_36.loc before the indexer
    started, because a fresh per-iteration work_dir has no run subdirectory.
    """
    cfg = tmp_path / "c.yaml"
    cfg.write_text(RUN_PATHS.replace("{WD}", str(tmp_path)))

    made = lute.ensure_run_dirs(str(cfg), 36)

    assert (tmp_path / "r0036").is_dir()            # in_file / phil_file parent
    assert (tmp_path / "r0036" / "index" / "logs").is_dir()
    assert str(tmp_path / "r0036") in made


def test_ensure_run_dirs_is_idempotent(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(RUN_PATHS.replace("{WD}", str(tmp_path)))
    lute.ensure_run_dirs(str(cfg), 36)
    # second call reports nothing new -- callers surface this list, so a
    # re-submission must not look like it rebuilt the workspace.
    assert lute.ensure_run_dirs(str(cfg), 36) == []


def test_ensure_run_dirs_needs_a_work_dir():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("IndexCCTBXXFEL:\n  phil_file: \"/tmp/x.phil\"\n")
        name = f.name
    try:
        lute.ensure_run_dirs(name, 36)
    except lute.LuteError as exc:
        assert "work_dir" in str(exc)
    else:
        raise AssertionError("expected LuteError when work_dir is absent")
