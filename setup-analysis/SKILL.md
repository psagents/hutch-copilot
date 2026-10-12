---
name: setup-analysis
description: >
  Stands up a LUTE analysis workflow for hutch-copilot and submits it. Renders the config
  YAML, patches DAGs, creates the workspace and venvs, registers the workflow with the
  eLog, submits. Ends the moment a job id exists — reading the output is
  `assess-analysis`.
  Triggers on: set up LUTE, configure analysis, render the config, register the workflow,
  patch the DAG, is LUTE ready, submit the analysis, analyze data.
---

# setup-analysis

You own everything up to **a job id existing**. You stop there.

You own the **setup**, not the **domain knowledge**. LUTE task names, YAML syntax, DAG
structure and science parameters come from `@ask-lute`.

**A missing `@ask-lute` does not block a submit** — that is the one place this skill
differs from the rest, and it is deliberate: the blocker list below is exhaustive, and an
unreviewed submit beats a stalled session. Say it is missing, show the operator the
rendered YAML so they can see exactly what will run, name the parameters that went
unreviewed, and submit. What the resulting numbers *mean* is `assess-analysis`, and there
the advisor is required.

---

## The precondition contract — read this before anything else

**Only the four blockers below may stop you.** Resolving facts is not progress toward a
submit; this is a hard rule, not a preference.

**Blocks a submit — these only:**

| Blocker | How you know |
|---|---|
| config does not render | `hutch-lute render` fails |
| the named DAG is absent | `hutch-lute plan` → non-empty `blocking` |
| no XTC2 on disk | `hutch-data xtc2` |
| no valid ticket, *and* this config needs one | `hutch-lute kerberos --yaml <cfg>` |

**Does NOT block a submit — never wait on these:**

geometry quality or provenance · which run was the calibrant · the optimal `d_min` ·
detector aliases beyond what the config already names · whether indexing parameters are
ideal · anything that needs the eLog when the eLog is unreachable.

**Submit, then read the result.** A failed job that names its failure in a log is cheaper
than an unsubmitted one, and `assess-analysis` is built to read exactly that. If a value
is unknown, record it as unknown, use the config's default, say which default you used,
and submit. Resolving one fact is worth it only when a *blocker* above depends on it.

---

## 1. Context — one call

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"
```

Use what is there. Ask the operator for a missing field **only if a blocker needs it** —
not because the skill would like to know it. Get the experiment root from
`hutch-state path`; never interpolate it.

---

## 2. An existing install → offer a fresh directory

```bash
bin/hutch-lute readiness --hutch "$H" --experiment "$E"
```

Report what it found: the config YAML, the DAGs, and the two flags that matter —
`configured` (artifacts are installed) and `has_run` (the `lute.db` has actually been
written). They differ: LUTE creates `lute.db` empty, so `configured: true, has_run:
false` is an install nothing has ever gone through.

**When an install exists, offer a fresh directory rather than rebuilding in place.**
Every `hutch-lute` verb takes `--directory <name>`, a subdirectory under `results/`, so a
new workspace costs one flag and destroys nothing:

```bash
bin/hutch-lute plan --hutch "$H" --experiment "$E" --directory <new> --workflow <w>
```

Rebuild in place only if the operator explicitly asks. **`envs` deletes the entire venv
tree and reinstalls** — never run it while a workflow is executing from those venvs.

---

## 3. Choose the workflow, render the config

Ask `@ask-lute` for the workflow, its template and its parameters given the experiment
context. **This repo vendors no templates** — `--template` is a path you were given or
found, never a name you recall:

```bash
bin/hutch-lute render --template <path to template> \
    --whitelist-dir "$LUTE_PATH/config/templates" \
    --set-context experiment="$E" --out <config path>
```

`--whitelist-dir` is not optional in practice: without it, or an explicit
`--no-whitelist`, render refuses.

**Read `inserted_keys` in the report.** An `inserted` where you expected `replaced` means
the key was not where you thought — a misspelling announcing itself, not a successful
override. `--set TASK.BLOCK.KEY=value` changes a parameter, `=__DELETE__` removes one.

---

## 4. Plan, show, install

```bash
bin/hutch-lute plan --hutch "$H" --experiment "$E" --workflow <name> --trigger <trigger>
```

`plan` touches nothing and prints everything: every precondition, the exact eLog
payloads, and a `destructive` warning if the venv tree exists. **Show it to the operator
before any write.** A non-empty `blocking` list means the install will refuse anyway.

`--trigger END_OF_RUN` makes a finished run fire its analysis; the other forms are
`START_OF_RUN`, `MANUAL` and `RUN_PARAM_IS_VALUE:<name>:<value>`. Triggers pair
positionally with `--workflow`, so order matters with more than one.

```bash
bin/hutch-lute all --hutch "$H" --experiment "$E" --workflow <name> \
    --trigger <trigger> --confirm write
```

`all` runs `workspace` → `dags` → `envs` → `register` after checking preconditions, under
one `--confirm write`. `envs` runs **before** `register`, so a registration failure
leaves the venvs already rebuilt.

**`register` needs a ticket at `FILE:$HOME/krb5cc.ticket` specifically**, not the ambient
cache — a fresh `kinit` in your shell does not satisfy it. The check validates expiry, so
`ok: false` on a file that exists means the credential is stale; the error prints the
`kinit` line to hand the operator.

---

## 5. Hand off

Record what you built, then stop:

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" lute_config "<config path>" \
    --source "rendered by setup-analysis" --kind resolved
```

Tell the operator the config path, the workflow and trigger registered, the directory
used, and the job id if one exists yet. Then hand to **`/assess-analysis`** — it owns
watching, reading the logs and the verdict. Do not read analysis output here.
