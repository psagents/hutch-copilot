# Dark / Pedestal Run — Reference

This file documents the two-step pedestal collection and deployment workflow
used for DARK runs at MFX (and other LCLS-II hutches).

---

## Overview

A pedestal / dark run is a **two-step process**:

1. **`takepeds()`** — records the DARK data (runs inside the hutch-python session)
2. **`makepeds`** — processes the data and deploys pedestal constants to the calibration store (runs via SSH on a psana/psffb node)

These are distinct operations. `takepeds` is a DAQ-side command; `makepeds` is an
analysis-side command. The agent must run both.

---

## Step 1 — `takepeds()` (DAQ side, hutch-python bridge)

### What it does

`takepeds()` is a hutch-python wrapper that calls:
```bash
epixquad_pedestal_scan --record 1 --hutch {hutch}
```

It steps the detector through all gain modes (typically 5 steps for Jungfrau-class
detectors, collecting ~300 events per step) and records one XTC2 file per step.
Unlike `autorun()`, it does **not** take a fixed duration — it is fully self-timed
by the gain-mode scan.

### Call from bridge

```python
takepeds()
```

No arguments needed for standard pedestal collection.

After `takepeds()` returns, get the run number:
```python
run_number = get_run()
```

### Detector-specific notes

| Detector | Steps | Events/step | Notes |
|---|---|---|---|
| Jungfrau 4M / 16M | ~5 | ~300 | "takepeds seems to work" (confirmed MFX Jul 2026) |
| ePix10k2M | 5 gain ranges | ~300 | 7 pedestals total (3 fixed + 2 auto gain modes) |
| ePix100a | 1 | ~300 | Single gain range |

### Known issues

- On some systems, `takepeds` sources an analysis conda env instead of the DAQ env.
  If it fails with an import error, ask the operator to run it directly from the
  DAQ terminal: `epixquad_pedestal_scan --record 1 --hutch mfx`

---

## Step 2 — `makepeds` (analysis side, via SSH to psdev)

### What it does

`makepeds` processes the dark/pedestal XTC data from Step 1 and deploys the
resulting pedestal constants to the LCLS calibration store at:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/calib/.../pedestals/{run_range}.data
```

### Command

```bash
ssh -o ConnectTimeout=30 psdev "makepeds -e {experiment} -r {run_number} -u {operator_account}"
```

Where `{operator_account}` is typically `{hutch}opr` (e.g., `mfxopr`).

### Full example (MFX)

```bash
ssh -o ConnectTimeout=30 psdev "makepeds -e mfxltest01 -r 43 -u mfxopr"
```

### Prerequisites

- Valid Kerberos ticket on psdev (usually handled by the operator's active session)
- The XTC2 files must already be present on S3DF (verified in Phase 4)
- Caller must be a member of the experiment group or hutch-scientist group

---

## Fallback — Direct Jungfrau Tools (when `makepeds` fails)

`makepeds` has known issues at MFX for Jungfrau detectors (as of April 2025).
If `makepeds` returns a non-zero exit code or reports an error, provide these
fallback commands to the user:

```bash
# Step A: Process the dark data
ssh psdev "source /reg/g/pcds/pyps/conda/py39env.sh && \
  jungfrau_dark_proc -k exp={experiment},run={run_number} -d jungfrau -o /tmp/peds_{run_number}"

# Step B: Deploy to calibration store
ssh psdev "source /reg/g/pcds/pyps/conda/py39env.sh && \
  jungfrau_deploy_constants -k exp={experiment},run={run_number} -d jungfrau \
  -o /tmp/peds_{run_number} -D"
```

Confirm deployment by checking for a new file in the calib store:
```bash
ls /sdf/data/lcls/ds/{hutch}/{experiment}/calib/
```

---

## Fallback — Direct epix10ka Tools

```bash
# Calculate pedestals
ssh psdev "source /reg/g/pcds/pyps/conda/py39env.sh && \
  epix10ka_pedestals_calibration -k exp={experiment},run={run_number} -d epix10ka"

# Deploy
ssh psdev "source /reg/g/pcds/pyps/conda/py39env.sh && \
  epix10ka_deploy_constants -k exp={experiment},run={run_number} -d epix10ka -D"
```

---

## LUTE and Dark Runs

After pedestals are deployed, LUTE may also process the DARK run via its own
workflow (triggered by the eLog ARP based on `run_type='DARK'`). This is
complementary to `makepeds` — LUTE handles any additional dark processing
defined in the experiment's LUTE YAML.

Monitor LUTE jobs via Phase 5 of `take-run/SKILL.md` (unified monitoring, no
special casing for DARK).

---

## Source References

- `takepeds` wrapper: `psdaq/psdaq/app/epixhr_pedestal_scan.py`
- `makepeds` script: `/reg/g/pcds/engineering_tools/R1.2.9/scripts/makepeds`
- Confluence: "Example Detector Installation — Jungfrau 4m" (page 591662237)
- Confluence: "EPIX10KA" (page 232083742) — gain-range handling and pedestal deployment
- Confluence: "MFX" (page 419728769) — Jungfrau-specific makepeds notes
