# hutch-copilot — Development Plan

**Goal:** Run a complete SFX experiment at MFX on **July 17, 2026** using agents
end-to-end, from beam readiness through data collection and processing.

---

## Architecture

```
hutch-copilot               ← orchestrator (this repo)
├── /are-we-ready           ← HAPPI/lightpath beam readiness check  [bridge]
├── /align-beam             ← beam alignment via Amine's routine    [bridge]
├── /take-run               ← DAQ run control + file verification   [bridge]
└── analyze-data/           ← data analysis sub-skill (offline, no bridge)
      ├── /setup    ────────→ ask-lute        ← LUTE reference + YAML wizard (sibling)
      │                           └──────────→ ask-cctbx-xfel  (sibling, SFX params)
      │                           └──────────→ ask-smalldata   (sibling, SmallData params)
      ├── /calibrate          dark runs + BayFAI geometry
      ├── /refine   ────────→ ask-lute /lute-refine
      └── /monitor-jobs ────→ ask-slurm-s3df + lcls-catalog
```

**Skill placement rules:**
- **Commands** (`hutch-copilot/commands/`): single workflow, bridge-coupled, no domain
  references needed (`/are-we-ready`, `/align-beam`, `/take-run`).
- **Sub-skills** (`hutch-copilot/analyze-data/`): multiple sub-commands, own reference
  files, different operational mode (offline), different owner.
- **Sibling skills** (top-level peers): pure knowledge experts independently useful
  beyond hutch-copilot — `ask-lute`, `ask-cctbx-xfel`, `ask-smalldata`, etc.

All live commands run through the hutch-python bridge (`nc localhost 9999`).
Sibling skills and `analyze-data` work in planning mode from any terminal.

---

## Skill Inventory

| Skill | Status | Owner | Notes |
|---|---|---|---|
| `hutch-copilot` orchestrator | draft | Louis | Renamed from beam-opr; trimmed to 3 live commands |
| `/are-we-ready` | draft | Claire | HAPPI/lightpath via bridge; Fred's MFX bridge needed |
| `/align-beam` | stub | Claire | Amine's routine exists in hutch-python; needs function name |
| `/take-run` | draft | Louis | DAQ control + XTC2 file check; needs live test |
| `analyze-data` sub-skill | draft | Constance | Subordinate to hutch-copilot; commands + SFX defaults written |
| `ask-lute` | near-complete | Louis | Environment discussion in progress; smalldata delegation TBD |
| `ask-cctbx-xfel` | not started | Constance | Parameter expert for SFX; delegated from analyze-data /setup |

---

## Roadmap

### Week 1 — June 30 – July 4: Foundation

| Task | Owner | Done when |
|---|---|---|
| Resolve LUTE environment discussion (central install path, new commands) | Louis | Decision made; `lute-setup.md` paths confirmed |
| `ask-lute`: add `ask-smalldata` delegation at Tier 2 for SubmitSMD categories | Louis | Step 3.4 updated; test conversation passes |
| `ask-lute`: add `ask-cctbx-xfel` delegation at Tier 2 for SFX indexing params | Louis + Constance | Step 3.4 updated; stub skill exists |
| `ask-cctbx-xfel`: SKILL.md + first draft (MFX CrystFEL params, xgandalf flags, CCTBX path) | Constance | Skill answers "CrystFEL or CCTBX?" and key params correctly |
| `are-we-ready`: get Fred's MFX HAPPI bridge, adapt `are-we-ready.md` | Claire + Fred | Bridge mechanism documented; command updated |
| Bridge setup guide: SSH tunnel steps, verification command | Louis | `references/bridge-setup.md` written |

**Exit gate:** `ask-lute` + `ask-cctbx-xfel` testable end-to-end in a simulated
MFX SFX conversation.

---

### Week 2 — July 7 – July 11: New Skills + Integration

| Task | Owner | Done when |
|---|---|---|
| `/align-beam`: get function name/signature from Amine; complete `align-beam.md` | Claire | Command drives Amine's routine via bridge |
| `ask-cctbx-xfel`: flesh out (CrystFEL flag reference, CCTBX phil params, when-to-use logic) | Constance | Handles full MFX SFX parameter set |
| `/take-run` live test on a past MFX `.xtc2` experiment | Louis + Claire | File-check and DAQ polling confirmed working |
| `/are-we-ready` live test via Fred's HAPPI bridge at MFX | Claire | MFX beam path report generated correctly |
| Calibration workflow: dark runs + BayFAI geometry — document procedure | Constance | `commands/calibrate.md` stub or Phase 0 block in `ask-lute` |
| `ask-lute` MFX SFX full walkthrough test (LCLS-II, ePix10k2M, CheetahRunner → CrystFEL) | Louis + Constance | Complete YAML generated and validated |

**Exit gate:** All 5 skills individually testable; bridge confirmed working at MFX.

---

### Week 3 — July 14 – July 17: Integration, Dry Run, Beamtime

| Task | Owner | Done when |
|---|---|---|
| Full dry run (July 14) using a past MFX `.xtc2` run — full scenario end-to-end | All | Issues list produced |
| Fix issues from dry run (July 14–15) | All | All blockers resolved |
| Operator runcard: one-page checklist for July 17 (bridge setup, command sequence, fallbacks) | Louis | `references/runcard-sfx.md` written |
| **July 17: Beamtime** | All | SFX experiment run with agents |

---

## July 17 Scenario — Full Command Sequence

```
1. Open hutch-python bridge (SSH tunnel → nc localhost 9999)
2. hutch-copilot: /are-we-ready mfx
3. hutch-copilot: /align-beam
4. hutch-copilot: /analyze-data /calibrate   (darks + .geom check)
5. hutch-copilot: /analyze-data /setup  →  ask-lute SFX wizard
                                              → ask-cctbx-xfel (indexing params)
6. hutch-copilot: /take-run 3min  sample: <protein>, jet: 50µm
7. hutch-copilot: /take-run ...   (repeat per sample condition)
8. hutch-copilot: /analyze-data /monitor-jobs   (track pipeline progress)
```

DAQ generation: **LCLS-II / psana2 / `.xtc2`** confirmed.

---

## Known Gaps (must resolve before July 17)

| Gap | Risk | Owner |
|---|---|---|
| LUTE environment discussion not resolved | High — blocks all ask-lute path changes | Louis |
| Fred's HAPPI bridge not yet integrated | Medium — `/are-we-ready` falls back to docs mode | Claire |
| Amine's function name/signature unknown | Medium — `/align-beam` is a stub | Claire |
| Calibration workflow drafted but untested | Medium — SFX indexing fails without correct geometry | Constance |
| No dry-run test scheduled on a real MFX experiment | High — first real test would be July 17 | All |
| `ask-cctbx-xfel` skill does not exist yet | Medium — ask-lute SFX Tier 2 falls back to user prompt | Constance |

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

---

## Team

| Person | Primary responsibility |
|---|---|
| **Louis** | hutch-copilot orchestrator, ask-lute refinements, /take-run, bridge guide, dry run coordination |
| **Claire** | /are-we-ready (Fred's bridge), /align-beam (Amine's routine), live testing |
| **Constance** | ask-cctbx-xfel skill, analyze-data /calibrate validation, SFX LUTE YAML validation |
| **Fred** | HAPPI bridge for /are-we-ready at MFX (consulting) |
| **Amine** | align-beam function in hutch-python (consulting) |
