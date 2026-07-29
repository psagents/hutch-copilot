# hutch-copilot — Development Plan

**Last updated:** 2026-07-29
**Source:** Jul 17 beamtime retro · 2026-07-24 BAWG
**Repository:** https://github.com/psagents/hutch-copilot

---

## Top Priority: Fake DAQ Setup

> **Goal:** Define how to set up and use a simulated / fake DAQ for hutch-copilot testing.
> fake beamtime walkthroughs can be run end-to-end.

Key questions to answer:
- What does a fake DAQ look like? (test stand, XTC2 replay, mock `daq` object in hutch-python, other?)
- Who owns / sets it up?
- Which hutch / experiment does it live under?
- How do the skills connect to it (bridge path, device names)?
- What is the minimum viable fake DAQ for testing `/take-run` and `/are-we-ready`?

---

## Fake Experiment Ground Truth

> **Goal:** Define three ground-truth experiments with beamline and instrument scientists.
> Each template lists all resource categories needed to run a complete fake beamtime
> end-to-end using hutch-copilot. Fill in with colleagues.
> **Target:** 2026-07-31 meeting.

---

### SFX — Serial Femtosecond Crystallography

#### Beamline Setup
- **Detector(s):**
    - Jungfrau
- **Sample delivery:**
- **Photon energy (eV):**
- **Repetition rate (Hz):**
- **Spectrometer / optics:** n/a

#### Controls & Software
- **hutch-python session / test stand:**
- **Happi DB entries required:**
- **AMI graph + key PVs + averaging:**
- **Beam status PVs (are-we-ready):**

#### Calibration & Reference Files
- **Calibration run types needed:**
- **Calibration standard:**
- **Geometry file (source / path):**
- **Mask file (source / path):**
- **Dark / pedestal deployment path:**

#### Analysis
- **LUTE task DAG:**
- **SmallData parameters:**
- **Slurm resources (nodes / walltime / partition):**
- **YAML config — first draft owner:**

#### Fake Beamtime Specifics
- **Replay dataset (experiment + run numbers):**
- **Simulated DAQ approach:**
- **Simulated beam status approach:**

#### People & Validation
- **Operator:**
- **Beamline / instrument scientist:**
- **Analysis validator:**
- **Success criteria:**

---

### SAXS — Small Angle X-ray Scattering

#### Beamline Setup
- **Detector(s):**
- **Sample delivery:**
- **Photon energy (eV):**
- **Repetition rate (Hz):**
- **Spectrometer / optics:** n/a

#### Controls & Software
- **hutch-python session / test stand:**
- **Happi DB entries required:**
- **AMI graph + key PVs + averaging:**
- **Beam status PVs (are-we-ready):**

#### Calibration & Reference Files
- **Calibration run types needed:**
- **Calibration standard:**
- **Geometry file (source / path):**
- **Mask file (source / path):**
- **Dark / pedestal deployment path:**

#### Analysis
- **LUTE task DAG:**
- **SmallData parameters:**
- **Slurm resources (nodes / walltime / partition):**
- **YAML config — first draft owner:**

#### Fake Beamtime Specifics
- **Replay dataset (experiment + run numbers):**
- **Simulated DAQ approach:**
- **Simulated beam status approach:**

#### People & Validation
- **Operator:**
- **Beamline / instrument scientist:**
- **Analysis validator:**
- **Success criteria:**

---

### XES — X-ray Emission Spectroscopy

#### Beamline Setup
- **Detector(s):**
- **Sample delivery:**
- **Photon energy (eV):**
- **Repetition rate (Hz):**
- **Spectrometer / optics:**

#### Controls & Software
- **hutch-python session / test stand:**
- **Happi DB entries required:**
- **AMI graph + key PVs + averaging:**
- **Beam status PVs (are-we-ready):**

#### Calibration & Reference Files
- **Calibration run types needed:**
- **Calibration standard:**
- **Geometry file (source / path):**
- **Mask file (source / path):**
- **Dark / pedestal deployment path:**

#### Analysis
- **LUTE task DAG:**
- **SmallData parameters:**
- **Slurm resources (nodes / walltime / partition):**
- **YAML config — first draft owner:**

#### Fake Beamtime Specifics
- **Replay dataset (experiment + run numbers):**
- **Simulated DAQ approach:**
- **Simulated beam status approach:**

#### People & Validation
- **Operator:**
- **Beamline / instrument scientist:**
- **Analysis validator:**
- **Success criteria:**

---

## Per-Skill Status

### bridge-to-cds

**Working**
- Connection script ran; bridge to hutch-python session established

**Needs refactor**
- Nothing structural identified yet

**Failed / did not work**
- Louis could not connect — not in `mfx-users` Unix group (T-109)

**Could be improved**
- Auto-check group membership before attempting connection; surface a clear actionable error
- Document exactly which groups are required per hutch

