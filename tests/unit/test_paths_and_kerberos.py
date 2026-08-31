"""Path resolution and the Kerberos-scoping fix."""

from __future__ import annotations

import pathlib

from hutchlib import lute, paths


def test_skill_root_is_self_resolving():
    # SKILL.md and are-we-ready/SKILL.md hardcoded
    # /sdf/home/f/fpoitevi/.claude/skills/hutch-copilot in three places, which
    # broke the bridge setup and both readiness checks for every other user.
    root = paths.skill_root()
    assert (root / "hutchlib" / "paths.py").exists()
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
    assert paths.xtc2_dir("mfx", "e1") == tmp_path / "mfx/e1/xtc2"


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
    from hutchlib import resolvers
    pdb = tmp_path / "x.pdb"
    pdb.write_text(
        "CRYST1   77.050   77.050   37.210  90.00  90.00  90.00 P 43 21 2     8\n"
    )
    rec = resolvers.cell(str(pdb))
    assert rec["value"]["unit_cell"] == "77.05 77.05 37.21 90.0 90.0 90.0"
    assert rec["value"]["space_group"] == "P 43 21 2"
    assert rec["source"].startswith("resolved:CRYST1 ")
