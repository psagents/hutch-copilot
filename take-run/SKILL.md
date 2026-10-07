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

This file owns the gate, the confirmation, and the verdict on what came back.

---

## 1. Context and the gate

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"    # everything known, with age
bin/hutch-resolve check-daq --hutch "$H"
```

`MissingValue` → ask the operator. `StaleValue` → re-resolve, or `--stale-ok` and report
the age. A detector name recorded last shift will tell you it is old rather than looking
current.

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

## 2. Run parameters

`run_type` first, and it is **uppercase**: LUTE's `!run_type` DAG keys are
`DARK`/`GEOM`/`DATA` and a lowercase value matches no branch.

| Operator says | `run_type` | Session call |
|---|---|---|
| dark, pedestal, background, no-beam | `DARK` | `takepeds()`, then `hutch-lute peds` |
| geometry, geom, calibration | `GEOM` | `geomrun()` — presets sample, tag and run_type itself |
| data, collect, science, sample | `DATA` | `autorun()` |

Duration: `run_length` in seconds, or `n_events / rep_rate_Hz` from state — report the
estimate and which input you used. `takepeds()` is self-timed by its gain-mode scan; do not
pass it a duration.

If `run_type` is ambiguous, ask with the three options. Never infer it silently.

---

## 3. Confirm — this is a write

Run the command **without `--confirm`** first: it prints the classification and refuses
(rc 5) without transmitting anything. Show the operator the command, the class and its
reasons, the duration and the sample, and wait for explicit approval. Then re-send with
the class you were actually granted:

```bash
bin/hutch-daq --hutch "$H" --timeout 300 --confirm write exec "<run command>"
bin/hutch-daq --hutch "$H" exec 'get_run()'
```

**`DARK` is two writes, and the second is gated.** `takepeds()` records through the
bridge; only **after XTC2 is confirmed** does the analysis side run, confirmed with the
operator separately from the run itself:

```bash
bin/hutch-lute peds --experiment "$E" --run {run} --hutch "$H" \
    --detector "{detector}" --confirm write
```

Record the time the run command returned — `watch` needs it next.

---

## 4. Verify and watch — mandatory, do not skip

```bash
bin/hutch-resolve xtc2 --hutch "$H" --experiment "$E" --run {run}
bin/hutch-lute watch --user "${H}opr" --since "$RUN_END_ISO" --timeout 90
bin/hutch-lute status "$JOB_ID"
```

Two judgments, neither of which is in the JSON:

- **You do not submit anything.** LUTE triggers from the eLog ARP; you only confirm it
  fired. If no job appears, say so and hand it to `/analyze-data`, which owns the
  workflow registration and can check it. **Do not
  re-submit** — a duplicate job on the same run is worse than a missing one.
- **`COMPLETED` is a Slurm verdict, not a scientific one.** A Task can fail inside a job
  Slurm considers successful.

If XTC2 did not land, or a run call raised, investigate with `hutch-daq logs`,
`hutch-daq config` and `hutch-lute status`, and report what you found. Never report a run
as recorded on the strength of the command having returned.

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

Then tell the operator what landed: run number and type, duration, XTC2 size and file
count, sample, and the LUTE job with its state.
