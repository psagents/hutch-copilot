# /review-scenario — Run a Fake Beamtime Test Scenario

Triggered by: `/review-scenario <scenario_dir>`, e.g. `/review-scenario tests/sfx_basic/`

You are the **reviewer agent**. Your job is to drive a fresh hutch-copilot sub-agent through
a fake beamtime scenario and evaluate its responses against ground-truth assertions.
You do not execute any hutch-copilot logic yourself — you only orchestrate, observe, and judge.

---

## Phase 0 — Setup

### Step 0.0 — Kerberos pre-flight

Before creating any directories or running the scenario, verify that a valid (non-expired)
Kerberos ticket is available:

```bash
klist -c FILE:${HOME}/krb5cc.ticket
```

- If the ticket is **missing or expired**: warn the user and stop entirely.
  Print:
  > "Cannot run scenario — Kerberos ticket missing or expired.
  > Run: `kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU`"
  Phase 2.5 LUTE integration will not be possible without a valid ticket.
- If the ticket is **valid**: proceed silently.

### Step 0.1 — Read scenario config

Read `{scenario_dir}/scenario.yaml`. Extract:
- `name`, `description`
- `entry_skill` — path relative to hutch-copilot root
- `test_dir` — substitute `{timestamp}` with the current `date +"%Y%m%d_%H%M%S"`
- `fixed_time.date` and `fixed_time.time`
- `chained` flag
- `clean_start` settings
- `steps` list
- `lute_integration` block (if present) — real LUTE job specs for Phase 2.5

### Step 0.2 — Read per-step mock responses and step files

For each step in the `steps` list, read:
- `{step_dir}/mock_responses.yaml` — **complete, standalone** mock table for that step
- `{step_dir}/prompt.txt`
- `{step_dir}/assertions.yaml`
- `{step_dir}/expected_lute_checks.yaml` (if it exists)

Each step's `mock_responses.yaml` is self-contained. There is no global fixtures file.
Mock tables do not inherit from each other — every step carries exactly the mocks it needs.

### Step 0.3 — Create test_dir and copy fixtures

```bash
mkdir -p {test_dir}/results/psagents
mkdir -p {test_dir}/results/lute_output
mkdir -p {test_dir}/xtc2
```

If `{scenario_dir}/fixtures/lute_output_r45/` exists, copy its contents into
`{test_dir}/results/lute_output/` so the sub-agent can read them in later steps:

```bash
cp {scenario_dir}/fixtures/lute_output_r45/* {test_dir}/results/lute_output/
```

Report: `"Scenario: {name}. test_dir: {test_dir}. {N} steps loaded."`

---

## Phase 1 — Build the Test-Mode Injection Block

This block is prepended to the **first** Task prompt only. It tells the sub-agent how to
behave in test mode without breaking the hutch-copilot skill logic.

```
=== TEST MODE ===

You are a hutch-copilot agent running a fake beamtime scenario.

SETUP INSTRUCTIONS (follow before processing any user message):
1. Read `{entry_skill}` to load your full role and capabilities.
2. Do NOT read any pre-existing state files at startup. Wait for the user prompt.

PATH OVERRIDE:
- Use `{test_dir}` as your base data directory in place of `/sdf/data/lcls/ds/`.
- State file path: {test_dir}/results/psagents/mfxltest01_state.json
- Log file path:   {test_dir}/results/psagents/mfxltest01_logs.md
- LUTE output:     {test_dir}/results/lute_output/
- XTC2 directory:  {test_dir}/xtc2/

TIMESTAMP OVERRIDE:
- For any `date +"%H:%M"` or `date +"%Y-%m-%d"` calls, use: {fixed_time.time} / {fixed_time.date}
- Use {fixed_time.date} as today's date for all log headers.

MOCK RESPONSES — STEP 1:
For every bash or bridge command you would normally execute to interact with hardware,
the DAQ, LUTE, Slurm, or eLog — do NOT run the real command. Instead, match the command
against the patterns below (case-insensitive regex, first match wins) and return that
response verbatim as if the command had run successfully.

{mock_responses_table_step_1}

MOCK TABLE UPDATE RULE:
If a [MOCK TABLE UPDATE] block appears before a user message in a future turn, replace
the current mock table entirely with the one in that block for that message only.
Apply first-match-wins as usual. After the message is processed, this rule has no further
effect until another [MOCK TABLE UPDATE] appears.

FILE OPERATIONS:
- Writing JSON, YAML, and Markdown files to `{test_dir}/...`: execute for real.
- Reading files under the hutch-copilot skill directory: execute for real.
- Any other real filesystem or network operations: substitute with mock if matched above,
  otherwise skip silently and note "(mocked: no response available)".

CONFIRMATION PROTOCOL:
- In test mode, displaying a confirmation block constitutes implicit user approval.
  After showing the "I'd like to execute / Shall I proceed?" block, proceed immediately
  — do NOT wait for a "yes" reply. Execute the action and continue to completion.
- Similarly, if a setup wizard asks for user input mid-phase (e.g., unit cell, space
  group, merging parameters), treat any field already provided in the user message as
  the answer. For any remaining unknown field, write a clearly marked placeholder
  (e.g., `# FILL IN: unit_cell`) and continue to completion without stopping.

