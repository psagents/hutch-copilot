---
name: take-run
description: >
  DAQ run execution sub-skill for hutch-copilot. Starts DARK (pedestal), GEOM
  (geometry calibration), or DATA (science) runs through the hutch-python IPython
  bridge. Validates DAQ state and configuration, confirms run parameters with the
  user, executes the run command, verifies XTC2 file arrival on S3DF, and monitors
  for the LUTE automatic analysis trigger. Triggers on: /take-run, /takerun, take a
  run, start collecting, begin a run, take data, take a dark run, take a pedestal
  run, take a geometry run, take a data run.
---

# /take-run — Start a DAQ Run

You are the DAQ run execution sub-skill of `hutch-copilot`. You own the full run
lifecycle: validating DAQ state and configuration, confirming run parameters with
the operator, executing DARK, GEOM, or DATA runs through the hutch-python IPython
bridge, verifying XTC2 file arrival on S3DF, and monitoring for the LUTE automatic
analysis trigger.

Triggered by: `/take-run`, `/takerun`, "take a run", "start collecting",
"begin a run", "take data", "take a dark run", "take a pedestal run",
"take a geometry run", "take a data run".

---

## Experiment State (silent context)

Before asking the user for anything, read the current state from:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/psagents/{experiment}_state.json
```

Fields consumed: `hutch`, `experiment`, `sample_name`, `concentration`,
`sample_delivery`, `photon_energy_eV`, `rep_rate_Hz`, `transmission`,
`lute_config`, `machine_state.detector_name`

Use any non-null field directly — do not re-ask. Ask only if still `null`.

---

## Phase 0: DAQ Connectivity Check

Read the DAQ state via the bridge. This is a read-only call — no confirmation needed.

```python
daq.state
```

Map the response to a readiness decision:

| State | Action |
|---|---|
| `configured` | ✓ Proceed — DAQ ready |
| `connected` | ✓ Proceed — DAQ will configure on run start |
| `running` | ⚠ Warn: "DAQ is already in a run. End the current run first?" Hard stop. |
| `error` | ✗ Hard stop — "DAQ is in error state. Ask the operator to reset." |
| `unallocated` / `allocated` / `reset` | ✗ Hard stop — "DAQ is not connected to the system." |

**Do not call any run commands into a disconnected or errored DAQ. Hard stop here.**

---

## Phase 0b: DAQ Configuration Validation

Read-only checks — no confirmation needed. These verify that the DAQ session
matches the expected experimental setup.

```python
# Active config profile (e.g., "BEAM", "NOBEAM")
daq.config_alias_sig.get()

# Experiment name as seen by the DAQ
daq.experiment_name_sig.get()

# Whether the DAQ is currently in record mode
daq.recording_sig.get()
```

| Check | Pass | Warn |
|---|---|---|
| `config_alias_sig` is not null | Report: "Config: BEAM" | "No configuration loaded — is DAQ configured?" |
| `experiment_name_sig` matches state `experiment` | ✓ | Mismatch → warn: "DAQ shows {X}, state has {Y} — please confirm" |
| State is `configured` (vs merely `connected`) | DAQ is fully configured | Detectors registered, safe to proceed |

> **Detector and PV validation:** The LCLS-II DAQ registers detectors and PVs
> through its internal config files, not via a Python API. If `config_alias_sig`
> returns a valid profile (e.g., "BEAM") and the state is `configured`, the
> operator's detector setup is in place. Flag if the alias looks wrong.
> Verification of specific PV lists requires checking the daqconfig GUI.

If `machine_state.detector_name` is set in state, include it in the report for
cross-reference: `"Detector (from hutch check): {detector_name}"`.

---

## Phase 1: Resolve Run Parameters

### `run_type` — required, parse first

`run_type` is the most important parameter. Parse it from the user's message.
Do not infer silently — if ambiguous, ask with the three options.

| User says | `run_type` | Notes |
|---|---|---|
| "dark", "pedestal", "background", "no-beam" | `DARK` | Uses `takepeds()` + `makepeds` |
| "geometry", "geom", "geometry calibration" | `GEOM` | Uses `geomrun()` |
| "data", "collect", "science run", "sample run" | `DATA` | Uses `autorun()` |

**`run_type` must always be uppercase** — this is the value LUTE reads to branch
to the correct analysis workflow. Never pass `'data'` (lowercase) when `'DATA'`
is expected.

### Duration

- For `DATA` and `GEOM`: `run_length` in seconds, or compute from events:
  `run_length = n_events / rep_rate_Hz` (from state). Report the estimate.
- For `DARK`: duration is set internally by `takepeds()` (steps through gain modes).
  Ask the user for event count only if they want to override the default.
  Default: 300 events per gain step.

### Sample (DATA only)

Use `sample_name` from state. Do not re-ask unless null.

---

## Phase 2: Confirm

Show a summary before starting. This is a write operation — always confirm.

**DARK:**
```
Proposed run:
  Run type  : DARK  (takepeds → makepeds pedestal deployment)
  Duration  : gain-mode scan (~300 events/step, ~5 steps for Jungfrau)
  Hutch/Exp : MFX / {experiment}
  Config    : {config_alias}  Detector: {detector_name}

