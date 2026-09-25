---
name: analyze-data
description: >
  Reads LUTE analysis output and judges the dataset for hutch-copilot. Finds the job,
  reads its logs, extracts the SFX metrics, names the limiting factor, and says whether
  to collect more or stop. Does NOT configure LUTE — workspace setup, DAG patching and
  eLog workflow registration happen outside this skill, before a shift. Calibration runs
  (GEOM, DARK) are handled by /take-run via run_type tags. Works entirely offline from
  S3DF. Triggers on: how does the data look, assess the run, is the dataset good, check
  output, analyze data, indexing rate is low, sfx metrics, read the merge log, one more
  run, are we done collecting.
---

# analyze-data

You read what LUTE produced and say what it means. You control no hardware, you run no
calibration (`/take-run` handles GEOM and DARK via `run_type` tags), and **you do not
configure LUTE** — see "Scope" below.

All mechanism is in `hutch-tools` (see the Tool Layer section of `../SKILL.md`). This file
owns the judgment: which number is limiting, and whether the dataset is done.

**Reference:** `../references/sfx-cctbx-patterns.md` — the CCTBX DAG pattern, the `d_min`
ranking hazard, coverage×yield, and the symptom→knob table.
**Optional advisors:** `@ask-lute` (LUTE internals), `@ask-cctbx-xfel` (SFX parameters),
`@ask-smalldata` (SmallData ROI / droplet / azimuthal integration), `@ask-lcls2` (psana2),
`@lcls-catalog` (file and XTC2 inventory). None is required — check with
`hutch-resolve skills` and, if one is missing, say so and reason from the reference.

---

## Scope — what this skill does not do

LUTE **configuration** is out of scope for these tools: no config rendering, no workspace
creation, no DAG patching, no eLog workflow registration. Those are writes that happen
once, outside a shift, by hand.

If no workflow fires after a run, or the config needs changing, say so plainly and name
what a human has to do — patch the YAML, or check the eLog ARP registration. Do not
improvise a substitute. A YAML-only change needs no re-registration; the next trigger
picks it up.

---

## 1. Context

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"
bin/hutch-state path  --hutch "$H" --experiment "$E"
```

`MissingValue` → ask the operator. `StaleValue` → re-resolve, or `--stale-ok` and report
the age. Do not assume a value in either case. Get the experiment root from
`hutch-state path` — never interpolate it.

---

## 2. Find the job

If a run has just finished and the eLog ARP should have triggered a workflow:

```bash
bin/hutch-lute watch --user "$USER" --since "$SUBMIT_TIME"
```

`watch` reports `polls` and `waited_s`, so **"no job found" is distinguishable from "did
not look"**. If `found` is false after a full timeout, the workflow did not fire — report
that as a registration or trigger problem, not as an analysis result.

```bash
bin/hutch-lute status "$JOB_ID"
```

`COMPLETED` is a Slurm verdict, not a scientific one — a Task can fail inside a job that
Slurm considers successful. Check `hit_wall_clock`: if true, the run was truncated and its
statistics are coverage-limited, which matters for rule 2 below.

---

## 3. Read the output yourself

`status` returns the job's output paths. **Read those files directly — do not ask the
operator to paste log contents.** The CCTBX index, scale and merge logs carry the numbers
you need.

Report the metrics that exist, and say which ones you could not find rather than
estimating them:

| Metric | What it tells you |
|---|---|
| CC½ | signal in the outer resolution shell |
| Completeness | fraction of unique reflections measured |
| Multiplicity | how many times each was measured |
| Rsplit | self-consistency between half-datasets |
| indexing rate, `n_indexed` | how much of the data is usable at all |

---

## 4. The verdict — name the limiting factor

Say whether to keep collecting or stop, and **name the number that decided it**. "The data
looks good" is not a verdict; "multiplicity is 4.2 against a target above 5, so one more
run" is.

Three rules the reference campaign paid for:

1. **Check the funnel before touching geometry.** A low indexing rate is not reliably a
   geometry problem; the one large effect found in ten iterations was a hit-finder floor
   set too high, discarding 407 frames that carried ≥20 spots.
2. **Compare on yield, not `n_indexed`.** Under a wall-clock limit `n_indexed` is
   coverage × yield, so two runs with identical parameters can differ by 35%. This is why
   `hit_wall_clock` from `status` matters.
3. **Never rank two runs with different `d_min`.** All three overall statistics are
   monotone in it — cutting `d_min` improves them without adding information.

Then work the symptom→knob table in `../references/sfx-cctbx-patterns.md`. Change **one
concept per iteration**, state the expected readout *before* the next run, and record a
null result as a result.

> **Publication thresholds are not settled.** CC½ > 0.5 in the outer shell, completeness
> > 95%, multiplicity > 5, Rsplit < 30% are plausible working numbers, **not** confirmed
> reference values (PLAN.md TBD-3 — needs a crystallographer). Quote them as the working
> targets they are, and say who has not yet signed off.

---

## → coordinate-experiment handoff (mandatory)

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" analysis_verdict \
    '{"job_id": "'"$JOB_ID"'", "limiting_factor": "multiplicity", "recommend": "one more run"}' \
    --source "cctbx merge log $LOG_PATH, job $JOB_ID" \
    --kind resolved
```

The `--source` must name the log you actually read, so the verdict can be re-derived.
Then pass to coordinate-experiment for logging.
