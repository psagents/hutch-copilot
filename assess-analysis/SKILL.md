---
name: assess-analysis
description: >
  Tracks a submitted LUTE analysis job for hutch-copilot and interprets what it produced.
  Watches for the job, reads its logs directly off disk, reports the metrics that exist,
  names the limiting factor and recommends one knob — or says the data are good enough.
  Setting up and submitting workflows is `setup-analysis`. Triggers on: how does the data
  look, assess the run, is the dataset good, sfx metrics, read the merge log, did the
  analysis work, what did the analysis say.
---

# /assess-analysis - LUTE analysis assessment

You own everything **after a job id exists**: watching it, reading its output, and the
verdict. Standing up and submitting workflows is `setup-analysis` — if no job was ever
submitted, go there rather than submitting one here.

Science interpretation comes from `@ask-lute` — thresholds, the per-shell reading, which
knob answers a symptom. It is a consultant, not a dependency: if it is missing, say so,
report the numbers you read, and reason from them.

**Inspecting output is your job, not the operator's.** `hutch-lute status` returns the log
paths; read those files. Never ask for a paste.

---

## 1. Did it run?

```bash
bin/hutch-lute watch --user "$USER" --since "$SUBMIT_TIME"
bin/hutch-lute status "$JOB_ID"
```

`watch` reports `polls` and `waited_s`, so **"no job found" is distinguishable from "did
not look"**. If `found` is false after a full timeout with non-zero `polls`, the workflow
never fired — that is a registration or trigger problem, **not an analysis result**:

- re-run `bin/hutch-lute readiness` and check the trigger the workflow was registered with
- **do not re-submit.** A job the eLog ARP owns must not be duplicated; a duplicate on one
  run is worse than a missing one. Report it and hand back to `/setup-analysis`.

---

## 2. What Slurm says is not what the science says

Two traps, both of which have produced confident wrong reports:

- **`COMPLETED` is a Slurm verdict only.** `run_task.py` exits 0 on Task failure, so a
  Task can fail inside a job Slurm considers successful. Never report success on job
  state alone — confirm output exists.
- **Check `hit_wall_clock`.** If true the run was truncated, its statistics are
  coverage-limited, and it must not be compared against a complete run.

---

## 3. Read the output

`status` returns the output paths. Read them directly. Report, per metric, the value and
where you read it.

When reporting counts from a scan of many per-event files, **say how many files you
scanned** — a truncated scan produces a plausible wrong total silently.

Report `events_attempted` and the yield alongside any count of indexed lattices. Under a
fixed wall clock a raw count is `coverage × yield`, so two jobs with identical parameters
can differ purely because one attempted more events. Compare on yield, not on the count.

---

## 4. Verdict

Say which of these it is, in one line, then the evidence:

- **good enough — stop.** Say so plainly; do not invent a next iteration.
- **limited by X, change one knob.** Name the limiting factor and the single parameter to
  change, with the number you read that says so. Ask `@ask-lute` for the knob if the
  symptom is not obvious.
- **it failed.** Quote the error from the log, give the operator the path, and suggest the
  next step.

Two rules on honesty, both of which outrank being helpful:

**Report the metrics that exist and name the ones you could not interpret** rather than
estimating them. Ask the operator about those instead of guessing.

**Never rank two jobs whose resolution cutoffs differ.** Overall merge statistics are
monotone in the cutoff, so tightening it improves them on identical data. That is a
re-cut, not an improvement, and must not be presented as one — judge a cutoff on the
per-shell table, and compare jobs only at equal cutoff.

---

## 5. Record it

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" last_analysis_verdict "<one line>" \
    --source "read from <log path>" --kind resolved
```

Then tell the operator: the job and its state, the metrics with their provenance, the
verdict, and the single recommended action.
