---
name: setup-analysis
description: >
  Stands up a LUTE analysis workflow for hutch-copilot and submits it. Asks @ask-lute
  what to set up, renders the config, installs the DAGs and the LUTE it runs on,
  registers the workflow with the eLog. Ends the moment the workflow is registered or a
  job id exists — reading the output is `assess-analysis`.
  Triggers on: set up LUTE, configure analysis, render the config, register the workflow,
  patch the DAG, is LUTE ready, submit the analysis, analyze data.
---

# setup-analysis

You own everything up to **a registered workflow, or a job id**. You stop there.

You own the **operation**, not the **setup knowledge**. What to render, which DAG, which
trigger and which LUTE come from **`@ask-lute`**: read its `SKILL.md`, follow its dispatch
to the sub-skill for this analysis (`bayfai`, `smalldata`, `cctbx`), and take that
sub-skill's **setup contract**:

| Contract field | Goes to |
|---|---|
| `template` | `hutch-lute render --template` |
| `defaults` | `--context <sub-skill>/defaults/<hutch>.yaml` (else `default.yaml`), for config and DAG alike |
| `context` | `--set-context K=V`, one per answered or resolved variable |
| `sets` | `--set TASK.BLOCK.KEY=value` |
| `dags` | a `.dag.j2` rendered (`hutch-lute render --no-whitelist`, same context), a `.dag` copied, to `lute_output/<workflow>.dag`; `--workflow` and `--trigger` in order |
| `lute` | `--lute` (§4): `release` → `central:<ver>`; `source` → `install:` or `clone:` |
| `validate` | run on the rendered config; non-zero = the config does not render |
| `unreviewed` | shown to the operator verbatim |
| `parse` | recorded for `assess-analysis` (§5) |

**`@ask-lute` not installed:** run `scripts/fetch-advisors.sh`, then `Read`
`~/.claude/skills/ask-lute/SKILL.md` directly — no restart is needed to read a file. Only
if that fails too, carry on without it: say it is missing, show the operator the rendered
YAML so they see exactly what will run, name the parameters that went unreviewed, and
submit. That is the one place this skill differs from the rest, and it is deliberate: the
blocker list below is exhaustive, and an unreviewed submit beats a stalled session.

---

## The precondition contract — read this before anything else

**Only the four blockers below may stop you.** Resolving facts is not progress toward a
submit; this is a hard rule, not a preference.

**Blocks a submit — these only:**

| Blocker | How you know |
|---|---|
| config does not render | `hutch-lute render` fails, or the contract's `validate` exits non-zero |
| the named DAG is absent, or the LUTE it runs on is not built | `hutch-lute plan` → non-empty `blocking` |
| no XTC2 on disk | `hutch-data xtc2` |
| no valid ticket, *and* this config needs one | `hutch-lute kerberos --yaml <cfg>` |

A contract `context` variable marked *blocker: ask* is a "config does not render" case:
ask the operator for it, and nothing else.

**The sub-skill's walk-through is one exchange:** its required values in one message,
its defaults shown once. That is the review — not a licence to resolve more facts
before submitting.

**Does NOT block a submit — never wait on these:**

geometry quality or provenance · which run was the calibrant · the optimal `d_min` ·
detector aliases beyond what the config already names · whether indexing parameters are
ideal · anything that needs the eLog when the eLog is unreachable.

**Submit, then read the result.** A failed job that names its failure in a log is cheaper
than an unsubmitted one, and `assess-analysis` is built to read exactly that. If a value
is unknown, use the template's default, list it as unreviewed, and submit.

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
new workspace costs one flag and destroys nothing.

Rebuild in place only if the operator explicitly asks. **`envs` deletes the entire venv
tree and reinstalls** — never run it while a workflow is executing from those venvs.

**One config per directory.** `plan` checks exactly `lute_output/<hutch>_lute.yaml`, so two
analyses that need different `SubmitSMD` blocks (BayFAI's `calib_max` vs the experiment's
SmallData) need two `--directory` workspaces. So do **variants run in parallel** — a Task
writes `figs/<exp>_r<run>_<det>.png` with no job id and keeps one `lute.db` per
`work_dir`. Variants fired one after another can share one.

---

## 3. Render the config

`plan` names the paths an install checks — render and copy to exactly those:

```bash
bin/hutch-lute plan --hutch "$H" --experiment "$E" --directory <d> --workflow <w>
# config_path: results/<d>/lute_output/<hutch>_lute.yaml   dags: lute_output/<w>.dag
```

```bash
bin/hutch-lute render --template <contract template> \
    --whitelist-dir <LUTE checkout>/config/templates \
    --set-context experiment="$E" work_dir=<results/<d>/lute_output> ... \
    --set <contract sets> --out <config_path>
```

`--whitelist-dir` is not optional in practice: without it, or an explicit
`--no-whitelist`, render refuses. Point it at the `config/templates` of the LUTE the
workflow will run on — for a `source` workflow, its checkout.

**Read `inserted_keys` in the report.** An `inserted` where you expected `replaced` means
the key was not where you thought — a misspelling announcing itself, not a successful
override. `=__DELETE__` removes a key.

Then run the contract's `validate`, if any, on `<config_path>`, and put each contract
DAG at `lute_output/<workflow>.dag` — render a `.dag.j2` with the same `--context` and
`--no-whitelist`, copy a `.dag`.

---

## 4. Plan, show, install

**Which LUTE.** The contract's `lute` field decides, through `--lute`:

