# hutch-copilot — Road to a first working demo (Sep → Dec 2026)

## Timeline

```
                     ──────────SEP──────────│──────────OCT──────────│────NOV────
                     09/09 09/15 09/22 09/29 10/06 10/13 10/20 10/27 11/03 11/10
M0  access           ████████████████████████                                     ◀ gate Sep 30
M1  desk work              ████████████████████████                               ◀ no beamline needed
M2  MFX live demo                                   ████████████████████████      ◀ full 3-act loop
                     ▲ today
```

| M | What | Window | Exit criterion |
|---|---|---|---|
| **M0** | Unblock access: Kerberos, psdev, both hutch groups, a live experiment, shift request | Sep 9 → **Sep 30** | `ssh psdev` works **and** shift dates exist. Slips here move everything |
| **M1** | Desk work: tool consolidation, SKILL.md updates, no beamline needed | Sep 15 → Oct 6 | **DONE Sep 23**, amended Sep 25. 5 tools, 301 tests green, no `hutch-bridge`/`hutch-config`/`hutch-install` reference left in skill files |
| **M2** | MFX live demo: are-we-ready + full collect→analyze→assess loop | October | Agent says "done" (or "one more run") based on real metrics from a real run |

---

## Demo goal

A scientist arrives at the hutch at the start of a shift. The loop runs end-to-end:

```
ACT 0 — /are-we-ready
  hutch-daq ping               → bridge live
  hutch-resolve beampath       → blocking devices list
  hutch-resolve daq            → DAQ state, config alias
  → agent reports: beam ready / not ready; DAQ connected

ACT 1 — /take-run
  hutch-daq exec 'autorun(...)' --confirm write
  hutch-resolve xtc2           → XTC2 files confirmed on S3DF
  hutch-lute watch             → LUTE job fired automatically

ACT 2 — LUTE runs (auto-triggered by eLog ARP)
  hutch-lute status $JOB_ID    → job state + path to merge log

ACT 3 — /analyze-data
  agent reads CCTBX merge log directly from the returned path
  → "CC½=0.87, completeness=96%, Rsplit=14%, multiplicity=4.2.
     Limiting factor: multiplicity (target >5). One more run recommended."

  [after second run]
  → "CC½=0.91, completeness=99%, multiplicity=6.3.
     Dataset is at publication quality. Recommend stopping data collection."
```

**No parser code. No `hutch-lute assess`.** `hutch-lute status` returns the log path;
`analyze-data` reads the file; the agent reasons from the text.

---

## Tool layer — 5 tools

Each tool owns exactly one class of infrastructure. The tool layer is **dumb data** — tools
return JSON, tools never say "good enough". Reasoning belongs to the skill.

| Tool | Infrastructure | Key change from current |
|---|---|---|
| `hutch-daq` | DAQ machine — bridge + processes + logs | Replaces `hutch-bridge`; adds `status`, `logs`; drops `run`; folds classify into `exec` |
| `hutch-lute` | LUTE / Slurm | Absorbs `hutch-config` + `hutch-install` |
| `hutch-resolve` | happi / DataSource / EPICS / DAQ conf | No CLI change; redocumented by authoritative source |
| `hutch-state` | Experiment state file | Unchanged |
| `hutch-log` | Beamtime log | Reduced to `init` + `append` only |

---

## Critical path

```
M0 access ──┬─ M1 desk work ──── M2 MFX live demo
            │
            └─ (shift request — weeks of lead time, not in Louis's control)
```

---

## M0 — Unblock access · week of Sep 9

All items are requests to other people. Nothing downstream can be scheduled until they resolve.

### Tests — run Sep 23

