# hutch-copilot — Development Plan

**Last updated:** July 16, 2026  
**Source:** Louis's plan shared Fri Jul 10 (`PLAN.md`) + BAWG notes Mon Jul 13 + Jul 15–16 session notes  
**Repository:** https://github.com/psagents/hutch-copilot

**Goal:** Run a complete SFX experiment at MFX on **July 17, 2026** using agents
end-to-end, from beam readiness through data collection and processing.

---

## How to Contribute

### Repository layout

```
hutch-copilot/
├── SKILL.md                        ← orchestrator; initializes coordinate-experiment on first trigger
├── commands/                       ← bridge commands (hutch-python, no sub-commands)
│   ├── take-run.md
│   └── align-spectrometer.md       ← /align-spectrometer stub
├── are-we-ready/                   ← sub-skill (promoted from flat command)
│   ├── SKILL.md
│   └── scripts/
│       └── check_beam_ready_mfx.py ← MFX beam-readiness script (7 checks)
├── coordinate-experiment/          ← ambient context manager; owns experiment state + log
│   └── SKILL.md
├── bridge-to-cds/                  ← psdev SSH bridge for /cds filesystem access
│   └── SKILL.md
├── analyze-data/                   ← offline data analysis sub-skill
│   ├── SKILL.md
│   ├── commands/
│   │   ├── setup.md
│   │   └── refine.md
│   └── references/
└── references/                     ← static lookup material (PVs, hutch configs)
    ├── beam-status-pvs.md
    └── hutches/
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
hutch-copilot                   ← orchestrator (this repo)
│   initializes coordinate-experiment on first trigger
│
├── /are-we-ready               ← upstream beam path check + MFX script  [bridge, sub-skill]
│       reads ─────→ coordinate-experiment: hutch, experiment
│       writes ────→ coordinate-experiment: machine_state.beam_present, last_checked
│
├── /align-spectrometer         ← VH auto-alignment + Amine's routine  [bridge, stub]
│       reads ─────→ coordinate-experiment: hutch, experiment, current_phase
│
├── /take-run                   ← DAQ run control + sample/calibration tagging  [bridge]
│       run_type = SAMPLE | GEOM | DARK | ...
│       tag written to XTC header + eLog run record
│       reads ─────→ coordinate-experiment: sample_name, concentration, delivery,
│                    photon_energy_eV, rep_rate_Hz, transmission, pump_*
│       writes ────→ coordinate-experiment: last_run_number, last_run_tag, daq_status
│       → Maestro branches on run_type when LUTE jobs fire at end-of-run
│
├── /coordinate-experiment      ← ambient context manager + beamtime log  [offline]
│       OWNS ──────→ {experiment}_state.json   (shared source of truth for all skills)
│       OWNS ──────→ {experiment}_logs.md      (timestamped shift log)
│       posts ─────→ @elog-copilot  (condition changes, shift handoff)
│       path: /sdf/data/lcls/ds/{hutch}/{experiment}/
│
├── bridge-to-cds/              ← psdev SSH bridge for /cds filesystem  [offline, NEW]
│
└── analyze-data/               ← data analysis sub-skill (offline, no bridge)
      reads ─────→ coordinate-experiment: hutch, experiment, photon_energy_eV,
                   last_run_number, sample_name, sample_delivery
      ├── /setup                ← LUTE wizard (Phases 1–6)
      │       consults ────→ ask-lute        (reference brain: task catalog, YAML, hutch refs)
      │       consults ────→ ask-cctbx-xfel  (sibling, SFX params)
      │       consults ────→ ask-smalldata   (sibling, SmallData params)
      └── /refine               ← inspect → adjust → re-trigger loop
              consults ────→ ask-lute        (YAML field conventions)
              consults ────→ ask-cctbx-xfel  (SFX-specific adjustments)
              consults ────→ ask-smalldata   (sibling, SmallData params)
```

### State propagation — coordinate-experiment as central hub

`coordinate-experiment` owns the canonical state file that every sub-skill reads
before asking the user anything. It is initialized once at session start and then
silently updated after every conversation turn.

```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/{experiment}_state.json
```

| Sub-skill | Fields read | Fields written |
|---|---|---|
| `/are-we-ready` | `hutch`, `experiment` | `machine_state.beam_present`, `machine_state.last_checked` |
| `/align-spectrometer` | `hutch`, `experiment`, `current_phase` | *(log entry only)* |
| `/take-run` | `sample_name`, `concentration`, `sample_delivery`, `delivery_details`, `photon_energy_eV`, `rep_rate_Hz`, `transmission`, `pump_laser`, `pump_delay_ps`, `last_run_tag` | `last_run_number`, `last_run_tag`, `machine_state.daq_status` |
| `analyze-data` | `hutch`, `experiment`, `photon_energy_eV`, `last_run_number`, `sample_name`, `sample_delivery` | *(none)* |
| `@elog-copilot` | `hutch`, `experiment`, current timestamp | *(none)* |

