# SFX Analysis Defaults — MFX

Reference values for MFX SFX experiments. Use these as sanity checks when
reviewing parameters produced by `@ask-cctbx-xfel` or `@ask-lute`, not as
pre-fills. Always confirm with the user.

---

## Detector Geometry

| Detector | Typical distance | Beam center | Geometry source |
|---|---|---|---|
| ePix10k2M | 80–120 mm | ~(900, 900) px | `MFX:ROB:CONT:POS:Z` PV |
| Jungfrau 16M | 100–200 mm | ~(2200, 2100) px | instrument scientist |

Geometry file location (CrystFEL):
```
/sdf/data/lcls/ds/mfx/{experiment}/results/lute_output/{detector}.geom
```

---

## Peak Finding — Cheetah (CheetahRunner)

Typical starting values for MFX SFX. Adjust based on hit rate feedback.

| Parameter | Typical value | Notes |
|---|---|---|
| `min_snr` | 6–8 | Signal-to-noise threshold per peak; lower = more peaks, more noise |
| `min_peaks` | 15–25 | Minimum peaks to classify a frame as a hit |
| `adc_threshold` | 200–400 ADU | Absolute ADU floor; detector-dependent |
| Expected hit rate | 5–30% | Depends on sample concentration and jet stability |

---

## Peak Finding — Peakfinder8 (FindPeaksSFX)

| Parameter | Typical value | Notes |
|---|---|---|
| `threshold` | 50–150 ADU | Absolute threshold above background |
| `min_snr` | 5–7 | |
| `min_pix_count` | 2 | Minimum connected pixels per peak |
| `max_pix_count` | 20 | |
| `local_bg_radius` | 3 | Background annulus radius (pixels) |
| `min_peaks` | 10–20 | |

---

## Indexing — CrystFEL (IndexCrystFEL)

| Parameter | Typical value | Notes |
|---|---|---|
| `indexing` | `xgandalf` | Default; robust for most protein crystals |
| `pushres` | 2.0 | Include peaks to N Å beyond nominal resolution |
| `min_peaks` | 15 | Frames with fewer peaks are not indexed |
| `multi` | true | Try multiple lattices per frame (common for SFX) |
| Expected indexing rate | 20–60% of hits | Depends on crystal quality and geometry accuracy |

For large unit cells (> 200 Å): consider `pinkindexer` or `mosflm`.
For known space group: pass `-y {symmetry}` and `--unit-cell={a},{b},{c},{al},{be},{ga}`.

---

## Merging — partialator (MergePartialator)

| Parameter | Typical value | Notes |
|---|---|---|
| `model` | `unity` (start) → `xsphere` (refine) | Start with unity for first pass |
| `iterations` | 3–5 | More iterations = slower but better partiality model |
| `symmetry` | protein-specific | Must match space group; e.g. `mmm` for orthorhombic |
| Min indexed patterns for good stats | ~5000 | Below this, CC* and completeness suffer |

---

## Merge Statistics — Typical Targets (first pass)

| Metric | Acceptable | Good |
|---|---|---|
| Completeness | > 80% | > 95% |
| CC* | > 0.9 | > 0.97 |
| R-split | < 20% | < 10% |
| Multiplicity | > 5 | > 20 |

Low CC* despite many patterns usually indicates geometry error or wrong symmetry.

---

## BayFAI Calibration (TR-SAXS / TR-WAXS)

Calibrant: AgBh (silver behenate) or LaB₆. Use the same detector position as
the experiment run.

| Parameter | Typical value |
|---|---|
| `n_pts` | 512 |
| `int_units` | `q_A^-1` |
| `npts_az` | 1 (isotropic SAXS) or 36 (sector analysis) |
| `n_iterations` BayFAI | 100 |

`.poni` file output: `{lute_output_dir}/bayFAI_output/{detector_alias}.poni`

---

## Common MFX SFX Failure Modes

| Symptom | Most likely cause |
|---|---|
| Indexing rate < 5% | Geometry wrong (beam center or distance off by > 5%) |
| Indexing rate 5–15% | `min_peaks` too high, or crystal lattice not in xgandalf library |
| High R-split (> 30%) | Wrong symmetry, or too few patterns, or partiality model mismatch |
| Empty stream file | Cheetah not finding hits; lower `min_snr` and `adc_threshold` |
| Missing detector in SmallData | psana alias mismatch; re-check with `run.detnames` |
