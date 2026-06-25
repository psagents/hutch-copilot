# LCLS / LCLS-II Machine Beam Status PVs

Use these PVs to check the status of the LCLS machine beam delivery. All reads are
non-destructive EPICS `caget` queries. In hutch-python these can be read directly
via `EpicsSignalRO`.

---

## Quick Beam Health Check

For a rapid first-pass diagnosis, read these four PVs:

| PV | Description | Expected during beam |
|---|---|---|
| `SIOC:SYS0:ML00:CALCOUT000` | Beam rate (LCLS-I, Hz) | 120 |
| `TPG:SYS0:1:DST00:RATE` | Beam rate (LCLS-II, Hz) | varies |
| `BPMS:DMP1:199:TMIT` | Beam charge at dump (pC) | > 0 when beam on |
| `MPS:FLT:SUMMARY` | MPS fault summary | 0 = no active faults |

---

## Beam Rate

| PV | Description |
|---|---|
| `SIOC:SYS0:ML00:CALCOUT000` | Computed beam rate, LCLS-I (Hz) |
| `TPG:SYS0:1:DST00:RATE` | Destination 0 rate, LCLS-II (Hz) |
| `EVR:UND1:IN20:CTRL.DG0E` | Event code 40 (beam) enable |
| `PATT:SYS0:1:MPSBURSTRATE` | MPS allowed burst rate |

---

## Beam Charge / Intensity

| PV | Description | Units |
|---|---|---|
| `BPMS:IN20:221:TMIT` | Gun section charge | e- × 10^7 |
| `BPMS:DMP1:199:TMIT` | Dump charge (end-of-linac) | e- × 10^7 |
| `BLEN:LI21:265:AIMAX` | Bunch length | ps |
| `FBCK:FB04:LG01:CHIRP` | Energy spread (chirp FB) | — |

---

## Beam Energy

| PV | Description | Units |
|---|---|---|
| `BEND:DMPH:400:BDES` | Dispersive bend energy setpoint | GeV |
| `BEND:DMPH:400:BACT` | Dispersive bend energy readback | GeV |
| `REFS:IN20:751:EDES` | Energy design setpoint | GeV |
| `BLD:SYS0:500:ENERGYHXBR` | Photon energy (HXR) readback | eV |
| `BLD:SYS0:500:ENERGYSXBR` | Photon energy (SXR) readback | eV |

To convert GeV electron energy to approximate photon energy, use undulator K-value
readbacks or the photon energy PVs above (more reliable).

---

## MPS (Machine Protection System)

| PV | Description |
|---|---|
| `MPS:FLT:SUMMARY` | Global MPS fault summary (0 = OK) |
| `MPS:FLT:0` – `MPS:FLT:N` | Individual fault bits |
| `SIOC:SYS0:ML01:CALC001` | MPS beam-permit status |
| `IOC:BSY0:MP01:BYKIKCTL` | Kicker permit |

If `MPS:FLT:SUMMARY` is non-zero, MPS is faulted. Contact the LCLS operator (control
room or on-call) — MPS faults cannot be reset from the hutch.

---

## BCS (Beam Containment System)

| PV | Description |
|---|---|
| `SIOC:SYS0:ML00:AO466` | BCS OK status |
| `BCS:MCC0:1:BTBEHW_LTCH` | BCS beam-to-beam enclosure latch |
| `FAST_FAULT:MCC0:1:FAST_FAULT` | Fast fault summary |

BCS faults also require control room intervention.

---

## Hutch Shutters (LCLS-II NEH/FEH)

| Hutch | Stopper PV pattern | Notes |
|---|---|---|
| MFX | `MFX:PPS:MMS:ST{N}:STATE` | Stoppers ST1, ST2 |
| TMO | `TMO:PPS:MMS:ST{N}:STATE` | |
| RIX | `RIX:PPS:MMS:ST{N}:STATE` | |
| CXI | `CXI:PPS:MMS:ST{N}:STATE` | |
| XPP | `XPP:PPS:MMS:ST{N}:STATE` | |

Stopper state values: `OUT` (beam passes), `IN` (beam blocked), `MOVING`.

Stoppers are controlled by PPS (Personnel Protection System) — opening requires
proper interlock conditions to be satisfied.

---

## Transmission / Attenuators

| PV pattern | Description |
|---|---|
| `{hutch}:ATT:COM:T_CALC` | Computed transmission (0–1) |
| `{hutch}:ATT:COM:REQSI` | Requested Si blade insertion |
| `{hutch}:ATT:COM:PREC` | Precision mode attenuator |

Hutch-specific attenuator PVs are in `references/hutches/{hutch}.md`.

---

## Undulator Gap / Photon Energy Tuning

| PV pattern | Description |
|---|---|
| `USEG:UND1:150:UGAPACT` | Undulator 1 gap readback (mm) |
| `USEG:UND1:150:UGAPDES` | Undulator 1 gap setpoint (mm) |
| `BLD:SYS0:500:ENERGYHXBR` | HXR photon energy readback (eV) |

For LCLS-II HXR: energy controlled via undulator taper. Contact operator to change energy.

---

## Beam Loss Monitors

| PV | Description |
|---|---|
| `BLEN:LI24:886:BLENROACT` | Linac exit beam loss |
| `IOC:BSY0:MP01:MS_LTCH` | Machine stop latch |

High loss triggers machine stop and MPS fault.
