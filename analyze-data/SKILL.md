---
name: analyze-data
description: >
  LUTE analysis setup and refinement sub-skill for hutch-copilot. Configures LUTE
  workflows after data collection — DAG design, detector-alias confirmation, config
  rendering, workflow registration — and drives the inspect/adjust/re-run loop once
  output exists. Calibration runs (GEOM, DARK) are handled by /take-run via run_type
  tags. Works entirely offline from S3DF. Triggers on: set up analysis, configure
  lute, sfx pipeline, refine parameters, check output, analyze data, lute setup,
  lute wizard, indexing rate is low, re-run lute.
---

# analyze-data

You own LUTE configuration and parameter refinement. You do not run calibration —
`/take-run` handles GEOM and DARK via `run_type` tags. You control no hardware.

All mechanism is in `hutch-tools` (see the Tool Layer section of `../SKILL.md`). This file
owns the DAG design, the human gates, and the refinement judgment.

**Reference:** `../references/sfx-cctbx-patterns.md` — the CCTBX DAG pattern, the `d_min`
ranking hazard, coverage×yield, and the symptom→knob table.
**Optional advisors:** `@ask-lute` (LUTE internals), `@ask-cctbx-xfel` (SFX parameters),
`@ask-smalldata` (SmallData ROI / droplet / azimuthal integration), `@ask-lcls2` (psana2),
`@lcls-catalog` (file and XTC2 inventory). None is required — check with
`hutch-resolve skills` and, if one is missing, say so and reason from the reference.

---

## 1. Context

```bash
bin/hutch-lute readiness --hutch "$H" --experiment "$E"   # already configured?
bin/hutch-state audit --hutch "$H" --experiment "$E"
bin/hutch-state path  --hutch "$H" --experiment "$E"
```

If `readiness` reports `configured: true`, **say so and ask what needs changing** rather than
reconfiguring from scratch. A YAML-only change needs no re-registration.

Ask only for what is genuinely missing: `version` (default `dev`) and an optional `directory`
under `results/`. Get the experiment root from `hutch-state path` — never interpolate it here.

`MissingValue` → ask the operator. `StaleValue` → re-resolve, or `--stale-ok` and report the
age. Do not assume a value in either case.

### Kerberos — scoped, not unconditional

A ticket is needed **only for eLog interaction**: resolving inputs from an eLog tag, and
registering workflows with `install_lute.py`. A pipeline whose inputs are explicit paths needs
none. Ask the config:

```bash
bin/hutch-lute kerberos --yaml "$CONFIG"
```

If `needs_kerberos` is true and `have_ticket` is false, show
`kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU`.
If false, **proceed without a ticket** — this used to be an unconditional gate, which would
have blocked a ten-iteration campaign that never touched the eLog.

---

## 2. Design the DAG — the judgment part

Ask `@ask-lute` (if available) for the task chain and starting template for this hutch and
technique. If the technique is unknown, ask one framing question first — SFX, TR-SAXS, XES,
RIXS, other?

Build a **minimal** DAG: exactly the tasks needed. For SFX with DARK/GEOM/DATA run types use
`!run_type` branching — canonical shape in `../references/sfx-cctbx-patterns.md`. Otherwise a
linear DAG is right.

Decisions that are yours, not the tool's:

- Which tasks belong in the chain at all. `SmallDataProducer2` is not needed for pure SFX.
- `slurm_params` per task — default `--nodes=1 --ntasks-per-node=1`; ask whether more compute
  is warranted, which it usually is for `MPIExecutor` tasks.
- Trigger: `END_OF_RUN` for offline analysis, `START_OF_RUN` for live SmallData, `MANUAL` for
  calibration or one-off computation.
- `wf_name` (e.g. `sfx_cctbx`, `xes`).

`slurm_params` carries no `--time` — nothing in LUTE does. If a wall-clock limit matters,
submit through `hutch-lute sbatch --time`, which refuses to emit a script without one.

Show the plan — chain, workflow name, DAG path, trigger, full DAG YAML — and get approval
before continuing.

---

## 3. Detector alias — a human gate, not a guess

```bash
bin/hutch-resolve detectors --experiment "$E" --run "$RUN" \
    --persist-to "$H" "$E" --field detectors
```

