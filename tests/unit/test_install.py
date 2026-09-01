"""LUTE install: the two bugs the port surfaced, plus the parts that were untestable.

`install_lute.py` was 511 lines with no tests, because `sys.exit()` inside its
library functions made them unusable from anything but the command line.
"""

from __future__ import annotations

import pathlib

import pytest

from hutch_tools import install, paths


# --------------------------------------------------------------------------
# Bug 1: DAG patching was not idempotent
# --------------------------------------------------------------------------
DAG = """\
!LUTE_DAG
task_name: "CCTBXIndexer"
slurm_params: '--nodes=16 --ntasks-per-node=50'
next:
- task_name: "CCTBXScaler"
  slurm_params: '--nodes=8 --ntasks-per-node=50'
  next: []
"""


def test_patch_sets_account_and_partition_on_every_line():
    out, changed = install.patch_slurm_params(DAG, partition="milano", account="lcls:e1")
    assert changed == 2
    assert out.count("--account=lcls:e1") == 2
    assert out.count("--partition=milano") == 2
    assert "--nodes=16 --ntasks-per-node=50" in out


def test_patching_twice_is_a_no_op():
    """The original appended unconditionally -- while its docstring told you to re-run."""
    once, _ = install.patch_slurm_params(DAG, partition="milano", account="lcls:e1")
    twice, _ = install.patch_slurm_params(once, partition="milano", account="lcls:e1")
    assert once == twice
    assert twice.count("--account=") == 2


def test_patching_repairs_a_dag_a_previous_version_doubled():
    doubled = (
        "slurm_params: '--nodes=4 --account=lcls:old --partition=milano "
        "--account=lcls:old --partition=milano'\n"
    )
    out, changed = install.patch_slurm_params(doubled, partition="roma", account="lcls:new")
    assert changed == 1
    assert out.count("--account=") == 1
    assert "--account=lcls:new" in out and "--partition=roma" in out
    assert "lcls:old" not in out
    assert "--nodes=4" in out          # the caller's own parameters survive


def test_patching_leaves_other_parameters_alone():
    line = "slurm_params: '--nodes=4 --exclusive --mem=0 --time=00:10:00'\n"
    out, _ = install.patch_slurm_params(line, partition="milano", account="lcls:e1")
    for token in ("--nodes=4", "--exclusive", "--mem=0", "--time=00:10:00"):
        assert token in out


def test_a_dag_with_no_slurm_params_reports_zero():
    out, changed = install.patch_slurm_params("task_name: X\n", partition="p", account="a")
    assert changed == 0 and out == "task_name: X\n"


# --------------------------------------------------------------------------
# Bug 2: preconditions were checked after the destructive steps
# --------------------------------------------------------------------------
@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    out = tmp_path / "mfx/e1/results/lute_output"
    out.mkdir(parents=True)
    (out / "mfx_lute.yaml").write_text("title: x\n")
    (out / "sfx_cctbx.dag").write_text(DAG)
    return out


def test_plan_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert list(tmp_path.iterdir()) == []


