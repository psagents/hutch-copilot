---
name: cctbx-sfx-workflow
description: >
  Iterative CCTBX SFX analysis workflow for LCLS experiments. Watches an eLog
  tag for newly-collected runs, submits each new run for indexing/scaling,
  triggers merges, versions merge outputs, and recommends d_min adjustments
  from multiplicity/completeness statistics. Requires the
  `@setup-cctbx-pipeline` skill to have been run first (config files, DAGs,
  submit script, LUTE clone must already exist). Triggers on: watch this
  experiment for new runs, monitor the cctbx pipeline, check if the pipeline
  should run, review merge statistics, decide on d_min, iterate the merge,
  approve dmin, cctbx-sfx-workflow.
---

# Skill: cctbx-sfx-workflow

Iterative CCTBX SFX analysis workflow for LCLS experiments.
Watches an eLog tag for new runs, triggers the analysis pipeline
for each new run, versions merge outputs, and recommends d_min
adjustments based on multiplicity and completeness statistics.

Requires `@setup-cctbx-pipeline` skill to have been run first
(config files, submit script, LUTE clone must already exist).

---

## Trigger conditions

Use this skill when a user asks to:
- Start monitoring an experiment tag for new runs
- Check if the pipeline should run for new data
- Review merge statistics and decide on d_min
- Iterate the merge with a new d_min

---

## Running the workflow

### Automated (recommended)

Submit the autonomous watcher as a SLURM batch job:

```bash
bash submit_cctbx_{experiment}.sh --watch
```

The watcher polls every **60 seconds**, drives the full loop autonomously,
and exits after **30 idle polls** (30 min with no new runs or state changes).
Pass a custom interval in seconds as an optional argument: `--watch 120`.

> **Agent note:** `--watch` always prints a `Monitor: tail -f
> {output_dir}/logs/watch_workflow_{JOBID}.log` line (with the real SLURM job
> ID substituted) once submission succeeds. Always surface that exact command
> back to the user as its own line immediately after submitting — don't just
> mention that logs exist, give the copy-pasteable command with the real job
> ID filled in, the same way it would appear if they ran `--watch` themselves.

```bash
# Follow the log live
tail -f {output_dir}/logs/watch_workflow_JOBID.log

# Check workflow state
python3 ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py \
  --output-dir {output_dir} status

# Approve a d_min change (watcher applies it on next WATCHING poll)
python3 ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py \
  --output-dir {output_dir} approve-dmin --d-min 1.6

# Cancel watcher
scancel JOBID
```

### Manual (agent-driven)

If the watcher is not running, ask the agent "check the workflow" to trigger
one pass of the loop. The agent runs check-runs, check-jobs, stats_parser,
and dmin_advisor and reports results in the conversation.

---

## State file

All workflow state is persisted in `{output_dir}/workflow_state.json`.
The watcher re-reads this file on every poll — edits take effect immediately.

Fields:
- `experiment`, `tag` — what we are watching
- `current_d_min` — resolution cutoff currently in use
- `known_runs` — runs already submitted; used to detect new ones
- `current_version` — most recent merge version (e.g. "v002")
- `state` — current phase: WATCHING | INDEXING_SCALING | MERGING
- `last_check` — ISO timestamp of last eLog poll
- `history` — ordered list of completed merge iterations with manifests
- `pending_dmin` — d_min recommended by dmin_advisor (advisory only; set by watcher)
- `approved_dmin` — d_min approved by user; triggers rescale+merge on next WATCHING poll
- `indexing_scale_runs` — run numbers currently being indexed/scaled (set on transition
  to INDEXING_SCALING, cleared on transition to MERGING); used by `check-jobs` to
  avoid false SCALE_COMPLETE from old lute.db records
- `last_merge_submission_time` — ISO timestamp set when `--merge` is submitted; used
  by `check-jobs` to filter MergeCCTBXXFEL completions to only those after this time,
  avoiding false MERGE_COMPLETE from prior merge records

---

## Agent workflow

### Step 0 — Initialize

Read `{output_dir}/workflow_state.json`.
If it does not exist, create it:

