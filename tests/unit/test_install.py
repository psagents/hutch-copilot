"""LUTE install: the two bugs the port surfaced, plus the parts that were untestable.

`install_lute.py` was 511 lines with no tests, because `sys.exit()` inside its
library functions made them unusable from anything but the command line.
"""

from __future__ import annotations

import pathlib

import pytest

from hutch_tools import common as paths, lute


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
    out, changed = lute.patch_slurm_params(DAG, partition="milano", account="lcls:e1")
    assert changed == 2
    assert out.count("--account=lcls:e1") == 2
    assert out.count("--partition=milano") == 2
    assert "--nodes=16 --ntasks-per-node=50" in out


def test_patching_twice_is_a_no_op():
    """The original appended unconditionally -- while its docstring told you to re-run."""
    once, _ = lute.patch_slurm_params(DAG, partition="milano", account="lcls:e1")
    twice, _ = lute.patch_slurm_params(once, partition="milano", account="lcls:e1")
    assert once == twice
    assert twice.count("--account=") == 2


def test_patching_repairs_a_dag_a_previous_version_doubled():
    doubled = (
        "slurm_params: '--nodes=4 --account=lcls:old --partition=milano "
        "--account=lcls:old --partition=milano'\n"
    )
    out, changed = lute.patch_slurm_params(doubled, partition="roma", account="lcls:new")
    assert changed == 1
    assert out.count("--account=") == 1
    assert "--account=lcls:new" in out and "--partition=roma" in out
    assert "lcls:old" not in out
    assert "--nodes=4" in out          # the caller's own parameters survive


def test_patching_leaves_other_parameters_alone():
    line = "slurm_params: '--nodes=4 --exclusive --mem=0 --time=00:10:00'\n"
    out, _ = lute.patch_slurm_params(line, partition="milano", account="lcls:e1")
    for token in ("--nodes=4", "--exclusive", "--mem=0", "--time=00:10:00"):
        assert token in out


def test_a_dag_with_no_slurm_params_reports_zero():
    out, changed = lute.patch_slurm_params("task_name: X\n", partition="p", account="a")
    assert changed == 0 and out == "task_name: X\n"


# --------------------------------------------------------------------------
# Bug 2: preconditions were checked after the destructive steps
# --------------------------------------------------------------------------
@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    out = tmp_path / "mfx/e1/results/lute_output"
    out.mkdir(parents=True)
    (out / "mfx_lute.yaml").write_text("title: x\n")
    (out / "sfx_cctbx.dag").write_text(DAG)
    return out


def test_plan_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert list(tmp_path.iterdir()) == []


