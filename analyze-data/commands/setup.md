# /setup — Configure LUTE Analysis Workflow

This command drives the full LUTE workspace setup for an LCLS experiment.
**Phases 1–4 are pure planning — no files are written and no scripts are run.**
Execution happens only in Phase 5, after the user has reviewed and approved the
complete plan.

Consult `ask-lute` as the reference brain throughout: use `ask-lute/references/`
for LUTE internals (task catalog, YAML syntax, hutch knowledge). The wizard logic
and experiment-level orchestration live here.

**Scripts referenced** (paths relative to `ask-lute/`):
- `ask-lute/scripts/install_lute.py` — workspace setup, DAG patching, eLog registration
- Templates: `ask-lute/templates/` — one YAML block per task

---

## Phase 1 — Gather Experiment Info

Ask the user for the following. Derive what can be derived; only ask for what cannot.

| Variable | How to obtain |
|---|---|
| `experiment` | Ask the user (e.g. `mfxl1013621`) |
| `hutch` | Derived: first 3 characters of `experiment` |
| `version` | Ask — default `dev` |
| `directory` | Ask — optional subdirectory under `results/`; default empty |

Derived paths:
```
results_dir     = /sdf/data/lcls/ds/{hutch}/{experiment}/results[/{directory}]
lute_output_dir = {results_dir}/lute_output
config_path     = {lute_output_dir}/{hutch}_lute.yaml
```

### Kerberos ticket — check now

Run immediately after paths are derived. `install_lute.py` requires a valid ticket to
register workflows in the eLog. Checking early lets the user obtain one in the background
while Phases 2–4 proceed.

```bash
klist -c FILE:$HOME/krb5cc.ticket
```

**Valid:** proceed silently.
**Missing or expired:** show the user this command to run in their own terminal
(the AI never asks for the password) and ask them to confirm once done:
```
kinit -c FILE:$HOME/krb5cc.ticket <username>@SLAC.STANFORD.EDU
```
Re-run `klist` to verify before continuing.

---

## Phase 2 — LUTE Install Type Decision

**Decision only — nothing is installed or executed yet.**

Ask the user whether they want a **central install** (default, read-only) or a
**fresh install** (local clone, allows code modifications).

### Option A — Central install (recommended for most users)

No build step required. Record these paths for Phase 5:
```
lute_path         = /sdf/group/lcls/ds/tools/lute/{version}/lute
arp_executable    = {lute_path}/install/bin/submit_launch_slurm.sh
launch_executable = {lute_path}/install/bin/launch_slurm
hutch_config      = {lute_path}/config/{hutch}.yaml
test_config       = {lute_path}/config/test.yaml
```

### Option B — Fresh install (for local code modifications)

Record the commands to run in Phase 5:
```bash
git clone https://github.com/slac-lcls/lute.git {results_dir}/lute
cd {results_dir}/lute && git checkout {version}
./build.sh -e
chmod -R 775 {results_dir}/lute
```

> IMPORTANT: Clone directly to `{results_dir}/lute`. Target directory must not pre-exist.
> `./build.sh -e` installs entry points and takes several minutes.
> If not working: `source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh`

Fresh-install paths to record:
```
lute_path         = {results_dir}/lute
arp_executable    = {lute_path}/install/bin/submit_launch_slurm.sh
launch_executable = {lute_path}/install/bin/launch_slurm
hutch_config      = {lute_path}/install/lib/python{X.Y}/site-packages/config/{hutch}.yaml
test_config       = {lute_path}/install/lib/python{X.Y}/site-packages/config/test.yaml
```
(`python{X.Y}` = Python version in the active environment, e.g. `python3.9`)

> **Development vs. production note:** Currently LUTE is deployed as a managed
> git + conda installation at S3DF. In the future, LUTE will be a standard pip or conda
> package — the install phase will be unnecessary for experiment data analysis.
> Phase 2 Option B will remain relevant for **developers** modifying LUTE source code.

---

## Phase 3 — Analysis & DAG Planning

**Still planning — no execution.** This phase produces two artefacts for the user to
approve before Phase 4: (1) the confirmed analysis chain, (2) the complete DAG YAML.

