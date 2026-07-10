# hutch-copilot — Development Plan

**Last updated:** July 10, 2026
**Repository:** https://github.com/psagents/hutch-copilot

**Goal:** Run a complete SFX experiment at MFX on **July 17, 2026** using agents
end-to-end, from beam readiness through data collection and processing.

---

## How to Contribute

### Repository layout

```
hutch-copilot/
├── SKILL.md                  ← top-level skill: orchestrator instructions
├── commands/                 ← one file per live bridge command
│   ├── are-we-ready.md
│   ├── align-spectrometer.md
│   └── take-run.md
├── analyze-data/             ← offline sub-skill (owns its own SKILL.md)
│   ├── SKILL.md
│   └── commands/
└── references/               ← static reference material (PVs, runcards, etc.)
```

### Working on a skill

Each skill or command is a plain Markdown file — no build step, no deployment.
To contribute:

1. **Clone the repo** on S3DF (or your laptop):
   ```bash
   git clone git@github.com:psagents/hutch-copilot.git
   ```
2. **Edit the relevant file** — `commands/<name>.md` for a bridge command,
   `analyze-data/commands/<name>.md` for an offline analysis command,
   or `SKILL.md` for orchestrator-level instructions.
3. **Test locally** by loading the skill in an OpenCode session and running
   through the command manually (bridge or planning mode as appropriate).
4. **Open a PR** against `main`. Tag the owner listed in the section header.

### Skill authoring conventions

- **Commands** (`commands/`): single workflow, bridge-coupled. Structure as
  Phase 1 → Phase N with explicit bridge invocations (`exec(open(...).read())`).
- **Sub-skills** (`analyze-data/`): own `SKILL.md` + `commands/` subtree.
  Use `@skill-name` delegation syntax to call sibling skills.
- **References** (`references/`): static lookup material only — no workflow logic.
- Keep prose tight. Use tables for tasks/owners/timelines; use code blocks for
  all scripts and bridge calls.

---

## Architecture

```
hutch-copilot               ← orchestrator (this repo)
├── /are-we-ready           ← HAPPI/lightpath beam readiness check  [bridge]
├── /align-spectrometer     ← VH auto-alignment + Amine's routine   [bridge]
├── /take-run               ← DAQ run control + sample tagging + cumulative aggregation  [bridge]
└── analyze-data/           ← data analysis sub-skill (offline, no bridge)
      ├── /setup            ← LUTE wizard (owns Phases 1–6)
      │       consults ────→ ask-lute        (reference brain: task catalog, YAML, hutch refs)
      │       consults ────→ ask-cctbx-xfel  (sibling, SFX params)
      │       consults ────→ ask-smalldata   (sibling, SmallData params)
      ├── /refine             inspect → adjust → re-trigger loop
      │       consults ────→ ask-lute        (YAML field conventions)
      │       consults ────→ ask-cctbx-xfel  (SFX-specific adjustments)
      │       consults ────→ ask-smalldata   (sibling, SmallData params)
```

**Skill placement rules:**
- **Commands** (`hutch-copilot/commands/`): single workflow, bridge-coupled, no domain
  references needed (`/are-we-ready`, `/align-spectrometer`, `/take-run`).
- **Sub-skills** (`hutch-copilot/analyze-data/`): multiple sub-commands, own reference
  files, different operational mode (offline), different owner.
- **Sibling skills** (top-level peers): pure knowledge experts independently useful
  beyond hutch-copilot — `ask-lute`, `ask-cctbx-xfel`, `ask-smalldata`, etc.

All live commands run through the hutch-python bridge (`nc localhost 9999`).
Sibling skills and `analyze-data` work in planning mode from any terminal.

---

## Skills

### hutch-copilot orchestrator

**Status:** draft | **Owner:** Louis

The top-level skill. Routes user intent to the right command or sub-skill,
holds the bridge setup guide, and owns the July 17 operator runcard.

**Consults:** `@.claude/cds-bridge` (bridge setup + device control), and makes to every possible skills available through session and in local `.claude/` 

