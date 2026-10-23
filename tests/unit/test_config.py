"""hutch-lute render: override surgery, silent-drop guard, validation."""

from __future__ import annotations

import json
import pathlib

import pytest

from hutch_tools import lute

TEMPLATE = """\
%YAML 1.3
---
title: "${{ experiment }} pipeline"
work_dir: ${{ work_dir }}
...
---
IndexCCTBXXFEL:
  # per-run phil avoids a race between parallel runs
  phil_file: "{{ work_dir }}/r{{ run:04d }}/cctbx_index.phil"
  phil_parameters:
    # tuned for MFX/jungfrau -- comment must survive an override
    dispatch_index: true
    spotfinder_sigma: 25.0

MergeCCTBXXFEL:
  phil_file: "{{ work_dir }}/merge/v010/cctbx_merge.phil"
  phil_parameters:
    input_experiments_suffix: ".expt"
    input_reflections_suffix: ".refl"
    tag: "Lyso"
    merging_d_min: 2.0
"""

INDEX_PHIL = """
dispatch { index = {{ dispatch_index }} }
{%- if spotfinder_sigma is defined and spotfinder_sigma is not none %}
spotfinder { sigma = {{ spotfinder_sigma }} }
{%- endif %}
"""

MERGE_PHIL = """
input {
  experiments_suffix = {{ input_experiments_suffix }}
  reflections_suffix = {{ input_reflections_suffix }}
  {%- if input_path is defined %}
  path = {{ input_path }}
  {%- endif %}
  {%- if tag is defined %}
  tag = {{ tag }}
  {%- endif %}
}
merging { d_min = {{ merging_d_min }} }
"""


@pytest.fixture()
def tpl(tmp_path):
    p = tmp_path / "pipeline.yaml.j2"
    p.write_text(TEMPLATE)
    return p


@pytest.fixture()
def phil_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "cctbx_index.phil").write_text(INDEX_PHIL)
    (d / "cctbx_merge.phil").write_text(MERGE_PHIL)
    return d


CTX = {"experiment": "mfx101624926", "work_dir": "/w"}


def test_renders_and_validates_clean(tpl, phil_dir):
    rep = lute.build(template=tpl, context=CTX, whitelist_dir=phil_dir)
    assert rep["documents"] == 2
    assert rep["tasks"] == ["IndexCCTBXXFEL", "MergeCCTBXXFEL"]
    assert rep["whitelist_checked"] is True


def test_override_replaces_and_reports_replaced(tpl, phil_dir):
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"IndexCCTBXXFEL.phil_parameters.spotfinder_sigma": 10.0},
    )
    assert rep["overrides"]["IndexCCTBXXFEL.phil_parameters.spotfinder_sigma"] == "replaced"
    assert "spotfinder_sigma: 10.0" in rep["text"]
    assert "spotfinder_sigma: 25.0" not in rep["text"]


def test_comments_survive_an_override(tpl, phil_dir):
    # The template's comments record hard-won choices; round-tripping through a
    # YAML parser would silently drop them.
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"IndexCCTBXXFEL.phil_parameters.dispatch_index": False},
    )
    assert "# tuned for MFX/jungfrau" in rep["text"]
    assert "# per-run phil avoids a race" in rep["text"]


def test_insert_is_reported_distinctly_from_replace(tpl, phil_dir):
    # An 'inserted' where you expected 'replaced' is how a misspelled key shows up.
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"MergeCCTBXXFEL.phil_parameters.input_path": ["/w/r0036/scale"]},
    )
    assert rep["overrides"]["MergeCCTBXXFEL.phil_parameters.input_path"] == "inserted"
    assert rep["inserted_keys"] == ["MergeCCTBXXFEL.phil_parameters.input_path"]


def test_delete_sentinel_removes_a_key(tpl, phil_dir):
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"MergeCCTBXXFEL.phil_parameters.tag": lute.DELETE},
    )
    assert rep["overrides"]["MergeCCTBXXFEL.phil_parameters.tag"] == "deleted"
    assert "tag:" not in rep["text"]


def test_list_values_render_as_a_block_sequence(tpl, phil_dir):
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"MergeCCTBXXFEL.phil_parameters.input_path": ["/a", "/b"]},
    )
    docs = lute.parse_documents(rep["text"])
    merge = next(d["MergeCCTBXXFEL"] for d in docs if "MergeCCTBXXFEL" in d)
    assert merge["phil_parameters"]["input_path"] == ["/a", "/b"]