**Skill placement rules:**
- **Commands** (`hutch-copilot/commands/`): single workflow, bridge-coupled, no sub-commands
  (`/align-spectrometer`, `/take-run`).
- **Sub-skill directories**: own `SKILL.md` + assets; used when a skill has scripts, references,
  or a different operational mode — `are-we-ready/`, `coordinate-experiment/`, `bridge-to-cds/`,
  `analyze-data/`.
- **Sibling skills** (top-level peers): pure knowledge experts independently useful
  beyond hutch-copilot — `ask-lute`, `ask-cctbx-xfel`, `ask-smalldata`, `ask-happi`, etc.

All live commands run through the hutch-python bridge (`nc localhost 9999`).
Sub-skills and `analyze-data` work in planning/offline mode from any S3DF terminal.

---

## Skills

### hutch-copilot orchestrator

**Status:** draft | **Owner:** Louis

The top-level skill. Routes user intent to the right command or sub-skill,
holds the bridge setup guide, and owns the July 17 operator runcard.

**Consults:** `@.claude/cds-bridge` / sibling `cds-bridge` (bridge setup + device
control); makes available skills from the session and local `.claude/`

| Task | When |
|---|---|
| Bridge setup guide: SSH tunnel steps, verification command | W1 ✅ |
| Integration testing across all commands | W3 |
| Operator runcard: one-page checklist for July 17 (bridge setup, command sequence, fallbacks) | W3 |
| Add `/coordinate-experiment` to command dispatch table | W3 |
| Add `@ask-happi` to sub-skill reference table | W3 |
| Create `/fixdaq` stub command | post-Jul-17 |
| Create `/checkout` command for pre-experiment motor & device verification | post-Jul-17 |

---

### /are-we-ready

**Status:** ✅ done (Jul 16) | **Owner:** James | **Due:** ~~Wed 2026-07-15~~ delivered Jul 16

Beam readiness check executed through the hutch-python bridge. Promoted from a
flat command file to a **sub-skill directory** (`are-we-ready/SKILL.md` +
`are-we-ready/scripts/`). Reads `hutch`/`experiment` from the state JSON; no
re-ask if already in context.

**MFX script delivered (Jul 16):** `are-we-ready/scripts/check_beam_ready_mfx.py`
implements 7 checks (beam destination via HOMS pitch, imagers/YAGs, valves,
pulse energy, undulator pointing, slits, DAQ run number) and returns `True` only
if all CRITICAL checks pass. Script is sent inline to `mfx-daq` via the bridge —
no file copy required.

**Execution path:** the command runs a standardized bridge script that Fred
committed to providing. That script is the primary path; the skill falls back
to a generated `lightpath`/`happi` query only if the script is not yet available.
The existing operator script at `/cds/home/opr/mfxopr/bin/awr` (PVs currently
hardcoded) serves as the MVP baseline until Fred's script lands.

**Consults:** `@ask-happi` (HAPPI device queries), `@ask-epics` (PV reference),
`references/beam-status-pvs.md` (machine-level escalation when all devices are OUT)

**Progressive capability:**

| Level | Description |
|---|---|
| **MVP** | Run and update the existing `/cds/home/opr/mfxopr/bin/awr` script |
| **Better** | Query HAPPI directly and generate a structured device summary |
| **Better+** | Compare current HAPPI state against the last experiment — "what worked last time?" |
| **Better++** | Continuous bookkeeping of all device changes throughout beamtime |