def test_plan_reports_every_precondition(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    names = [c["check"] for c in p["checks"]]
    assert "config exists" in names
    assert "dag exists: sfx_cctbx" in names
    assert any("kerberos" in n for n in names)
    assert next(c for c in p["checks"] if c["check"] == "config exists")["ok"] is True


def test_missing_dag_is_blocking(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["absent_wf"])
    assert "dag exists: absent_wf" in p["blocking"]


def test_missing_ticket_is_not_blocking_for_the_local_steps(workspace):
    """A ticket is only needed to register; it must not gate workspace or dags."""
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert not any("kerberos" in b for b in p["blocking"])


def test_all_refuses_before_mutating_when_preconditions_fail(workspace, monkeypatch):
    # Routed through hutch-lute since M1.b; there is no hutch-install binary.
    from hutch_tools import lute
    rc = lute.main(["all", "-e", "e1", "--hutch", "mfx", "-W", "absent_wf",
                    "--confirm", "write"])
    assert rc != 0
    assert not (workspace.parent / "lute_envs").exists()   # nothing was destroyed


def test_envs_refuses_without_confirmation(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    with pytest.raises(lute.InstallError, match="re-run with --confirm write"):
        lute.create_venvs(p, confirm=None)


def test_register_refuses_without_confirmation(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    with pytest.raises(lute.InstallError, match="writes to the eLog"):
        lute.register(p, confirm=None)


def test_plan_names_the_destructive_step_when_envs_already_exist(workspace, monkeypatch):
    monkeypatch.setattr(lute, "_internet", lambda: None)
    (workspace.parent / "lute_envs").mkdir()
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"], lute="pip")
    assert p["envs_dir_exists"] is True
    assert "deletes" in p["destructive"]


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# --lute: which LUTE a workflow runs on
# --------------------------------------------------------------------------
@pytest.fixture()
def built_lute(tmp_path):
    bin_ = tmp_path / "lute/install/bin"
    bin_.mkdir(parents=True)
    for name in ("launch_slurm", "submit_launch_slurm.sh", "submit_slurm", "run_task"):
        (bin_ / name).write_text("#!/bin/sh\n")
    return tmp_path / "lute"


@pytest.fixture()
def central(tmp_path, monkeypatch):
    """A central tree shaped like /sdf/group/lcls/ds/tools/lute/dev."""
    top = tmp_path / "central/dev"
    bin_ = top / "lute_env_py39/bin"
    bin_.mkdir(parents=True)
    for name in ("launch_slurm", "submit_launch_slurm.sh", "submit_slurm", "activate"):
        (bin_ / name).write_text("#!/bin/sh\n")
    (top / "lute_env_py39/lib/python3.9/site-packages/lute_lcls-0.4.0+dev.dist-info").mkdir(
        parents=True)
    monkeypatch.setenv("HUTCH_LUTE_CENTRAL", str(tmp_path / "central"))
    return bin_


def test_central_dev_is_the_default_and_needs_no_build(workspace, central, monkeypatch):
    monkeypatch.setattr(lute, "_internet", lambda: pytest.fail("central must not probe"))
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert p["lute"]["kind"] == "central" and p["lute"]["version"] == "0.4.0+dev"
    assert p["arp_executable"] == str(central / "submit_launch_slurm.sh")
    assert p["blocking"] == [] and p["destructive"] is None


def test_lute_install_supplies_the_executables_and_skips_envs(workspace, built_lute, monkeypatch):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"],
                  lute=f"install:{built_lute}")
    assert p["arp_executable"] == str(built_lute / "install/bin/submit_launch_slurm.sh")
    assert p["launch_executable"] == str(built_lute / "install/bin/launch_slurm")
    assert p["destructive"] is None and p["blocking"] == []
    assert p["elog_payloads"][0]["executable"] == p["arp_executable"]

    calls = []
    monkeypatch.setattr(lute, "create_venvs", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(lute, "setup_workspace", lambda p: {})
    monkeypatch.setattr(lute, "register", lambda *a, **k: {"registered": [], "failed": []})
    rc = lute.main(["all", "-e", "e1", "--hutch", "mfx", "-W", "sfx_cctbx",
                    "--lute-install", str(built_lute), "--reservation", "none",
                    "--confirm", "write"])
    assert rc == 0 and calls == []


def test_an_unbuilt_lute_install_is_blocking(workspace, tmp_path):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"],
                  lute=f"install:{tmp_path / 'never_built'}")
    assert "lute built" in p["blocking"]


@pytest.mark.parametrize("spec", ["clone:LouConreux/lute@ENH/x", "pip:0.4.0"])
def test_no_internet_blocks_clone_and_pip_without_waiting(workspace, monkeypatch, spec):
    monkeypatch.setattr(lute, "_internet", lambda: "https://github.com unreachable")
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"], lute=spec)
    assert any(b.startswith("internet") for b in p["blocking"])


def test_a_bad_lute_spec_is_refused(workspace):
    with pytest.raises(lute.InstallError, match="kind must be"):
        lute.plan(experiment="e1", hutch="mfx", lute="conda:x")


@pytest.mark.parametrize("spec", ["START_OF_RUN", "END_OF_RUN", "MANUAL",
                                 "start_of_run"])
def test_simple_triggers(spec):
    assert lute.parse_trigger(spec) == {"trigger": spec.upper()}


def test_run_param_trigger():
    assert lute.parse_trigger("RUN_PARAM_IS_VALUE:SmallData:done") == {
        "trigger": "RUN_PARAM_IS_VALUE",
        "run_param_name": "SmallData", "run_param_value": "done",
    }


@pytest.mark.parametrize("spec,match", [
    ("END_OF_RUN:extra", "unexpected fields"),
    ("RUN_PARAM_IS_VALUE:only_one", "requires RUN_PARAM_IS_VALUE"),
    ("NONSENSE", "unknown trigger type"),
])
def test_bad_triggers_are_refused(spec, match):
    with pytest.raises(lute.InstallError, match=match):
        lute.parse_trigger(spec)


def test_trigger_defaults_per_workflow(workspace):
    p = lute.plan(experiment="e1", hutch="mfx",
                     workflows=["a", "b"], triggers=["MANUAL"])
    assert p["workflows"][0]["trigger"] == "MANUAL"
    assert p["workflows"][1]["trigger"] == lute.DEFAULT_TRIGGER


# --------------------------------------------------------------------------
# Paths and payloads
# --------------------------------------------------------------------------
def test_results_path_comes_from_paths_not_an_fstring(tmp_path, monkeypatch):
    """The original built /sdf/data/lcls/ds/... by hand."""
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    p = lute.plan(experiment="e1", hutch="mfx", workflows=[])
    assert p["results_dir"] == str(tmp_path / "mfx/e1/results")


def test_optional_subdirectory_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    p = lute.plan(experiment="e1", hutch="mfx", directory="lconreux", workflows=[])
    assert p["lute_output_dir"].endswith("results/lconreux/lute_output")


def test_hutch_defaults_to_the_experiment_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    assert lute.plan(experiment="mfxl1013621", workflows=[])["hutch"] == "mfx"


def test_account_defaults_to_the_experiment(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    assert lute.plan(experiment="e1", hutch="mfx", workflows=[])["account"] == "lcls:e1"


def test_payload_shape(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"],
                     triggers=["MANUAL"])
    payload = p["elog_payloads"][0]
    assert payload["name"] == "lute_sfx_cctbx"
    assert payload["location"] == "S3DF"
    assert payload["trigger"] == "MANUAL"
    assert payload["executable"].endswith("lute_env_py39/bin/submit_launch_slurm.sh")
    assert "-c " in payload["parameters"] and "-W " in payload["parameters"]


def test_debug_flag_reaches_the_payload(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"], debug=True)
    assert p["elog_payloads"][0]["parameters"].endswith("--debug")


# --------------------------------------------------------------------------
# Workspace and dag steps
# --------------------------------------------------------------------------
def test_workspace_is_idempotent(workspace, monkeypatch):
    monkeypatch.setattr(lute, "grant_group", lambda *a, **k: {"ok": True})
    p = lute.plan(experiment="e1", hutch="mfx", workflows=[])
    first = lute.setup_workspace(p)
    second = lute.setup_workspace(p)
    assert first["db_created"] is True
    assert second["db_created"] is False
    assert pathlib.Path(first["db"]).exists()


def test_patch_dags_reports_no_change_on_a_second_run(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"])
    assert lute.patch_dags(p)["sfx_cctbx"]["modified"] is True
    assert lute.patch_dags(p)["sfx_cctbx"]["modified"] is False


def test_patch_dags_refuses_a_dag_without_resource_specs(workspace):
    (workspace / "bare.dag").write_text("task_name: X\n")
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["bare"])
    with pytest.raises(lute.InstallError, match="missing its resource"):
        lute.patch_dags(p)


def test_patch_dags_refuses_a_missing_dag(workspace):
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["nope"])
    with pytest.raises(lute.InstallError, match="DAG not found"):
        lute.patch_dags(p)


# --------------------------------------------------------------------------
# Kerberos: needed for the eLog, and for nothing else
# --------------------------------------------------------------------------
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
    """A ticket is only needed for eLog interaction.

    Making it an unconditional gate before anything proceeds would block a
    pipeline that resolves its inputs from explicit paths -- which is how the
    reference campaign ran all ten iterations, with no ticket on the host.
    """
    needed, why = lute.needs_kerberos(PATHED)
    assert needed is False
    assert "no eLog interaction" in why


def test_the_two_ticket_checks_are_not_the_same_question(monkeypatch):
    """`have_ticket` asks whether any ticket exists; `elog_ticket` asks for one at
    FILE:$HOME/krb5cc.ticket, which is what register() sets KRB5CCNAME to. A
    ticket in the ambient cache does not satisfy registration."""
    monkeypatch.setenv("HOME", "/nonexistent")
    ok, ticket = lute.elog_ticket()
    assert ok is False
    assert ticket.startswith("FILE:/nonexistent")


def test_elog_ticket_asks_klist_for_validity_not_existence(monkeypatch):
    """`klist -c` exits 0 on an expired ticket; only `-s` checks it is still valid."""
    seen = []
    monkeypatch.setattr(lute.subprocess, "run", lambda cmd, **k: seen.append(cmd) or
                        type("P", (), {"returncode": 1})())
    assert lute.elog_ticket()[0] is False
    assert "-s" in seen[0]


# --------------------------------------------------------------------------
# The generated sbatch script
#
# These replace the `traps.check("sbatch", ...)` self-check that build_sbatch used
# to run over its own output. That check was dropped with traps.py, which left the
# template with no coverage at all -- the fixes below are the whole reason this
# function exists instead of `submit_slurm.py`, so they are pinned here.
# --------------------------------------------------------------------------
import re as _re

SBATCH_KW = dict(task="index", config="/w/c.yaml", log_dir="/w/logs",
                 lute_path="/w/lute", experiment="e1", run=36, time_limit="00:30:00")


def test_sbatch_sets_an_explicit_wall_clock():
    """LUTE's submit_slurm.py injects only --output/--error, so a job without this
    inherits the partition default (24 h on milano)."""
    assert _re.search(r"^#SBATCH --time=00:30:00$", lute.build_sbatch(**SBATCH_KW), _re.M)


def test_psconda_is_sourced_before_pythonpath_is_set():
    """psconda.sh overwrites PYTHONPATH, so a repo-checkout LUTE cannot be imported
    if the order is reversed -- `import lute` silently resolves to the installed
    package instead."""
    text = lute.build_sbatch(**SBATCH_KW)
    assert text.index("psconda.sh") < text.index("PYTHONPATH=")


def test_mpi_jobs_get_a_per_job_prrte_session_dir():
    """LUTE sets PRTE_MCA_* and a hostfile, which fixes resource discovery, but no
    prte_tmpdir_base -- so two MPI jobs sharing a node collide in the default
    session dir and die at daemon launch."""
    text = lute.build_sbatch(**SBATCH_KW)
    assert "prte_tmpdir_base" in text
    assert "/tmp/prte_${SLURM_JOB_ID}" in text
    # The mkdir must run on every node: /tmp is node-local, so a base created only
    # on the batch node leaves the remote daemons with nowhere to put theirs.
    assert "srun --ntasks=${SLURM_NNODES}" in text


def test_no_mpi_omits_the_prrte_setup():
    assert "prte_tmpdir_base" not in lute.build_sbatch(**SBATCH_KW, mpi=False)


def test_the_task_exit_code_is_the_job_exit_code():
    """LUTE's executor.py exits -1 on FAILED/TIMEDOUT/CANCELLED/UNRESPONSIVE, so RC
    already reflects Task failure. The script must pass it through, not swallow it
    and not re-derive it by grepping the log."""
    text = lute.build_sbatch(**SBATCH_KW)
    assert text.rstrip().endswith("exit ${RC}")
    assert "Task failed with return code" not in text


# --------------------------------------------------------------------------
# db | runs | merge-version
# --------------------------------------------------------------------------
@pytest.fixture()
def lute_db(tmp_path):
    """The four tables the query joins, in the shape LUTE v2 writes them."""
    import sqlite3
    db = tmp_path / "lute.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE tasks (id integer PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE config (id integer PRIMARY KEY, run TEXT);
            CREATE TABLE results (id integer PRIMARY KEY, status INTEGER);
            CREATE TABLE executions (id integer PRIMARY KEY, task_id INTEGER,
                config_id INTEGER, result_id INTEGER, timestamp DATETIME);
            INSERT INTO tasks VALUES (1, 'ScaleCCTBXXFEL');
            INSERT INTO config VALUES (1, '36'), (2, '37');
            INSERT INTO results VALUES (1, 'TaskStatus.FAILED'), (2, 'TaskStatus.COMPLETED');
            INSERT INTO executions VALUES
                (1, 1, 1, 1, '2026-08-10 10:00:00'), (2, 1, 1, 2, '2026-08-10 11:00:00'),
                (3, 1, 2, 1, '2026-08-10 12:00:00');
        """)
    return str(db)


def test_db_reports_the_latest_status_per_run(lute_db):
    r = lute.db_rows(lute_db, "ScaleCCTBXXFEL")
    assert r["runs"]["36"]["status"] == "COMPLETED"      # the retry wins over the failure
    assert r["runs"]["37"]["status"] == "FAILED" and r["all_completed"] is False


def test_db_names_runs_that_never_ran(lute_db):
    r = lute.db_rows(lute_db, "ScaleCCTBXXFEL", runs=[36, 40])
    assert r["runs"]["40"]["status"] == "ABSENT" and r["all_completed"] is False
    assert lute.db_rows(lute_db, "ScaleCCTBXXFEL", runs=[36])["all_completed"] is True


def test_db_since_drops_older_rows(lute_db):
    r = lute.db_rows(lute_db, "ScaleCCTBXXFEL", since="2026-08-10T11:30:00")
    assert list(r["runs"]) == ["37"]


def test_runs_refuses_a_sample_name_that_matches_everything():
    with pytest.raises(lute.LuteError, match="matches every run"):
        lute.elog_runs("e1", sample="All Samples")


def test_merge_version_counts_up_and_creates(tmp_path):
    (tmp_path / "merge_Lyso/v001").mkdir(parents=True)
    (tmp_path / "merge_Lyso/v003").mkdir()
    r = lute.merge_version(str(tmp_path), "Lyso", create=True)
    assert r["existing"] == ["v001", "v003"] and r["next"] == "v004"
    assert (tmp_path / "merge_Lyso/v004").is_dir()
    assert lute.merge_version(str(tmp_path / "new"), "Lyso")["next"] == "v001"


def test_workspace_refuses_when_the_group_cannot_be_granted(workspace):
    """e1 is no real group: setfacl fails, and that must block, not pass silently."""
    p = lute.plan(experiment="e1", hutch="mfx", workflows=[])
    with pytest.raises(lute.InstallError, match="could not be given access"):
        lute.setup_workspace(p)


def test_reservation_reaches_dags_and_payloads(workspace):
    out, _ = lute.patch_slurm_params(DAG, partition="milano", account="lcls:e1",
                                     reservation="lcls:onshift")
    again, _ = lute.patch_slurm_params(out, partition="milano", account="lcls:e1",
                                       reservation="lcls:onshift")
    assert again == out and out.count("--reservation=lcls:onshift") == 2
    p = lute.plan(experiment="e1", hutch="mfx", workflows=["sfx_cctbx"],
                  reservation="lcls:onshift")
    assert "--reservation=lcls:onshift" in p["elog_payloads"][0]["parameters"]


def test_experiment_reservation_matches_the_account_list(monkeypatch):
    monkeypatch.setattr(lute, "_slurm", lambda argv, **k: (
        "ReservationName=other Accounts=lcls:e10\n"
        "ReservationName=lcls:onshift Accounts=lcls:x1@milano,lcls:e1@milano\n"))
    assert lute.experiment_reservation("e1") == "lcls:onshift"
    assert lute.experiment_reservation("e2") is None


# --------------------------------------------------------------------------
# run-task / run-workflow
# --------------------------------------------------------------------------
@pytest.fixture()
def cfg(tmp_path):
    c = tmp_path / "ws/lute_output/mfx_lute.yaml"
    c.parent.mkdir(parents=True)
    c.write_text(f"work_dir: {tmp_path / 'ws'}\n")
    return c


def test_run_task_uses_submit_slurm_from_work_dir(cfg, built_lute):
    r = lute.run_by_hand(experiment="e1", run=25, config=str(cfg), task="BayFAIOptimizer2",
                         lute=f"install:{built_lute}", reservation="lcls:onshift",
                         slurm=["--ntasks-per-node=64"])
    cmd = r["command"]
    assert r["submitted"] is False
    assert f"install/bin/activate_installation" in cmd
    assert f"cd {cfg.parent.parent} && " in cmd
    assert "submit_slurm --taskname BayFAIOptimizer2" in cmd and "-r 25" in cmd
    assert "--reservation=lcls:onshift" in cmd and "--ntasks-per-node=64" in cmd


def test_run_workflow_uses_the_eLog_launcher(cfg, central):
    r = lute.run_by_hand(experiment="e1", run=4, config=str(cfg), dag="/w/smd.dag")
    assert f"{central}/submit_launch_slurm.sh {central}/launch_slurm" in r["command"]
    assert "-W /w/smd.dag" in r["command"] and "--account=lcls:e1" in r["command"]
    assert f"source {central}/activate" in r["command"]


def test_run_by_hand_submits_through_slurm_and_reads_the_job_id(cfg, built_lute, monkeypatch):
    seen = []
    monkeypatch.setattr(lute, "_slurm", lambda argv, **k: seen.append(argv) or
                        "Using TCP\nSubmitted batch job 41716793\n")
    r = lute.run_by_hand(experiment="e1", run=25, config=str(cfg), task="T",
                         lute=f"install:{built_lute}", confirm="write")
    assert r["job_id"] == "41716793" and seen[0][:2] == ["bash", "-lc"]


def test_run_by_hand_refuses_an_unbuilt_lute(cfg, tmp_path):
    with pytest.raises(lute.LuteError, match="not a built LUTE"):
        lute.run_by_hand(experiment="e1", run=1, config=str(cfg), task="T",
                         lute=f"install:{tmp_path / 'nope'}")


def test_sbatch_runs_the_installs_entry_point(built_lute):
    text = lute.build_sbatch(task="T", config="/c.yaml", log_dir="/l", lute_path=str(built_lute),
                             experiment="e1", run=1, time_limit="00:10:00")
    assert f"{built_lute}/install/bin/run_task -c /c.yaml -t T" in text
    assert "run_task.py" not in text


# --------------------------------------------------------------------------
# Slurm routing: a hutch host's own cluster must never answer for S3DF
# --------------------------------------------------------------------------
def test_off_s3df_slurm_goes_over_ssh_to_psana(monkeypatch):
    monkeypatch.setattr(lute, "_on_s3df", lambda: False)
    calls = []
    monkeypatch.setattr(lute, "ssh", lambda host, cmd, **k: calls.append((host, cmd)) or "ok")
    assert lute._slurm(["sacct", "-j", "1"]) == "ok"
    assert calls == [("psana", "sacct -j 1")]


def test_unreachable_psana_raises_instead_of_reporting_no_job(monkeypatch):
    from hutch_tools.session import BridgeUnavailable

    def down(*a, **k):
        raise BridgeUnavailable("ssh to psana timed out")
    monkeypatch.setattr(lute, "_on_s3df", lambda: False)
    monkeypatch.setattr(lute, "ssh", down)
    with pytest.raises(lute.LuteError, match="via psana"):
        lute.job_status("41")


def test_grant_group_sets_default_acls_and_strips_world_write(tmp_path):
    import grp
    import os
    import shutil
    if not shutil.which("setfacl"):
        pytest.skip("no setfacl on this host")
    group = grp.getgrgid(os.getgid()).gr_name
    (tmp_path / "ws/lute_output").mkdir(parents=True)
    db = tmp_path / "ws/lute_output/lute.db"
    db.touch()
    db.chmod(0o666)
    (tmp_path / "ws").chmod(0o2777)
    res = paths.grant_group(group, rw=[tmp_path / "ws"])
    assert res["ok"], res
    assert db.stat().st_mode & 0o7777 == 0o775
    assert (tmp_path / "ws").stat().st_mode & 0o7777 == 0o2775
    import subprocess
    acl = subprocess.run(["getfacl", "-p", str(tmp_path / "ws/lute_output")],
                         capture_output=True, text=True).stdout
    assert f"default:group:{group}:rwx" in acl