def test_unknown_phil_key_is_refused_not_silently_dropped(tpl, phil_dir):
    """phil_parameters is pydantic extra='allow', so LUTE accepts any key and the
    fixed template then ignores what it does not reference. An unknown key is an
    invisible no-op, which is why this is an error rather than a warning."""
    with pytest.raises(lute.ConfigError) as exc:
        lute.build(
            template=tpl, context=CTX, whitelist_dir=phil_dir,
            overrides={"IndexCCTBXXFEL.phil_parameters.max_events": 5000},
        )
    assert "silently ignored" in str(exc.value)
    assert "max_events" in str(exc.value)


def test_known_phil_keys_pass(tpl, phil_dir):
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=phil_dir,
        overrides={"IndexCCTBXXFEL.phil_parameters.spotfinder_sigma": 10.0},
    )
    assert rep["whitelist_checked"] is True


def test_template_whitelist_scans_block_tags_too():
    """The real LUTE phil templates guard most values with
    `{% if x is defined %}`, so a {{ }}-only scan would under-report the legal
    key set and reject valid keys as unknown."""
    assert lute.template_whitelist(
        "dispatch.index = {{ dispatch_index }}\n"
        "{%- if spotfinder_sigma is defined and spotfinder_sigma is not none %}\n"
        "spotfinder.sigma = {{ spotfinder_sigma }}\n"
        "{%- endif %}\n"
    ) == {"dispatch_index", "spotfinder_sigma"}


def test_no_whitelist_dir_skips_the_check(tpl):
    rep = lute.build(
        template=tpl, context=CTX, whitelist_dir=None,
        overrides={"IndexCCTBXXFEL.phil_parameters.max_events": 5000},
    )
    assert rep["whitelist_checked"] is False


def test_unknown_task_names_the_ones_that_exist(tpl, phil_dir):
    with pytest.raises(lute.ConfigError, match="Present at top level"):
        lute.build(template=tpl, context=CTX, whitelist_dir=phil_dir,
                     overrides={"ScaleCCTBXXFEL.phil_parameters.x": 1})


def test_malformed_override_key_is_rejected(tpl, phil_dir):
    with pytest.raises(lute.ConfigError, match="TASK.SUBBLOCK.key"):
        lute.build(template=tpl, context=CTX, whitelist_dir=phil_dir,
                     overrides={"just_a_key": 1})


def test_phil_templates_are_discovered_from_the_config_itself(tpl, phil_dir):
    rep = lute.build(template=tpl, context=CTX, whitelist_dir=phil_dir)
    assert lute.phil_templates(rep["text"]) == {
        "IndexCCTBXXFEL": "cctbx_index.phil",
        "MergeCCTBXXFEL": "cctbx_merge.phil",
    }


def test_missing_phil_template_fails_loudly(tpl, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(lute.ConfigError, match="absent from"):
        lute.build(template=tpl, context=CTX, whitelist_dir=empty)


def test_missing_whitelist_dir_says_what_to_do(tpl, tmp_path):
    with pytest.raises(lute.ConfigError, match="--no-whitelist"):
        lute.build(template=tpl, context=CTX, whitelist_dir=tmp_path / "nope")


@pytest.mark.parametrize("raw,expected", [
    ("k=true", True), ("k=false", False), ("k=20", 20), ("k=2.2", 2.2),
    ('k="P 43 21 2"', "P 43 21 2"), ("k=suppressed", "suppressed"),
    ("k=[1,2]", [1, 2]), (f"k={lute.DELETE}", lute.DELETE),
])
def test_parse_set_types(raw, expected):
    assert lute.parse_set([raw])["k"] == expected


def test_parse_set_rejects_a_bare_key():
    with pytest.raises(lute.ConfigError, match="KEY=VALUE"):
        lute.parse_set(["nope"])


def test_campaign_params_file_shape_is_accepted(tmp_path):
    # params/iterNN.json wraps overrides under an "overrides" key; the CLI
    # unwraps it so the campaign's own files can be replayed unchanged.
    spec = {"label": "x", "d_min": 2.2,
            "overrides": {"IndexCCTBXXFEL.phil_parameters.dispatch_index": False}}
    p = tmp_path / "iter10.json"
    p.write_text(json.dumps(spec))
    loaded = json.loads(p.read_text())
    assert "overrides" in loaded and len(loaded["overrides"]) == 1


def test_model_only_keys_are_not_flagged(tpl, tmp_path):
    """tag never reaches cctbx_merge.phil -- LUTE resolves input_path from it."""
    d = tmp_path / "upstream"
    d.mkdir()
    (d / "cctbx_index.phil").write_text(INDEX_PHIL)
    (d / "cctbx_merge.phil").write_text(MERGE_PHIL.replace(
        "  {%- if tag is defined %}\n  tag = {{ tag }}\n  {%- endif %}\n", ""))
    assert "tag" not in (d / "cctbx_merge.phil").read_text()
    assert lute.build(template=tpl, context=CTX, whitelist_dir=d)["whitelist_checked"]