=== END TEST MODE ===
```

To build `{mock_responses_table_step_1}`, format each entry from step 1's
`mock_responses.yaml` as:

```
Pattern: {pattern}
Response:
  {response}
---
```

---

## Phase 2 — Execute the Scenario (chained mode)

### Launching the first step

Use the Task tool to spawn a fresh sub-agent:

```
Task(
  subagent_type = "general",
  description   = "hutch-copilot {name} step 01",
  prompt        = "{test_mode_injection_block}\n\nUser message:\n{step_01_prompt}"
)
```

Save the returned `task_id` — you will resume this same session for all subsequent steps.

### Continuing subsequent steps

For each step N > 1:

1. Load the step's `mock_responses.yaml`.
2. Format it as a mock table string `{mock_table_N}` using the same pattern/response format.
3. Compose and send the Task prompt:

```
Task(
  subagent_type = "general",
  description   = "hutch-copilot {name} step NN",
  prompt        = "[MOCK TABLE UPDATE]\n{mock_table_N}\n[END MOCK TABLE UPDATE]\n\nUser message:\n{step_N_prompt}",
  task_id       = {saved_task_id}
)
```

The sub-agent retains full conversation history and all files it created — no re-injection
of the test-mode block is needed. The MOCK TABLE UPDATE block overrides the active mock
table for that message only, then reverts.

### Collecting output

After each Task call, capture:
- The full text response from the sub-agent
- The list of tool calls it made (file reads, bash commands)

Store these as `step_output[N]` and `step_tool_calls[N]`.

---

## Phase 2.5 — Real LUTE Integration (after confirmed take-run steps)

After each take-run step completes, check whether `step_output[N]` confirms a run was
taken (look for "Run {N} complete" and a `run_type` tag). If confirmed, trigger the
corresponding real LUTE job from the `lute_integration` block in `scenario.yaml`.

**These jobs run independently of the scenario evaluation.** Results are reported as
`lute_integration` checks in Phase 4 and do NOT affect the scenario PASS/FAIL score.
Submit jobs immediately after confirmation and let them run asynchronously — check results
at Phase 4 report time. Mark SKIPPED (not FAIL) if a job has not completed by then.

### Environment prerequisites (check once before Phase 2.5)

All Phase 2.5 jobs use the **same LUTE virtual env** that the sub-agent created for
`mfxltest01` during step 06_setup. This env is at:

```
{test_dir}/results/lute_envs/lute_env_py39/
{test_dir}/results/lute_envs/lute_env_py311/
```

`submit_launch_slurm.sh` and `launch_slurm` are installed there as entry points.
No separate LUTE installation is needed from the reference experiments.

Before any submission, verify Kerberos:
```bash
klist -c FILE:${HOME}/krb5cc.ticket
```
If missing, stop Phase 2.5 entirely — all jobs are SKIPPED (not FAIL). Print:
> "Phase 2.5 skipped — no Kerberos ticket at $HOME/krb5cc.ticket. Run:
> `kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU`"

---

### Submission helper

All Phase 2.5 jobs are submitted by writing a small bash script, making it executable,
and running it with `bash`. The activation block is identical for every job:

```bash
#!/bin/bash
set -e
export KRB5CCNAME=FILE:${HOME}/krb5cc.ticket

