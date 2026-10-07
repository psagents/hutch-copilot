---
name: analyze-data
description: >
  Owns analysis for hutch-copilot, both halves. Sets up LUTE workflows — renders the
  config YAML, patches DAGs, creates the workspace and venvs, registers the workflow with
  the eLog — and then tracks the jobs it triggered, reads their output, interprets results.
  Calibration runs (GEOM, DARK) belong to /take-run via run_type tags. Triggers on: set up 
  LUTE, configure analysis, render the config, register the workflow, patch the DAG, is 
  LUTE ready, how does the data look, assess the run, is the dataset good, analyze data.
---

# analyze-data

You own everything that is data analysis, everything that stands after data collection,
from setting up analysis workflows, to interpreting the results.

You own the **setup**, not the **domain knowledge**. LUTE task names, YAML syntax and DAG
structure come from `@ask-lute`; the science parameters from `@ask-cctbx-xfel` (SFX) and
`@ask-smalldata` (SmallData ROI, droplet/photon, azimuthal integration). Consult them
before writing a config. They are consultants, not dependencies — if one is missing, say
so, show the operator the rendered YAML, and proceed on your own reasoning.

**Reference:** `../references/sfx-cctbx-patterns.md` — the CCTBX DAG pattern, the `d_min`
ranking hazard, coverage×yield, and the symptom→knob table.

---

## 1. Context

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"
```

`MissingValue` → ask the operator. `StaleValue` → re-resolve, or `--stale-ok` and report
the age. Do not assume a value in either case. Get the experiment root from
`hutch-state path` — never interpolate it.

---

## Part 1 — Set up the workflows

### Check first, and stop if it is already done

```bash
bin/hutch-lute readiness --hutch "$H" --experiment "$E"
```

**`configured: true` → stop.** Report what it found — the config YAML, the DAGs, the
`lute.db`, and `recorded.lute_config` — and do not rebuild. Setting up again destroys
working state. Proceed only if the operator explicitly asks to rebuild.

`configured: false` → continue. Note that `readiness` cannot check eLog registration
(that needs a ticket), so it says so in `not_checked` rather than guessing.

### Identify the appropriate analysis workflow

Call `@ask-lute` for the appropriate analysis workflow based on the current experiment context.
If needed more insight, consult `@ask-cctbx-xfel` and `@ask-smalldata` for more science about
the choice of analysis workflow.

### Configuration YAML setup

Once workflow is identified, set up the configuration YAML accordingly.
Look at the available templates and choose the one that matches the identified workflow.
Consult advisors `@ask-lute`, `@ask-cctbx-xfel`, and `@ask-smalldata` for guidance on the parameter
values.

```bash
bin/hutch-lute render --list-templates
bin/hutch-lute render --template <template> \
    --whitelist-dir "$LUTE_PATH/config/templates" \
    --set-context experiment="$E" --out <config path>
```

**`--whitelist-dir` is not optional in practice.** Without it — or an explicit
`--no-whitelist` — render refuses.

Read the report it prints back:

- **`inserted_keys` is the one to check.** An `inserted` where you expected `replaced`
  means the key was not where you thought it was — that is a misspelling announcing
  itself, not a successful override.
- Use `--set TASK.BLOCK.KEY=value` to change a parameter, `=__DELETE__` to remove one.

### Plan before you write anything

```bash
bin/hutch-lute plan --hutch "$H" --experiment "$E" --workflow <name> --trigger <trigger>
```

`plan` touches nothing and prints everything: every precondition check, the exact eLog
payloads, and a `destructive` warning if the venv tree already exists. **Show it to the
operator before any step below.** A non-empty `blocking` list means the install will
refuse anyway.

`--trigger END_OF_RUN` is what makes a finished run fire its analysis. The four forms are
`START_OF_RUN`, `END_OF_RUN`, `MANUAL` and `RUN_PARAM_IS_VALUE:<name>:<value>`; triggers
pair positionally with `--workflow`, so order matters when there is more than one.

### Install

```bash
bin/hutch-lute workspace --hutch "$H" --experiment "$E"                     # idempotent
bin/hutch-lute dags      --hutch "$H" --experiment "$E" --workflow <name>   # idempotent
bin/hutch-lute envs      --hutch "$H" --experiment "$E" --confirm write
bin/hutch-lute register  --hutch "$H" --experiment "$E" --workflow <name> \
    --trigger <trigger> --confirm write
```

Or `hutch-lute all --confirm write`, which runs those four in order after checking
preconditions. Two things about it: `envs` runs **before** `register`, so a registration
failure leaves the venvs already rebuilt; and the same `--confirm write` satisfies both
gates.

**`envs` deletes the entire venv tree and reinstalls** — it is not a repair in place.
Never run it while a workflow is executing from those venvs.

**`register` needs a ticket at `FILE:$HOME/krb5cc.ticket` specifically**, not the ambient
cache. If it is missing the error prints the exact `kinit` line to hand the operator.
`hutch-lute kerberos --yaml <config>` says whether this config needs a ticket at all — it
reports, it does not gate.

---

## Part 2 — Track the jobs

```bash
bin/hutch-lute watch --user "$USER" --since "$SUBMIT_TIME"
bin/hutch-lute status "$JOB_ID"
```

`watch` reports `polls` and `waited_s`, so **"no job found" is distinguishable from "did
not look"**. If `found` is false after a full timeout the workflow did not fire — that is
a registration or trigger problem, not an analysis result. You can now answer it in this
skill: re-run `readiness`, and check the trigger the workflow was registered with. **Do
not re-submit** a job the eLog ARP owns.

`COMPLETED` is a Slurm verdict, not a scientific one — a Task can fail inside a job that
Slurm considers successful. Check `hit_wall_clock`: if true, the run was truncated and its
statistics are coverage-limited.

---

## Part 3 — Read the output yourself

`status` returns the job's output paths. **Read those files directly — do not ask the
operator to paste log contents.**

Report the metrics that exist, and say which ones you could not understand rather than
estimating them. Ask the operator for clarification in that case. The `references/` 
directory may provide additional help for basic interpretation.

If results look bad, document the issues clearly and provide any relevant logs or metrics 
to the operator for further investigation, suggest next steps or corrective actions,
if possible.

---