| Task | When | Status |
|---|---|---|
| Implement `check_beam_ready_mfx.py` (7-check MFX script) | W3 | ✅ done (Jul 16) |
| Promote to sub-skill directory with `scripts/` | W3 | ✅ done (Jul 16) |
| Read state JSON for hutch/experiment context | W3 | ✅ done (Jul 16) |
| Compare output against existing tools (e.g. Matt's GUI) — validate coverage | W3 | open |
| Live test at MFX via bridge | W3 | open |

---

### bridge-to-cds  *(sub-skill — CDS filesystem + controls bridge)*

**Status:** ✅ done (Jul 16) | **Owner:** Louis

Enables access to `/cds/` filesystem resources from S3DF sessions via SSH to
`psdev`. Used whenever a sub-skill needs hutch-python configs, happi DB,
`conf.yml`, presets, or IOC files that are not mounted on S3DF. Also documents
the three distinct network environments (S3DF, CDS/psdev, controls network) and
the path to `caget`/`caput` on a hutch controls machine when EPICS CA access is
required.

**Key patterns covered:** pipe Python via `psdev` stdin, read `/cds/` files,
list hutch presets, look up happi entries by name, list beamline devices by
z-position, SSH to `mfx-control` for live PV reads.

**Consults:** none — reference/execution bridge only. No further active development
planned for July 17.

---

### /align-spectrometer

**Status:** stub | **Owner:** Louis (+ **Amine** consulting)

Drives Amine's VH auto-alignment routine via the bridge, exposes Amine's routine and runs the script.
Routines are at `https://github.com/pcdshub/mfx/tree/vonhamos_automation/mfx/optimize`.
Agent should make sure bridge is on and can communicate with hutch-python session.
Agent should make sure the AMI graph is already setup and set AMI graph parameters
(averaging of epix100 images mainly) (through PVs).
Once everything is ready, run the commands.

**Consults:** `@experimental-hutch-python` (bridge execution + device moves),
`@ask-ami` (AMI graph setup + PV configuration), `@ask-epics` (AMI PV documentation)

| Task | When |
|---|---|
| Get VH alignment function name/signature from Amine — **Fred follows up (T-71)** | ASAP |
| Confirm AMI2 is **latest from Seshu** (T-67) | ASAP |
| Add AMI graph verification + set averaging PVs on epix100 before routine | W3 |
| Scaffold Phase 3 (Execute) with `vonhamos_automation` repo invocation pattern | W3 |
| Implement motor restore-on-failure logic | W3 |
| Complete command (bridge calls + geometry validation step) | W3 |
| Live test | W3 |

---

### /take-run

**Status:** draft | **Owner:** Fred / Louis | **Top priority**

DAQ run control + XTC2 file verification. Enhanced with sample tagging and
cumulative data aggregation.
DAQ is assumed to be running — a pre-check verifies it is live before proceeding.
Handle sample tagging, calibration run labeling, and `run_type` definition.

**Calibration runs** use the same command as data runs — no separate `/calibrate`
command exists. Pass a `run_type` tag (`GEOM`, `DARK`, etc.) to `/take-run`; the
tag is written to the XTC header and the eLog run record. When LUTE's Maestro
workflow fires at end-of-run, it inspects `run_type` and branches to the correct
DAG path (e.g. BayFAI/GeometryOptimizer for `GEOM`, pedestal processing for
`DARK`). This replaces the deprecated `/calibrate` command.

**Sample tagging** uses both mechanisms:
- **hutch-python directly** — pass sample name via bridge before DAQ start so it
  lands in the native run record
- **elog-copilot** — after the run, call `@elog-copilot` to write a structured JSON
  entry with sample metadata keyed to the run number (feeds Murali's elog tab)

**Cumulative Data Aggregation:** filter on sample → merge → aggregate → append to
run summary. Enables per-sample tracking across multiple runs.

**Consults:** `@experimental-hutch-python` (DAQ control + bridge execution),
`@elog-copilot` (post-run structured JSON tag), `@lcls-catalog` (XTC2 file
verification), `@daq-logs` (DAQ error diagnosis)

| Task | When | Status |
|---|---|---|
| Research: what metadata fields can be written into a run (`run_type`, etc.)? | W2 | open (T-30) |
| Add DAQ pre-check (verify DAQ is running before Phase 1 proceeds) | W2 | ✅ done (`daq.status()` Phase 0) |
| Add sample tagging via hutch-python (bridge call before DAQ start) | W2–W3 | open |
| Add sample tagging via `@elog-copilot` (structured JSON post-run log) | W2–W3 | open |
| Implement cumulative aggregation logic + run summary output | W3 | open |
| Live test on past MFX experiment | W3 | open |

---

### /coordinate-experiment

**Status:** ✅ done (Jul 16) | **Owner:** Louis (+ Fred seed)

Ambient context manager for the entire beamtime session. Initialized once on the
first hutch-copilot trigger and then silently active for the whole shift. Owns the
canonical experiment state JSON and the timestamped log file. All other sub-skills
read the state file before asking the user for anything — `coordinate-experiment` is
the initialization prerequisite for the session.

**Persistent files** (top-level of experiment directory, pre-provisioned by LCLS DM):
- `{experiment}_state.json` — canonical state (sample, beam, run, machine_state)
- `{experiment}_logs.md` — timestamped shift log; never restructured, only appended

**Explicit user commands:** `/context` (print current state), `/handoff` (draft + post shift summary)

**Ambient (silent):** condition updates → state JSON + log append + eLog post via
`@elog-copilot`; run metadata from `/take-run` output → state JSON + log; machine
state from bridge triggers → state JSON only.

**Consults:** `@elog-copilot` (condition-update + handoff eLog posts)

| Task | Owner | When | Status |
|---|---|---|---|
| Publish / hand off `beamtime-logger` seed | Fred | W2 | ✅ |
| Build `coordinate-experiment/SKILL.md` from beamtime-logger skeleton | Louis | W3 | ✅ |
| Define JSON state schema (sample, beam, run, machine_state, `_timestamps`) | Louis + Fred | W3 | ✅ |
| Define integration / context-propagation table for all sub-skills | Louis | W3 | ✅ |
| Add to orchestrator command dispatch table + initialization flow | Louis | W3 | ✅ |

---

### analyze-data  *("analysis maker")*

**Status:** testing | **Owner:** Constance (sub-skill) + Louis (PRs)

LUTE wizard and parameter refinement. LUTE-LCLS environment is **ready**.
Currently in testing. Calibration is handled via `/take-run` run_type tags
(no `/calibrate` command). Three PRs in flight (Louis):

| PR | Status |
|---|---|
| SMD templating | next |
| `run_type` branching | next |
| Beamline summary task | next |

**Related LUTE (Constance, 2026-07-13):** https://github.com/slac-lcls/lute/pull/131
**merged** by Fred — unblocks CCTBX validation rerun (**T-64**).

**Consults:** `@ask-lute` (LUTE reference brain), `@ask-cctbx-xfel` (SFX
indexing/merging params), `@ask-smalldata` (SmallData detector params),
`@ask-slurm-s3df` (job submission + monitoring), `@lcls-catalog` (file finding,
XTC2 inventory), `@ask-lcls2` (psana2 data inspection + calibration check)

| Task | Owner | When | Status |
|---|---|---|---|
| Centralize LUTE install path decision (always virtual envs) | Louis | W2 | ✅ done |
| Update ask-lute references (virtual env install, lute_template_cfg) | Louis | W3 | ✅ done |
| Add `@ask-smalldata` delegation at Step 3.4 Tier 2 | Louis | W2 | open |
| Add `@ask-cctbx-xfel` delegation at Step 3.4 Tier 2 | Louis | W2–W3 | open |
| Reflect SMD templating PR in `setup.md` Phase 4 | Louis | W3 | open |
| Reflect `run_type` branching PR in `setup.md` | Louis | W3 | blocked — job termination bug (Gabriel) |
| Add beamline summary task to task catalog in `setup.md` | Louis | W3 | open |
| Design **`takepeds`/`makepeds`** invoke path (CDS terminal → pedestal deploy) | Fred + Louis | W3 (T-69) | open |
| Full MFX SFX walkthrough test (LCLS-II, ePix10k2M, CheetahRunner → CrystFEL / CCTBX) | Constance + Louis + Pam | W3 | open |
| **CCTBX validation:** rerun **`mfx101624926` run 36** with **Pam mask**; merging stats + CC1/2 by bin | **Constance** | ASAP (T-64) | open |
| LUTE design: **"samples"** not only **"runs"** (`lute.db` + eLog REST) | **Constance** | design (T-65) | open |

---

### ask-lute  *(sibling skill — reference brain)*

**Status:** reference-only (updated Jul 15) | **Owner:** Louis

Pure reference brain: LUTE task catalog, YAML syntax, hutch-specific knowledge.
The wizard was moved out into `analyze-data/commands/setup.md` and `refine.md`.
`analyze-data` owns the wizard and consults `@ask-lute` for LUTE internals.

**Updated Jul 15:** references updated to reflect latest LUTE repo changes —
virtual env install (`lute-lcls` pip into `lute_env_py39`/`lute_env_py311`),
`lute_template_cfg` requirement for `SubmitSMD`, `setup_lute` context.

**Consults:** none — pure reference, consulted by others, no sub-delegation

No further active development tasks for July 17 — kept up to date as LUTE evolves.

---

### ask-cctbx-xfel  *(sibling skill — SFX parameter expert)*

**Status:** not started | **Owner:** Louis + Pam (+ **Constance** available through Jul 17)

SFX parameter expert consulted by `analyze-data /setup` (Step 3.4 Tier 2) and
`/refine`. Covers CrystFEL vs. CCTBX decision logic, xgandalf flags, indexing
strategy, and MFX-specific SFX defaults. Pam to advise on missing pieces.

**Consults:** `@confluence-doc` (LCLS SFX documentation) — pure reference skill,
no bridge, no sub-delegation

| Task | Owner | When |
|---|---|---|
| SKILL.md first draft (MFX CrystFEL params, xgandalf flags, CCTBX path) | Louis + Pam (+ Constance) | W3 |
| Flesh out full MFX SFX parameter set | Louis + Pam | W3 |
| Validate: skill correctly answers "CrystFEL or CCTBX?" for MFX SFX | Louis + Pam | W3 / post if needed |

> **Note — hit/indexing rate vs. time/shot (CCTBX beamline summary):**
> May be implementable as **LUTE tasklets** wrapping CCTBX beamline summary
> output. Pam to advise on exposing existing CCTBX beamline summary tasks as
> tasklets surfaced in the eLog.

---

## Timeline

| Week | Dates | Status | Focus | Exit gate |
|---|---|---|---|---|
| **W1** | Jun 30 – Jul 4 | ✅ complete | Foundation: renames, architecture, and `lute` code | — |
| **W2** | Jul 7 – Jul 10 | ✅ complete (Fri) | Write 5 skills individually testable; bridge confirmed at MFX | Solo tests |
| **W3** | **Jul 13 – Jul 16** | **in progress** | Integrate & test on previous experiments; fix buffer | Dry run pass |
| **Jul 17** | Fri | **beamtime** | SFX experiment run end-to-end with agents | — |

*(Louis's Friday plan had W3 = Jul 14–16; Mon Jul 13 is included in the active
integration window after today's BAWG.)*

**W2 highlights (closed Fri Jul 10):**
- `run_type` branching tested with `/take-run` (soon to be done)
- **ask-happi** + **beamtime-logger** published to psagents GH (Fred)
- `are-we-ready`: James to integrate `ask-happi` — **due Wed Jul 15**

**W3 skills in scope:**

| Skill | Mode | Status |
|---|---|---|
| `are-we-ready` | bridge | ✅ **done (Jul 16)** — MFX script + sub-skill dir |
| `bridge-to-cds` | offline | ✅ **done (Jul 16)** — psdev SSH patterns |
| `coordinate-experiment` | offline | ✅ **done (Jul 16)** — state JSON + log + eLog |
| `align-spectrometer` | bridge | stub — blocked on Amine |
| `take-run` | bridge | draft — calibration tags + sample tagging with Fred **Jul 16** |
| `analyze-data` | offline | testing — install path ✅; run_type branching blocked (Gabriel) |

---

## Action Items

### ① Finish `analyze-data` PRs → reflect in skill files
**Owner:** Louis | **When:** W3 | **Status:** in progress

Three PRs in flight:
- **SMD templating** — merged!
- **`run_type` branching** — gates LUTE task selection on `run_type` (SFX / SAXS / XES /
  GEOM / DARK); update `setup.md` decision tree; add `run_type` as required Phase 1
  parameter. ⚠️ **Issue (Jul 15):** jobs not terminating properly — Gabriel investigating.
  `setup.md` update blocked until resolved.

**Done when:** `run_type` PR merged + `setup.md` + `refine.md` reflect new paths/flags.

---

### ② Finish `are-we-ready`
**Owner:** James | **When:** W3 — **by Wed Jul 15** | **Status:** ✅ **done (Jul 16)**

- Push / finish `are-we-ready.md` in `hutch-copilot`.

**Unblocked:** ask-happi on GH (2026-07-10). Still useful: Fred AWR bridge script.

**Done when:** skill runs via bridge at MFX and can tell if MFX is ready or not.

---

### ③ `/take-run`: sample tagging + `run_type` field
**Owner:** Fred / Louis | **When:** W3 | **Status:** in progress · **T-30**

Two tagging paths, both required:
- **hutch-python (pre-run):** pass `sample_name` and `run_type` via bridge before
  `daq.configure()`.
- **`@elog-copilot` (post-run):** structured JSON keyed to run number; schema with Murali.

**Done when:** a test run shows `sample_name` and `run_type` (including calibration
tags) in both the native run record and the elog JSON entry.

**Debugging** We will collect runs without specific skill delegation and prompt
OpenCode to build the skill after success.

---

### ④ Build `/coordinate-experiment` from `beamtime-logger`
**Owner:** Louis (+ Fred) | **When:** W3 | **Status:** ✅ **done (Jul 16)**

**beamtime-logger** published https://github.com/psagents/beamtime-logger (T-63).
`coordinate-experiment/SKILL.md` built from that skeleton. Skill lives as a
sub-skill directory with its own `SKILL.md`. JSON state schema, all context-
propagation integration points, and orchestrator dispatch table entry are complete.

**Done when:** ✅ first draft exists with JSON schema, integration points, and
dispatch table entry — all complete.

---

### ⑤ `ask-cctbx-xfel` SKILL.md first draft
**Owner:** Louis + Pam (+ Constance) | **When:** W3 | **Status:** not started

Scope: MFX CrystFEL flags; CrystFEL vs CCTBX decision logic; indexing strategy;
CCTBX phil params; Pam validates.

**Note (2026-07-13):** Constance is **available** through Jul 17 (not on holiday) —
can help; remote from France starts **Jul 21**.

**Done when:** skill answers "CrystFEL or CCTBX?" for a standard MFX SFX run.

---

### ⑥ DAQ pre-check in `/take-run` Phase 0
**Owner:** Fred / Louis | **When:** W2 | **Status:** ✅ done

`daq.status()` guard in `commands/take-run.md`. Hard stop if DAQ not connected /
in fault — never call `daq.configure()` / `daq.begin()` into a non-running DAQ.

---

### ⑦ Constance — CCTBX workflow validation (Jul 13 BAWG)
**Owner:** Constance | **When:** ASAP (before Jul 17) | **Status:** open · **T-64**

Rerun on **run 36** of **`mfx101624926`** with the **mask from Pam's prior manual
run**; check **merging stats**; ideally **CC1/2 per resolution bin** (not just
overall). Feeds CX-P1 goal 2 / **T-8**.

**Prerequisite done:** lute#131 merged
(https://github.com/slac-lcls/lute/pull/131).

---

### ⑧ Constance — LUTE samples design principle (Jul 13 BAWG)
**Owner:** Constance | **When:** design / ongoing | **Status:** open · **T-65**

Design principle for handling **"samples"** (not only **"runs"**), possibly via
**`lute.db`** + eLog **REST API**. Ties to `/take-run` sample tagging and
`/coordinate-experiment`.

---

### ⑨ Claire — metadata handling (Jul 13 BAWG)
**Owner:** Claire | **When:** this week | **Status:** open · **T-66**

Think through / crystallize the discussion between **Gabriel** and **Leland** on
**metadata handling**. Complements Murali / elog JSON track (**T-32**).

**Solution:** `/coordinate-experiment` can handle that for the time being?

---

### ⑩ AMI2 version check (Jul 13 Fred+Louis)
**Owner:** Fred + Louis | **When:** ASAP | **Status:** open · **T-67**

Confirm **AMI2** is the **latest from Seshu** (needed for `/align-spectrometer` AMI
graph / epix100 averaging PVs).

---

### ⑪ Louis → James on `/are-we-ready` (Jul 13)
**Owner:** Louis | **When:** before Wed Jul 15 | **Status:** ✅ done

Follow up with James on `/are-we-ready` progress (pairs with **T-21**).

**Done when:** PR is merged on `hutch-copilot` with James' changes.

---

### ⑫ `takepeds` / `makepeds` orchestration (Jul 13 Fred+Louis)
**Owner:** Fred + Louis | **When:** before Jul 17 | **Status:** open · **T-69**

These are **not** hutch-python functions — run on **CDS terminal** to take a dark
and process/deploy detector pedestal. Design how agents/orchestration invoke them
alongside `/take-run run_type:DARK` (the DARK tag triggers Maestro processing, but
`takepeds`/`makepeds` still need to run separately on the CDS terminal).

---

### ⑬ Bridge practice IRL (Tue Jul 14)
**Owner:** Fred + Louis | **When:** **2026-07-14** | **Status:** ✅ done · **T-70**

Practiced the bridge(s) in person.

---

### ⑭ Fred → Amine on `/align-spectrometer` (Jul 13)
**Owner:** Fred | **When:** ASAP | **Status:** open · **T-71**

Follow up with Amine on plan for `/align-spectrometer` (VH function signature +
timeline with Louis).

---

## July 17 MVP Scenario

`coordinate-experiment` is **always active** from the first prompt — it silently
reads and writes `{experiment}_state.json` after every interaction so no sub-skill
ever re-asks for already-known context.

DAQ generation: **LCLS-II / psana2 / `.xtc2`** confirmed.

---

### Annotated Walkthrough

```
────────────────────────────────────────────────────────────────────────────
SESSION START
────────────────────────────────────────────────────────────────────────────

User: "Let's start the experiment. We're at MFX, experiment mfxl1013621,
       lysozyme at 20 mM via 50 µm Rayleigh jet, 9500 eV."

  hutch-copilot loads → reads coordinate-experiment/SKILL.md
  coordinate-experiment: looks for mfxl1013621_state.json → not found
  → asks one compact prompt (hutch, experiment, sample, energy)
  → creates state JSON + log file at:
       /sdf/data/lcls/ds/mfx/mfxl1013621/results/psagents/mfxl1013621_state.json
       /sdf/data/lcls/ds/mfx/mfxl1013621/results/psagents/mfxl1013621_logs.md

  state.json after init:
  {
    "hutch": "MFX",  "experiment": "mfxl1013621",
    "shift_date": "2026-07-17",  "shift_start": "18:00",
    "sample_name": "lysozyme",  "concentration": "20 mM",
    "sample_delivery": "rayleigh jet",  "delivery_details": "50 µm nozzle",
    "photon_energy_eV": 9500,
    "last_run_number": null,  "last_run_tag": null,
    "machine_state": { "beam_present": null, "daq_status": null, "last_checked": null }
  }

  logs.md:
  ## Shift Start
  - **18:00** Experiment context initialized.
  - Sample: lysozyme, 20 mM, Rayleigh jet (50 µm)
  - Photon energy: 9500 eV

────────────────────────────────────────────────────────────────────────────
BEAM READINESS
────────────────────────────────────────────────────────────────────────────

1.  Bring in beam with Matt's GUI

User: "/are-we-ready" or "are we ready?"

  are-we-ready/SKILL.md loaded
  → reads hutch, experiment from state.json (no re-ask)
  → runs check_beam_ready_mfx.py via bridge (inline, no file copy)
  → all CRITICAL checks pass → reports beam-present summary to user

  coordinate-experiment silently updates:
  state.json:  machine_state.beam_present = true,
               machine_state.last_checked = "2026-07-17T18:20:00"
  logs.md:     (no entry — status read, not an operational event)

────────────────────────────────────────────────────────────────────────────
ALIGNMENT  [stub — Amine's routine pending]
────────────────────────────────────────────────────────────────────────────

2.  hutch-copilot: /align-spectrometer (VH auto-align)
  → reads hutch, experiment, current_phase from state.json (no re-ask)
  → runs optimization routine via bridge
  → user declares: "alignment looks good"

  coordinate-experiment silently:
  state.json:  current_phase = "alignment" → "calibration"
               machine_state.beam_present refreshed via PV
  logs.md:     "19:30 VH alignment complete — operator confirmed."

────────────────────────────────────────────────────────────────────────────
ANALYSIS SETUP  (offline, no bridge)
────────────────────────────────────────────────────────────────────────────

User: "@analyze-data /setup" or "set up the SFX analysis for this experiment"

  analyze-data/SKILL.md loaded → dispatches to commands/setup.md
  → reads hutch, experiment, photon_energy_eV, sample_name, sample_delivery
    from state.json — does NOT ask user for these
  → LUTE wizard runs: task selection, YAML config, SFX params
    (consults @ask-lute for task catalog, @ask-cctbx-xfel for indexing params,
     @ask-smalldata for SmallData detector params)
  → LUTE YAML written to /sdf/data/lcls/ds/mfx/mfxl1013621/results/lute_output/
  → One workflow one YAML for smd --> BayFAI
  → One workflow one YAML for smd --> XES

  coordinate-experiment: no state.json change (no condition change detected)

────────────────────────────────────────────────────────────────────────────
CALIBRATION — GEOMETRY RUN
────────────────────────────────────────────────────────────────────────────

User: "/take-run run_type:GEOM, calibrant is AgBh, 2 minute-run"

  commands/take-run.md loaded
  → reads sample_name, concentration, delivery, photon_energy_eV etc. from state.json
  → shows confirmation summary without re-asking
  → bridge: run `autorun()`
  → Phase 4: monitor XTC2 arrival; reports "Run 7 started"
  → Maestro fires BayFAI/GeomOpt DAG branch

  coordinate-experiment silently:
  state.json:  last_run_number = 7,  last_run_tag = "GEOM"
               machine_state.daq_status = "stopped"
               _timestamps.last_run_number = "2026-07-17T20:00:00"
  logs.md:     "**18:22** Run 7 (GEOM) — 120 s. XTC2 confirmed."

────────────────────────────────────────────────────────────────────────────
CALIBRATION — DARK RUN  (if needed)
────────────────────────────────────────────────────────────────────────────

User: "/take-run run_type:DARK"
  → ARP script /takepeds /makepeds
  state.json:  last_run_number = 8,  last_run_tag = "DARK"
  logs.md:     "**20:30** Run 8 (DARK) — pedestal run."

────────────────────────────────────────────────────────────────────────────
DATA COLLECTION
────────────────────────────────────────────────────────────────────────────

User: "/take-run for 5 minutes, placebo sample, run_type:DATA" 
  → all sample/beam context already in state.json → minimal confirmation prompt
  → bridge: run `autorun()`
  → Run 9 started; XTC2 landing confirmed;

  coordinate-experiment silently:
  state.json:  last_run_number = 9,  last_run_tag = "DATA"
  logs.md:     "**21:00** Run 9 (DATA) — 300 s, plcaebo 20 mM 50 µm."

────────────────────────────────────────────────────────────────────────────
CONDITION CHANGE
────────────────────────────────────────────────────────────────────────────

User: "we switched to FeNO6, 10 mM, same nozzle"
(XES - Vandana's sample)

  coordinate-experiment condition update flow:
  → parses: sample_name = FeNO6, concentration = 10 mM
  → updates state.json + _timestamps
  → appends to logs.md
  → posts to eLog via @elog-copilot:
       Tag: condition-update
       Title: Condition Update – 10:15
       [10:15] Sample changed to FeNO6 (10 mM, 50 µm Rayleigh jet).
               Previous: lysozyme.
  → confirms: "Updated: sample_name=FeNO6, concentration=10 mM. Posted to eLog ✓"

  state.json after change:
  {  ...  "sample_name": "FeNO6",  "concentration": "10 mM",
     "_timestamps": { "sample_name": "2026-07-22:00:00", ... }  }

  Next /take-run will read FeNO6 directly — no re-ask.

────────────────────────────────────────────────────────────────────────────
CONTEXT CHECK  (any time)
────────────────────────────────────────────────────────────────────────────

User: "/context" or "what are the current conditions?"

  coordinate-experiment renders state summary:
  ## Current Experiment Context
  Hutch: MFX          Experiment: mfxl1013621
  Date: 2026-07-17    Shift start: 18:00
  Phase: data_collection

  ### Sample
  - Name: FeNO6 / 10 mM / Rayleigh jet — 50 µm nozzle

  ### Beam
  - 9500 eV / 120 Hz

  ### Last Run
  - Run #9  Tag: DATA

  ### Machine State  [last checked: 18:30 ← STALE — offer /are-we-ready refresh]

────────────────────────────────────────────────────────────────────────────
END OF SHIFT
────────────────────────────────────────────────────────────────────────────

User: "/handoff" or "write the shift handoff"

  coordinate-experiment:
  → reads full mfxl1013621_logs.md + mfxl1013621_state.json
  → drafts Slack-format shift summary (beam conditions, issues, science summary)
  → user reviews + approves
  → appends to logs.md + posts to eLog (tag: shift-handoff) via @elog-copilot
```

──── screen record everything ──────────────────────────────────────────────

---

## Known Gaps  *(must resolve before July 17)*

| Gap | Risk | Owner |
|---|---|---|
| Amine's VH alignment function name/signature unknown | High — `/align-spectrometer` is a stub | Louis + Amine |
| What metadata fields can be written into a run (`runtype`?) | High — blocks `/take-run` calibration + sample tagging | Fred |
| `/take-run` calibration tags (GEOM, DARK) + sample tagging not yet implemented | High — blocks run_type Maestro branching for Jul 17 | Fred / Louis (Jul 16) |
| `run_type` branching job-termination bug | High — blocks Maestro GEOM/DARK branch; Gabriel investigating | Gabriel |
| CX-P1 CCTBX stats not yet validated on clean run with Pam mask | High — Jul 17 analysis demo | Constance (T-64) |
| **DoT operator TBD** for Jul 17 data collection | High — blocks stretch `/take-run` path | Leland → Ray (SED) |
| `/are-we-ready` output not yet validated against existing tools | Medium — could miss beam path gaps | Claire (+ James) |
| `takepeds`/`makepeds` not in agent path (CDS terminal, not hutch-python) | Medium — darks/pedestals for calibration | Fred + Louis (T-69) |
| AMI2 may not be latest from Seshu | Medium — align-spectrometer AMI setup | Fred + Louis (T-67) |
| `ask-cctbx-xfel` skill does not exist yet | Medium — `/setup` Step 3.4 Tier 2 falls back to user prompt | Louis + Pam (+ Constance) |
| Dry run must land early in W3 (Jul 13–16) | Medium — fix-or-ship before Fri | All |
| ~~`/coordinate-experiment` SKILL.md skeleton~~ | ~~Low~~ | ✅ done (Jul 16) |
| `/fixdaq` referenced in error handling but does not exist | Low — operators know the manual fix | Louis (post-Jul-17) |
| `/checkout` has no command file; referenced in `mfx.md` | Low — operators do this manually today | Louis (post-Jul-17) |

---

## Murali Item  *(separate track — not blocking July 17)*

If `/take-run` writes structured sample metadata as JSON messages into the elog
(via `@elog-copilot`), can Murali provide a dedicated elog tab for better
visualization and interaction of that metadata?

This is a natural downstream consumer of the `/take-run` elog-copilot tagging work.
No July 17 dependency, but the JSON schema should be agreed before Fred finalizes
the elog-copilot integration.

**Related (2026-07-13):** Claire **T-66** (Gabriel–Leland metadata discussion);
Constance **T-65** (LUTE samples via lute.db / eLog REST).

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
- Build `/checkout` command for pre-experiment motor & device verification
- Build `/fixdaq` command stub for DAQ recovery guidance
- Implement Constance's LUTE **samples** design (T-65)

---

## Team

| Person | Primary responsibility | Availability |
|---|---|---|
| **Louis** | hutch-copilot orchestrator, `/align-spectrometer` (+ Amine), `analyze-data` PRs, bridge guide, dry-run coordination, `/coordinate-experiment` | available |
| **James** | `/are-we-ready` (ask-happi) — **by Wed Jul 15** | available |
| **Fred** | `/take-run` (sample tagging, aggregation) + HAPPI/AWR bridge consulting | available |
| **Constance** | `analyze-data` ownership, CCTBX validation (**T-64**), LUTE samples design (**T-65**), `ask-cctbx-xfel` help | **available through Jul 17**; remote from France **Jul 21** |
| **Claire** | `/are-we-ready` validation vs tools; metadata handling (**T-66**) | **available through Jul 17**; remote from France **Jul 21** |
| **Pam** | SFX expertise — `ask-cctbx-xfel`, Pam mask for CCTBX rerun | consulting |
| **Amine** | `/align-spectrometer` VH function in hutch-python | consulting |
| **Murali** | Elog metadata visualization tab (separate track) | consulting |

> **Correction vs Louis Fri plan:** Constance & Claire are **not** on official holiday
> this week — still tracked. Remote starts Jul 21 (after the demo).