# LUTE env created by install_lute.py during step 06_setup (mfxltest01 review env)
LUTE_VENV={test_dir}/results/lute_envs/lute_env_py39
source ${LUTE_VENV}/bin/activate
export LUTE_VIRTUAL_ENV="${LUTE_VENV}"
export LUTE_VIRTUAL_ENV_PY39="${LUTE_VENV}/bin/python"
export LUTE_VIRTUAL_ENV_PY311={test_dir}/results/lute_envs/lute_env_py311/bin/python

# submit_launch_slurm.sh is now in PATH
submit_launch_slurm.sh launch_slurm \
  -c {config_yaml} \
  -W {dag_file} \
  -e {experiment} \
  -r {run} \
  --type {run_type}     # GEOM | DATA — routes the !run_type DAG branch
```

Capture stdout/stderr to extract Slurm job IDs (`Submitted batch job XXXXXXXX`).
Then poll:
```bash
squeue -j {job_ids} --noheader --format="%i %j %T"
sacct -j {job_ids} --noheader --format=State,ExitCode
```

---

### 2.5.1 — After step 07_dark confirmed (DARK branch)

DARK branch is empty — no LUTE jobs to submit.
Log: `"lute_integration: 07_dark — DARK branch empty (expected). takepeds + makepeds confirmed."`

---

### 2.5.2 — After step 08_geom confirmed (GEOM branch)

Run `SmallDataProducer2 → BayFAIOptimizer2` on `mfx101591026` run 15 (LaB6 calibrant).

**Step 1 — Create output dir and write LUTE config**

```bash
mkdir -p {test_dir}/lute_integration/mfx101591026_r15_output
```

Config at `{test_dir}/lute_integration/mfx101591026_r15.yaml`:

```yaml
%YAML 1.3
---
title: "sfx_basic integration — GEOM mfx101591026 r15"
experiment: mfx101591026
run: 15
date: "{fixed_time.date}"
lute_version: 0.2.0
task_timeout: 7200
work_dir: "{test_dir}/lute_integration/mfx101591026_r15_output"
---
SubmitSMD:
  # smd_producer.py and prod_config_mfx.py already exist in this experiment
  producer: "/sdf/data/lcls/ds/mfx/mfx101591026/results/smalldata_tools/lcls2_producers/smd_producer.py"
  lute_template_cfg:
    template_name: "smd2_prod_config_template.py"
    output_path: "/sdf/data/lcls/ds/mfx/mfx101591026/results/smalldata_tools/lcls2_producers/prod_config_mfx.py"
  producer_parameters:
    detnames: ["jungfrau"]
    detSumAlgos:
      jungfrau:
        - "calib_max"

BayFAI:
  detname: "jungfrau"
  calibrant: "LaB6"
  center:
    dist: 0.080
    poni1: 0.0
    poni2: 0.0002
    rot1: 0.0
    rot2: 0.0
    rot3: 0.0
  bounds:
    dist: [-0.05, 0.05]
    poni1: [-0.005, 0.005]
    poni2: [-0.005, 0.005]
    rot1: [-0.1, 0.1]
    rot2: [-0.1, 0.1]
    rot3: [-0.1, 0.1]
```

**Step 2 — Write DAG** at `{test_dir}/lute_integration/geom_calib.dag`:

```yaml
!LUTE_DAG
task_name: "SmallDataProducer2"
slurm_params: "--partition=milano --account=lcls:mfx101591026 --nodes=4 --ntasks-per-node=50 --exclusive"
next:
- task_name: "BayFAIOptimizer2"
  slurm_params: "--partition=milano --account=lcls:mfx101591026 --nodes=1 --ntasks=120 --exclusive"
  next: []
```

**Step 3 — Write and run submission script** at `{test_dir}/lute_integration/submit_geom.sh`:

```bash
#!/bin/bash
set -e
export KRB5CCNAME=FILE:${HOME}/krb5cc.ticket