```python
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py init \
  --output-dir OUTPUT_DIR \
  --experiment EXPERIMENT \
  --tag TAG \
  --d-min D_MIN \
  --work-dir WORK_DIR \
  --pdb PDB_FILE \
  --geometry GEOMETRY_FILE \
  --mask MASK_FILE \
  --detector-address DETECTOR_ADDRESS \
  [--wavelength-offset VAL --spectrum-ev-per-pixel VAL --spectrum-ev-offset VAL] \
  [--two-theta-low VAL --two-theta-high VAL]
```

D_MIN should be read from the existing `{experiment}_cctbx.yaml`
(`merging_d_min` field) so it stays in sync.

Pass the same calibration values, and the same `--two-theta-low`/`--two-theta-high`
values (default 12.5/22.8, MFX/jungfrau), used in the original `render.py` call
(from `@setup-cctbx-pipeline` step 4/4b) — the watcher stores these in
`render_args` and reuses them on every versioned re-render. If calibration was
not used, omit all three `--wavelength-offset` / `--spectrum-ev-per-pixel` /
`--spectrum-ev-offset` flags.

> **Critical: `known_runs` bootstrapping.** The `init` command creates
> `workflow_state.json` with `known_runs: []`. If you are bootstrapping
> from a pipeline that already ran a baseline merge, you may be tempted to
> seed `known_runs` with all the runs that were indexed. **Do not do this.**
> `known_runs` must only contain the runs that were actually eLog-tagged
> **at the time of the baseline merge** — i.e. what `check-runs` would have
> returned. If you over-populate `known_runs` with runs that weren't yet
> tagged, those runs will be invisible to the watcher when they later get
> tagged, causing `check-runs` to return `NO_NEW_RUNS` incorrectly.
>
> If bootstrapping from an existing merge, record the baseline iteration
> with only the eLog-tagged runs:
> ```bash
> python3 ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py \
>   --output-dir OUTPUT_DIR record \
>   --version v001 --runs 36 37 --d-min 1.8 --trigger new_run
> ```
> Verify `known_runs` matches what was eLog-tagged at that time.

### Step 1 — Check for new runs (poll eLog)

```bash
KRB5CCNAME="FILE:$HOME/krb5cc.ticket" \
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/monitor.py check-runs \
  --output-dir OUTPUT_DIR
```

Prints: `NEW_RUNS: [38, 39]` or `NO_NEW_RUNS`.

If `NO_NEW_RUNS` and state is `WATCHING`: report to user and stop.
If state is not `WATCHING`: check job completion (Step 2) instead.

> **Kerberos required.** `check-runs` makes an authenticated HTTPS request to
> `pswww.slac.stanford.edu`. Without a valid ticket it silently returns
> `NO_NEW_RUNS`. Always set `KRB5CCNAME=FILE:$HOME/krb5cc.ticket` and confirm
> the ticket is valid (`klist -s`) before interpreting a `NO_NEW_RUNS` result.
> The watcher sets this automatically via `do_watch()` in the submit script.
> When running manually, obtain a ticket first:
> ```bash
> kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU
> ```

### Step 2 — Check job completion

```bash
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/monitor.py check-jobs \
  --output-dir OUTPUT_DIR
```

Prints one of:
- `PENDING` — jobs still running
- `SCALE_COMPLETE` — scaling done, ready to merge
- `MERGE_COMPLETE` — merge done, ready for stats review
- `FAILED: <details>` — a job failed

### Step 3 — Submit new runs (INDEXING_SCALING)

When new runs are detected (e.g. `NEW_RUNS: [38, 39]`):

For **each** new run N, submit index+scale only (no merge):
```bash
bash {output_dir}/submit_cctbx_{experiment}.sh --index-run N
```

`--index-run N` uses `cctbx_index_scale.dag` with `-r N` (not `--tag`), so
only run N is indexed and scaled. Already-processed runs are never re-submitted.

Update state: `state=INDEXING_SCALING`, add new runs to `known_runs`.

**Do NOT use `--submit`** for incremental runs — it submits the full pipeline
for ALL tagged runs via `--tag`, causing already-indexed runs to be re-indexed.

> **Note:** When running the automated watcher (`--watch`), Steps 3–6 are
> handled automatically. The watcher calls `--index-run N`, monitors lute.db,
> renders versioned configs, submits merges, and records iterations without
> human intervention. Manual steps are only needed when running agent-driven.