| Task | When |
|---|---|
| Bridge setup guide: SSH tunnel steps, verification command | W1 |
| Integration testing across all commands | W2 |
| Operator runcard: one-page checklist for July 17 (bridge setup, command sequence, fallbacks) | W3 |
| Add `experiment-coordinator` to command dispatch table | W3 |
| Add `@ask-happi` to sub-skill reference table | W2 |
| Create `/fixdaq` stub command | post-Jul-17 |
| Create `/checkout` command for pre-experiment motor & device verification | post-Jul-17 |

---

### /are-we-ready

**Status:** draft | **Owner:** James

HAPPI/lightpath beam readiness check executed through the hutch-python bridge.
Queries every device upstream of the hutch (by z-position), reports insertion
state and transmission, and flags anything that may be blocking beam delivery.

**Execution path:** the command runs a standardized bridge script that Fred
committed to providing. That script is the primary path; the skill falls back
to a generated `lightpath`/`happi` query only if the script is not yet available.
The existing operator script at `/cds/home/opr/mfxopr/bin/awr` (PVs currently
hardcoded) serves as the MVP baseline until Fred's script lands.

**Consults:** `@ask-happi` (HAPPI device queries — to build), `@ask-epics` (PV reference), `references/beam-status-pvs.md` (machine-level escalation when all devices are OUT)

**Progressive capability:**

| Level | Description |
|---|---|
| **MVP** | Run and update the existing `/cds/home/opr/mfxopr/bin/awr` script |
| **Better** | Query HAPPI directly and generate a structured device summary |
| **Better+** | Compare current HAPPI state against the last experiment — "what worked last time?" |
| **Better++** | Continuous bookkeeping of all device changes throughout beamtime |

