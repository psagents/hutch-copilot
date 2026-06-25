# /experiment_coordinator — Experiment Session State

Triggered by: `/experiment_coordinator`, "new sample", "sample changed", "update
experiment", "show state", "what's the current sample", "we switched to".

The coordinator is the memory of the session. It tracks the current experimental
conditions so that `/takerun`, `/checkout`, and other commands can operate without
asking the same questions repeatedly. It also logs every condition change to the eLog
so the session can be reconstructed if the AI context is lost.

---

## State Schema

```json
{
  "hutch": null,
  "experiment": null,
  "sample_name": null,
  "concentration": null,
  "sample_form": null,
  "photon_energy_eV": null,
  "pump_laser": false,
  "pump_laser_details": null,
  "detector_distance_mm": null,
  "last_run": null,
  "run_label": null,
  "notes": ""
}
```

**Field descriptions:**

| Field | Example | Notes |
|---|---|---|
| `hutch` | `"mfx"` | Lowercase 3-char hutch code |
| `experiment` | `"mfxl1013621"` | Full experiment name |
| `sample_name` | `"FeNO6"` | Short chemical/sample name |
| `concentration` | `"10mM"` | Concentration with units |
| `sample_form` | `"50µm Rayleigh jet"` | Delivery form: jet, tape, fixed target, etc. |
| `photon_energy_eV` | `7114.0` | Beam energy in eV; used by `/smd-config` |
| `pump_laser` | `true` | Whether pump laser is in use |
| `pump_laser_details` | `"400nm, 200µJ, 1ps"` | Free text laser description |
| `detector_distance_mm` | `100.0` | Sample-to-detector distance; used by `/smd-config` |
| `last_run` | `47` | Most recent run number |
| `run_label` | `"FeNO6_10mM_jet"` | Default label for next run |
| `notes` | `"noisy beam today"` | Free text notes |

---

## Sub-commands

### `/experiment_coordinator <update>`

Parse the user's message and extract new field values. Show what changed and ask
for confirmation before updating:

```
State update:
  sample_name      : "FeNO3" → "FeNO6"
  concentration    : "5mM"   → "10mM"
  sample_form      : (unchanged) "50µm Rayleigh jet"

Confirm? (yes / adjust)
```

After confirmation, apply the update and post to eLog.

### `/experiment_coordinator show`

Print the current state in a readable format:

```
Current experiment state
──────────────────────────────────────────
Hutch        : MFX
Experiment   : mfxl1013621
Sample       : FeNO6, 10mM, 50µm Rayleigh jet
Photon energy: 7114.0 eV
Pump laser   : Yes — 400nm, 200µJ, 1ps
Detector dist: 100 mm
Last run     : 47
Notes        : (none)
──────────────────────────────────────────
```

### `/experiment_coordinator reset`

Clear all fields and start fresh. Confirm before clearing.

---

## eLog Logging

Every confirmed state update must be posted to the eLog so the session can be
recovered after a context loss. Use `@elog-copilot` to post.

**Log format:**

```
[beam-opr] Experiment state update — {timestamp}

Sample:        {sample_name}, {concentration}, {sample_form}
Photon energy: {photon_energy_eV} eV
Pump laser:    {pump_laser} — {pump_laser_details}
Notes:         {notes}

(Auto-logged by beam-opr /experiment_coordinator)
```

Post tag: `beam-opr` or `operator-log` (use whatever tag convention is active for
the experiment; ask once and remember).

---

## Context Recovery

If a user says "restore context" or "what was our last sample", check the eLog for
the most recent `beam-opr` log entry via `@elog-copilot` and reconstruct the state
from it. Show what was recovered and ask the user to confirm or correct it.

---

## Downstream Consumers

When state is populated, other commands can operate silently:

- `/takerun` — pulls `sample_name`, `concentration`, `sample_form` for run labels
- `/checkout` — uses `hutch` + `pump_laser` to determine which PVs to check
- `/awr` — uses `hutch` for HAPPI beampath query
- `/smd-config` → `@ask-lute` — uses `photon_energy_eV`, `detector_distance_mm`

Always check state before asking the user for values already known.