def test_plan_reports_every_precondition(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    names = [c["check"] for c in p["checks"]]
    assert "config exists" in names
    assert "dag exists: sfx_cctbx" in names
    assert any("kerberos" in n for n in names)
    assert next(c for c in p["checks"] if c["check"] == "config exists")["ok"] is True


def test_missing_dag_is_blocking(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["absent_wf"])
    assert "dag exists: absent_wf" in p["blocking"]


def test_missing_ticket_is_not_blocking_for_the_local_steps(workspace):
    """A ticket is only needed to register; it must not gate workspace or dags."""
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert not any("kerberos" in b for b in p["blocking"])


def test_all_refuses_before_mutating_when_preconditions_fail(workspace, monkeypatch):
    rc = install.main(["all", "-e", "e1", "--hutch", "mfx", "-W", "absent_wf",
                       "--confirm", "write"])
    assert rc != 0
    assert not (workspace.parent / "lute_envs").exists()   # nothing was destroyed


def test_envs_refuses_without_confirmation(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    with pytest.raises(install.InstallError, match="re-run with --confirm write"):
        install.create_venvs(p, confirm=None)


def test_register_refuses_without_confirmation(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    with pytest.raises(install.InstallError, match="writes to the eLog"):
        install.register(p, confirm=None)


def test_plan_names_the_destructive_step_when_envs_already_exist(workspace):
    (workspace.parent / "lute_envs").mkdir()
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert p["envs_dir_exists"] is True
    assert "deletes" in p["destructive"]


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("spec", ["START_OF_RUN", "END_OF_RUN", "MANUAL",
                                 "start_of_run"])
def test_simple_triggers(spec):
    assert install.parse_trigger(spec) == {"trigger": spec.upper()}


def test_run_param_trigger():
    assert install.parse_trigger("RUN_PARAM_IS_VALUE:SmallData:done") == {
        "trigger": "RUN_PARAM_IS_VALUE",
        "run_param_name": "SmallData", "run_param_value": "done",
    }


@pytest.mark.parametrize("spec,match", [
    ("END_OF_RUN:extra", "unexpected fields"),
    ("RUN_PARAM_IS_VALUE:only_one", "requires RUN_PARAM_IS_VALUE"),
    ("NONSENSE", "unknown trigger type"),
])
def test_bad_triggers_are_refused(spec, match):
    with pytest.raises(install.InstallError, match=match):
        install.parse_trigger(spec)


def test_trigger_defaults_per_workflow(workspace):
    p = install.plan(experiment="e1", hutch="mfx",
                     workflows=["a", "b"], triggers=["MANUAL"])
    assert p["workflows"][0]["trigger"] == "MANUAL"
    assert p["workflows"][1]["trigger"] == install.DEFAULT_TRIGGER


# --------------------------------------------------------------------------
# Paths and payloads
# --------------------------------------------------------------------------
def test_results_path_comes_from_paths_not_an_fstring(tmp_path, monkeypatch):
    """The original built /sdf/data/lcls/ds/... by hand."""
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    p = install.plan(experiment="e1", hutch="mfx", workflows=[])
    assert p["results_dir"] == str(tmp_path / "mfx/e1/results")


def test_optional_subdirectory_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    p = install.plan(experiment="e1", hutch="mfx", directory="lconreux", workflows=[])
    assert p["lute_output_dir"].endswith("results/lconreux/lute_output")


def test_hutch_defaults_to_the_experiment_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    assert install.plan(experiment="mfxl1013621", workflows=[])["hutch"] == "mfx"


def test_account_defaults_to_the_experiment(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DS_ROOT", tmp_path)
    assert install.plan(experiment="e1", hutch="mfx", workflows=[])["account"] == "lcls:e1"


def test_payload_shape(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"],
                     triggers=["MANUAL"])
    payload = p["elog_payloads"][0]
    assert payload["name"] == "lute_sfx_cctbx"
    assert payload["location"] == "S3DF"
    assert payload["trigger"] == "MANUAL"
    assert payload["executable"].endswith("lute_env_py39/bin/submit_launch_slurm.sh")
    assert "-c " in payload["parameters"] and "-W " in payload["parameters"]


def test_debug_flag_reaches_the_payload(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"], debug=True)
    assert p["elog_payloads"][0]["parameters"].endswith("--debug")


# --------------------------------------------------------------------------
# Workspace and dag steps
# --------------------------------------------------------------------------
def test_workspace_is_idempotent(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=[])
    first = install.setup_workspace(p)
    second = install.setup_workspace(p)
    assert first["db_created"] is True
    assert second["db_created"] is False
    assert pathlib.Path(first["db"]).exists()


def test_patch_dags_reports_no_change_on_a_second_run(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert install.patch_dags(p)["sfx_cctbx"]["modified"] is True
    assert install.patch_dags(p)["sfx_cctbx"]["modified"] is False


def test_patch_dags_refuses_a_dag_without_resource_specs(workspace):
    (workspace / "bare.dag").write_text("task_name: X\n")
    p = install.plan(experiment="e1", hutch="mfx", workflows=["bare"])
    with pytest.raises(install.InstallError, match="missing its resource"):
        install.patch_dags(p)


def test_patch_dags_refuses_a_missing_dag(workspace):
    p = install.plan(experiment="e1", hutch="mfx", workflows=["nope"])
    with pytest.raises(install.InstallError, match="DAG not found"):
        install.patch_dags(p)
