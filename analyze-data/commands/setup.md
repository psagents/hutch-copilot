# /setup — Configure LUTE Analysis Workflow

Phases 1–4 are pure planning — no files written, no scripts run.
Execution happens only in Phase 5 after user approval.

**Reference brain:** use `@ask-lute` for all LUTE internals (task catalog, YAML syntax,
DAG structure, hutch+technique references, and templates).

**Scripts referenced:**
- `analyze-data/scripts/install_lute.py` — venv setup, DAG patching, eLog registration

---

## Experiment State (silent context)

Before asking the user for anything, read:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/psagents/{experiment}_state.json
```
Use any non-null field directly. If `hutch` or `experiment` are unknown, ask first.

Derived paths:
```
results_dir     = /sdf/data/lcls/ds/{hutch}/{experiment}/results[/{directory}]
lute_output_dir = {results_dir}/lute_output
config_path     = {lute_output_dir}/{hutch}_lute.yaml
lute_envs_dir   = {results_dir}/lute_envs
```

---

## Phase 1 — Gather Experiment Info

| Variable | How to obtain |
|---|---|
| `experiment` | State file; ask only if null (e.g. `mfxl1013621`) |
| `hutch` | State file; derive from first 3 chars of experiment as fallback |
| `version` | Ask — default `dev` |
| `directory` | Ask — optional subdirectory under `results/`; default empty |

### Kerberos check (run immediately after paths are derived)

```bash
klist -c FILE:$HOME/krb5cc.ticket
```

Valid → proceed silently. Missing/expired → show the user:
```
kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU
```
Re-run `klist` to verify before continuing.

---

## Phase 2 — Analysis & DAG Planning

Ask `@ask-lute`:
> "For hutch `{hutch}`, technique `{technique}`, give me the LUTE task chain,
> the experiment reference (`references/{hutch}/{technique}.md`), and the starting
> template (`templates/{hutch}/{technique}.yaml`)."

If the technique is not yet known, ask a single framing question first:
> "What technique are you running? (e.g. SFX, TR-SAXS, XES, RIXS, other?)"

Then ask `@ask-lute` for the appropriate reference.

### Design the DAG

Build a minimal custom DAG — exactly the tasks needed, nothing more.

**For SFX experiments with DARK / GEOM / DATA run types**, use `!run_type` branching
(see canonical template in `references/sfx-analysis-defaults.md`):

```yaml
!LUTE_DAG
- !run_type
  DARK: []          # empty — no tasks for pedestal runs
  GEOM:
    task_name: "SmallDataProducer2"
    slurm_params: "..."
    next:
    - task_name: "BayFAIOptimizer2"
      slurm_params: "..."
      next: []
  DATA:
    task_name: "CCTBXIndexer"   # or first task of chosen pipeline
    slurm_params: "..."
    next: [...]
```

For **other techniques** (no run_type branching needed), use a simple linear DAG:

```yaml
!LUTE_DAG
task_name: "<ManagedTaskName>"
slurm_params: "<params>"
next:
- task_name: "<NextManagedTaskName>"
  slurm_params: "<params>"
  next: []
```

`slurm_params` defaults (write verbatim) to `--nodes=1 --ntasks-per-node=1`
Ask the user if more compute power is needed, usually the case for `MPIExecutor` managed tasks.

Choose a `wf_name` (e.g. `sfx_crystfel`, `sfx_cctbx`, `xss`, `xes`).

Default SLURM globals (ask only if overriding): `--partition=milano`, `--account=lcls:{experiment}`

`--trigger` spec per workflow:

| Spec | When to use |
|---|---|
| `END_OF_RUN` | Offline analysis after run completes |
| `START_OF_RUN` | SmallData in live mode |
| `MANUAL` | Calibration or one-time computation |

### Confirm analysis plan

```
Analysis plan
──────────────────────────────────────────
Hutch    : {hutch}  (LCLS-I/psana1 or LCLS-II/psana2)
Chain    : {Task1} → {Task2} → ...
Workflow : {wf_name}
  DAG    : {lute_output_dir}/{wf_name}.dag
  Trigger: {trigger_spec}