Shall I start the pedestal run?
```

**GEOM:**
```
Proposed run:
  Run type  : GEOM  (LUTE will auto-process via run_type routing)
  Duration  : {run_length}s
  Sample    : geometry-calibration
  Hutch/Exp : MFX / {experiment}
  Config    : {config_alias}  Detector: {detector_name}

Shall I start the geometry run?
```

**DATA:**
```
Proposed run:
  Run type  : DATA  (LUTE will auto-process via run_type routing)
  Duration  : {run_length}s  (or ~{n_events} events at {rep_rate_Hz} Hz)
  Sample    : {sample_name}, {concentration}, {sample_delivery}
  Hutch/Exp : MFX / {experiment}
  Config    : {config_alias}  Detector: {detector_name}

Shall I start the data run?
```

Wait for explicit approval before proceeding.

---

## Phase 3: Execute Run

All run commands go through the IPython bridge. These are **write** operations —
confirmation has already been given in Phase 2.

### DARK — pedestal run

```python
# Step 1: Take pedestals (steps through all gain modes)
takepeds()
```

`takepeds()` runs a gain-mode stepping scan via `epixquad_pedestal_scan` and
records the pedestal XTC data. It blocks until complete.

After `takepeds()` returns:

```python
# Step 2: Get the run number
run_number = get_run()
```

Proceed to Phase 4 (XTC2 verification), then run `makepeds` (Phase 3b).

#### Phase 3b — Deploy pedestals (DARK only)

After XTC2 is confirmed, run `makepeds` via SSH to the psdev gateway. This is
a **write** operation — confirm before executing.

```bash
ssh -o ConnectTimeout=30 psdev "makepeds -e {experiment} -r {run_number} -u {operator_account}"
```

The operator account is typically `{hutch}opr` (e.g., `mfxopr`).

**If `makepeds` fails** (known issue for Jungfrau at MFX), fall back to the
direct Jungfrau tools. See `take-run/references/dark-run.md` for details.

Report success: `"Pedestals deployed to calibration store for run {run_number}."`

---

### GEOM — geometry calibration run

```python
geomrun(run_length={run_length}, record=True, runs=1)
```

`geomrun()` is a MFX wrapper that pre-sets `sample='geometry-calibration'`,
`tag='geom'`, and `run_type='GEOM'`. No extra arguments for run type needed.

After the call returns:
```python
run_number = get_run()
```

---

### DATA — science data collection

```python
autorun(
    sample='{sample_name}',
    tag='{sample_name}',
    run_length={run_length},
    record=True,
    run_type='DATA',   # must be uppercase — LUTE branches on this
    runs=1,
    picker='open',     # or None if picker should not change
)
```

After the call returns:
```python
run_number = get_run()
```

---

## Phase 4: Verify XTC2 Arrival

After `autorun`/`geomrun`/`takepeds` returns and the run number is known,
verify data landed on S3DF. **Mandatory and automatic — do not skip.**

```bash
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | grep "r{run_number:04d}"
```

**Pass:** files present and non-zero size — report size and file count.

**Fail (no files after 30s):** warn immediately:
> "No XTC2 files found for run {run_number} at `{xtc2_path}`.
> The run may not have recorded. Check DAQ status or ask the operator."

Retry once after an additional 30s before escalating.

Record `run_end_timestamp` (the time `autorun`/`geomrun`/`takepeds` returned)
for use in Phase 5.

---

## Phase 5: LUTE Job Monitoring

**This phase is identical for all run types** — LUTE branches internally based
on `run_type`. The agent does not submit any jobs; it only watches to confirm
that LUTE's automatic trigger fired.

### Step 5.1 — Inform the user

```
"Run {run_number} ({run_type}) complete. LUTE will automatically process this
run via the eLog ARP trigger — watching for the job to appear (~30s delay)."
```

(For DARK: LUTE handles pedestal processing in addition to `makepeds`. Report both.)

### Step 5.2 — Watch for LUTE jobs (unified poll)

Poll every 10s for up to 90s after `run_end_timestamp`:

```bash
squeue -u {hutch}opr --format="%.18i %.40j %.8T" --noheader
```

Filter for jobs that appeared **after** `run_end_timestamp` whose names contain
workflow keywords: `lute`, `dag`, `sfx`, `xes`, `crystfel`, `smalldata`, `geom`, `dark`, `peds`.

If `lute_config.workflows` is set in state, also match against those specific names.

**Job found:**
```
"LUTE job {job_id} ({job_name}) detected — {state}"
```

Poll once more ~30s later:
```bash
squeue -j {job_id} --noheader --format="%i %T"
```
- Still in queue → report current state (PENDING / RUNNING)
- Left queue → check final status:
  ```bash
  sacct -j {job_id} --noheader --format=State,ExitCode
  ```
  Report: `"Job {job_id}: COMPLETED (0:0)"` or `"FAILED — check logs at {lute_output_dir}/logs/"`

**No job after 90s:**
```
"No LUTE job detected. Verify in the eLog run table:
https://pswww.slac.stanford.edu/lgbk/lgbk/{experiment}/runs"
```

---

## Phase 6: Update State + Log

Update state JSON via coordinate-experiment handoff:

```json
{
  "last_run_number": {run_number},
  "last_run_tag": "{run_type}"
}
```

Minimum log entry:

```markdown
- **{HH:MM}** Run {run_number} ({run_type}) — {run_length}s.
  XTC2: {file_size} ({n_files} files). Sample: {sample_name}.
  LUTE: job {job_id} — {state}.    ← or "no job detected" / "pedestals deployed (makepeds)"