### Step 3.1 — Pre-inference: read the hutch reference, then ask one question

Read `ask-lute/references/hutches/{hutch}.md` (where `{hutch}` = first 3 characters
of the experiment name from Phase 1, e.g. `ask-lute/references/hutches/mfx.md` for
`mfxl1013621`).

Use it to:
- Determine DAQ generation (LCLS-I/psana1 or LCLS-II/psana2) and the correct SmallData task
- Identify the 1–3 most likely experiment types for that hutch
- Understand which detectors, beam monitors, and PVs are available — for use as
  **suggestions** when walking the user through Phase 4 YAML filling, not as pre-fills

**The hutch file is a reference for consultation, not a source of default values.**
Walk every YAML parameter explicitly with the user (Phase 4). Use the hutch file to
make informed suggestions and to recognise when a user-provided alias or PV looks
plausible vs. unusual.

Ask a single framed question that collects technique, pump laser, and scientific output
in one go. Frame technique as a confirmation based on hutch context. Example for `mfx`:
> "MFX typically runs SFX, TR-SAXS/WAXS, or XES. Which best describes yours?
> (a) SFX, (b) TR-SAXS/WAXS + pump-probe, (c) XES/RIXS, (d) something else?
> Also: is there a pump laser, and what output do you need?"

Record technique, pump-laser flag, and scientific output from this single answer. Do not
ask a separate Step 3.2 question — all three are answered here.

### Step 3.3 — Tier 1: Match against the LUTE task catalog

Identify which LUTE tasks are needed and how they chain. Output: an **ordered task list**.

Read `ask-lute/references/lute-configuration.md` for YAML parameter details and
`ask-lute/references/task-creation.md` for task internals if needed.

#### LUTE Task Catalog

Two different names exist for each task and they are used in different places:
- **YAML key** (Task class name) — the key used in the YAML config file
- **ManagedTask name** — the name used in `.dag` files and eLog workflow registration

These are NOT interchangeable. Using the wrong name in either place causes silent
misconfiguration or a validation error.

```
SmallData production  (entry point for all per-shot analysis)
  YAML key             ManagedTask name (DAG)     Notes
  SubmitSMD            SmallDataProducer           LCLS-I; psana1; raw XTC → HDF5
  SubmitSMD            SmallDataProducer2          LCLS-II; psana2; raw XTC2 → HDF5
  (same YAML key for both — DAG selects the right producer via !branch_daq2)
  Required for: any downstream per-shot analysis.

Downstream analysis  (each requires SmallData to complete first)
  YAML key                   ManagedTask name (DAG)
  AnalyzeSmallDataXSS        SmallDataXSSAnalyzer   Difference scattering (SAXS/WAXS/TR-XSS)
  AnalyzeSmallDataXAS        SmallDataXASAnalyzer   X-ray absorption (XAS/XANES/EXAFS)
  AnalyzeSmallDataXES        SmallDataXESAnalyzer   X-ray emission (XES/RIXS)

SFX / serial crystallography  (operates on XTC directly; no SmallData prerequisite)
  YAML key                   ManagedTask name (DAG)
  Peak finding — choose one backend:
    RunCheetah                 CheetahRunner          Cheetah; XTC → .cxi/stream (MFX/CXI standard)
    FindPeaksSFX               PeakFinderSFX          psana-native Peakfinder8/PyAlgos

  Indexing — choose one backend:
    IndexCrystFEL              CrystFELIndexer        CrystFEL indexamajig (xgandalf, mosflm, ...)
    IndexCCTBXXFEL             CCTBXIndexer           CCTBX.XFEL indexing

  Post-indexing (CrystFEL path):
    ConcatenateStreamFiles     StreamFileConcatenator Merge per-node .stream files
    MergePartialator           PartialatorMerger      CrystFEL partialator merging
    CompareHKL                 HKLComparer            Merge statistics
    ManipulateHKL              HKLManipulator         HKL scaling / format conversion
    DimpleSolve                DimpleSolver           Molecular replacement (DIMPLE)
    RunSHELXC                  SHELXRunner            Ab initio phasing (SHELXC/D/E)

  Post-indexing (CCTBX path):
    MergeCCTBXXFEL             CCTBXMerger            CCTBX.XFEL merging

Geometry calibration  (separate calibrant run, e.g. AgBh powder)
  YAML key              ManagedTask name (DAG)
  BayFAIOptimizer       BayFAIOptimizer        Bayesian PyFAI optimization (LCLS-I)
  BayFAIOptimizer2      BayFAIOptimizer2       Same for LCLS-II
  GeometryOptimizer     GeometryOptimizer      Exhaustive AgBh geometry search

Data format conversion
  ConvertXtc1to2        Xtc1to2Converter       XTC1 → XTC2 format conversion
  ConvertSMDToNexus     SMDtoNeXusConverter    SmallData HDF5 → NeXus
```