| Task | When |
|---|---|
| Obtain Fred's standardized AWR bridge script; integrate as primary execution path | W1 |
| Compare `/are-we-ready` output against existing tools (e.g. Matt's GUI) — validate coverage | W1 |
| Live test at MFX via Fred's bridge | W2 |
| Integrate `@ask-happi` delegation step (currently absent from command file) | W2 |
| Add machine-PV escalation block (from `references/beam-status-pvs.md`) when all devices OUT but beam missing | W2 |

---
### /align-spectrometer

**Status:** stub | **Owner:** Louis

Drives Amine's VH auto-alignment routine via the bridge, exposes Amine's routine and run the script.
Routines are at `https://github.com/pcdshub/mfx/tree/vonhamos_automation/mfx/optimize`.
Agent should make sure bridge is on and can communicate with hutch-python session.
Agent should make sure the AMI graph is already setup and set AMI graph parameters 
(averaging of epix100 images mainly) (through PVs).
Once everything is ready, run the commands.

**Consults:** `@experimental-hutch-python` (bridge execution + device moves), `@ask-ami` (AMI graph setup + PV configuration), `@ask-epics` (AMI PV documentation)

| Task | When |
|---|---|
| Get VH alignment function name/signature from Amine | W1 |
| Add AMI graph verification + set averaging PVs on epix100 before routine | W2 |
| Scaffold Phase 3 (Execute) with `vonhamos_automation` repo invocation pattern | W2 |
| Implement motor restore-on-failure logic | W2 |
| Complete command (bridge calls + geometry validation step) | W2 |
| Live test | W2 |

---
### /take-run

**Status:** draft | **Owner:** Fred / Louis | **Top priority**

DAQ run control + XTC2 file verification. Enhanced with sample tagging and
cumulative data aggregation.
DAQ is assumed to be running — a pre-check verifies it is live before proceeding.
Handle sample tagging and `run_type` definition.

**Sample tagging** uses both mechanisms:
- **hutch-python directly** — pass sample name via bridge before DAQ start so it
  lands in the native run record
- **elog-copilot** — after the run, call `@elog-copilot` to write a structured JSON
  entry with sample metadata keyed to the run number (feeds Murali's elog tab)

**Cumulative Data Aggregation:** filter on sample → merge → aggregate → append to
run summary. Enables per-sample tracking across multiple runs.

**Consults:** `@experimental-hutch-python` (DAQ control + bridge execution), `@elog-copilot` (post-run structured JSON tag), `@lcls-catalog` (XTC2 file verification), `@daq-logs` (DAQ error diagnosis)

| Task | When |
|---|---|
| Research: what metadata fields can be written into a run (`runtype`, etc.)? | W1 |
| Add DAQ pre-check (verify DAQ is running before Phase 1 proceeds) | W2 |
| Add sample tagging via hutch-python (bridge call before DAQ start) | W1 |
| Add sample tagging via `@elog-copilot` (structured JSON post-run log) | W1 |
| Implement cumulative aggregation logic + run summary output | W2 |
| Live test on past MFX experiment | W2 |

---
### experiment-coordinator

**Status:** draft | **Owner:** 

Starts with beamtime-logger.
Checks the experiment status every time a call is made to whatever commands:
Bookkeeping check for sample change, write YAMLs with configuration, etc... 
Keeps track of the "current" experiment context and translates that to the various skills' contexts.
Something that @hutch-copilot would run in the background. 

**Consults:** `@elog-copilot` (write structured eLog entries), `@ask-lcls2` (run metadata inspection), `@lcls-catalog` (experiment file inventory)

| Task | Owner | When |
|---|---|---|
| Fred sends `beamtime-logger` artifact | Fred | W2 |
| Define YAML schema for experiment state (sample, config, run mapping) | Louis + Fred | W2 |
| Define integration points with `/take-run`, `analyze-data`, `@elog-copilot` | Louis | W3 |

---

### analyze-data  *("analysis maker")*

**Status:** testing | **Owner:** Constance (sub-skill) + Louis (PRs)

LUTE wizard, calibration, refinement, and job monitoring. LUTE-LCLS environment
is **ready**. Currently in testing. Three PRs in flight (Louis):

| PR | Status |
|---|---|
| SMD templating | next |
| `run_type` branching | next |
| Beamline summary task | next |

**Consults:** `@ask-lute` (LUTE reference brain: task catalog, YAML, hutch refs), `@ask-cctbx-xfel` (SFX indexing/merging params), `@ask-smalldata` (SmallData detector params), `@ask-slurm-s3df` (job submission + monitoring), `@lcls-catalog` (file finding, XTC2 inventory), `@ask-lcls2` (psana2 data inspection + calibration check)

| Task | Owner | When |
|---|---|---|
| Centralize LUTE install path decision | Louis | W1 |
| Add `@ask-smalldata` delegation at Step 3.4 Tier 2 | Louis | W1 |
| Add `@ask-cctbx-xfel` delegation at Step 3.4 Tier 2 | Louis | W1 |
| Reflect SMD templating PR in `setup.md` Phase 4 | Louis | W2 |
| Reflect `run_type` branching PR in `setup.md` | Louis | W2 |
| Add beamline summary task to task catalog in `setup.md` | Louis | W2 |
| Add hit/indexing rate vs. time/shot display to `monitor-jobs.md` | Louis | W2–W3 |
| Verify `/calibrate` geometry pool path is current for S3DF | Louis | W2 |
| Full MFX SFX walkthrough test (LCLS-II, ePix10k2M, CheetahRunner → CrystFEL) | Louis + Pam | W2 |

---

### ask-lute  *(sibling skill — reference brain)*

**Status:** reference-only (complete) | **Owner:** Louis

Pure reference brain: LUTE task catalog, YAML syntax, hutch-specific knowledge.
The wizard was moved out into `analyze-data/commands/setup.md` and `refine.md`.
`analyze-data` owns the wizard and consults `@ask-lute` for LUTE internals.

**Consults:** none — pure reference, consulted by others, no sub-delegation

No active development tasks for July 17 — kept up to date as LUTE evolves.

---

### ask-cctbx-xfel  *(sibling skill — SFX parameter expert)*

**Status:** not started | **Owner:** Louis + Pam *(Constance on holiday)*

SFX parameter expert consulted by `analyze-data /setup` (Step 3.4 Tier 2) and
`/refine`. Covers CrystFEL vs. CCTBX decision logic, xgandalf flags, indexing
strategy, and MFX-specific SFX defaults. Pam to advise on missing pieces.

**Consults:** `@confluence-doc` (LCLS SFX documentation) — pure reference skill, no bridge, no sub-delegation

| Task | Owner | When |
|---|---|---|
| SKILL.md first draft (MFX CrystFEL params, xgandalf flags, CCTBX path) | Louis + Pam | W2 |
| Flesh out full MFX SFX parameter set (CrystFEL flag reference, CCTBX phil params, when-to-use logic) | Louis + Pam | W2 |
| Validate: skill correctly answers "CrystFEL or CCTBX?" for MFX SFX | Louis + Pam | W3 |

> **Note — hit/indexing rate vs. time/shot (CCTBX beamline summary):**
> The feature (hit rate + indexing rate vs. time/shot)
> may be implementable as **LUTE tasklets** wrapping CCTBX beamline summary output.
> Pam to advise on whether existing CCTBX beamline summary tasks can be exposed
> as tasklets and fed into the `/monitor-jobs` display.

---

## Timeline

| Week | Dates | Status | Focus | Exit gate |
|---|---|---|---|---|
| **W1** | Jun 30 – Jul 4 | complete | Foundation: renames, architecture, and `lute` code | — |
| **W2** | Jul 7 – Jul 10 | **in progress (ends today)** | Write 5 skills individually testable; bridge confirmed at MFX | All skills pass solo test |
| **W3** | Jul 14 – Jul 16 | upcoming | Integrate & test on previous experiments | Dry run pass |
| **Jul 17** | Fri | **beamtime** | SFX experiment run end-to-end with agents | — |

**W2 highlights:**
- `run_type` branching tested with `/take-run` (soon to be done)
- `experiment-coordinator`: starts from `beamtime-logger` — Fred to send (pending)
- `are-we-ready`: integrate `ask-happi` reference (pending)

## Action Items

| Item | Owner | When |
|---|---|---|
| Finish `analyze-data` PRs (SMD templating, `run_type` branching, beamline summary); reflect in skill files | Louis | W2 |
| Push `are-we-ready` skill; integrate `@ask-happi` delegation | James | W2 |
| Write `/take-run`: add sample tagging (hutch-python + `@elog-copilot`) and `run_type` field | Fred / Louis | W2 |
| Send `beamtime-logger` as starting point for `experiment-coordinator` | Fred | W2 |
| Create `ask-cctbx-xfel` SKILL.md first draft (MFX CrystFEL params, indexing strategy) | Louis + Pam | W2–W3 |
| Add DAQ pre-check to `/take-run` Phase 1 | Fred / Louis | W2 |

**W2 skills in scope:**

| Skill | Mode | Status |
|---|---|---|
| `are-we-ready` | bridge | in progress |
| `align-spectrometer` | bridge | stub — blocked on Amine |
| `take-run` | bridge | draft — missing sample tagging |
| `experiment-coordinator` | offline | draft — blocked on Fred's beamtime-logger |
| `analyze-data` | offline | testing — 3 PRs in flight |

---

## July 17 MVP Scenario

```
1.  Open hutch-python bridge (SSH tunnel → nc localhost 9999)
2.  Bring in beam with Matt's GUI
3.  hutch-copilot: /are-we-ready mfx   (step through each imager in sequence)
4.  hutch-copilot: /align-spectrometer   (VH auto-align)
5.  hutch-copilot: /analyze-data /calibrate   (darks + .geom check)
5.5 hutch-copilot: push geometry file to calibration database (LCLSGeom)
6.  hutch-copilot: /analyze-data /setup      (LUTE wizard)
                                              consults ask-lute (reference)
                                              consults ask-cctbx-xfel (indexing params)
──── MVP ends here ────────────────────────────────────────────────────────
7.  hutch-copilot: /take-run sample:lysozyme jet:50µm
8.  hutch-copilot: /take-run ...    (repeat per sample condition)
9.  hutch-copilot: /analyze-data /monitor-jobs   (hit/indexing rate vs. time/shot)
──── screen record everything ─────────────────────────────────────────────
```

DAQ generation: **LCLS-II / psana2 / `.xtc2`** confirmed.

---

## Known Gaps  *(must resolve before July 17)*

| Gap | Risk | Owner |
|---|---|---|
| Amine's VH alignment function name/signature unknown | High — `/align-spectrometer` is a stub | Louis + Amine |
| What metadata fields can be written into a run (`runtype`?) | High — blocks `/take-run` sample aggregation | Fred |
| `/take-run` missing sample tagging (hutch-python + `@elog-copilot` paths) | High — blocks run metadata for July 17 | Fred / Louis |
| Fred's HAPPI bridge not yet integrated | Medium — `/are-we-ready` falls back to docs mode | Claire + Fred |
| `/are-we-ready` output not yet validated against existing tools | Medium — could miss beam path gaps | Claire |
| `ask-cctbx-xfel` skill does not exist yet; Constance on holiday | Medium — `analyze-data /setup` Step 3.4 Tier 2 falls back to user prompt | Louis + Pam |
| No second dry run scheduled after July 10 | Medium — July 14–16 is fix-only buffer | All |
| `experiment-coordinator` has no skill file; blocked on beamtime-logger from Fred | Medium — experiment bookkeeping will not work without it | Fred |
| `/fixdaq` referenced in error handling but does not exist | Low — operators know the manual fix | Louis |
| `/checkout` has no command file; referenced in `mfx.md` | Low — operators do this manually today | Louis (post-Jul-17) |

---

## Murali Item  *(separate track — not blocking July 17)*

If `/take-run` writes structured sample metadata as JSON messages into the elog
(via `@elog-copilot`), can Murali provide a dedicated elog tab for better
visualization and interaction of that metadata?

This is a natural downstream consumer of the `/take-run` elog-copilot tagging work.
No July 17 dependency, but the JSON schema should be agreed before Fred finalizes
the elog-copilot integration.

---

## Post-July-17 Refactor

**1. ~~Reduce `ask-lute` to a reference-only skill~~ — DONE**

`ask-lute/commands/lute-setup.md` and `ask-lute/commands/lute-refine.md` have
been moved into `analyze-data/commands/setup.md` and `analyze-data/commands/refine.md`.
`ask-lute` is now a pure reference brain: task catalog, YAML syntax, hutch knowledge.
`analyze-data` owns the wizard and consults `@ask-lute` for LUTE internals.

**2. Other identified refactors** (lower priority, post-beamtime):
- Add hutch references for TMO, RIX, CXI to `hutch-copilot/references/hutches/`
- Generalize `analyze-data/references/sfx-analysis-defaults.md` for other
  techniques (TR-SAXS, XES)
- Build `/checkout` command for pre-experiment motor & device verification (MFX beam path + sample area motors)
- Build `/fixdaq` command stub for DAQ recovery guidance

---

## Team

| Person | Primary responsibility | Availability |
|---|---|---|
| **Louis** | hutch-copilot orchestrator, `/align-spectrometer`, `analyze-data` PRs (SMD templating, `run_type` branching, beamline summary), bridge guide, dry run coordination | available |
| **Claire** | `/are-we-ready` (Fred's HAPPI bridge), comparison vs existing tools, live testing | **on holiday** |
| **Fred** | `/take-run` (DAQ control, sample tagging, cumulative aggregation) + HAPPI bridge for `/are-we-ready` (consulting) | available |
| **Constance** | `analyze-data` sub-skill ownership, `ask-cctbx-xfel`, `/calibrate` validation, SFX LUTE YAML validation | **on holiday** |
| **Pam** | SFX expertise — help identify missing pieces for `ask-cctbx-xfel` (CrystFEL params, indexing strategy) | consulting |
| **Amine** | `/align-spectrometer` function in hutch-python (consulting) | consulting |
| **Murali** | Elog metadata visualization tab (separate track) | consulting |
