# /checkout — Instrument Checkout

Triggered by: `/checkout`, "instrument checkout", "check motors", "check the
instrument", "are the motors in position", "pre-experiment check".

This command performs a read-only audit of the beamline instrument and generates a
formatted report. It never moves anything — all queries are read-only.

---

## Phase 1: Identify Hutch and Experiment Type

Pull from session state. If not in state, ask once:

1. **Hutch** — e.g., "mfx", "tmo", "rix"
2. **Experiment type** — generic or specialised:
   - `generic` — standard checkout (motors, slits, attenuators, diagnostics)
   - `laser` — generic + pump laser PV family (`:LAS:` prefix)
   - `sfx` — generic + crystal delivery, sample stages
   - `waxs/saxs` — generic + detector position, beamstop stage

The user can specify inline: "/checkout MFX for an ultrafast laser experiment"

---

## Phase 2: Load Device List

Read `references/hutches/{hutch}.md` for the hutch-specific device inventory.

If the hutch file does not exist (not yet documented), use the generic device
categories below and note in the report that hutch-specific verification is needed.

**Generic device categories** (all hutches):

| Category | What to check |
|---|---|
| Beam-defining slits | Position (H and V gap, center) |
| Attenuators | Transmission setpoint vs readback |
| Beam position monitors (BPM) | Readback values |
| Sample area motors | X, Y, Z positions |
| Detector motors | Distance, angle |
| Shutters | Open/closed state |
| Sample delivery | Jet pressure, flow, or position (if applicable) |

**Laser additions** (when `experiment_type == "laser"`):

Query the `:LAS:` or `MFX:LAS:` PV family (see hutch reference for exact PV prefixes):
- Laser shutter state
- Delay stage position
- Pulse picker status
- Power meter readback (if available)

---

## Phase 3: Execute Queries

Run all queries through the bridge. These are **read-only** — no confirmation needed.

For each device, send a read command and record the result:

```python
# Position query
{motor}.position

# State query (shutters, inserters)
{device}.inserted
{device}.removed

# PV read (for raw EPICS devices)
caget {pv_name}
```

Process each query individually. If a device times out or returns an error, mark it
`ERROR` in the report — do not halt the entire checkout.

---

## Phase 4: Generate Report

Assemble a structured markdown report. Use ✓ / ⚠ / ✗ status indicators.

**Status rules:**
- ✓ — Value is within nominal range (from hutch reference) or device is in expected state
- ⚠ — Value is present but outside nominal range, or nominal not defined (needs human review)
- ✗ — Query failed, device unreachable, or device is in an unexpected state

```markdown
# MFX Instrument Checkout
Date: 2026-06-25 14:32 PST
Experiment: mfxl1013621
Type: Ultrafast laser

## Beam Path
| Device          | Value             | Status |
|-----------------|-------------------|--------|
| dg1_h_slit      | gap=0.5mm, x=0.0  | ✓      |
| dg1_v_slit      | gap=0.5mm, y=0.0  | ✓      |
| attenuator_1    | T=100%            | ✓      |
| mfx_bpm         | x=-0.1, y=0.2     | ✓      |

## Sample Area
| Device          | Value             | Status |
|-----------------|-------------------|--------|
| sample_x        | 0.15 mm           | ✓      |
| sample_y        | -2.35 mm          | ✓      |
| sample_z        | 0.00 mm           | ✓      |

## Laser (Ultrafast Experiment)
| PV / Device     | Value             | Status |
|-----------------|-------------------|--------|
| MFX:LAS:SHUTTER | Closed            | ✓      |
| MFX:LAS:DELAY   | 12.3 ps           | ✓      |
| MFX:LAS:PICKER  | Single-shot       | ✓      |

## Summary
Devices OK: 12   Warnings: 1   Errors: 0

⚠ mfx_bpm: Y offset (0.2) is outside nominal range (-0.05–0.05). Check with operator.
```

---

## Phase 5: Offer Actions

After presenting the report:

1. If there are ✗ errors: "Would you like me to help troubleshoot the failed devices?"
2. If there are ⚠ warnings: "The warnings are informational — shall I note them in the eLog?"
3. If all ✓: "Checkout complete. Ready to proceed."

---

## Bridge Not Available

If the bridge is not connected, provide a checklist of commands for the user to run
manually, formatted as a copy-paste block for their hutch-python session:

```python
# Paste this into your hutch-python session to run the checkout:
print("=== BEAM PATH ===")
print(f"dg1_h_slit: {dg1_h_slit.position}")
print(f"attenuator: {attenuator.transmission.get()}")
# ... etc.
```

Generate the checklist from the hutch reference device list, substituting the
actual Python object names.
