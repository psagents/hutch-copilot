# /takerun — Start a DAQ Run

Triggered by: `/takerun`, "take a run", "start collecting", "begin a run", "take data".

---

## Experiment State (silent context)

Before asking the user for anything, read the current state from:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/{experiment}_state.json
```
Fields consumed by this command:
`hutch`, `experiment`, `sample_name`, `concentration`, `sample_delivery`,
`delivery_details`, `photon_energy_eV`, `rep_rate_Hz`, `transmission`,
`pump_laser`, `pump_delay_ps`, `last_run_tag`, `lute_config`

Use any non-null field directly — do not re-ask the user. Ask only for fields
that are still `null` after reading the file. If `hutch` or `experiment` are
unknown, ask for them first, then read the file.

---

## Phase 0: Verify DAQ is Running

Before resolving any run parameters, confirm the DAQ is live via the bridge.

```python
daq.status()
```

Parse the response:
- If state is `Running`, `Configured`, or `Open` — proceed to Phase 1.
- If state shows **not connected**, **disconnected**, **unconnected**, or any **fault** — stop and warn the user:

> "DAQ does not appear to be running or is in a fault state (`{state}`).
> Resolve the DAQ issue before attempting configure/begin.
> You may want to run `/fixdaq`, or ask the operator to restart the DAQ manually."

**Do not call `daq.configure()` or `daq.begin()` into a non-running DAQ.** Hard stop here.

---

## Phase 1: Resolve Run Parameters

Pull what you already know from the session state. Ask only for what is missing.

**Required parameters:**
- `duration` (seconds or minutes) OR `n_events` (shot count) — ask if neither known
- `sample_name` — from state, or ask if absent
- `run_label` — optional free-text label (suggest `{sample_name}_{concentration}`)

**Never ask for parameters already in state.** If the user says "/takerun for 3 minutes"
and the sample is already in context, do not ask about the sample again.

If both `duration` and `n_events` are provided, prefer `duration`.

---

## Phase 2: Confirm

Show a summary before starting. This is a write operation — always confirm.

```
Proposed run:
  Duration  : 3 min (180 s)
  Sample    : FeNO6, 10mM, 50µm Rayleigh jet
  Label     : FeNO6_10mM
  Hutch     : MFX  /  Experiment: mfxl1013621

Shall I start the run?
```

Wait for explicit approval before proceeding.

---

## Phase 3: Run via autorun()

If live bridge is available, issue a single `autorun()` call. All DAQ setup,
begin, and end_run are handled internally. This is a **write** class operation —
confirmation has already been given in Phase 2.

```python
# By duration (seconds):
autorun(duration=180, record=True)

# Or by event count:
autorun(events=54000, record=True)
```

`autorun()` blocks until the run completes — no separate `daq.end_run()` needed.

**Immediately after `autorun()` returns**, query the run number:

```python
daq.status()
```

Parse for `run_number` and report it to the user ("Run 47 complete").

---

## Phase 4: Verify XTC2 Arrival (automatic — runs every time)

After `autorun()` returns and the run number is known, verify data landed on S3DF.
This step is **mandatory and automatic** — do not skip it or make it optional.

```bash
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | grep "r{run_number:04d}"
```

**Pass:** files present and non-zero size — report size and file count to the user.

**Fail (no files after 30s):** warn immediately:
> "No XTC2 files found for run {run_number} at
> `/sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/`.
> The run may not have recorded. Check DAQ status or run `/fixdaq`."

Retry once after an additional 30s before escalating.

---

## Phase 5: LUTE MANUAL Workflow Submission

After XTC2 is confirmed, check whether the run_tag maps to a MANUAL-triggered
workflow. If it does, **the agent submits the LUTE job automatically** — no
operator action required.

### Step 5.1 — Check for a MANUAL workflow match

Read `lute_config` from the session state:
```json
"lute_config": {
  "yaml_path": ".../{hutch}_lute.yaml",
  "workflows": ["lute_geom_calib", "lute_sfx_crystfel", "lute_xes_analysis"],
  "configured_at": "..."
}
```

If `lute_config` is null or `yaml_path` is null — skip this phase silently.

Match `run_tag` to a workflow using this table:

| run_tag | MANUAL workflow | Trigger |
|---|---|---|
| `GEOM` | `geom_calib` | MANUAL |
| `DARK` | *(none by default — DARK handled by ARP pedestal scripts)* | — |
| `DATA`, `SFX`, `XES` | END_OF_RUN or START_OF_RUN — fires automatically | skip |

If no MANUAL match → skip this phase silently.

### Step 5.2 — Submit the workflow

This is a **write** operation. Show the command and confirm before executing:

> **I'd like to submit the LUTE `{workflow_name}` workflow for run {run_number}.**
> ```bash
> source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh
> {results_dir}/lute_envs/lute_env_py39/bin/launch_slurm \
>   -c {lute_output_dir}/{hutch}_lute.yaml \
>   -W {lute_output_dir}/{workflow_name}.dag \
>   -e {experiment} \
>   -r {run_number} \
>   --type {run_tag}
> ```
> **Shall I proceed?**

On confirmation, execute the command and report the job output to the user.

### Step 5.3 — Update session state

```json
{ "last_run_number": <run_number>, "last_run_tag": "<run_tag>" }
```

---

## Phase 6: Verify Data

XTC2 verification was already performed in Phase 4. This phase updates session state
and logs the run if not already done in Phase 5.

Report file size and count if not already reported.

---

---

## → coordinate-experiment handoff (mandatory — runs after every completed take-run)

After Phase 5 or Phase 6 completes (or after any error that ends the run), pass
to coordinate-experiment for ambient logging. Do this even if the run failed.

Minimum entries to write:
```markdown
- **{HH:MM}** Run {run_number} ({run_tag}) — {duration}s / {n_events} events.
  XTC2: {file_size} ({n_files} files). Sample: {sample_name}.
  [If LUTE job submitted]: lute_{workflow} submitted.
  [If XTC2 missing]: WARNING — no XTC2 files detected.
```

Update state JSON: `last_run_number`, `last_run_tag`.

---

## Error Handling

| Symptom | Response |
|---|---|
| `daq.begin()` throws exception | Call `daq.end_run()` anyway; then suggest `/fixdaq` |
| No XTC2 files after 60s | Warn; check `daq.status()`; suggest `/fixdaq` |
| Run ends unexpectedly | Check `daq.status()` for error state; document in elog |
| Bridge not connected | Provide commands for user to run manually; no auto-execution |

---

## Bridge Not Available

If the bridge is not connected, provide the command for the user to run manually
in their hutch-python session:

```python
# In your hutch-python session:
autorun(duration=180, record=True)   # or events=N instead of duration
```

After the run completes, **infer the run number from context** — do not ask the user:

1. **Expected run number:** `last_run_number + 1` from the session state (or run 1
   if no prior runs).
2. **Confirm via XTC2 directory** — look for the expected run file on S3DF:

```bash
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | grep "r{expected_run:04d}"
```

3. If the expected file is present → use that run number, update state, report to user.
4. If not found → scan the last few entries in the directory to find the newest run:

```bash
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | head -10
```

Parse the run number from the filename (pattern: `*-r{NNNN}-*`) and confirm with the
user only if the run number is ambiguous.