### Step 3.4 — Disambiguation (Tiers 2 and 3)

**Tier 2 — Consult specialist skills** when optional parameter configuration is ambiguous:

| Condition | Specialist skill | Example trigger |
|---|---|---|
| SmallData optional blocks unclear (detector algorithm, azimuthal integration type, photon counting strategy, ROI design) | `@ask-smalldata` | "I have an ePix10k2M — droplet finding or photon counting?" |
| SFX indexing/merging strategy unclear (CrystFEL vs CCTBX, post-merge pipeline) | `@ask-cctbx-xfel` | "CrystFEL or CCTBX for indexing?" |

Pass to the specialist: user's experiment description, candidate task list, specific question.

**Tier 3 — Targeted user prompt** when Tier 2 cannot resolve. Ask one specific question at a time:
- "Are you using CrystFEL or CCTBX.XFEL for indexing?"
- "Does a `.poni` calibration file already exist for this detector?"
- "What is the ADU-per-photon for your ePix detector at this gain setting?"
- "Are your raw files `.xtc` (LCLS-I/psana1) or `.xtc2` (LCLS-II/psana2)?"

### Step 3.5 — Design the custom DAG

Build the DAG YAML from the confirmed analysis chain. **Always build a custom minimal
DAG** — it contains exactly the tasks needed, nothing more.

For DAG YAML syntax, `!branch_daq2` usage, and `slurm_params` defaults, read
`ask-lute/references/workflow-creation.md`.

**DAG YAML syntax:**

Write the correct `slurm_params` for every task directly from the table below.
**Do NOT include `--partition` or `--account`** — `install_lute.py` appends those
at registration time from its `--partition` / `--account` arguments.

```yaml
!LUTE_DAG
task_name: "SmallDataProducer2"
slurm_params: "--nodes=4 --ntasks-per-node=50 --exclusive"
next:
- task_name: "SmallDataXESAnalyzer"
  slurm_params: "--nodes=1 --ntasks-per-node=1"
  next: []
```

**`slurm_params` per task (write these values verbatim into the DAG):**

| Managed Task Name | `slurm_params` to write |
|---|---|
| `SmallDataProducer` | `--nodes=4 --ntasks-per-node=50 --exclusive` |
| `SmallDataProducer2` | `--nodes=4 --ntasks-per-node=50 --exclusive` |
| `BayFAIOptimizer` | `--nodes=1 --ntasks-per-node=120` |
| `BayFAIOptimizer2` | `--nodes=1 --ntasks-per-node=120` |
| All other tasks | `--nodes=1 --ntasks-per-node=1` |

> **CRITICAL — production task names only.**
> Always use the exact Managed Task names above (`SmallDataProducer2`, not
> `SmallDataProducer2Test` or any other `...Test` variant). Test-suffixed tasks
> are development stubs; using them in a production DAG causes silent resource
> misallocation. If in doubt, verify against `managed_tasks.py` on GitHub.
> To change resources for a specific task after setup, edit the `.dag` file directly.

Default SLURM globals (ask only if user wants to override):
- `--partition=milano`
- `--account=lcls:{experiment}`

**Choose a meaningful workflow name** (e.g., `xss_analysis`, `sfx_crystfel`,
`rix_rixs`). This becomes the DAG filename and the eLog workflow registration name
(the eLog entry will be `lute_{wf_name}`).

### Step 3.6 — Confirm analysis plan

Show the complete plan and get explicit user approval before proceeding to Phase 4:

