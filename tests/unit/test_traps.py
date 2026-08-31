"""The incident register must actually fire on the configurations that caused it."""

from __future__ import annotations

import pytest

from hutchlib import lute, traps


def _ids(violations):
    return {v["id"] for v in violations}


# --------------------------------------------------------------------------
# Slurm
# --------------------------------------------------------------------------
def test_generated_sbatch_passes_every_sbatch_trap():
    text = lute.build_sbatch(
        task="CCTBXIndexer", config="/tmp/c.yaml", log_dir="/tmp/logs",
        lute_path="/tmp/lute", experiment="mfx101624926", run=36,
        time_limit="00:10:00", nodes=4, ntasks_per_node=60,
    )
    assert traps.check("sbatch", {"sbatch_text": text, "mpi": True}) == []


def test_time_limit_is_mandatory():
    bad = "#!/bin/bash\n#SBATCH --nodes=4\nsource psconda.sh\n"
    assert "no-time-limit" in _ids(traps.check("sbatch", {"sbatch_text": bad, "mpi": False}))


def test_pythonpath_before_psconda_is_caught():
    bad = (
        "#!/bin/bash\n#SBATCH --time=00:10:00\n"
        'export PYTHONPATH="/x:$PYTHONPATH"\nsource /a/psconda.sh\n'
    )
    assert "pythonpath-after-psconda" in _ids(
        traps.check("sbatch", {"sbatch_text": bad, "mpi": False})
    )


def test_missing_prte_tmpdir_is_caught_for_mpi_jobs_only():
    bad = "#!/bin/bash\n#SBATCH --time=00:10:00\nsource /a/psconda.sh\n"
    assert "prte-session-collision" in _ids(
        traps.check("sbatch", {"sbatch_text": bad, "mpi": True})
    )
    assert "prte-session-collision" not in _ids(
        traps.check("sbatch", {"sbatch_text": bad, "mpi": False})
    )


def test_swallowed_exit_code_is_caught():
    bad = (
        "#!/bin/bash\n#SBATCH --time=00:10:00\nsource /a/psconda.sh\n"
        "python -OB /x/run_task.py -c c.yaml -t CCTBXIndexer\n"
    )
    assert "exit-code-swallowed" in _ids(traps.check("sbatch", {"sbatch_text": bad, "mpi": False}))


def test_build_sbatch_refuses_to_emit_a_trapped_script(monkeypatch):
    # If someone edits the template badly, generation fails rather than
    # producing a script that dies ten minutes into a beamtime.
    monkeypatch.setattr(lute, "PSCONDA", "/a/psconda.sh")
    orig = lute.build_sbatch.__globals__["traps"].check
    monkeypatch.setattr(
        lute.build_sbatch.__globals__["traps"], "check",
        lambda scope, ctx: [{"id": "synthetic", "problem": "boom", "evidence": "test"}],
    )
    with pytest.raises(lute.LuteError, match="violates known traps"):
        lute.build_sbatch(task="T", config="c", log_dir="/l", lute_path="/p",
                          experiment="e", run=1, time_limit="00:10:00")
    monkeypatch.setattr(lute.build_sbatch.__globals__["traps"], "check", orig)


# --------------------------------------------------------------------------
# LUTE YAML
# --------------------------------------------------------------------------
GOOD_YAML = """
IndexCCTBXXFEL:
  phil_file: "{{ work_dir }}/r{{ run:04d }}/cctbx_index.phil"
ScaleCCTBXXFEL:
  phil_file: "{{ work_dir }}/r{{ run:04d }}/cctbx_scale.phil"
MergeCCTBXXFEL:
  phil_file: "{{ work_dir }}/merge_Lyso/v010/cctbx_merge.phil"
  phil_parameters:
    input_experiments_suffix: ".expt"
    input_reflections_suffix: ".refl"
    dispatch_step_list: >-
      input model_scaling statistics_unitcell group errors_merge merge
"""


def test_good_yaml_is_clean():
    assert traps.check("lute-yaml", {"yaml_text": GOOD_YAML}) == []


def test_parallel_a2a_is_caught():
    assert "parallel-a2a" in _ids(
        traps.check("lute-yaml", {"yaml_text": GOOD_YAML + "    parallel_a2a: true\n"})
    )


def test_commented_parallel_a2a_is_not_a_violation():
    assert "parallel-a2a" not in _ids(
        traps.check("lute-yaml", {"yaml_text": GOOD_YAML + "    # parallel_a2a: true\n"})
    )


def test_modify_in_merge_step_list_is_caught():
    bad = GOOD_YAML.replace("input model_scaling", "input modify model_scaling")
    assert "merge-modify-step" in _ids(traps.check("lute-yaml", {"yaml_text": bad}))


def test_missing_merge_suffix_is_caught():
    bad = GOOD_YAML.replace('input_experiments_suffix: ".expt"', "")
    assert "merge-suffix" in _ids(traps.check("lute-yaml", {"yaml_text": bad}))


def test_phil_file_without_run_variable_is_caught():
    bad = GOOD_YAML.replace('"{{ work_dir }}/r{{ run:04d }}/cctbx_index.phil"',
                            '"{{ work_dir }}/cctbx_index.phil"')
    assert "phil-file-race" in _ids(traps.check("lute-yaml", {"yaml_text": bad}))


def test_merge_version_padding_is_caught():
    bad = GOOD_YAML.replace("merge_Lyso/v010", "merge_Lyso/v10")
    assert "merge-version-padding" in _ids(traps.check("lute-yaml", {"yaml_text": bad}))


# --------------------------------------------------------------------------
# Silent-drop guard
# --------------------------------------------------------------------------
TEMPLATE = """
dispatch.hit_finder.minimum_number_of_reflections = {{ hit_min }}
spotfinder.threshold.dispersion.sigma_background = {{ sigma_bkgnd }}
radial_average.enable = {{ radial_average_enable }}
"""


def test_template_whitelist_extraction():
    assert traps.template_whitelist(TEMPLATE) == {
        "hit_min", "sigma_bkgnd", "radial_average_enable"
    }


def test_unknown_phil_key_is_reported_rather_than_silently_dropped():
    ctx = {
        "whitelist": traps.template_whitelist(TEMPLATE),
        "overrides": {
            "IndexCCTBXXFEL.phil_parameters.sigma_bkgnd": 10,
            "IndexCCTBXXFEL.phil_parameters.max_events": 5000,  # does not exist
        },
    }
    violations = traps.check("phil-overrides", ctx)
    assert "phil-key-silently-dropped" in _ids(violations)
    assert "max_events" in violations[0]["problem"]


def test_known_phil_keys_pass():
    ctx = {
        "whitelist": traps.template_whitelist(TEMPLATE),
        "overrides": {"IndexCCTBXXFEL.phil_parameters.hit_min": 20},
    }
    assert traps.check("phil-overrides", ctx) == []


# --------------------------------------------------------------------------
# Run params
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rt", ["DARK", "GEOM", "DATA"])
def test_uppercase_run_types_pass(rt):
    assert traps.check("run-params", {"run_type": rt}) == []


@pytest.mark.parametrize("rt", ["data", "Dark", "geom", "science"])
def test_lowercase_run_type_is_caught(rt):
    assert "run-type-case" in _ids(traps.check("run-params", {"run_type": rt}))


def test_every_registered_trap_carries_evidence():
    for entry in traps.registry():
        assert entry["evidence"].strip(), entry["id"]
        assert entry["what"].strip(), entry["id"]
