#!/usr/bin/env python3
"""
cctbx-sfx-workflow TUI: operator dashboard for the tag-based CCTBX SFX pipeline.

Tabs:
  Setup    - gather pipeline inputs (PDB, geometry, mask, d_min, detector),
             run render.py (from the setup-cctbx-pipeline skill).
  LUTE     - clone/build LUTE in work_dir only if it isn't already there.
  Kerberos - show ticket validity, refresh via a real kinit prompt.
  Watcher  - start/restart/cancel the autonomous watcher, view live state
             and merge history, tail its log, approve d_min changes.

Usage:
  python3 tui.py --output-dir /path/to/results_dir
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Static,
    TabbedContent,
    TabPane,
)

SKILL_DIR = Path(__file__).resolve().parent
SETUP_DIR = Path.home() / ".claude" / "skills" / "setup-cctbx-pipeline"
RENDER_PY = SETUP_DIR / "render.py"
ITERATION_LOG_PY = SKILL_DIR / "iteration_log.py"
KRB5_CCACHE = Path.home() / "krb5cc.ticket"


# -----------------------------------------------------------------------------
# Plain helpers (no Textual dependency) — kept separate so they're testable
# and reusable from worker coroutines without touching widgets directly.
# -----------------------------------------------------------------------------
def state_path(output_dir: Path) -> Path:
    return output_dir / "workflow_state.json"


def load_state(output_dir: Path) -> Optional[dict]:
    p = state_path(output_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def sidecar_path(output_dir: Path) -> Path:
    return output_dir / ".tui_watcher.json"


def load_sidecar(output_dir: Path) -> dict:
    p = sidecar_path(output_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_sidecar(output_dir: Path, data: dict) -> None:
    sidecar_path(output_dir).write_text(json.dumps(data, indent=2))


def klist_status() -> dict:
    """Check the FILE ccache the pipeline scripts expect (not the default cache)."""
    env = os.environ.copy()
    env["KRB5CCNAME"] = f"FILE:{KRB5_CCACHE}"
    try:
        r = subprocess.run(
            ["klist"], capture_output=True, text=True, env=env, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"valid": False, "detail": str(exc)}
    if r.returncode != 0:
        return {"valid": False, "detail": r.stderr.strip() or "no ticket"}
    principal = None
    expires = None
    for line in r.stdout.splitlines():
        if line.startswith("Default principal:"):
            principal = line.split(":", 1)[1].strip()
        m = re.match(
            r"^\S+\s+\S+\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})\s+krbtgt/", line
        )
        if m:
            expires = m.group(1)
    if expires:
        try:
            exp_dt = datetime.strptime(expires, "%m/%d/%Y %H:%M:%S")
            valid = exp_dt > datetime.now()
        except ValueError:
            valid = True  # klist already returned 0; trust it over our own parse
    else:
        valid = True
    return {"valid": valid, "principal": principal, "expires": expires}


def squeue_status(job_id: Optional[str]) -> Optional[str]:
    if not job_id:
        return None
    try:
        r = subprocess.run(
            ["squeue", "-j", str(job_id), "-h", "-o", "%T"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return out or None


def lute_status(work_dir: Path) -> dict:
    lute_dir = work_dir / "lute"
    cloned = lute_dir.exists()
    built = (lute_dir / "install" / "bin" / "launch_slurm").exists()
    is_link = lute_dir.is_symlink()
    return {"cloned": cloned, "built": built, "is_symlink": is_link, "path": lute_dir}


async def run_streamed(cmd: list[str], log: Log, cwd: Optional[Path] = None) -> int:
    """Run cmd, writing each line of combined stdout/stderr to `log` as it arrives."""
    log.write_line(f"$ {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        log.write_line(raw_line.decode(errors="replace").rstrip())
    return await proc.wait()


# -----------------------------------------------------------------------------
# Setup tab
# -----------------------------------------------------------------------------
class SetupPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("Pipeline setup (setup-cctbx-pipeline)", classes="title")
        with Horizontal(classes="row"):
            yield Label("Experiment:", classes="field-label")
            yield Input(placeholder="mfx101624926", id="inp_experiment")
        with Horizontal(classes="row"):
            yield Label("Tag:", classes="field-label")
            yield Input(placeholder="Lyso", id="inp_tag")
        with Horizontal(classes="row"):
            yield Label("Work dir:", classes="field-label")
            yield Input(placeholder="(defaults to --output-dir)", id="inp_workdir")
        with Horizontal(classes="row"):
            yield Label("PDB file:", classes="field-label")
            yield Input(placeholder="/path/to/reference.pdb", id="inp_pdb")
        with Horizontal(classes="row"):
            yield Label("Geometry (.expt):", classes="field-label")
            yield Input(placeholder="/path/to/refined.expt", id="inp_geometry")
        with Horizontal(classes="row"):
            yield Label("Mask file:", classes="field-label")
            yield Input(placeholder="/path/to/mask.mask", id="inp_mask")
        with Horizontal(classes="row"):
            yield Label("Starting d_min (Å):", classes="field-label")
            yield Input(placeholder="1.8", id="inp_dmin")
        with Horizontal(classes="row"):
            yield Label("Detector address:", classes="field-label")
            yield Input(placeholder="jungfrau", id="inp_detector")
            yield Button("Auto-detect (psana2)", id="btn_detect")
        with Horizontal(classes="row"):
            yield Label("Probe run # (for auto-detect / initial template):", classes="field-label")
            yield Input(placeholder="36", id="inp_proberun")
        yield Static(
            "Unit cell and space group are read directly from the PDB's CRYST1 "
            "record by render.py — not entered here.",
            classes="hint",
        )
        with Horizontal(classes="row"):
            yield Button("Generate pipeline config", id="btn_generate", variant="primary")
        yield Log(id="setup_log", highlight=True)


# -----------------------------------------------------------------------------
# LUTE tab
# -----------------------------------------------------------------------------
class LutePane(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("LUTE install (tag_based_multirun_workflows branch)", classes="title")
        yield Static("", id="lute_status")
        with Horizontal(classes="row"):
            yield Button("Refresh status", id="btn_lute_refresh")
            yield Button("Clone + build LUTE", id="btn_lute_build", variant="primary")
        yield Static(
            "Only clones if {work_dir}/lute does not already exist. "
            "Existing installs are never overwritten.",
            classes="hint",
        )
        yield Log(id="lute_log", highlight=True)


# -----------------------------------------------------------------------------
# Kerberos tab
# -----------------------------------------------------------------------------
class KerberosPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("Kerberos ticket (FILE:$HOME/krb5cc.ticket)", classes="title")
        yield Static("", id="krb_status")
        with Horizontal(classes="row"):
            yield Button("Refresh status", id="btn_krb_refresh")
            yield Button("Renew ticket (kinit)", id="btn_krb_kinit", variant="primary")
        yield Static(
            "Renewing suspends this TUI and hands the terminal to kinit so your "
            "password never passes through this app.",
            classes="hint",
        )


# -----------------------------------------------------------------------------
# Watcher tab
# -----------------------------------------------------------------------------
class WatcherPane(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("Autonomous watcher", classes="title")
        yield Static("", id="watcher_state")
        yield Static("", id="watcher_dmin_banner")
        with Horizontal(classes="row"):
            yield Button("Start / restart watcher", id="btn_watch_start", variant="primary")
            yield Button("Cancel watcher", id="btn_watch_cancel", variant="error")
            yield Button("Approve pending d_min", id="btn_approve_dmin")
        yield Label("Iteration history", classes="subtitle")
        yield DataTable(id="history_table")
        yield Label("Live log (auto-refreshing)", classes="subtitle")
        yield Log(id="watcher_log", highlight=True)


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
class CctbxTUI(App):
    CSS = """
    .title { text-style: bold; color: $accent; margin-top: 1; }
    .subtitle { text-style: bold; margin-top: 1; }
    .row { height: auto; margin-bottom: 1; align-vertical: middle; }
    .field-label { width: 32; content-align: right middle; padding-right: 1; }
    .hint { color: $text-muted; margin-bottom: 1; }
    Input { width: 1fr; }
    Log { height: 12; border: round $primary; }
    #watcher_log { height: 28; }
    DataTable { height: 8; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, output_dir: Path) -> None:
        super().__init__()
        self.output_dir = output_dir

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab_watcher" if load_state(self.output_dir) else "tab_setup"):
            with TabPane("Setup", id="tab_setup"):
                yield SetupPane()
            with TabPane("LUTE", id="tab_lute"):
                yield LutePane()
            with TabPane("Kerberos", id="tab_krb"):
                yield KerberosPane()
            with TabPane("Watcher", id="tab_watcher"):
                yield WatcherPane()
        yield Footer()

    def on_mount(self) -> None:
        self.title = "cctbx-sfx-workflow"
        self.sub_title = str(self.output_dir)
        self.query_one("#history_table", DataTable).add_columns(
            "version", "runs", "d_min", "trigger", "date"
        )
        self._prefill_setup()
        self.refresh_lute_status()
        self.refresh_krb_status()
        self.refresh_watcher_status()
        self.set_interval(5.0, self.refresh_watcher_status)
        self.set_interval(30.0, self.refresh_krb_status)

    # -- Setup tab -----------------------------------------------------------
    def _prefill_setup(self) -> None:
        state = load_state(self.output_dir)
        if not state:
            self.query_one("#inp_workdir", Input).value = str(self.output_dir)
            return
        ra = state.get("render_args", {})
        self.query_one("#inp_experiment", Input).value = state.get("experiment", "")
        self.query_one("#inp_tag", Input).value = state.get("tag", "")
        self.query_one("#inp_workdir", Input).value = state.get(
            "work_dir", str(self.output_dir)
        )
        self.query_one("#inp_pdb", Input).value = ra.get("pdb", "")
        self.query_one("#inp_geometry", Input).value = ra.get("geometry", "")
        self.query_one("#inp_mask", Input).value = ra.get("mask", "")
        self.query_one("#inp_dmin", Input).value = str(state.get("current_d_min", ""))
        self.query_one("#inp_detector", Input).value = ra.get("detector_address", "")

    @work(exclusive=True)
    async def do_detect_detector(self) -> None:
        log = self.query_one("#setup_log", Log)
        experiment = self.query_one("#inp_experiment", Input).value.strip()
        run = self.query_one("#inp_proberun", Input).value.strip()
        if not experiment or not run:
            log.write_line("Need Experiment and Probe run # filled in first.")
            return
        code = (
            "import psana\n"
            f"ds = psana.DataSource(exp='{experiment}', run={run})\n"
            "r = next(ds.runs())\n"
            "for k in r.detinfo.keys():\n"
            "    print(k)\n"
        )
        cmd = [
            "bash",
            "-c",
            "source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh 2>/dev/null && "
            f"PYTHONNOUSERSITE=1 python3 -c \"{code}\"",
        ]
        rc = await run_streamed(cmd, log)
        if rc != 0:
            log.write_line(
                "psana2 query failed — enter the detector address manually. "
                "Never guess the colloquial name (e.g. jungfrau4M); it fails "
                "silently per-event at indexing time."
            )

    @work(exclusive=True)
    async def do_generate_config(self) -> None:
        log = self.query_one("#setup_log", Log)
        log.clear()
        experiment = self.query_one("#inp_experiment", Input).value.strip()
        tag = self.query_one("#inp_tag", Input).value.strip()
        work_dir = self.query_one("#inp_workdir", Input).value.strip() or str(
            self.output_dir
        )
        pdb = self.query_one("#inp_pdb", Input).value.strip()
        geometry = self.query_one("#inp_geometry", Input).value.strip()
        mask = self.query_one("#inp_mask", Input).value.strip()
        d_min = self.query_one("#inp_dmin", Input).value.strip()
        detector = self.query_one("#inp_detector", Input).value.strip()
        probe_run = self.query_one("#inp_proberun", Input).value.strip() or "1"

        missing = [
            name
            for name, val in [
                ("Experiment", experiment),
                ("Tag", tag),
                ("PDB", pdb),
                ("Geometry", geometry),
                ("Mask", mask),
                ("d_min", d_min),
                ("Detector address", detector),
            ]
            if not val
        ]
        if missing:
            log.write_line(f"Missing required field(s): {', '.join(missing)}")
            return

        cmd = [
            "python3",
            str(RENDER_PY),
            "--output-dir",
            str(self.output_dir),
            "--experiment",
            experiment,
            "--runs",
            probe_run,
            "--tag",
            tag,
            "--work-dir",
            work_dir,
            "--pdb",
            pdb,
            "--geometry",
            geometry,
            "--mask",
            mask,
            "--d-min",
            d_min,
            "--detector-address",
            detector,
        ]
        rc = await run_streamed(cmd, log)
        log.write_line(
            "=== Config generated and validated ===" if rc == 0 else "=== FAILED ==="
        )
        self.refresh_lute_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn_detect":
            self.do_detect_detector()
        elif bid == "btn_generate":
            self.do_generate_config()
        elif bid == "btn_lute_refresh":
            self.refresh_lute_status()
        elif bid == "btn_lute_build":
            self.do_lute_build()
        elif bid == "btn_krb_refresh":
            self.refresh_krb_status()
        elif bid == "btn_krb_kinit":
            self.do_kinit()
        elif bid == "btn_watch_start":
            self.do_watch_start()
        elif bid == "btn_watch_cancel":
            self.do_watch_cancel()
        elif bid == "btn_approve_dmin":
            self.do_approve_dmin()

    # -- LUTE tab --------------------------------------------------------
    def _work_dir(self) -> Path:
        state = load_state(self.output_dir)
        if state and state.get("work_dir"):
            return Path(state["work_dir"])
        wd = self.query_one("#inp_workdir", Input).value.strip()
        return Path(wd) if wd else self.output_dir

    def refresh_lute_status(self) -> None:
        info = lute_status(self._work_dir())
        widget = self.query_one("#lute_status", Static)
        if not info["cloned"]:
            widget.update(f"[yellow]Not present[/] at {info['path']}")
        elif info["built"]:
            kind = "symlink" if info["is_symlink"] else "clone"
            widget.update(f"[green]Ready[/] ({kind}) at {info['path']}")
        else:
            widget.update(f"[yellow]Cloned but not built[/] at {info['path']}")

    @work(exclusive=True)
    async def do_lute_build(self) -> None:
        log = self.query_one("#lute_log", Log)
        work_dir = self._work_dir()
        info = lute_status(work_dir)
        if info["built"]:
            log.write_line(f"Already built at {info['path']} — nothing to do.")
            return
        if not info["cloned"]:
            rc = await run_streamed(
                [
                    "git",
                    "clone",
                    "-b",
                    "tag_based_multirun_workflows",
                    "https://github.com/lcls-mlcv/lute.git",
                    str(info["path"]),
                ],
                log,
            )
            if rc != 0:
                log.write_line("Clone failed.")
                return
        rc = await run_streamed(
            [
                "bash",
                "-c",
                "source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh && "
                f"cd {info['path']} && "
                "bash build.sh -e -s /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh",
            ],
            log,
        )
        log.write_line("=== Build complete ===" if rc == 0 else "=== Build FAILED ===")
        self.refresh_lute_status()

    # -- Kerberos tab ------------------------------------------------------
    def refresh_krb_status(self) -> None:
        info = klist_status()
        widget = self.query_one("#krb_status", Static)
        if info["valid"]:
            widget.update(
                f"[green]Valid[/] — {info.get('principal', '?')}, "
                f"expires {info.get('expires', '?')}"
            )
        else:
            widget.update(f"[red]Invalid/expired[/] — {info.get('detail', '')}")

    def do_kinit(self) -> None:
        user = getpass.getuser()
        with self.suspend():
            print(f"Running kinit for {user}@SLAC.STANFORD.EDU ...")
            subprocess.run(
                ["kinit", "-c", f"FILE:{KRB5_CCACHE}", f"{user}@SLAC.STANFORD.EDU"]
            )
            input("Press Enter to return to the TUI...")
        self.refresh_krb_status()

    # -- Watcher tab -------------------------------------------------------
    def _submit_script(self, state: dict) -> Path:
        return self.output_dir / f"submit_cctbx_{state['experiment']}.sh"

    def refresh_watcher_status(self) -> None:
        state = load_state(self.output_dir)
        widget = self.query_one("#watcher_state", Static)
        table = self.query_one("#history_table", DataTable)
        banner = self.query_one("#watcher_dmin_banner", Static)
        log = self.query_one("#watcher_log", Log)

        if not state:
            widget.update(
                "[yellow]No workflow_state.json yet[/] — fill in Setup, "
                "generate the config, then Start/restart here (this will run "
                "iteration_log.py init automatically)."
            )
            banner.update("")
            return

        sidecar = load_sidecar(self.output_dir)
        job_id = sidecar.get("job_id")
        job_state = squeue_status(job_id)
        job_line = (
            f"SLURM job {job_id}: [green]{job_state}[/]"
            if job_state
            else f"SLURM job {job_id}: [red]not running[/]"
            if job_id
            else "[yellow]never started from this TUI[/]"
        )
        widget.update(
            f"Experiment [bold]{state['experiment']}[/]  Tag [bold]{state['tag']}[/]\n"
            f"State: [bold]{state['state']}[/]   Current d_min: {state['current_d_min']} Å\n"
            f"Known runs: {state.get('known_runs', [])}\n"
            f"Current version: {state.get('current_version', 'none')}   "
            f"Last check: {state.get('last_check', 'never')}\n"
            f"{job_line}"
        )

        pending = state.get("pending_dmin")
        approved = state.get("approved_dmin")
        banner_lines = []
        if pending is not None and pending != approved:
            banner_lines.append(
                f"[bold yellow]d_min change recommended: "
                f"{state['current_d_min']} → {pending} Å (not yet approved)[/]"
            )
        elif approved is not None:
            banner_lines.append(
                f"[green]d_min {approved} Å approved, applying on next poll[/]"
            )
        failed_runs = state.get("failed_runs", {})
        if failed_runs:
            run_list = ", ".join(
                f"{r} ({v['attempts']} attempts)" for r, v in sorted(failed_runs.items())
            )
            banner_lines.append(
                f"[bold red]Retries exhausted, needs attention: {run_list} — "
                f"see log below for diagnosis[/]"
            )
        banner.update("\n".join(banner_lines))

        table.clear()
        for h in state.get("history", []):
            table.add_row(h["version"], str(h["runs"]), str(h["d_min"]), h["trigger"], h["timestamp"][:10])

        log_path = sidecar.get("log_path")
        if log_path and Path(log_path).exists():
            lines = Path(log_path).read_text(errors="replace").splitlines()[-40:]
            log.clear()
            for line in lines:
                log.write_line(line)

    @work(exclusive=True)
    async def do_watch_start(self) -> None:
        log = self.query_one("#watcher_log", Log)
        sidecar = load_sidecar(self.output_dir)
        old_job_id = sidecar.get("job_id")
        if old_job_id and squeue_status(old_job_id):
            subprocess.run(["scancel", str(old_job_id)])
            log.write_line(f"Cancelled previous watcher job {old_job_id} before restarting.")
        state = load_state(self.output_dir)
        if not state:
            experiment = self.query_one("#inp_experiment", Input).value.strip()
            tag = self.query_one("#inp_tag", Input).value.strip()
            work_dir = self.query_one("#inp_workdir", Input).value.strip() or str(
                self.output_dir
            )
            pdb = self.query_one("#inp_pdb", Input).value.strip()
            geometry = self.query_one("#inp_geometry", Input).value.strip()
            mask = self.query_one("#inp_mask", Input).value.strip()
            d_min = self.query_one("#inp_dmin", Input).value.strip()
            detector = self.query_one("#inp_detector", Input).value.strip()
            if not all([experiment, tag, pdb, geometry, mask, d_min, detector]):
                log.write_line(
                    "No workflow_state.json and Setup tab isn't fully filled in "
                    "— fill in Setup first."
                )
                return
            init_cmd = [
                "python3",
                str(ITERATION_LOG_PY),
                "--output-dir",
                str(self.output_dir),
                "init",
                "--experiment",
                experiment,
                "--tag",
                tag,
                "--d-min",
                d_min,
                "--work-dir",
                work_dir,
                "--pdb",
                pdb,
                "--geometry",
                geometry,
                "--mask",
                mask,
                "--detector-address",
                detector,
            ]
            rc = await run_streamed(init_cmd, log)
            if rc != 0:
                return
            state = load_state(self.output_dir)

        submit_script = self._submit_script(state)
        if not submit_script.exists():
            log.write_line(f"Submit script not found: {submit_script}")
            return

        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(submit_script),
            "--watch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        output_lines: list[str] = []
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            output_lines.append(line)
            log.write_line(line)
        await proc.wait()

        job_id = None
        log_path = None
        for line in output_lines:
            m = re.search(r"SLURM job\s*:\s*(\d+)", line)
            if m:
                job_id = m.group(1)
            m = re.search(r"Log\s*:\s*(\S+\.log)", line)
            if m:
                log_path = m.group(1)
        if job_id:
            save_sidecar(self.output_dir, {"job_id": job_id, "log_path": log_path})
            log.write_line(f"=== Watcher running as job {job_id} ===")
        self.refresh_watcher_status()

    def do_watch_cancel(self) -> None:
        sidecar = load_sidecar(self.output_dir)
        job_id = sidecar.get("job_id")
        log = self.query_one("#watcher_log", Log)
        if not job_id:
            log.write_line("No tracked job id — nothing to cancel.")
            return
        subprocess.run(["scancel", str(job_id)])
        log.write_line(f"Sent scancel for job {job_id}.")
        self.refresh_watcher_status()

    @work(exclusive=True)
    async def do_approve_dmin(self) -> None:
        state = load_state(self.output_dir)
        log = self.query_one("#watcher_log", Log)
        if not state or state.get("pending_dmin") is None:
            log.write_line("No pending d_min recommendation to approve.")
            return
        cmd = [
            "python3",
            str(ITERATION_LOG_PY),
            "--output-dir",
            str(self.output_dir),
            "approve-dmin",
            "--d-min",
            str(state["pending_dmin"]),
        ]
        await run_streamed(cmd, log)
        self.refresh_watcher_status()


def main() -> None:
    p = argparse.ArgumentParser(description="cctbx-sfx-workflow operator TUI.")
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    raw = Path(args.output_dir)
    if not raw.is_absolute():
        # A relative path here silently resolves against wherever the TUI
        # happens to be launched from (e.g. the skill's own directory) and
        # gets mkdir -p'd into existence — a typo then builds LUTE somewhere
        # nobody is looking, wasting ~15 minutes without any visible error.
        print(
            f"ERROR: --output-dir must be an absolute path (got '{args.output_dir}').\n"
            f"       Relative paths resolve against wherever this TUI was launched from,\n"
            f"       not your experiment directory — pass the full /sdf/data/... path.",
            file=__import__("sys").stderr,
        )
        raise SystemExit(1)

    output_dir = raw.resolve()
    existed = output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not existed:
        print(f"Note: created new directory {output_dir}")
        print("Press Enter to continue, or Ctrl-C if this path is wrong...")
        input()

    CctbxTUI(output_dir).run()


if __name__ == "__main__":
    main()
