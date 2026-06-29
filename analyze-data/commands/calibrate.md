# /calibrate — Detector Calibration

Triggered by: `/calibrate`, "dark runs", "calibrate detector", "run darks",
"bayFAI", "geometry calibration", "calibrant run", "AgBh", "LaB6".

This command guides the user through the two calibration prerequisites for SFX
and scattering experiments:

1. **Dark calibration** — pedestal subtraction constants for the detector
2. **Geometry calibration** — detector distance, beam center, and tilt via BayFAI
   (for scattering) or a `.geom` file (for SFX / CrystFEL)

Both must be complete before running `/setup`.

---

## Phase 1: Identify What Is Needed

Ask only if not already in state:
- Experiment type (SFX → `.geom`; scattering → `.poni`)
- Detector alias (from session state or ask once)

Then check what already exists:

```bash
# Dark / pedestal calibration (psana2 path)
ls /sdf/data/lcls/ds/{hutch}/{experiment}/calib/

# BayFAI geometry (.poni file)
find /sdf/data/lcls/ds/{hutch}/{experiment}/results/ -name "*.poni" 2>/dev/null

# CrystFEL geometry (.geom file)
find /sdf/data/lcls/ds/{hutch}/{experiment}/results/ -name "*.geom" 2>/dev/null
```

Report what is present vs. missing. If both exist and look recent (same experiment),
offer to skip to `/setup`.

---

## Phase 2: Dark Calibration

Dark runs collect detector frames with no X-ray beam to measure the pedestal
(electronic offset) per pixel.

### Taking dark runs

Dark runs are taken via the DAQ — refer back to `hutch-copilot /take-run` with:
- Beam blocked (stopper in or attenuator at T=0)
- Typically 100–1000 events
- Label: `dark_{detector_alias}_run{N}`

The user takes dark runs manually or via hutch-copilot; this command only guides
the calibration processing step.

### Processing darks with psana2

```python
# Confirm pedestals are registered in the calibration database
from psana import DataSource
ds = DataSource(exp='{experiment}', run={dark_run})
det = ds.runs().__next__().Detector('{detector_alias}')
print(det.calibconst.keys())
# Should include 'pedestals'
```

If pedestals are missing, use `@ask-lcls2` for psana2 calibration procedure.

---

## Phase 3: Geometry Calibration

### SFX / CrystFEL — `.geom` file

CrystFEL requires a `.geom` detector geometry file. For MFX, a starting geometry
is typically provided by the instrument scientist. Refinement can be done with
CrystFEL's `geoptimiser` after an initial indexing pass.

Check for an existing geometry:
```bash
find /sdf/data/lcls/ds/{hutch}/{experiment}/results/ -name "*.geom"
# Also check shared geometry pool:
ls /cds/group/psdm/detector/geometry/{detector_type}/
```

If no geometry exists, ask the user:
- "Does the instrument scientist have a starting `.geom` for this detector configuration?"
- "What is the approximate detector distance (mm) and beam center (pixels)?"

Document the geometry path — it becomes `geom_file` in the CrystFEL LUTE task
(`RunCheetah` or `IndexCrystFEL`). Pass it to `@ask-lute` during `/setup`.

### Scattering — `.poni` file via BayFAI

For SAXS/WAXS/XSS experiments, use LUTE's BayFAI geometry optimizer with a
calibrant run (AgBh or LaB₆ powder).

**Calibrant run requirements:**
- A dedicated run with AgBh or LaB₆ powder at the same detector position as the
  experiment
- Sufficient statistics: typically 100–500 events

**LUTE BayFAI workflow** — invoke via `@ask-lute`:
```
@ask-lute — set up a geometry calibration workflow for experiment {experiment},
detector {alias}, using BayFAI with AgBh calibrant, run {calibrant_run}.
```

`@ask-lute` will configure the `BayFAIOptimizer2` task (LCLS-II) or
`BayFAIOptimizer` (LCLS-I) and register it as a `MANUAL` trigger workflow.

The output `.poni` file path becomes `poni_file` in `getAzIntPyFAIParams` during
`/setup`. Read `references/sfx-analysis-defaults.md` for typical BayFAI parameters
at MFX.

---

## Phase 4: Summary

Report calibration status before handing off to `/setup`:

```
Calibration status for {experiment}
────────────────────────────────────
Dark / pedestals : {found at path / MISSING}
Geometry         : {.poni at path / .geom at path / MISSING}

{Ready to run /setup. OR: Complete missing items above first.}
```
