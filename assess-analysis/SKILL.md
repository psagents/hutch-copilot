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

You hold the **decision** — good enough, one knob, failed, iterate — and no tools or
thresholds of your own. The facts and the criteria come from the advisors:

| Analysis | Facts: parser | Criteria |
|---|---|---|
| BayFAI | `@ask-lute` `bayfai/parse_bayfai_log.py` | `@ask-lute` `bayfai/SKILL.md` |
| SmallData | `@ask-lute` `smalldata/parse_smd_output.py` | `@ask-lute` `smalldata/SKILL.md` |
| CCTBX SFX | `@ask-lute` `cctbx/parse_merge_log.py` | `@ask-cctbx-xfel` `references/merge-assessment.md` |

`setup-analysis` recorded the parse command as `lute_parse` in `hutch-state`. **These
advisors are required here, because the verdict is exactly that interpretation.** If one is
missing, follow the required-advisor contract in `SKILL.md`: fetch it and read it; only if
that fails, report every number with the path you read it from, say which advisor is
missing and that you cannot interpret them, and stop — do not rank runs, recommend a knob,
or call a dataset good from a threshold you remember. Ask the operator for the threshold
instead and record it `--kind operator` if they give you one.

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

`status` returns the output paths. Run the analysis's parser on them and report, per
metric, the value and the file it came from. Read the log itself too when the parser
lists `errors` or `missing_sections` — those say where to look, not what happened.

When reporting counts from a scan of many per-event files, **say how many files you
scanned** — a truncated scan produces a plausible wrong total silently.

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

## 4b. CCTBX: iterating a multi-run merge

Per-run index and scale fire on their own (`cctbx_index_scale`, `END_OF_RUN`). The merge
is yours to fire, and the loop is:

1. **Which runs are in the dataset**, and are they all scaled:

   ```bash
   bin/hutch-lute runs -e "$E" --tag <tag>            # or --sample <sample>
   bin/hutch-lute db --db <work_dir>/lute.db --task ScaleCCTBXXFEL --runs <those runs>
   ```

   `all_completed: false` names each run still `ABSENT` or `FAILED`. Merge anyway if the
   operator wants a look now — the merge takes only scaled runs — but say which are out.
2. **Where the merge goes**: `bin/hutch-lute merge-version --work-dir <work_dir> --tag
   <tag> --create`. Render the config with that `merge_version` (`setup-analysis` §3) so
   iterations never overwrite each other.
3. **Fire it**: `cctbx_merge` (or `cctbx_rescale` after a d_min change) is a `MANUAL`
   workflow; launch it from the eLog, or submit with the `launch_slurm` the workflow runs
   on.
4. **Read it**: `parse_merge_log.py` on `merge_<tag>/<vNNN>/merge_<tag>_main.log`, then
   the per-shell criteria in `@ask-cctbx-xfel` `references/merge-assessment.md`.
5. **A d_min change is the operator's call.** Show the shells that support it and record
   their answer: `bin/hutch-state set ... cctbx_d_min <value> --kind operator`. Then
   `cctbx_rescale` at the new d_min — no re-indexing.

Compare merges only at equal d_min (§4).

---

## 5. Record it

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" last_analysis_verdict "<one line>" \
    --source "read from <log path>" --kind resolved
```

Then tell the operator: the job and its state, the metrics with their provenance, the
verdict, and the single recommended action.