# LUTE env created by install_lute.py during step 06_setup (mfxltest01 review env)
LUTE_VENV={test_dir}/results/lute_envs/lute_env_py39
source ${LUTE_VENV}/bin/activate
export LUTE_VIRTUAL_ENV="${LUTE_VENV}"
export LUTE_VIRTUAL_ENV_PY39="${LUTE_VENV}/bin/python"
export LUTE_VIRTUAL_ENV_PY311={test_dir}/results/lute_envs/lute_env_py311/bin/python

submit_launch_slurm.sh launch_slurm \
  -c {test_dir}/lute_integration/mfx101591026_r15.yaml \
  -W {test_dir}/lute_integration/geom_calib.dag \
  -e mfx101591026 \
  -r 15 \
  --type GEOM
```

```bash
chmod +x {test_dir}/lute_integration/submit_geom.sh
bash {test_dir}/lute_integration/submit_geom.sh 2>&1 | tee {test_dir}/lute_integration/submit_geom.log
```

Capture job IDs from the log (`Submitted batch job XXXXXXXX`).
Poll every 60 s. Timeout: 90 minutes.

**Step 4 — Validate**

Check `{test_dir}/lute_integration/mfx101591026_r15_output/bayFAI_output/jungfrau.poni`:
- `Distance` ≈ 0.080 m ± 0.005 m
- `PONI2` ≈ 0.0002 m ± 0.00005 m (x-shift ~0.2 mm)
- `|PONI1|` < 0.001 m (near zero)

Log: `"lute_integration: 08_geom — {PASS|FAIL}. dist={X}mm poni2={Y}mm poni1={Z}mm"`

---

### 2.5.3 — After step 09_data confirmed (DATA run 1)

Run `CCTBXIndexer → CCTBXScaler → CCTBXMerger` on `mfx101624926` run 36.

**Step 1 — Create output dir and write LUTE config**

```bash
mkdir -p {test_dir}/lute_integration/mfx101624926_r36_output
```

Config at `{test_dir}/lute_integration/mfx101624926_r36.yaml`:

```yaml
%YAML 1.3
---
title: "sfx_basic integration — DATA mfx101624926 r36"
experiment: mfx101624926
run: 36
date: "{fixed_time.date}"
lute_version: 0.2.0
task_timeout: 14400
work_dir: "{test_dir}/lute_integration/mfx101624926_r36_output"
---
IndexCCTBXXFEL:
  data_spec:
    experiment: "mfx101624926"
    run: "36"
    detector_address: "jungfrau"

ScaleCCTBXXFEL:
  phil_parameters:
    input_path: ""          # auto-resolved from IndexCCTBXXFEL DB result
    merging_d_min: "2.0"    # placeholder — adjust after first pass
    output_output_dir: "{test_dir}/lute_integration/mfx101624926_r36_output/scaled"

MergeCCTBXXFEL:
  phil_parameters:
    dispatch_step_list: >-
      input model_scaling statistics_unitcell statistics_beam model_statistics
      statistics_resolution group errors_merge statistics_intensity merge
      statistics_intensity_cxi
    input_path: ""          # auto-resolved from ScaleCCTBXXFEL DB result
    merging_d_min: "2.0"
```

**Step 2 — Write linear DAG** at `{test_dir}/lute_integration/cctbx_data.dag`:

```yaml
!LUTE_DAG
task_name: "CCTBXIndexer"
slurm_params: "--partition=milano --account=lcls:mfx101624926 --nodes=16 --ntasks-per-node=50 --exclusive"
next:
- task_name: "CCTBXScaler"
  slurm_params: "--partition=milano --account=lcls:mfx101624926 --nodes=8 --ntasks-per-node=50 --exclusive"
  next:
  - task_name: "CCTBXMerger"
    slurm_params: "--partition=milano --account=lcls:mfx101624926 --nodes=4 --ntasks-per-node=50 --exclusive"
    next: []
```

> Note: a simple linear DAG is used here (no `!run_type`) because we are explicitly
> submitting one pipeline for one known run — no eLog trigger routing is needed.

**Step 3 — Write and run submission script** at `{test_dir}/lute_integration/submit_data_r36.sh`:

```bash
#!/bin/bash
set -e
export KRB5CCNAME=FILE:${HOME}/krb5cc.ticket

