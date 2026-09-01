---
name: take-run
description: >
  DAQ run execution sub-skill for hutch-copilot. Starts DARK (pedestal), GEOM
  (geometry calibration), or DATA (science) runs through the hutch-python IPython
  bridge. Validates DAQ state, confirms run parameters with the operator, executes
  the run, verifies XTC2 arrival on S3DF, and watches for the LUTE trigger.
  Triggers on: /take-run, /takerun, take a run, start collecting, begin a run,
  take data, take a dark run, take a pedestal run, take a geometry run,
  take a data run.
---

# /take-run — Start a DAQ Run

All mechanism is in `hutch-tools` (see the Tool Layer section of `../SKILL.md`). This file
owns the gate, the confirmation, and the triage.

---

## 1. Context

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"    # everything known, with age
bin/hutch-resolve daq --hutch "$H"                      # state, config_alias, experiment, recording, run
```

`MissingValue` → ask the operator. `StaleValue` → re-resolve, or `--stale-ok` and report the
age. A detector name recorded last shift will tell you it is old rather than looking current.

Fields consumed: `sample_name`, `concentration`, `sample_delivery`, `photon_energy_eV`,
`rep_rate_Hz`, `transmission`, `lute_config`, `machine_state.detector_name`.

**Hard gate on DAQ state — do not proceed past this:**

| `state` | Action |
|---|---|
| `configured` | proceed |
| `connected` | proceed; DAQ configures on run start |
| `running` | **stop.** "DAQ is already in a run. End it first?" |
| `error` | **stop.** Operator must reset. |
| `unallocated` / `allocated` / `reset` | **stop.** DAQ not connected. |

Cross-check `daq.experiment` against state `experiment`. A mismatch is a warning to the
operator, never something to accept silently. If `config_alias` is null, say so — the DAQ
may not be configured.

---

## 2. Resolve run parameters

`run_type` first, and it is **uppercase**: LUTE's `!run_type` DAG keys are `DARK`/`GEOM`/`DATA`
and a lowercase value matches no branch. Checked, not remembered:

```bash
bin/hutch-lute traps --scope run-params    # the assertion and the evidence behind it
```

| Operator says | `run_type` | Command |
|---|---|---|
| dark, pedestal, background, no-beam | `DARK` | `takepeds()` then `makepeds` |
| geometry, geom, calibration | `GEOM` | `geomrun()` |
| data, collect, science, sample | `DATA` | `autorun()` |

Duration: `run_length` in seconds, or `n_events / rep_rate_Hz` from state — report the
estimate and which input you used. For `DARK`, `takepeds()` sets its own duration by
stepping gain modes (~300 events/step); ask for an event count only to override.

If `run_type` is ambiguous, ask with the three options. Never infer it silently.

---

## 3. Confirm — this is a write

Show what you will run, its safety class, and the parameters. Get the class from the
classifier rather than asserting it yourself:

```bash
bin/hutch-bridge --hutch "$H" classify \
  "autorun(sample='Lyso', run_length=60, record=True, run_type='DATA')"
```

```
Proposed run
  Type      : DATA   (LUTE routes on run_type)
  Duration  : {run_length}s  (~{n_events} events at {rep_rate_Hz} Hz)
  Sample    : {sample_name}, {concentration}, {sample_delivery}
  Hutch/Exp : {HUTCH} / {experiment}
  Config    : {config_alias}   Detector: {detector_name}
  Class     : write ({classifier reasons})

Shall I start the run?
```

Wait for explicit approval. Then execute with the class you were actually granted:

```bash
bin/hutch-bridge --hutch "$H" --timeout 300 --confirm write exec \
  "autorun(sample='{sample}', tag='{sample}', run_length={n}, record=True, run_type='DATA', runs=1, picker='open')"
bin/hutch-bridge --hutch "$H" exec 'get_run()'
```

`GEOM`: `geomrun(run_length={n}, record=True, runs=1)` — it presets sample, tag and
`run_type` itself.

**`DARK` is two steps, and the second is a separate write.** `takepeds()` records the data
through the bridge (self-timed by the gain-mode scan — do not pass a duration). Only **after
XTC2 is confirmed** does the analysis side run:

```bash
bin/hutch-lute peds --experiment "$E" --run {run} --hutch "$H" \
    --detector "{detector}" --confirm write
```

The tool refuses if no XTC2 has landed, and if `makepeds` fails it names the per-detector
fallback and how to run it — `makepeds` has documented failures for Jungfrau at MFX. Confirm
this step with the operator separately from the run itself.

Record the time the run command returned — `watch` needs it in step 4.

---

## 4. Verify and watch — mandatory, do not skip

```bash
bin/hutch-resolve xtc2 --hutch "$H" --experiment "$E" --run {run}
bin/hutch-lute watch --user "${H}opr" --since "$RUN_END_ISO" --timeout 90
bin/hutch-lute status "$JOB_ID"
```

`watch` reports how long it waited and how many times it polled, so "no job found" cannot be
confused with "did not look". You do **not** submit anything here — LUTE triggers from the
eLog ARP; you only confirm it fired.

Triage:

| Symptom | Response |
|---|---|
| `xtc2` reports `present: false` after 30 s | Retry once at 60 s, then warn: the run may not have recorded. Check `daq.state`. |
| No LUTE job after 90 s | Point at the eLog run table. **Do not re-submit.** |
| Job `COMPLETED` but no output | `run_task.py` exits 0 even when the Task failed — check the Executor log. `hutch-lute status` also flags `hit_wall_clock`. |
| `takepeds()` raises | Report it; ask the operator to check detector config and DAQ state. If it fails on an import, it may have sourced an analysis conda env — have them run `epixquad_pedestal_scan --record 1 --hutch {hutch}` from the DAQ terminal. |
| `makepeds` fails | Expected for Jungfrau at MFX. Re-run `hutch-lute peds --fallback --detector {detector} --confirm write`; the tool reports the reason and the commands. |
| Bridge offline | Give the operator the commands to run by hand, then infer the run number from `last_run_number + 1` and confirm it against the XTC2 directory. |

---

## 5. Handoff (mandatory, even on failure)

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" last_run_number {run} \
    --source "get_run() via ${H}-daq after autorun" --kind resolved
bin/hutch-state set --hutch "$H" --experiment "$E" last_run_tag "{run_type}" \
    --source "run_type confirmed with operator" --kind operator
```

Note the two `--kind` values: the run number was measured, the run type was asserted by a
human. A later reader can tell them apart.

Then pass to coordinate-experiment for ambient logging. Minimum log entry:

```markdown
- **{HH:MM}** Run {run} ({run_type}) — {run_length}s. XTC2: {size} ({n} files).
  Sample: {sample}. LUTE: job {job_id} — {state}.
```
