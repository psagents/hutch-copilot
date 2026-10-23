"""Path resolution and the Kerberos-scoping fix."""

from __future__ import annotations

import pathlib

import pytest

from hutch_tools import common as paths


def test_skill_root_is_self_resolving():
    # SKILL.md and are-we-ready/SKILL.md hardcoded
    # /sdf/home/f/fpoitevi/.claude/skills/hutch-copilot in three places, which
    # broke the bridge setup and both readiness checks for every other user.
    root = paths.skill_root()
    assert (root / "hutch_tools" / "common.py").exists()
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
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    assert paths.xtc2_dir("mfx", "e1") == tmp_path / "mfx/e1/xtc"


# --------------------------------------------------------------------------
# external_path -- bin/hutch-env owns every path outside this checkout
#
# The EPICS bin dir used to be written down twice, in daq.py and in
# bin/hutch-env, free to drift apart on the next EPICS upgrade. These pin the
# contract that replaced it: one declaration, read by both languages.
# --------------------------------------------------------------------------
#: Every name bin/hutch-env must declare. One that stops being declared is a
#: path some tool resolves to nothing.
DECLARED = [
    "EPICS_BIN", "HAPPI_DB", "PYTHON_APPS", "PCDS_PYTHON", "PSCONDA",
    "LUTE_PY39", "LUTE_PY311", "DAQ_CONFIG_DIR", "OPR_LOG_ROOT",
    "DS_ROOT", "SKILL_ROOTS",
]


@pytest.mark.parametrize("name", DECLARED)
def test_every_external_path_is_declared(name, monkeypatch):
    """With no environment at all, the value still comes from bin/hutch-env."""
    monkeypatch.delenv("HUTCH_" + name, raising=False)
    assert paths.external_path(name)


def test_the_environment_wins_over_the_file(monkeypatch):
    monkeypatch.setenv("HUTCH_EPICS_BIN", "/tmp/somewhere-else")
    assert paths.external_path("EPICS_BIN") == "/tmp/somewhere-else"


@pytest.mark.parametrize("name", ["DAQ_CONFIG_DIR", "OPR_LOG_ROOT"])
def test_a_templated_value_keeps_its_fields(monkeypatch, name):
    """`${VAR:-default}` cannot hold `{hutch}`: bash reads the template's
    closing brace as the end of its own expansion and yields
    `pds/{hutch/scripts}` with the brace moved. Hence the `if [ -z ]` guard.

    Both now also carry `{hutch[0]}` for the `/sdf/home/<letter>` level, which
    only resolves because the caller formats with a keyword.
    """
    monkeypatch.delenv("HUTCH_" + name, raising=False)
    value = paths.external_path(name)
    assert "{hutch[0]}" in value and "{hutch}" in value
    assert value.format(hutch="mfx").startswith("/sdf/home/m/mfxopr/daq/")


def test_an_undeclared_path_raises(monkeypatch):
    """Never a guessed default -- the bug this whole contract removes."""
    monkeypatch.delenv("HUTCH_NOT_A_REAL_PATH", raising=False)
    with pytest.raises(FileNotFoundError, match="HUTCH_NOT_A_REAL_PATH"):
        paths.external_path("NOT_A_REAL_PATH")


def test_xtc2_lives_in_xtc_not_xtc2(tmp_path, monkeypatch):
    """The directory is `xtc`; the `2` belongs to the file extension.

    This assertion said `xtc2` until Sep 2026, which locked in a path no
    experiment on disk has -- 175 mfx directories use `xtc/` and none use
    `xtc2/`. So `hutch-resolve xtc2` answered `present: false` for data that
    had landed, and the peds trap refused every DARK run on that evidence.
    """
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    assert paths.xtc2_dir("mfx", "e1").name == "xtc"


def test_only_xtc2_files_count_as_landed_data(tmp_path, monkeypatch):
    """`xtc/` also holds index/, md5/ and smalldata/ entries carrying the run
    number, so a bare `*r0004*` glob over-counts."""
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
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




def test_cell_string_matches_the_format_in_production_use(tmp_path):
    """Pin the unit_cell spelling.

    ask-lute's cctbx/validate.py greps for this string verbatim in the
    rendered YAML, so '90.0' vs '90' is the difference between passing and a
    spurious failure. setup-cctbx-pipeline's read_cryst1 emitted str(float); match that.
    """
    from hutch_tools import data as resolvers
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




