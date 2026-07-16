# MFX — Macromolecular Femtosecond Crystallography

Operator reference for `beam-opr`. Covers motors, PVs, beam path, and typical
experiment configurations. Fill in sections marked `# FILL IN` as values are confirmed
with the hutch operators.

For LUTE-specific information (detectors, analysis chains, SmallData config), see
`@ask-lute` references/hutches/mfx.md which has comprehensive experiment-type coverage.

---

## Hutch Identity

| Property | Value |
|---|---|
| Hutch code | `mfx` |
| Operator account | `mfxopr` |
| DAQ node | `mfx-daq` |
| Hutch-python launcher | `mfx3` (or `mfx4` — confirm with operator) |
| Experiment path | `/sdf/data/lcls/ds/mfx/{experiment}/` |
| XTC2 data path | `/sdf/data/lcls/ds/mfx/{experiment}/xtc2/` |
| SmallData path | `/sdf/data/lcls/ds/mfx/{experiment}/hdf5/smalldata/` |

---

## Beam Path Devices (for `/checkout` and `/awr`)

Ordered upstream → downstream. Confirmed device names from live hutch-python
session (mfx101609126, July 2026).

### MFX Beam Path (confirmed)

| Device | hutch-python name | PV prefix | Nominal state |
|---|---|---|---|
| Stopper 1 | `# FILL IN` | `MFX:PPS:MMS:ST1` | OUT during ops |
| Stopper 2 | `# FILL IN` | `MFX:PPS:MMS:ST2` | OUT during ops |
| DG1 valve 1 | `mfx_dg1_valve_1` | — | OUT (open) |
| DG1 valve 2 | `mfx_dg1_valve_2` | — | OUT (open) |
| DG1 slits | `mfx_dg1_slits` | `MFX:DG1:JAWS:` | gap: 0.5–2mm |
| DG1 imager (PIM) | `mfx_dg1_pim` | — | removed |
| DG1 IPM | `mfx_dg1_ipm` | `MFX:DG1:IPM:` | upstream monitor |
| MXT valve | `mfx_mxt_valve` | — | OUT (open) |
| Attenuator (AT2L0) | `at2l0` | — | T depends on exp |
| DG2 upstream slits | `mfx_dg2_upstream_slits` | `MFX:DG2:JAWS:` | gap: 0.2–1mm |
| DG2 midstream slits | `mfx_dg2_midstream_slits` | — | gap: 0.2–1mm |
| DG2 downstream slits | `mfx_dg2_downstream_slits` | — | gap: 0.2–1mm |
| DG2 imager (PIM) | `mfx_dg2_pim` | — | removed |
| DG2 IPM | `mfx_dg2_ipm` | `MFX:DG2:IPM:` | pre-sample monitor |
| DVD valve | `mfx_dvd_valve` | — | OUT (open) |
| DIA valve 01 | `mfx_dia_valve_01` | — | OUT (open) |
| DIA valve 02 | `mfx_dia_valve_02` | — | OUT (open) |
| DIA imager (PIM) | `mfx_dia_pim` | — | removed |
| DIA IPM | `mfx_dia_ipm` | — | downstream monitor |

### YAGs / Imagers

| Device | hutch-python name | Notes |
|---|---|---|
| YAG 0 | `yag0` | upstream, `.removed` = True when out |
| YAG 1 | `yag1` | mid, `.removed` = True when out |
| YAG 2 | `yag2` | downstream, `.removed` = True when out |

### Beam Destination Mirror

| Device | hutch-python name | MFX position | MEC position |
|---|---|---|---|
| MR1L4 pitch | `mr1l4_homs.pitch` | −562.035 µrad | +819.2 µrad |

> **Do not use the Beam Destination pyDM GUI** — it resets MR2L0 pointing.

### Beam Intensity Monitors

| Device | hutch-python name | PV prefix | Notes |
|---|---|---|---|
| IPM DG1 | `mfx_dg1_ipm` | `MFX:DG1:IPM:` | upstream monitor |
| IPM DG2 | `mfx_dg2_ipm` | `MFX:DG2:IPM:` | pre-sample monitor |
| Wave8 motor | `mfx_dg1_wave8_motor` | — | wave8 positioning |
| beam_status | `beam_status` | — | returns BeamCheckTuple (ev, mj1–4, mj_avg1–4) |

---

## Sample Area Motors (for `/checkout`)

| Motor | hutch-python name | PV prefix | Nominal / range |
|---|---|---|---|
| Sample X (coarse) | `# FILL IN` | `MFX:MMS:` | # FILL IN |
| Sample Y (coarse) | `# FILL IN` | `MFX:MMS:` | # FILL IN |
| Sample Z | `# FILL IN` | `MFX:MMS:` | # FILL IN |
| Sample X (fine) | `# FILL IN` | `MFX:USR:MMS:` | # FILL IN |
| Sample Y (fine) | `# FILL IN` | `MFX:USR:MMS:` | # FILL IN |
| Beamstop X | `# FILL IN` | `MFX:MMS:` | # FILL IN |
| Beamstop Y | `# FILL IN` | `MFX:MMS:` | # FILL IN |
| Detector distance | `# FILL IN` | `MFX:MMS:` | 70–500 mm |

---

## Pump Laser PVs (for `/checkout --laser`)

When the experiment involves an optical pump laser, add these checks to `/checkout`:

| Device | hutch-python name | PV | Nominal |
|---|---|---|---|
| Laser shutter | `# FILL IN` | `MFX:LAS:SHUTTER:` | Closed unless taking data |
| Delay stage | `# FILL IN` | `MFX:LAS:DELAY:` | experiment-specific |
| Pulse picker | `# FILL IN` | `MFX:LAS:PICKER:` | single-shot or every-shot |
| Waveplate (power) | `# FILL IN` | `MFX:LAS:WP:` | experiment-specific |
| Laser power monitor | `# FILL IN` | `MFX:LAS:PWR:` | check for stability |

---

## DAQ Configuration

| Property | Value |
|---|---|
| DAQ generation | LCLS-II (as of July 2025; confirm with operator) |
| Data format | `.xtc2` (psana2) |
| XTC2 path | `/sdf/data/lcls/ds/mfx/{experiment}/xtc2/` |
| Typical DRP nodes | `# FILL IN` |

---

## Typical Experiment Types

For LUTE analysis chains and SmallData configuration per experiment type, read
`@ask-lute`'s `references/hutches/mfx.md`.

| Type | Key detectors | Typical scan |
|---|---|---|
| SFX (serial femtosecond crystallography) | ePix10k2M or Jungfrau 16M | Fixed target or jet; no scan |
| TR-SAXS / TR-WAXS | ePix10k2M, Wave8 | Delay scan |
| XES | ePix100a (spectrometer) | Energy or delay scan |
| TR-XAS | ePix100a, CCM | Energy scan |

---

## Checkout Notes

- MFX uses a Rayleigh jet for most solution experiments — check jet pressure and
  flow stability before starting a run.
- The ePix10k2M gain mode should match the experiment (auto-gain vs. fixed gain).
  Confirm with the scientist before running.
- For SFX: confirm the hit rate is > 0 before starting a long run (check the AMI
  hit rate monitor or a short test run).
- Jungfrau 16M (arriving 2025): update device names when deployed.
