# /setup — Configure a LUTE Analysis Workflow

Planning is conversational; writing is not. Nothing is written until the operator approves
the plan, and then it is written by `hutch-config`, not by a heredoc.

**Reference brain:** `@ask-lute` for LUTE internals (task catalog, YAML syntax, DAG
structure, templates). `@ask-cctbx-xfel` for SFX parameter reasoning.
`references/sfx-cctbx-patterns.md` for the CCTBX DAG pattern and the d_min hazard.

---

## 1. Context and paths

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"
bin/hutch-state path  --hutch "$H" --experiment "$E"
```

Ask only for what is genuinely missing: `version` (default `dev`) and an optional `directory`
under `results/`. Derived locations follow the convention
`{results_dir}[/{directory}]/lute_output`, with `lute_envs` beside it — but get the
experiment root from `hutch-state path`, never by interpolating it here.

### Kerberos — scoped, not unconditional

A ticket is needed **only for eLog interaction**: resolving inputs from an eLog tag, and
registering workflows with `install_lute.py`. A pipeline whose inputs are explicit paths needs
none. Ask the config rather than assuming:

```bash
bin/hutch-lute kerberos --yaml "$CONFIG"
```

If `needs_kerberos` is true and `have_ticket` is false, show:
`kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU`.
If false, **proceed without a ticket**. This was an unconditional gate, which would have
blocked a ten-iteration campaign that never touched the eLog.

---

## 2. Design the DAG — this is the judgment part

Ask `@ask-lute` for the task chain and starting template for this hutch and technique. If the
technique is unknown, ask one framing question first (SFX, TR-SAXS, XES, RIXS, other?).

Build a **minimal** DAG — exactly the tasks needed. For SFX with DARK/GEOM/DATA run types use
`!run_type` branching; the canonical shape is in `references/sfx-cctbx-patterns.md`. For other
techniques a linear DAG is right.

Decisions that are yours, not the tool's:

- Which tasks belong in the chain at all. `SmallDataProducer2` is not needed for pure SFX.
- `slurm_params` per task — default `--nodes=1 --ntasks-per-node=1`; ask whether more compute
  is warranted, which it usually is for `MPIExecutor` tasks.
- Trigger: `END_OF_RUN` for offline analysis, `START_OF_RUN` for live SmallData, `MANUAL` for
  calibration or one-off computation.
- `wf_name` (e.g. `sfx_cctbx`, `xes`).

Note that `slurm_params` carries no `--time` — nothing in LUTE does. If a wall-clock limit
matters, submit through `hutch-lute sbatch --time`, which refuses to emit a script without one.

Show the plan (chain, workflow name, DAG path, trigger, full DAG YAML) and get approval
before continuing.

---

## 3. Detector alias — a human gate, not a guess

Resolve it, then confirm it:

```bash
bin/hutch-resolve detectors --experiment "$E" --run "$RUN" \
    --persist-to "$H" "$E" --field detectors
```

The tool returns every key plus `area_candidates`. **Show the list and ask which is the area
detector** — there are many detectors per instrument and the auto-detected value can be
ambiguous. Never write an alias the operator has not confirmed.

Same rule for the other inputs: get the unit cell from `hutch-resolve cell <pdb>` (never
hand-typed), and `ls` the experiment's `results/common/` tree for geometry and mask rather
than recalling paths — a campaign lost time to a mask path documented without its `.mask`
extension.

---

## 4. Render the config

```bash
bin/hutch-config \
  --template  "$TEMPLATE" \
  --context   context.json \
  --set       TASK.phil_parameters.KEY=VALUE ... \
  --whitelist-dir "$LUTE_PATH/config/templates" \
  --out       "$CONFIG"
```

Read the report before showing anything to the operator:

- **`overrides`** says `replaced` or `inserted` per override. An `inserted` where you expected
  `replaced` means the key is misspelled — say so rather than reporting success.
- **Unknown phil keys are refused.** `phil_parameters` is pydantic `extra='allow'`, so LUTE
  accepts anything and the fixed jinja template silently ignores what it does not reference.
  `--whitelist-dir` turns that invisible no-op into an error. Only pass `--no-whitelist` if
  the operator accepts that an unknown key will be dropped.
- The incident register runs automatically: `parallel_a2a`, `modify` in the merge step list,
  missing merge suffixes, `phil_file` without `{{ run }}`, merge-version padding, and
  `merging_d_min` disagreeing between the scale and merge stages.

Then show the operator the rendered config and the report together, and get approval before
step 5.

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

`install_lute.py` talks to the eLog, so this step **does** need a ticket. Verify afterwards:
`lute.db` (664), `{hutch}_lute.yaml` (666), `{wf_name}.dag` (666), and the workflow appearing
as `lute_{wf_name}` under Workflow Definitions in the eLog. Re-run with the same `-W` to
update registration after a DAG or trigger change.

---

## 6. Handoff (mandatory)

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" lute_config \
    '{"yaml_path": "'"$CONFIG"'", "workflows": ["lute_'"$WF_NAME"'"]}' \
    --source "install_lute.py -W $WF_NAME, verified in eLog Workflow Definitions" \
    --kind resolved
```

Then pass to coordinate-experiment for logging, and note any `# FILL IN` items still
outstanding in the config.