```
Analysis plan
──────────────────────────────────────────
Hutch    : {hutch}  ({LCLS-I/psana1 or LCLS-II/psana2})
Chain    : {Task1} → {Task2} → ...

Workflows:
  {wf_name}
    DAG    : {lute_output_dir}/{wf_name}.dag
    Trigger: {END_OF_RUN | MANUAL | RUN_PARAM_IS_VALUE:SmallData:done}
  (repeat for each workflow)

DAG YAML:
{full DAG YAML content}

install_lute.py (will be run at end of Phase 5):
  python ask-lute/scripts/install_lute.py -e {experiment} -v {version} \
    [-f]                          # only when fresh install (Phase 2 Option B) \
    -W {wf1} [{wf2}] --trigger {spec1} [{spec2}]
──────────────────────────────────────────
Proceed to YAML configuration? (yes / adjust)
```

Do not advance to Phase 4 until the user approves this plan.

---

## Phase 4 — YAML Configuration

**Still planning — no files written yet.** Walk every parameter explicitly with the
user. Never guess a value and present it for approval — ask first, then assemble.

For template blocks, read `ask-lute/templates/` for the relevant task template.

The protocol for every block is:
1. Ask the **enabling question** ("Do you need X?")
2. If yes, ask **each parameter** in the block one at a time
3. Show the **assembled block** as a checkpoint
4. Get confirmation before moving to the next block

### Step 4.0 — Detector alias pre-flight (hard gate)

Collect every detector alias that will appear anywhere in the YAML **before** filling
in any field. A wrong alias produces no error — just silently missing data.

Ask the user to run the appropriate command for a representative run:

> **psana1:** `ds = psana.DataSource('exp={experiment}:run={run}:smd'); print(next(ds.events()).keys())`
>
> **psana2:** `ds = DataSource(exp='{experiment}', run={run}); print(next(ds.runs()).detnames)`

Record all confirmed aliases. Never write a detector name that the user has not
confirmed. If uncertain, write `"# VERIFY ALIAS"` as a placeholder.

### Step 4.1 — YAML header

Ask each field explicitly. For variable substitution syntax see
`ask-lute/references/lute-configuration.md`.

1. **title** — "Optional: Brief description of this experiment config?"
2. **experiment** — already known from Phase 1; confirm with user
3. **run** — leave empty (`""`); filled automatically at runtime
4. **date** — today's date (`YYYY/MM/DD`)
5. **task_timeout** — "Maximum runtime per task in seconds? [3600]"
6. **work_dir** — already known (`{lute_output_dir}`); confirm

> **Checkpoint — header block.** Correct? (yes / adjust)

### Step 4.2 — SubmitSMD: output directory

Ask: "Where should SmallData HDF5 files be written? Default:
`/sdf/data/lcls/ds/{hutch}/{experiment}/hdf5/smalldata`"

> **Checkpoint — directory field.** Correct? (yes / adjust)

### Step 4.3 — SubmitSMD: producer_parameters

Walk the relevant categories from the template at `ask-lute/templates/`. Pre-filter
using hutch context and technique from Phase 3 — skip categories that are clearly
inapplicable (see full skip rules in `ask-lute/references/lute-configuration.md`).

| Category | Skip silently when |
|---|---|
| A — Scattering (SAXS/WAXS/XSS) | Technique is XES, RIXS, XAS, SFX, or photoemission |
| B — Beam monitors | Hutch has no FIM/Wave8 wiring |
| C — Integrating detectors | No Archon/Andor/CCD mentioned |
| D — Area detector image saves | Not doing XES/RIXS/spectroscopy |
| E — Sparse photon counting | High-flux scattering; not sparse XES |
| F — XPCS/autocorrelation | Not XCS and user did not mention speckle |
| G — SVD multi-bunch | User did not mention multi-bunch shots |
| H — cRIXS/Pressio compression | Not TMO/RIX or no Rowland circle spectrometer |
| I — Timing tool | User explicitly said no pump laser |
| J — Detector accumulation | **Never skip** — always ask |

For each relevant category: ask the enabling question; if yes, ask each parameter;
then checkpoint. Delegate to `@ask-smalldata` if the detector algorithm choice
is ambiguous (Tier 2, Step 3.4).