### Step 4 — Trigger merge (after scaling)

When `SCALE_COMPLETE`:

1. Determine next version:
```bash
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py next-version \
  --output-dir OUTPUT_DIR
```
Prints: `v003`

2. Generate versioned config:
```bash
python ~/.claude/skills/setup-cctbx-pipeline/render.py \
  --output-dir OUTPUT_DIR \
  --merge-version v003 \
  [... same args as original render.py call, read from workflow_state.json ...]
```

3. Submit merge only (passing the versioned config):
```bash
bash {output_dir}/submit_cctbx_{experiment}.sh --merge \
  {output_dir}/{experiment}_cctbx_v003.yaml
```

Update state: `state=MERGING`, `current_version=v003`.

### Step 5 — Stats review (after merge)

When `MERGE_COMPLETE`:

```bash
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/stats_parser.py \
  --log {output_dir}/merge_{tag}/v003/merge_{tag}_main.log
```

Display the statistics table to the user. Show:
- First-bin image acceptance %
- Overall completeness %
- CC½
- Whether each target is met (✓ or ✗)

Then run d_min advisor:

```bash
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/dmin_advisor.py \
  --log {output_dir}/merge_{tag}/v003/merge_{tag}_main.log \
  --current-dmin CURRENT_DMIN
```

Prints one of:
- `RECOMMEND_DMIN: 1.8` — a bin qualifies (multiplicity + completeness) and overall CC½ ≥ 95%
- `NO_CHANGE` — current d_min is already at or beyond the rule
- `INSUFFICIENT_DATA` — not enough bins pass the multiplicity/completeness criteria
- `CC_HALF_BELOW_TARGET` — a bin would otherwise qualify, but overall CC½ is
  below the 95% target from the Target statistics table below. Held at the
  current d_min — report this to the user rather than treating it as
  `NO_CHANGE`, since it means the merge quality target isn't met yet, not
  that the resolution target has been reached.

Write iteration to history:
```bash
python ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py record \
  --output-dir OUTPUT_DIR \
  --version v003 \
  --runs 36 37 38 \
  --d-min 2.5 \
  --trigger new_run
```

Update state: `state=WATCHING`.

### Step 6 — d_min change (requires explicit user approval)

When `dmin_advisor.py` recommends a change:

**Watcher behavior:** merges continue at the current d_min. The recommendation
is written to `pending_dmin` in state and logged. New runs keep being indexed,
scaled, and merged at the old d_min until the user approves.

**To approve:**
```bash
python3 ~/.claude/skills/hutch-copilot/cctbx-sfx-workflow/iteration_log.py \
  --output-dir {output_dir} approve-dmin --d-min Y.Y
```

On the next `WATCHING` poll the watcher will:
1. Determine next version (e.g. v004)
2. Call `render.py --d-min Y.Y --merge-version v004`
3. Call `--rescale v004config.yaml` (re-scales ALL tagged runs at new d_min)
4. Update `current_d_min` and clear `pending_dmin` / `approved_dmin`
5. Record trigger as `dmin_change` in history
6. When MERGE_COMPLETE: parse stats, go to Step 5

**Agent-driven (manual) behavior:** show the user the qualifying bins and ask
for explicit approval before calling `--rescale`.

---

## Target statistics (all three must be met to declare success)

| Metric | Target |
|---|---|
| First-bin image acceptance | 100% |
| Overall completeness | ≥ 99% |
| CC½ (CCint) | ≥ 95% |

When all targets are met, inform the user and offer to stop watching
or continue collecting more data.

---

## Load-bearing assumptions

- `@setup-cctbx-pipeline` skill has been run: config files, DAGs, and
  submit script exist in `output_dir`.
- LUTE is already cloned and built at `{work_dir}/lute/`.
- The eLog tag must be applied to runs BEFORE this workflow is triggered.
- `render.py` args from the original invocation should be stored in
  `workflow_state.json` (`render_args` field) so re-renders are reproducible.
- `--rescale` re-scales ALL tagged runs — do not call it unless you intend
  to overwrite `r00XX/scale/` for all runs.
- Scale output directory (`r00XX/scale/`) is overwritten on rescale.
  Only merge outputs are versioned.