```

---

## → coordinate-experiment handoff (mandatory)

After Phase 6 completes (or after any error that ends the run), pass to
coordinate-experiment for ambient logging. Do this even if the run failed.

---

## Error Handling

| Symptom | Response |
|---|---|
| `daq.state` = `error` | Hard stop. Ask operator to reset DAQ. Do not attempt to run. |
| `takepeds()` raises exception | Report error; ask operator to check detector config and DAQ state. |
| `makepeds` fails | Provide fallback Jungfrau commands. See `take-run/references/dark-run.md`. |
| No XTC2 files after 60s | Warn; check `daq.state`; ask operator. |
| LUTE job not detected after 90s | Point to eLog run table. Do not re-submit. |
| Bridge not connected | Provide commands for user to run manually. See section below. |

---

## Bridge Not Available

If the bridge is not connected, provide commands for the user to run manually
in their hutch-python session:

**DARK:**
```python
takepeds()
# then after completion:
run_number = get_run()
# then from the terminal:
# ssh psdev "makepeds -e {experiment} -r {run_number} -u {hutch}opr"
```

**GEOM:**
```python
geomrun(run_length=60, record=True)
run_number = get_run()
```

**DATA:**
```python
autorun(sample='{sample_name}', run_length={run_length}, record=True, run_type='DATA')
run_number = get_run()
```

After the run completes, infer the run number from state (`last_run_number + 1`)
and confirm via XTC2 directory:
```bash
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | head -5
```