DAG YAML:
{full DAG YAML}
──────────────────────────────────────────
Proceed to YAML configuration? (yes / adjust)
```

Do not advance to Phase 3 until approved.

---

## Phase 3 — YAML Configuration

Ask `@ask-lute` for the experiment template:
> "Give me `templates/{hutch}/{technique}.yaml` — I will walk the user through
> each field that needs verification."

### Step 3.0 — Detector alias pre-flight (hard gate)

Collect every detector alias before filling any field. Ask the user to run:

> **psana2:** `ds = DataSource(exp='{experiment}', run={run}); print(next(ds.runs()).detnames)`
> **psana1:** `ds = psana.DataSource('exp={experiment}:run={run}:smd'); print(next(ds.events()).keys())`

Never write an alias the user has not confirmed. If uncertain, write `"# VERIFY ALIAS"`.

### Step 3.1 — Walk the template

For each block in the template:
1. Ask the enabling question ("Do you need X?")
2. Ask each `# VERIFY WITH USER` / `# FILL IN` field one at a time
3. Show the assembled block as a checkpoint — get confirmation before moving on

For SmallData optional blocks (detector algorithm, azimuthal integration, ROI design):
delegate to `@ask-smalldata` if the choice is ambiguous.

For SFX indexing/merging parameters (CrystFEL vs CCTBX.XFEL, unit cell, symmetry):
delegate to `@ask-cctbx-xfel`. If unavailable, ask directly:
- "CrystFEL or CCTBX.XFEL for indexing?"
- "Is the unit cell known? If yes, provide a,b,c,α,β,γ."
- "What is the point group / space group for merging?"

### Step 3.2 — Full YAML review

```
Here is the complete YAML to be written to:
  {config_path}

{full two-document YAML}

Proceed to Phase 4 (execution)? (yes / adjust)
```

---

## Phase 4 — Execute

### Step 4.1 — Create output directory

```bash
mkdir -p {lute_output_dir}
chmod 777 {lute_output_dir}
```

### Step 4.2 — Write DAG file

```bash
cat > {lute_output_dir}/{wf_name}.dag << 'EOF'
{full DAG YAML from Phase 2}
EOF
chmod 666 {lute_output_dir}/{wf_name}.dag
```

### Step 4.3 — Write YAML configuration

```bash
cat > {config_path} << 'EOF'
{full two-document YAML from Phase 3}
EOF
chmod 666 {config_path}
```

### Step 4.4 — Run install_lute.py

Source psana environment first (required every time):

```bash
source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh
```

Then run:

```bash
python analyze-data/scripts/install_lute.py \
  -e {experiment} \
  -v {version}    \
  [-D {directory}]              \
  -W {wf1} [{wf2} ...]          \
  --trigger {spec1} [{spec2} ...] \
  [--partition {partition}]     \
  [--account {account}]
```

> Always pass `-D {directory}` when a subdirectory was chosen in Phase 1.

### Step 4.5 — Verify

```bash
ls -la {lute_output_dir}/
# Expected: lute.db (664), {hutch}_lute.yaml (666), {wf_name}.dag (666)
```

---

## Phase 5 — Verify eLog Registration

```
https://pswww.slac.stanford.edu/lgbk/lgbk/{experiment}/
→ Workflow Definitions tab
```

Workflows appear as `lute_{wf_name}`. Re-run `install_lute.py` with the same `-W` arguments
to update registration after a DAG or trigger change.

---

## → coordinate-experiment handoff (mandatory after Phase 5)

1. Update `lute_config` in state JSON:
```json
"lute_config": {
  "yaml_path": "{config_path}",
  "workflows": ["lute_{wf1}", ...],
  "configured_at": "{ISO timestamp}"
}
```

2. Append to log:
```markdown
- **{HH:MM}** LUTE configured. Workflows: {workflows}. Config: {config_path}
- **Action:** Fill `# FILL IN` items remaining in the YAML (geometry, symmetry, etc.)
```

3. Check for new PVs or detector aliases added during Phase 3 not yet in
   `@ask-lute` `references/{hutch}/{technique}.md` — backfill per the
   coordinate-experiment knowledge backfill rule.

---

## Quick Reference — Phase Order

```
Phase 1  Gather info       → experiment, hutch, paths, Kerberos
Phase 2  Analysis plan     → @ask-lute for chain + DAG; user approves
Phase 3  YAML config       → @ask-lute template; walk fields; user approves
Phase 4  Execute           → mkdir, write DAGs, write YAML, install_lute.py
Phase 5  Verify            → confirm workflows in eLog UI
```