### Step 4.4 — Downstream task parameters

For each downstream task, walk every non-commented field in the corresponding
template at `ask-lute/templates/`. Common fields:
- `smd_path` — set silently to `""`; auto-populated from LUTE DB
- `ipm_var` — "Which IPM alias for X-ray intensity filtering?"
- `scan_var` — "Is there a scan variable? If yes, what is its DAQ alias?"

For SFX tasks (`IndexCrystFEL`, `MergePartialator`, etc.): delegate parameter
selection to `@ask-cctbx-xfel` (Tier 2, Step 3.4) before filling the template.
Cross-check results against `analyze-data/references/sfx-analysis-defaults.md`.

> **Checkpoint after each downstream task block.** Correct?

### Step 4.5 — Full YAML review

Assemble the complete two-document YAML and present it in full for user approval
before writing anything to disk.

```
Here is the complete YAML that will be written to:
  {config_path}

{full two-document YAML}

Please review carefully before I write anything to disk.
Shall I proceed to Phase 5 (execution)? (yes / adjust)
```

---

## Phase 5 — Execute

The user has approved the plan (Phase 3) and the YAML (Phase 4). Execute in order.

### Step 5.1 — Fresh install build (Option B only)

```bash
git clone https://github.com/slac-lcls/lute.git {results_dir}/lute
cd {results_dir}/lute && git checkout {version}
./build.sh -e
chmod -R 775 {results_dir}/lute
```

### Step 5.2 — Create output directory

```bash
mkdir -p {lute_output_dir}
chmod 777 {lute_output_dir}
```

### Step 5.3 — Write DAG file(s)

```bash
cat > {lute_output_dir}/{wf_name}.dag << 'EOF'
{full DAG YAML from Phase 3}
EOF
chmod 666 {lute_output_dir}/{wf_name}.dag
```

### Step 5.4 — Write YAML configuration

```bash
cat > {config_path} << 'EOF'
{full two-document YAML from Phase 4}
EOF
chmod 666 {config_path}
```

### Step 5.5 — Run install_lute.py

```bash
python ask-lute/scripts/install_lute.py \
  -e {experiment} \
  -v {version}    \
  [-f]                          \   # include when fresh install (Phase 2 Option B)
  [-D {directory}]             \
  -W {wf1} {wf2} ...           \
  --trigger {spec1} {spec2} ... \
  [--partition {partition}]     \
  [--account {account}]
```

> **Important:** Always pass `-D {directory}` when a subdirectory was chosen in Phase 1.
> Omitting it causes `install_lute.py` to default to `results/lute_output/` and create
> a spurious workspace there instead of the correct `results/{directory}/lute_output/`.

`--trigger` specs (one per `-W` workflow, in matching order):

| Spec | When to use |
|---|---|
| `END_OF_RUN` | Workflow processes raw data offline |
| `START_OF_RUN` | SmallData in live mode |
| `MANUAL` | Calibration or one-time computation |
| `RUN_PARAM_IS_VALUE:SmallData:done` | Workflow depends on SmallData completing |

### Step 5.6 — Verify

```bash
ls -la {lute_output_dir}/
# Expected: lute.db (664), {hutch}_lute.yaml (666), {wf_name}.dag (666)
```

---

## Phase 6 — Verify eLog Registration

Confirm workflows appear in the eLog:
```
https://pswww.slac.stanford.edu/lgbk/lgbk/{experiment}/
→ Workflow Definitions tab
```

Workflows are registered as `lute_{wf_name}`. If re-registration is needed after
a DAG or trigger change, re-run `install_lute.py` with the same `-W` arguments —
the workspace and DAG patching steps are idempotent.

---

## Quick Reference — Phase Order

```
Phase 1  Gather info          → experiment name, hutch, paths
Phase 2  Install type         → central or fresh (decide only)
Phase 3  Analysis & DAG plan  → task chain, DAG YAML, triggers   ← user approves
Phase 4  YAML configuration   → alias pre-flight, ask every param ← user approves each block
Phase 5  Execute              → mkdir, write DAGs, write YAML, install_lute.py
Phase 6  Verify               → confirm workflows in eLog UI
```

Nothing touches the filesystem before Phase 5.