| # | Run | Expect | Status |
|---|---|---|---|
| 0.1 | `klist` (the shell's own cache, `/tmp/krb5cc_$UID`) | `Expires` in the future | **pass** — valid, flags `FRIA` (forwardable, renewable) |
| 0.2 | `ssh -o BatchMode=yes psdev hostname` | a hostname, rc=0 | **FAIL** — `Permission denied (publickey,gssapi-keyex,gssapi-with-mic,password)`. See B1 below |
| 0.3 | `ssh -o BatchMode=yes -J psdev mfx-daq hostname` | `mfx-daq` | blocked by 0.2 |
| 0.4 | `id -Gn \| tr ' ' '\n' \| grep mfx` | MFX membership | **pass** — 14 `mfx*` groups. RIX is out of scope, so this is no longer a two-hutch test |
| 0.5 | `bin/hutch-resolve conf --hutch mfx` | `{"value": {...}}`, rc=0 | **FAIL rc=4** `BridgeUnavailable` — downstream of 0.2, nothing else |
| 0.6 | `bin/hutch-resolve xtc2 --hutch mfx --experiment <exp> --run <r>` | `"present": true` | **pass** after fixing B2 — `mfx100852324` r355: `present: true`, `count: 10`, `1030.3 GB` |

> **B1 — 0.2 is stricter than "I can log in".** Interactive password login to psdev works. The
> tools cannot use it: `daq._ssh()` passes `-o BatchMode=yes`, which disables every prompt by
> design, because a resolver that can block on a TTY at 03:00 is worse than one that fails
> loudly. Key auth is also not yet in play — psdev homes are on `/cds`, which is **not mounted
> on S3DF**, so `~/.ssh/authorized_keys` here is a different file from the one psdev reads.
> GSSAPI is enabled client-side (`/etc/ssh/ssh_config.d/10-sdf.conf`) and the ticket is
> forwardable, yet psdev rejects `gssapi-with-mic`, which points at the account not being
> Kerberos-enabled there rather than at a local misconfiguration.
>
> **Next step:** push `~/.ssh/id_ed25519.pub` to psdev's `/cds` home from one interactive
> login. If that does not take, the account is not authorized and it needs a ticket asking for
> interactive psdev access with Kerberos/GSSAPI login — noting that password auth already
> works, which makes it a narrow request.

> **B2 — 0.6 was a code bug, not an access one.** `paths.xtc2_dir()` returned
> `<exp>/xtc2`. Every experiment on disk uses `<exp>/xtc` — 175 mfx directories have `xtc/`,
> none anywhere under `/sdf/data/lcls/ds` has `xtc2/`. The `2` is the file extension, not the
> directory. So `hutch-resolve xtc2` reported `present: false` for data that had landed, and
> the peds trap refused every DARK run on that evidence. `test_paths_and_kerberos.py` asserted
> the same wrong path, and three fixtures in `test_peds_and_readiness.py` created `xtc2/` as a
> literal, so the suite agreed with the code and neither matched the filesystem. Fixed, and the
> fixtures now build their paths through `paths.xtc2_dir()` so the two cannot diverge again.

### Asks (not tests) — still open

- Name four owners: *tools* (Louis), *MFX shift co-driver* (can start a hutch-python session),
  *readiness checklists* (a beamline scientist per hutch), *LUTE/DAG contact*.
- Request MFX shift. **A no-beam maintenance window is sufficient and preferable for M2** — the
  bridge, resolvers, and safety gate can all be validated without beam.

**Gate:** by Sep 30, shift dates exist or de-scope to M1 only.

---

## M1 — Desk work · Sep 15 → Oct 6 · **DONE Sep 23**

All testable from S3DF with no beamline. Ordered by dependency.

**Outcome:** 7 binaries → 5. **301 unit tests green** — the "311" recorded here on Sep 23 was
never verified against a run; the suite was 199 when the config/install trim landed, and is
301 now that those subcommands and their tests are back. Do not write a count into this file
without running `pytest` first.

Three bugs fixed that the plan did not know about:

- **B2** — `paths.xtc2_dir()` pointed at a directory no experiment has. Detail under M0.
- **B3** — `take-run/SKILL.md` documented a `hutch-daq` invocation argparse rejected
  (`--timeout`/`--confirm` before the subcommand, where they belonged to `exec`). Both
  positions are now accepted, trailing wins. Fixing it by moving the flags to the top level
  *alone* would have turned M1.a.5's expected refusal into an `unrecognized arguments` usage
  error — rc 2 where the gate wants rc 5 — so `tests/unit/test_daq_cli.py` now pins the exit
  code in both positions. The doc-drift test could not have caught either: it compares flags
  as an unordered union of parent and subparser, so position is invisible to it.
- The CLI path from `SafetyRefusal` to rc 5 had **no test at all** — only the underlying
  function was covered. It has one now, with a fake `_ssh` that fails the test if a refusal
  ever reaches the network.

Two smaller fixes fell out: `hutch-daq exec` returned 0 on a remote traceback, and
`hutch-lute`'s docstring advertised a `submit` subcommand the parser never had.

### M1.a — `hutch-bridge` → `hutch-daq` *(2–3 days)*

Rename the binary and module throughout. Expand scope:

- **Drop `run`**: its only callers (`are-we-ready` MFX scripts) are replaced by resolver
  calls in M1.e. If the session needs a multi-line script, pass the file content via `exec`.
- **Fold classify into `exec`**: calling `exec` without `--confirm` on code that requires
  confirmation returns the classification JSON and exits non-zero — nothing is transmitted.
  The separate `classify` subcommand is removed; the behavior is preserved.
- **Add `status`**: routes `daqmgr status <hutch>.py` via SSH to the DAQ host. Returns a
  process-name → state mapping.
- **Add `logs`**: list and tail per-process log files. Files live at
  `/cds/home/opr/<hutch>opr/<YYYY>/<MM>/<timestamp>_<host>:<process>.log`.

> **TBD — `hutch-daq logs` interface:** Exact filter options (by process name, by date,
> most-recent N files) need confirmation against a live MFX experiment. Blocked until
> M0.6 passes. Key unknowns: whether log directory layout is stable across DAQ versions,
> which process names to expose as filter values, and log retention period.

| # | Run | Expect | Fails if |
|---|---|---|---|
| 1.a.1 | `grep -rn "hutch-bridge\|hutch_bridge" bin/ hutch_tools/ are-we-ready/ take-run/ analyze-data/ coordinate-experiment/ SKILL.md` | no output | old name still present anywhere |
| 1.a.2 | `bin/hutch-daq --help` | lists exactly `ping install exec status logs` | `run` or `classify` still listed |
| 1.a.3 | `bin/hutch-daq --hutch mfx exec 'True'` (no `--confirm`, read-only) | executes and returns output | needless confirmation demanded for read-only code |
| 1.a.4 | `bin/hutch-daq --hutch mfx exec 'daq.begin()'` (no `--confirm`) | returns classification JSON, rc≠0, **not transmitted** | transmitted without confirmation |
| 1.a.5 | `bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write` | **rc=5, refused** — beam-critical understated as write | executes |
| 1.a.6 | `$PY -m pytest tests/unit -q` (see tests/README.md for `$PY`) | all green | — |

### M1.b — Absorb `hutch-config` + `hutch-install` into `hutch-lute` *(1–2 days)*

Move `hutch-config` and `hutch-install` subcommands under `hutch-lute`. The standalone
binaries become thin shims or are deprecated. No logic changes — only the CLI surface moves.

> **RESOLVED (TBD-2):** `hutch-config` is now **`hutch-lute render`** -- not `config`, because
> `hutch-lute sbatch --config <path>` already means "the config file to submit", which is a
> different thing from producing one. The six `hutch-install` subcommands map 1:1. Both old
> binaries are deleted, not shimmed.
>
> **Amended Sep 25:** they are absorbed as *code inside `lute.py`*, not as `config.py` and
> `install.py` modules. One module per binary is the rule, and `test_package_api.py`
> enforces it. The cost is a 1400-line `lute.py`, the largest module in the package by a
> factor of two; if that becomes unreadable, the lever is to split `render` back out, since
> it is the part carrying the dependencies.
>
> jinja2/PyYAML (for `render`) and requests/krtc (for `register`) are imported inside the
> functions that use them, so `hutch-lute watch` still runs on a machine without them.
> `test_package_api.py` checks module-scope imports separately from deferred ones -- moving
> an import inside a function must not become a way to add a dependency quietly -- and pins
> that `build_parser()` leaves jinja2 and yaml out of `sys.modules`.

| # | Run | Expect |
|---|---|---|
| 1.b.1 | `grep -rn "bin/hutch-config\|bin/hutch-install" SKILL.md are-we-ready/ take-run/ analyze-data/ coordinate-experiment/` | no output |
| 1.b.2 | `bin/hutch-lute --help` | 13 subcommands: the 5 LUTE/Slurm ones plus `render` and the 6 install ones |
| 1.b.3 | `ls hutch_tools/*.py` | 7 files — no `config.py`, no `install.py`, no `traps.py` |
| 1.b.4 | `$PY -m pytest tests/unit -q` | green |

### M1.c — Redocument `hutch-resolve` by authoritative source *(½ day)*

No CLI change. Update `SKILL.md`, `PLAN.md` Tool Reference, and any sub-skill that
references `hutch-resolve` to group subcommands by the truth source they read:

```
happi:    devices  device  pv  preset  beampath
data:     detectors  xtc2  cell
epics:    caget
control:  daq  conf
```

Verification: `grep -n "hutch-resolve" SKILL.md` lists only subcommands that appear in
one of the four groups above.

### M1.d — Simplify `hutch-log` *(½ day)*

Remove all subcommands except `init` and `append`. Update `coordinate-experiment/SKILL.md`
to match. Any reference to `section`, `tail`, `path`, or `dump` is removed.

| # | Run | Expect |
|---|---|---|
| 1.d.1 | `bin/hutch-log --help` | lists only `init` and `append` |
| 1.d.2 | `grep -rn "hutch-log" SKILL.md coordinate-experiment/SKILL.md` | only `init` and `append` references |

### M1.e — Update all SKILL.md files *(1 day, last)*

Single pass over every skill file once M1.a–d are complete:

- Replace all `hutch-bridge` → `hutch-daq`.
- Replace the two `hutch-bridge run <script>` calls in `are-we-ready/SKILL.md` with individual
  `hutch-daq exec` + resolver calls. The MFX bundled scripts (`check_beam_ready_mfx.py`,
  `check_hutch_ready_mfx.py`) are no longer shipped to the DAQ machine — `run` is gone.
- Replace `hutch-config` + `hutch-install` references with `hutch-lute` equivalents.
- Add the convergence verdict section to `analyze-data/SKILL.md`: the skill reads the merge
  log path returned by `hutch-lute status`, reads the file content directly, and reasons about
  publication readiness. No parser code, no new tool call.

> **TBD — publication-quality thresholds:** The exact SFX metrics the agent should reason
> from when giving the convergence verdict. Likely: CC½ > 0.5 in the outer resolution shell,
> completeness > 95%, multiplicity > 5, Rsplit < 30% — but these should be confirmed with a
> crystallographer before being written into SKILL.md as reference values.

> **TBD — CCTBX merge log fields:** Completeness and multiplicity may live in a different
> log section than what `collect_iter.py` currently reads. Verify against a real merge output
> before writing the `analyze-data` convergence section.

| # | Run | Expect |
|---|---|---|
| 1.e.1 | `grep -rn "hutch-bridge\|hutch-config\|hutch-install" SKILL.md */SKILL.md` | no output |
| 1.e.2 | `grep -rn "hutch-bridge.*run " are-we-ready/SKILL.md` | no output |
| 1.e.3 | `$PY -m pytest tests/unit -q` | all green |

---

## M2 — MFX live demo · October

**Goal:** the full 3-act loop runs end-to-end with a real operator, real or replayed beam,
and real LUTE output.

### Safety gate — mandatory, every shift

| # | Run | Expect | Fails if |
|---|---|---|---|
| 2.1 | `bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()'` (no `--confirm`) | classification JSON, rc≠0, not transmitted | transmitted |
| 2.2 | `bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write` | **rc=5, refused** | executes — **stop the shift** |
| 2.3 | unrecognised code with `--confirm read-only` | rc=5 — unrecognised defaults to write | treated as read-only |

### Tool tests — transport and resolvers

| # | Run | Expect | Fails if |
|---|---|---|---|
| 2.4 | `bin/hutch-daq --hutch mfx ping` before listener starts | `{"ok": false, "mode": "offline"}` | reports live with no listener |
| 2.5 | `bin/hutch-daq --hutch mfx install` + operator starts listener | next `ping` returns `{"ok": true, "mode": "live"}` | — |
| 2.6 | `bin/hutch-resolve daq --hutch mfx` | state, config_alias, experiment, recording, run_number | any field prefixed `ERROR:` |
| 2.7 | `bin/hutch-resolve beampath --hutch mfx` | blocking list; `method` names the lightpath source (not the fallback) | fallback used silently — report it |
| 2.8 | `bin/hutch-resolve detectors --experiment <exp> --run <r>` | alias list + area_candidates; expect `jungfrau` for MFX | marker absent → psana2 could not open the run |
| 2.9 | `bin/hutch-resolve xtc2 --hutch mfx --experiment <exp> --run <r>` after a run | `present: true`, count, total_human | `present: true` with `count: 0` |

### Agent tests

| # | Prompt | Expect |
|---|---|---|
| 2.A | `/are-we-ready` | Pings bridge first; resolves beampath and DAQ state live; groups report by consequence (BLOCKERS / EXPECTED IN / OK / ERRORS) |
| 2.B | `/take-run` — "take a data run" | Checks DAQ state first (hard gate); confirms before `daq.begin`; watches for LUTE job; does not re-ask for things already in state |
| 2.C | `/analyze-data` after LUTE completes | Reads merge log path from `hutch-lute status`; reads the file; extracts and reports CC½, Rsplit, completeness, multiplicity |
| 2.D | Metrics below thresholds | Names the limiting factor; recommends one more run; does not say "done" |
| 2.E | Metrics above thresholds | Says "recommend stopping data collection" with the supporting numbers |
| 2.F | "we swapped to a new sample" | `coordinate-experiment` logs the change; does not re-ask for hutch or experiment |
| 2.G | `/handoff` at end of shift | Appends a summary to the log via `hutch-log append`; state written with provenance |

**Exit:** all three acts run by a human at a real beamline at least once. Every failure
logged as a regression test with its evidence — `traps.py` was cut on Sep 25, so a trap
now lives as a test that fails if the incident recurs, not as a registry entry.

---

## Verification summary

```bash
# M1 -- desk, no beamline. Bare `python` has no pytest; see tests/README.md.
PY=/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python
$PY -m pytest tests/unit -q                                           # 301 passed
grep -rn "hutch-bridge" bin/ hutch_tools/ */SKILL.md SKILL.md        # → nothing
grep -rn "hutch-config\|hutch-install" */SKILL.md SKILL.md           # → nothing
grep -rn "traps" hutch_tools/ bin/                                   # → nothing
ls hutch_tools/*.py              # 7: __init__ common daq log lute resolve state
ls bin/                          # 5: daq, log, lute, resolve, state
bin/hutch-daq  --help            # ping install exec status logs
bin/hutch-log  --help            # init append
bin/hutch-lute --help            # sbatch status watch peds readiness kerberos render
                                 # plan workspace dags envs register all   (13)

# jinja2/PyYAML are render-only, so building the parser must not import them
$PY -c "import hutch_tools.lute as l; l.build_parser(); import sys; \
        assert 'jinja2' not in sys.modules and 'yaml' not in sys.modules"

# the gate, in both flag positions -- all four MUST be rc=5
bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write
bin/hutch-daq --hutch mfx --confirm write exec 'mfx_stopper_1.remove()'
bin/hutch-daq --hutch mfx exec 'daq.begin()'                 # no --confirm: verdict + rc 5
bin/hutch-daq --hutch mfx exec 'x()' --confirm read-only     # unrecognised errs upward

# B2 -- xtc2 arrival, against an experiment you are in the group for
bin/hutch-resolve xtc2 --hutch mfx --experiment mfx100852324 --run 355   # present: true

# M0/M2 — access, then live
klist                    # the shell's cache: /tmp/krb5cc_$UID, not $HOME/krb5cc.ticket
ssh -o BatchMode=yes psdev hostname
bin/hutch-resolve conf --hutch mfx
bin/hutch-daq --hutch mfx ping                                        # {"ok":true,"mode":"live"}
bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write   # MUST be rc=5
```

Two acceptance tests are behavioral and worth stating plainly:

- **M1.a.5 / M2.2:** understating a beam-critical action must be refused. Run on every shift.
- **M2.E:** agent says "recommend stopping" when metrics cross publication thresholds.

---

## De-scope levers, in pull order

1. Drop `hutch-daq status` (daqmgr routing); keep `ping`, `install`, `exec`, `logs`. Saves ~1 day of M1.a.
2. Drop `hutch-daq logs`; keep `ping`, `install`, `exec`. Simplest possible DAQ tool. Saves another day.
3. Drop `are-we-ready` from the demo; start the loop at `/take-run`. Cuts ~40% of M2 surface.
4. Drop the convergence verdict from `analyze-data`; ship "collects and analyzes" without the "done?" signal. Cheap cut, high cost to the demo story.

Do **not** cut M1.a.5 / M2.2. The safety refusal test runs on every shift regardless of what else is cut.

---

---

# Tool Reference

Quick-reference for the meeting. Each tool, its scope, and its subcommands — one line each.
Items marked **TBD** need a decision or external input before they can be implemented.

---

## hutch-daq

*Everything that requires talking to the DAQ machine — bridge, process management, log files.*

Connects via SSH through `psdev` to `{hutch}-daq`. The hutch-python bridge runs as a
listener process on that machine; `status` and `logs` use the same SSH path.

| Subcommand | What it does |
|---|---|
| `ping` | Check whether the hutch-python bridge listener is alive. Returns `{ok, mode, host}`. Read-only, never needs confirmation. |
| `install` | Copy the bridge listener script to `/tmp` on the DAQ machine. Prints the operator instruction (`exec(open('/tmp/oc_bridge.py').read())`). |
| `exec '<code>'` | Execute a Python snippet in the live hutch-python session. **Without `--confirm`**: classifies the snippet; if confirmation is required, returns the classification JSON and exits non-zero — nothing is transmitted. **With `--confirm <class>`**: transmits after verifying the declared class is not weaker than the actual class. Beam-critical understated as write is a refusal (rc=5), not a downgrade. |
| `exec -` | Same, reading the code from stdin: `exec - < script.py`. This is why `run` was dropped rather than replaced — a whole script arrives without shipping a file to the DAQ machine, and is classified as **one unit**, so a write buried in an otherwise read-only script gates the entire thing. |
| `status` | Run `daqmgr status <hutch>.py` on the DAQ machine via SSH. Returns process-name → state mapping (RUNNING / NOCONNECT / etc.). |
| `logs` | List and tail per-process log files from `/cds/home/opr/<hutch>opr/<YYYY>/<MM>/`. Works even after a crash (equivalent to "View Logfile" in the daqstat GUI). |

> **TBD — `logs` interface:** Exact filter options (by process name, by date, most-recent N)
> need confirmation against a live MFX experiment (blocked on M0.6). Unknowns: whether the
> log directory layout is stable across DAQ versions; which process names to expose as filter
> values (`drp`, `teb`, `meb`, `control`, `hsdioc_*`, `pyxpm`); how far back logs are retained.

---

## hutch-lute

*Everything LUTE: job submission, monitoring, pedestals, readiness, configuration, and
workspace setup. Absorbs `hutch-config` and `hutch-install` — as code inside `lute.py`, not
as separate modules.*

The tool returns raw data — log paths, job states, parsed config reports. It does not
interpret results or say whether an analysis succeeded scientifically.

> **Audited against LUTE v0.3.0/dev (`/sdf/group/lcls/ds/tools/lute`) on Sep 25.** Several
> things this tool was built to work around are fixed upstream. What is left:
>
> | Ours | Upstream equivalent | Verdict |
> |---|---|---|
> | `sbatch` | `launch_scripts/submit_slurm.py` | **keep, narrowly.** It still sets no `#SBATCH --time` — only `--output`/`--error`, everything else passed through. `task_timeout` is application-level, not a wall clock. Also still no `prte_tmpdir_base`: LUTE sets `PRTE_MCA_*` + a hostfile, which fixes resource *discovery*, not two jobs colliding in the shared session dir. |
> | *(exit-status grep)* | `executor.py` exits −1 on FAILED/TIMEDOUT/CANCELLED/UNRESPONSIVE | **CUT Sep 25.** The generated script greps the log for "Task failed with return code" to recover a status LUTE now propagates. Removed; `exit $RC` is enough. This was trap 5 of five, so the module docstring is down to three. |
> | `status` `watch` | `launch_slurm_v1.py:141-176` (sacct/scontrol), plus a push-based status `HTTPServer` | overlap, but ours adds `hit_wall_clock` and a poll count so "no job found" ≠ "did not look". Cheap to keep. |
> | `render` | `tasks/task.py:435-469` renders **phil** templates | complementary — LUTE does not render the config YAML. The unknown-phil-key check is a **real** gap: `extra="allow"` at four sites, non-strict Jinja `Undefined`, no `find_undeclared_variables` anywhere. Unfixed upstream. |
> | `plan` `workspace` `dags` `envs` `register` `all` | `utilities/setup/setup_lute.py` (642 lines, same author) | **keep — see below.** |
> | `peds` `readiness` `kerberos` | none | no upstream equivalent. `makepeds`/`takepeds` appear nowhere in the LUTE tree. |
>
> **Why the install six survive the overlap.** `setup_lute` can only register a DAG it
> already has: it resolves `<name>.dag` against `{lute_output}` then `workflows/common/`
> — a catalogue of five (`bayfai sfx smd test xpp_compression`) — and on a miss logs
> *"Workflow definition not found … Skipping workflow"* and continues. Its trigger is
> hardcoded by substring: `START_OF_RUN` if `"smd"` is in the name, else `MANUAL`. The
> demo needs a DAG `analyze-data` composes for the experiment at hand, registered with
> `END_OF_RUN` — neither of which `setup_lute` can express. `parse_trigger` accepts all
> four, including `RUN_PARAM_IS_VALUE:<name>:<value>`.
>
> **Where ours is behind, and should not be pretended otherwise.** Upstream
> `update_dag_params` also applies per-task `nodes`/`ntasks_per_node` from a
> `DEFAULT_CONFIG` table (`SmallDataProducer`, `BayFAIOptimizer`, …); `patch_slurm_params`
> does not. Upstream never deletes a venv (`"already exists … Reusing"`), so the
> preconditions-before-destruction ordering `plan()` exists to guarantee is moot there —
> `envs` here still `rmtree`s, which is why it alone needs `--confirm write`. Both bugs the
> port claimed credit for fixing are fixed upstream too.

| Subcommand | What it does |
|---|---|
| `sbatch` | Submit a LUTE task to Slurm directly. Requires a `--time` wall-clock limit — refuses to emit a script without one. |
| *(`submit` is not a subcommand)* | Submission is `sbatch --submit`. The docstring advertised a bare `submit` that the parser never had; removed. |
| `status <job_id>` | Return job state, elapsed time, and **output file paths** — `stdout`/`stderr` from sacct, which is how `analyze-data` finds the merge log without a parser. `COMPLETED` ≠ scientific success — checks the Task-level exit status separately. |
| `watch` | Poll Slurm for jobs matching a user/workflow since a given timestamp. Reports poll count so "no job found" cannot be confused with "did not look". |
| `peds` | Run `makepeds` for a DARK run, on `psdev`. Gated by the same classifier `hutch-daq exec` uses, on the command text — `--confirm write` or rc 5, decided before the XTC2 check so a refusal does not depend on disk state. Refuses if XTC2 has not landed. Names the per-detector fallback (Jungfrau at MFX) when `makepeds` fails. A subcommand rather than an `exec` because it is a shell command on `psdev`, not Python in the session; `takepeds()` *is* `hutch-daq exec 'takepeds()'`. |
| `readiness` | Read-only: report configured workflows, unregistered items, and Kerberos requirement. Never installs as a side effect. |
| `kerberos` | Report whether a Kerberos ticket is needed for a given config. Only eLog-touching operations need one; a pipeline with explicit input paths does not. |
| *(absorbed from `hutch-config`)* | |
| `render` | Render a LUTE config YAML from a Jinja2 template + context JSON + `--set` overrides. Validates overrides against the template whitelist — unknown phil keys are errors, not silent drops. | 
| *(absorbed from `hutch-install`)* | |
| `plan` | Read-only: show all workspace paths, eLog payloads, blocking preconditions. Touches nothing. Show this to the operator before any install step. |
| `workspace` | Create the LUTE results directory structure. |
| `dags` | Patch DAG YAML with `--account` and `--partition`. Idempotent — safe to re-run. |
| `envs` | Delete and recreate LUTE virtual environments. Destructive — requires `--confirm write`. |
| `register` | Register eLog workflow definitions. Requires a Kerberos ticket. |
| `all` | Run workspace → dags → envs → register in order. Re-checks preconditions before each step. |

> **RESOLVED (TBD-2):** `hutch-lute render`, and the six install subcommands 1:1. See M1.b.
>
> **CUT — the `traps` subcommand and `traps.py` (Sep 25).** The incident register was a
> decorator, a module-level registry and 14 scoped checks. Of its four call sites, three
> were not load-bearing: the `sbatch` checks re-verified the template `build_sbatch` itself
> generates (the fixes are *in* that template), and the `peds` check is inlined in
> `deploy_pedestals` with better coverage than the registry entry it replaced. Only the
> `phil-overrides` check was real, and it survives as two plain functions in `lute.py` --
> `template_whitelist` plus an unknown-key comparison -- so `render` still errors on a phil
> key the template would silently drop. **Deliberately lost:** the eight CCTBX `lute-yaml`
> checks (parallel_a2a, modify-in-merge, merge suffixes, merge version, d_min mismatch,
> phil race, run_type case, composite ordered endrun). Nothing in the repo records those
> incidents now.

---

## hutch-resolve

*Machine truth from authoritative sources. The tool fetches; the skill reasons.*

No CLI change — subcommands are unchanged from the current `hutch-resolve`. They are
documented here grouped by the source they read, to make the required connectivity explicit.

### happi — device database (reads via `psdev`)

| Subcommand | What it does |
|---|---|
| `devices --hutch H` | Full device inventory for a hutch, sorted by z-position. Source: happi database, `beamline=<HUTCH>`. |
| `device --hutch H <name>` | Single happi entry for a named device. rc≠0 if not found — never silent miss. |
| `pv --hutch H <device> --suffix <field>` | Compose a PV name from the happi prefix + a suffix. Returns prefix, device class, and the full PV with provenance. |
| `preset --hutch H <device>` | Read the preset YAML for a device from `/cds`. Pointing setpoints come from here, not from source literals. |
| `beampath --hutch H` | Blocking devices list via `lightpath.BeamPath`. Reports which method was used — lightpath native or fallback. An `ERROR:` state is not `OUT`. |

### data — DataSource / XTC2 (reads via psconda)

| Subcommand | What it does |
|---|---|
| `detectors --experiment E --run R` | Detector aliases from an XTC2 file, with area detector candidates flagged. Show list and ask the operator to confirm — never write an alias that hasn't been confirmed. |
| `xtc2 --hutch H --experiment E --run R` | Whether XTC2 files have landed on S3DF: `present` flag, file count, total size. |
| `cell <pdb>` | Unit cell parameters from a PDB file. Never hand-type a unit cell — every hand-encoded value in the reference campaign was wrong. |

### epics — Channel Access (reads via `{hutch}-control`)

| Subcommand | What it does |
|---|---|
| `caget --hutch H <pv>` | Live PV value. Routed through `{hutch}-control` — CA is firewalled from both S3DF and psdev. |

### control — DAQ configuration (reads via `psdev`)

| Subcommand | What it does |
|---|---|
| `daq --hutch H` | DAQ state, config alias, experiment name, recording flag, current run number. |
| `conf --hutch H` | Parsed `conf.yml` for the hutch. Cross-check the DAQ host here against the `daq` result. |

### files — raw `/cds` read (reads via `psdev`)

| Subcommand | What it does |
|---|---|
| `cat <path>` | Read any `/cds` file. The escape hatch for what `conf`/`preset`/`device` do not cover — `/cds` is not mounted on S3DF. Path is positional, not `--path`. |

### local — this checkout (no network)

| Subcommand | What it does |
|---|---|
| `skills [<name>]` | Which skills are installed and where, across every known root. Replaces the hand-written advisor tables that went stale; `analyze-data` and `coordinate-experiment` check an advisor exists before recommending it. |

---

## hutch-state

*The experiment state file — the single source of truth for the session.*

Three rules enforced by the tool and never worked around:
1. Every write requires `--source` and `--kind` (`resolved` / `operator` / `inferred`).
2. `get` refuses a value past its TTL unless `--stale-ok` is passed — which also reports the age.
3. A resolver that cannot reach its source raises. Never substitute a remembered value.

| Subcommand | What it does |
|---|---|
| `path --hutch H --experiment E` | Return the canonical path to the state file. Never write this path by hand — six SKILL.md files used to restate it in prose and three were wrong. |
| `get --hutch H --experiment E <field>` | Read a field. Refuses if past TTL unless `--stale-ok`. |
| `set --hutch H --experiment E <field> <value> --source <s> --kind <k>` | Write a field with mandatory provenance. |
| `unset --hutch H --experiment E <field>` | Remove a field. |
| `audit --hutch H --experiment E` | All fields with age, TTL status, and provenance. Use this for `/context` — it already has everything. |
| `dump --hutch H --experiment E` | Raw JSON of the state file. |

---

## hutch-log

*The shift beamtime log. Two subcommands.*

| Subcommand | What it does |
|---|---|
| `init --hutch H --experiment E --shift-date D` | Create the log with a timestamped header. `append` refuses before `init` — a log always has its header. |
| `append --hutch H --experiment E '<message>'` | Add a timestamped entry. `--reconstructed` marks backfilled entries so they are not read as observed-live. |

---

## Open TBDs — summary

| # | Item | Blocked on |
|---|---|---|
| **TBD-1** | `hutch-daq logs` filter interface: process names, date range, retention period | M0.6 — needs a live MFX experiment |
| ~~TBD-2~~ | ~~`hutch-lute` subcommand naming~~ | **Resolved:** `render`, install 1:1, old binaries deleted, `traps` cut |
| **TBD-3** | Publication-quality metric thresholds for `analyze-data` convergence verdict (CC½, completeness floor, multiplicity floor, Rsplit ceiling) | Input from a crystallographer |
| **TBD-4** | CCTBX merge log fields: whether completeness and multiplicity are in the same log section as CC½/Rsplit, or need a different file | Test against a real merge output |
