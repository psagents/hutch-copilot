---
name: take-run
description: >
  DAQ run execution sub-skill for hutch-copilot. Starts DARK (pedestal), GEOM
  (geometry calibration), or DATA (science) runs through the hutch-python CLI.
  Validates DAQ state, confirms run parameters with the operator, executes
  the run, verifies XTC2 arrival on S3DF, and watches for the LUTE trigger.
  Triggers on: /take-run, /takerun, take a run, start collecting, begin a run,
  take data, take a dark run, take a pedestal run, take a geometry run,
  take a data run.
---

> Part of `hutch-copilot`: its rules apply here, and its commands run from that skill's
> directory (`bin/hutch-*`). If it is not loaded in this session, load it first.

# /take-run — Start a DAQ Run

This file owns the gate, the confirmation, and the verdict on what came back.

---

## 1. Context and the gate

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"    # everything known, with age
```

and the DAQ state: `bin/hutch-daq --hutch "$H" state` in `MODE: local`, `daq_state()` in `MODE: mcp`.


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

**This table is MFX vocabulary, not a facility contract.** `geomrun`/`takepeds` exist
only at MFX. CXI has an `autorun` with an *incompatible* signature —
`autorun(sample_name=…, runs=, delay=, pulsepicker=)` against MFX's
`autorun(sample=…, tag=, run_type=…)` — so an MFX snippet raises `TypeError`
part-way through a run there. RIX and TMO are scan-shaped and have no run verb at
all. At any hutch other than MFX, confirm the function and its parameters before
sending; do not translate this table.

---

## 3. Confirm — this is a write

Tell the operator the command, the duration and the sample first. **You cannot approve it
yourself**, in any session:

- **`MODE: local`**: send it **without `--confirm`** first. It prints the class and its reasons
  and refuses (rc 5) without running anything. Show those to the operator, wait for explicit
  approval, then re-send with the class they actually granted:
  ```bash
  bin/hutch-py --hutch "$H" --timeout 300 --confirm write run "<run command>"
  ```
- **`MODE: mcp`**: `hutch_py_run(code="<run command>")`. The server classifies it and, for a
  write, shows the operator a form with the class, its reasons and the code. It runs only if they
  type the class (`write`, `beam-critical`) and accept. A caller the server's policy does not
  grant is refused before the form: report it as it is.
- **No `MODE:` line** (a terminal off the hutch): there is no route to the hutch here. Say so,
  and send the operator to Cogito or to Claude Code on `$H-daq`.

A decline, a cancel, or a client that cannot show the form is final, and nothing ran: report it, and never retry to wear them down. Then read the DAQ state again for
`run_number` / `last_run_number`.

`run` launches a fresh device load and needs no live session. It pays that load on
every call (tens of seconds), so batch a multi-step sequence into one snippet rather
than issuing several. `rc 1` means the snippet itself raised — the traceback is in
`error`; `rc 4` means the launcher or hutch could not be reached; `rc 5` means the
class confirmed was weaker than the snippet's.

The run number comes from the DAQ, not from `get_run()`. `get_run()` is an HTTP GET
against the elog (`mfx/macros.py:619-622`) despite its docstring claiming otherwise, so
it lags the DAQ and needs off-hutch HTTPS. The DAQ state already reports
`run_number` and `last_run_number` from `DaqControl.getStatus()`.

**`DARK` is two writes, and the second is gated.** `takepeds()` records through
`hutch_py_run`; only **after XTC2 is confirmed** does the analysis side run, confirmed with the
operator separately from the run itself:

```bash
bin/hutch-lute peds --experiment "$E" --run {run} --hutch "$H" \
    --detector "{detector}" --confirm write
```

Record the time the run command returned — `watch` needs it next.

---

## 4. Verify and watch — mandatory, do not skip

```bash
bin/hutch-data xtc --hutch "$H" --experiment "$E" --run {run}
bin/hutch-lute watch --user "${H}opr" --since "$RUN_END_ISO" --timeout 90
bin/hutch-lute status "$JOB_ID"
```

Two judgments, neither of which is in the JSON:

- **You do not submit anything.** LUTE triggers from the eLog ARP; you only confirm it
  fired. If no job appears, say so and hand it to `/setup-analysis`, which owns the
  workflow registration and can check it. **Do not
  re-submit** — a duplicate job on the same run is worse than a missing one.
- **`COMPLETED` is a Slurm verdict, not a scientific one.** A Task can fail inside a job
  Slurm considers successful.

If XTC2 did not land, or a run call raised, hand it to `/debug-daq` with the run number and
the time you attempted it — it collects the readings and routes them to `@psana-daq`. Check
`hutch-lute status` yourself; that half is the analysis side. Never report a run as recorded
on the strength of the command having returned.

---

## 5. Handoff (mandatory, even on failure)

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" last_run_number {run} \
    --source "daq_state run_number after autorun" --kind resolved
bin/hutch-state set --hutch "$H" --experiment "$E" last_run_tag "{run_type}" \
    --source "run_type confirmed with operator" --kind operator
```

Note the two `--kind` values: the run number was measured, the run type was asserted by a
human. A later reader can tell them apart.

Then tell the operator what landed: run number and type, duration, XTC2 size and file
count, sample, and the LUTE job with its state.