# LUTE env created by install_lute.py during step 06_setup (mfxltest01 review env)
LUTE_VENV={test_dir}/results/lute_envs/lute_env_py39
source ${LUTE_VENV}/bin/activate
export LUTE_VIRTUAL_ENV="${LUTE_VENV}"
export LUTE_VIRTUAL_ENV_PY39="${LUTE_VENV}/bin/python"
export LUTE_VIRTUAL_ENV_PY311={test_dir}/results/lute_envs/lute_env_py311/bin/python

submit_launch_slurm.sh launch_slurm \
  -c {test_dir}/lute_integration/mfx101624926_r36.yaml \
  -W {test_dir}/lute_integration/cctbx_data.dag \
  -e mfx101624926 \
  -r 36 \
  --type DATA
```

```bash
chmod +x {test_dir}/lute_integration/submit_data_r36.sh
bash {test_dir}/lute_integration/submit_data_r36.sh 2>&1 | tee {test_dir}/lute_integration/submit_data_r36.log
```

Capture job IDs. Poll every 5 min. Timeout: 4 hours (run 36 is ~330 GB).

**Step 4 — Validate**

All three jobs must reach `COMPLETED 0:0` in `sacct`.
Log: `"lute_integration: 09_data — {PASS|FAIL}. CCTBXIndexer: {state}  CCTBXScaler: {state}  CCTBXMerger: {state}"`

---

### 2.5.4 — After step 11_data2 confirmed (DATA run 2)

Same pipeline as 2.5.3 but for `mfx101624926` run 37.
Use config `mfx101624926_r37.yaml` (copy of r36 with `run: 37` and `work_dir` updated),
DAG `cctbx_data.dag` (same file), script `submit_data_r37.sh`, flag `--type DATA`.
Log: `"lute_integration: 11_data2 — {PASS|FAIL}. CCTBXIndexer: {state}"`

---

## Phase 3 — Assert (run after EACH step completes)

Run all assertion types that are present in the step's `assertions.yaml`.
Treat a missing assertion block as "not checked" (not a failure).

### A. `skill_loads` checks

Inspect `step_tool_calls[N]` for file Read operations.

**`required`:** For each path listed:
- PASS if the sub-agent read that file during this step.
- FAIL if the file was not read.
- Note: path matching is suffix-based (e.g. `are-we-ready/SKILL.md` matches any read
  whose path ends with `are-we-ready/SKILL.md`).

**`forbidden`:** For each path listed:
- PASS if the file was NOT read during this step.
- FAIL if it was read.

### B. `state_checks`

Read `{test_dir}/results/psagents/mfxltest01_state.json`.
For each field specified:

| Assertion format | Check |
|---|---|
| `field: value` | `state[field] == value` (exact) |
| `field: {not_null: true}` | `state[field] is not None` |
| `field: {not_empty: true}` | `state[field]` is a non-empty list or string |
| `field: {one_of: [...]}` | `state[field] in [...]` |
| `field: {contains_one_of: [...]}` | any element appears in `state[field]` (string contains) |
| `field: null` | `state[field] is None` or key absent |

For nested fields use dot notation: `machine_state.beam_present` → `state["machine_state"]["beam_present"]`.

### C. `files_created`

For each path pattern in `files_created`:
- Substitute `{test_dir}` with the actual test dir.
- Glob the pattern — PASS if at least one matching file exists and is non-empty.
- FAIL otherwise.

### D. `files_modified`

For each path pattern in `files_modified`:
- PASS if the file exists AND its modification time is after the step started.
- FAIL otherwise.

### E. `log_checks`

Read `{test_dir}/results/psagents/mfxltest01_logs.md`.

| Format | Check |
|---|---|
| `contains: "string"` | string is present anywhere in the log file |
| `contains_one_of: [...]` | at least one of the listed strings is present |
| `not_contains: "string"` | string is absent from the log file |

### F. `expected_lute_checks` (setup step only — `06_setup`)

Find `{test_dir}/results/lute_output/*.yaml` — use the first match.
Parse it as YAML, then check:

**`required_tasks`:** For each entry with `name_matches_one_of`:
- PASS if any top-level key in the YAML matches one of the listed names
  (case-insensitive substring or exact match).
- FAIL if none match.

**`parameter_checks`:** For each entry:
- Navigate to `yaml[task][field]`.
- If `range: [min, max]` — PASS if `min <= value <= max`.
- If `allowed_values: [...]` — PASS if `value in [...]`.
- If `not_empty: true` — PASS if value is not None and not `""`.

**`detector_check`:** Search the YAML (all string values) for the `field` key.
- PASS if the value contains one of `contains_one_of`.

**`forbidden_tasks`:** For each listed name:
- PASS if no top-level key in the YAML matches (case-insensitive).
- FAIL if matched.

### G. `llm_judge`

You (the reviewer) are the judge. Read the step's `llm_judge.rubric` and the sub-agent's
full response for this step (`step_output[N]`). Evaluate whether the response satisfies
every numbered criterion in the rubric.

Return:
- `PASS` if all criteria are met.
- `FAIL` with a brief per-criterion breakdown noting which criteria failed and why.

Be strict: a criterion fails if the evidence for it is absent from the response,
even if the criterion seems likely to have been satisfied offscreen.

---

## Phase 4 — Report

After all steps complete, write the report to `{test_dir}/report.md` **and** print it
to the conversation. Use the Write tool to create the file — do not use bash echo or
heredoc. The file must exist at the end of the run so it can be reviewed offline.

The report format:

```
══════════════════════════════════════════════════════════════
  SCENARIO: {name}
  test_dir: {test_dir}
══════════════════════════════════════════════════════════════

Step 01 — init                    [PASS / FAIL]
  skill_loads                     PASS
  state_checks                    PASS
  files_created                   PASS
  log_checks                      PASS
  llm_judge                       PASS

Step 02 — awr_beam_fail           [PASS / FAIL]
  skill_loads                     PASS
  state_checks                    PASS
  log_checks                      PASS
  llm_judge                       PASS

...

══════════════════════════════════════════════════════════════
  SCENARIO TOTAL: X/13 steps passed  |  Y checks failed

══════════════════════════════════════════════════════════════
  LUTE INTEGRATION (informational — does not affect scenario score)
══════════════════════════════════════════════════════════════

  07_dark     no_jobs                                        [PASS]
  08_geom     BayFAI  mfx101591026 r15 (LaB6)               [PASS / FAIL / SKIPPED]
    dist=80.1mm  poni2=0.19mm  poni1=0.01mm
  09_data     CCTBX   mfx101624926 r36                       [PASS / FAIL / SKIPPED]
    CCTBXIndexer: COMPLETED  CCTBXScaler: COMPLETED  CCTBXMerger: COMPLETED
  11_data2    CCTBX   mfx101624926 r37                       [PASS / FAIL / SKIPPED]
    CCTBXIndexer: COMPLETED  CCTBXScaler: COMPLETED  CCTBXMerger: COMPLETED

══════════════════════════════════════════════════════════════
```

For each failed check, include the failure detail on the line below:
```
  state_checks             FAIL
    ✗ last_run_number: expected 43, got null
```

---

## Notes for the reviewer

- **Do not intervene** in the sub-agent's responses. If it goes off-track, record the
  failure and continue to the next step (do not correct it).
- **Carry over failures:** if step N fails a `state_checks` assertion, note the delta
  but still attempt step N+1 using the same sub-agent session. Downstream steps may
  cascade-fail — that is expected and informative.
- **LLM judge strictness:** when in doubt, prefer FAIL over PASS. The rubric criteria
  are written to be unambiguous; if you cannot find evidence of a criterion in the
  response, it fails.
- **Mock response matching:** if the sub-agent issued a bash command and you cannot tell
  whether it used a mock, check whether the response text contains the expected mock
  output (e.g., "Run 43 complete", "PASS — all critical checks OK"). If the mock output
  appears in the response, the mock was used.
- **MOCK TABLE UPDATE delivery:** always format the mock table exactly as shown in Phase 2
  before the user message. The sub-agent was primed at step 1 to recognize this block.
- **Phase 2.5 timing:** GEOM jobs take ~10–30 min; CCTBX DATA jobs take several hours for
  large runs (mfx101624926 r36 is ~330 GB). Submit immediately after confirmation and
  record results at Phase 4 report time. Mark SKIPPED if not yet complete.