The tool returns every key plus `area_candidates`. **Show the list and ask which is the area
detector** — instruments carry many, and the auto-detected value can be ambiguous. Never write
an alias the operator has not confirmed.

Same rule for the other inputs: unit cell from `hutch-resolve cell <pdb>` (never hand-typed),
and `ls` the experiment's `results/common/` tree for geometry and mask rather than recalling
paths — a campaign lost time to a mask path documented without its `.mask` extension.

---

## 4. Render the config

```bash
bin/hutch-config \
  --template  cctbx_pipeline \
  --context   context.json \
  --set       TASK.phil_parameters.KEY=VALUE ... \
  --whitelist-dir "$LUTE_PATH/config/templates" \
  --out       "$CONFIG"
```

`--template` defaults to the vendored `cctbx_pipeline`; `--list-templates` shows what ships.

**Read the report before showing anything to the operator:**

- **`overrides`** says `replaced` or `inserted` per override. An `inserted` where you expected
  `replaced` means the key is misspelled — say so rather than reporting success.
- **Unknown phil keys are refused.** `phil_parameters` is pydantic `extra='allow'`, so LUTE
  accepts anything and the fixed template silently ignores what it does not reference.
  `--whitelist-dir` turns that invisible no-op into an error. Use `--no-whitelist` only if the
  operator accepts that an unknown key will be dropped.
- The incident register runs automatically: `parallel_a2a`, `modify` in the merge step list,
  missing merge suffixes, `phil_file` without `{{ run }}`, merge-version padding, and
  `merging_d_min` disagreeing between the scale and merge stages.

Show the operator the rendered config **and** the report together, and get approval.

---

## 5. Register the workflow

```bash
mkdir -p "$LUTE_OUTPUT_DIR" && chmod 777 "$LUTE_OUTPUT_DIR"
cat > "$LUTE_OUTPUT_DIR/$WF_NAME.dag" << 'EOF'
{approved DAG YAML}
EOF
chmod 666 "$LUTE_OUTPUT_DIR/$WF_NAME.dag"

source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh
python analyze-data/scripts/install_lute.py -e "$E" -v "$VERSION" [-D "$DIRECTORY"] \
    -W "$WF_NAME" --trigger "$TRIGGER" [--partition milano] [--account "lcls:$E"]
```

> **Known gap.** This step is the last mechanism in the repo still written as shell in prose —
> `mkdir`/`chmod`/heredoc/`install_lute.py`. It is `MIGRATION.md` item 4: `install_lute.py`
> (511 lines, untested) becomes a command, at which point this section collapses to one call.
> Until then, run it exactly as written and check the result rather than trusting it.

`install_lute.py` talks to the eLog, so this step **does** need a ticket. Verify with
`hutch-lute readiness`, then confirm the workflow appears as `lute_{wf_name}` under Workflow
Definitions in the eLog — that part `readiness` cannot check for you. Re-run with the same
`-W` to update registration after a DAG or trigger change.

---

## 6. Refine — the inspect / adjust / re-run loop

Once output exists. Read it yourself; do not ask the operator to paste log contents.

```bash
bin/hutch-lute status "$JOB_ID"     # COMPLETED can still mean the Task failed
```

Then work the symptom→knob table in `../references/sfx-cctbx-patterns.md`. Three rules the
campaign paid for:

1. **Check the funnel before touching geometry.** A low indexing rate is not reliably a
   geometry problem; the one large effect found in ten iterations was a hit-finder floor set
   too high, discarding 407 frames that carried ≥20 spots.
2. **Compare on yield, not `n_indexed`.** Under a wall-clock limit `n_indexed` is
   coverage × yield, so two runs with identical parameters can differ by 35%.
3. **Never rank two runs with different `d_min`.** All three overall statistics are monotone
   in it — cutting `d_min` improves them without adding information.

Change **one concept per iteration**, write the expected readout *before* submitting, and
record a null result as a result. A YAML-only change needs no re-registration; the next
trigger picks it up.

---

## → coordinate-experiment handoff (mandatory)

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" lute_config \
    '{"yaml_path": "'"$CONFIG"'", "workflows": ["lute_'"$WF_NAME"'"]}' \
    --source "install_lute.py -W $WF_NAME, verified in eLog Workflow Definitions" \
    --kind resolved
```

Then pass to coordinate-experiment for logging, and note any `# FILL IN` items still
outstanding in the config.