| `--lute` | Use when | Writes | Needs internet |
|---|---|---|---|
| `central[:<ver>]` — **default `central:dev`** | the contract says `release`; the group build under `/sdf/group/lcls/ds/tools/lute/<ver>` | nothing | no |
| `install:<dir>` | `source`, and `<dir>/install/bin` is already built | nothing | no |
| `clone:<owner/repo>@<branch>` | `source`, not built yet | clones + `./build.sh -e` into `results/<d>/lute` | **yes** |
| `pip[:<ver>]` | a release that has no central build | venvs in `results/<d>/lute_envs` (destructive) | **yes** |

`plan` reports the resolved version (`central:dev` is `lute-lcls 0.4.0+dev` today). For
`clone`/`pip` it probes github.com and pypi.org for 5 s first; **the hutch hosts have no
outbound internet**, so there that is a blocker: show the operator the clone-and-build
commands from the sub-skill to run on an S3DF node, then use `install:<dir>`. Never retry
a clone in-session.

```bash
bin/hutch-lute plan --hutch "$H" --experiment "$E" --directory <d> \
    --workflow <w>... --trigger <t>... [--lute <spec>]
```

`plan` touches nothing and prints everything: every precondition, the exact eLog
payloads, and a `destructive` warning if `envs` would delete a venv tree. **Show it to the
operator before any write.** A non-empty `blocking` list means the install will refuse.

Triggers pair positionally with `--workflow`, so order matters with more than one:
`START_OF_RUN`, `END_OF_RUN`, `MANUAL`, `RUN_PARAM_IS_VALUE:<name>:<value>`.

```bash
bin/hutch-lute all --hutch "$H" --experiment "$E" --directory <d> \
    --workflow <w>... --trigger <t>... [--lute <spec>] --confirm write
```

`all` runs `workspace` → `dags` → LUTE (`pip` builds venvs, `clone` clones and builds;
`central`/`install` skip it) → `register`, after checking preconditions, under one
`--confirm write`. The LUTE step runs **before** `register`, so a registration failure
leaves it already built.

**Reservation.** `plan`/`dags`/`register` default `--reservation` to the S3DF reservation
whose accounts list `lcls:<E>` (`lcls:onshift` today) and add it to every DAG step and
the eLog payload. `--reservation none` skips it.

**The workspace is the experiment's, not yours.** An eLog workflow runs as whoever
clicks it. `workspace` gives the experiment group (`<E>`) `rwX` plus default ACLs on the
workspace and `rX` on a `clone:`/`install:` LUTE, removes any world-write, and verifies
it; a path it could not grant blocks. Paths owned by someone else are listed in
`skipped_not_owned` — tell their owner. The hutch hosts' NFS mount has no ACL support, so
the tool sets them over `ssh psana`. Never fix access with `777`.

**`register` needs a ticket at `FILE:$HOME/krb5cc.ticket` specifically**, not the ambient
cache — a fresh `kinit` in your shell does not satisfy it. The check validates expiry, so
`ok: false` on a file that exists means the credential is stale; the error prints the
`kinit` line to hand the operator. **They run it in their own terminal**, not as a `!`
command: `!` has no TTY for the password prompt, so Kerberos falls back to PKINIT and fails.

That ticket is the *registerer's*. Each person who fires the workflow needs their own
`~/krb5cc.ticket` for the eLog summary to post; without one the analysis still runs. You
cannot make one for them — name who lacks it.

**Geometry workflows fire on a calibrant run only.** A `MANUAL` geometry workflow (BayFAI)
is fired on the run the operator names as the calibrant, recorded `--kind operator`, and
its result is recorded with the detector-z range it holds for. Run 4 of mfx101941027 got
six BayFAI jobs and no calibrant.

For a config with per-run output directories (CCTBX), `hutch-lute sbatch --submit`
creates them for its run; an eLog-triggered run does not — create
`r<run>/…` ahead of the first run, as the sub-skill's traps table says.

---

## 4b. Run by hand — one Task, or the whole DAG

Two verbs, both LUTE's own launchers, both from `work_dir` (Slurm writes the job log to
the submit directory). Without `--confirm write` they print the command and submit nothing.

```bash
# One managed Task — no DAG, no eLog. Re-running BayFAI on a powder that already exists.
bin/hutch-lute run-task -e "$E" -r <run> -c <config> -t BayFAIOptimizer2 \
    [--lute <spec>] --nodes=1 --ntasks-per-node=64 [--confirm write]

# The whole DAG for a run you choose — the same launch an eLog trigger does.
bin/hutch-lute run-workflow -e "$E" -r <run> -c <config> -W <dag> [--lute <spec>] [--confirm write]
```

Any other `--long=value` goes to `sbatch`. LUTE keeps one rank, so `--ntasks-per-node=64`
gives the Task 63. Use `run-task` when only one step must re-run; `run-workflow` when a
trigger missed a run. **Never `run-workflow` a run the eLog ARP already launched** —
check `watch` first.

---

## 5. Hand off

Record what you built, then stop:

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" lute_config "<config path>" \
    --source "rendered by setup-analysis from <contract template>" --kind resolved
bin/hutch-state set --hutch "$H" --experiment "$E" lute_parse "<contract parse>" \
    --source "ask-lute/<sub-skill>" --kind resolved
```

Tell the operator the config path, the workflows and triggers registered, the directory
and LUTE used, and the job id if one exists yet. Then hand to **`/assess-analysis`** — it
owns watching, reading the logs and the verdict. Do not read analysis output here.