**Action items**
- T-109: Wilco → add Louis to `mfx-users`; re-test bridge once added

---

### are-we-ready

**Working**
- Jul 17 success case passed end-to-end

**Needs refactor**
- Upstream object list is hardcoded — not configurable by instrument scientist

**Failed / did not work**
- Items outside the predefined upstream list were not checked

**Could be improved**
- Configurable checklist (instrument-scientist defined, not hardcoded)
- James was working on some code right? 

**Action items**
- T-54: configurable checklist; follow up on all items that did not work beyond success case

---

### align-spectrometer

**Working**
- Amine's underlying scripts (`find_signal`, `align_yaw`) ran

**Needs refactor**
- Skill was never invoked as a skill during beamtime — completely untested in agent context

**Failed / did not work**
- `find_signal` and `align_yaw` scan too quickly relative to AMI moving average — cannot pick up signal
- AMI PVs not updating on GUI despite changes taking effect (Seshu's latest AMI changes introduced a display lag)
- Needed to bump averaging by several shots before any signal was visible — not in the skill workflow

**Could be improved**
- Add configurable sleep between scan steps
- Set AMI averaging explicitly (via PV) as a mandatory step before running any routine
- Add AMI GUI verification step — confirm values are live before proceeding
- Resolve epix100 → PV / controls wiring (T-117, Fred → Patrick Opperman)

**Action items**
- T-117: Fred → Patrick Opperman (epix100 → PV / controls)
- Add `sleep` parameter to `find_signal` / `align_yaw` invocation pattern in skill
- Document AMI averaging bump as an explicit required step before alignment

---

### analyze-data

**Working**
- LUTE jobs submitted and ran successfully
- Setup phase executed

**Needs refactor**
- SKILL.md is too large — needs significant cleanup and decomposition
- Required parameters are not clearly surfaced for non-expert users

**Failed / did not work**
- Louis did not know what parameters to enter → jobs broke and remained broken after he left
- No parameter guidance or defaults documented for non-SFX techniques

**Could be improved**
- Explicit required-parameters checklist before setup runs, with per-technique defaults
- Iterate with Pam / Leland on a full fake experiment walkthrough
- Move reference detail out of main SKILL.md into sub-files

**Action items**
- T-87 / T-88: SKILL.md cleanup and refactor
- Define per-technique parameter defaults (SFX / SAXS / XES) — captured in fake experiment templates above
- T-116: include analyze-data `/setup` in fake beamtime walkthrough with Pam / Leland

---

### coordinate-experiment

**Working**
- State JSON initialized correctly
- Context propagation to sub-skills functional

**Needs refactor**
- Saves too infrequently; not enough fields tracked across a shift

**Failed / did not work**
- Beamtime log nearly empty — partly because only one run was collected, partly because automatic log entries are not verbose enough

**Could be improved**
- More verbose automatic log entries (more event types trigger a log append)
- Save more fields more often to state JSON
- Feedback loop with Leland / Pam on what the state schema should track

**Action items**
- T-113: iterate with Leland / Pam on state schema completeness and log verbosity
- Increase number of event types that trigger an automatic log entry

---

### take-run

**Working**
- Not tested

**Needs refactor**
- Untested — cannot assess

**Failed / did not work**
- Not tested during beamtime
- XES and SFX workflows EXITED during DoT warm-up on `mfx101609126` (T-118)

**Could be improved**
- Simulated or offline DAQ path for testing without live beamtime
- XTC2 replay approach so the full run flow can be validated offline
- Identify a test stand or recorded dataset (captured in fake experiment templates above)

**Action items**
- T-116: fake beamtime walkthrough must include `/take-run`
- T-118: follow up on EXITED XES / SFX workflows (`mfx101609126`)
- Identify simulated DAQ approach — coordinate with fake experiment template owners

---

## Priorities

### P1 — 2026-07-31 Meeting

- Document fake DAQ setup
- Fill fake experiment templates (SFX / SAXS / XES) with beamline and instrument scientists — bring this document to the meeting
- T-116: schedule fake / practice beamtime — full hutch-copilot walkthrough end-to-end
- T-109: Wilco → add Louis to `mfx-users`; re-test bridge connection

### P2 — Short term

- **align-spectrometer:** add sleep between scans; AMI averaging bump as explicit step; T-117 (Fred → Patrick, epix100 → PV)
- **analyze-data:** SKILL.md cleanup (T-87 / T-88); document required parameters per technique using fake experiment templates
- **are-we-ready:** configurable checklist (T-54); validate coverage beyond predefined list
- **coordinate-experiment:** more verbose log entries; more frequent state saves (T-113)

### P3 — Post fake-beamtime

- **take-run:** simulated DAQ path; investigate T-118 EXITED workflows
- General SKILL.md housekeeping (T-87, T-88 — lower priority items)
- **coordinate-experiment:** full schema iteration with Leland / Pam based on fake beamtime experience
