# /refine — Refine Analysis Parameters After First Output

Triggered by: `/refine`, "check output", "refine parameters", "bad ROI",
"field not found in HDF5", "wrong detector", "re-run lute", "adjust thresholds",
"indexing rate is low", "too few peaks".

This command drives the **inspect → adjust → re-run** loop after an initial LUTE
workflow has produced its first output. It assumes `/setup` is complete and at least
one output file exists (SmallData HDF5 or CrystFEL stream).

Consult `ask-lute/references/` for LUTE YAML syntax and field name conventions.
For SFX-specific parameter adjustments, delegate to `@ask-cctbx-xfel`.

---

## Experiment State (silent context)

Before asking the user for anything, read the current state from:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/beamtime/{experiment}_state.json
```
Fields consumed by this command: `hutch`, `experiment`, `last_run_number`

Use any non-null field directly — do not re-ask the user.

---

## Step R.1 — Locate the first output

```bash
# SmallData HDF5
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/hdf5/smalldata/*.h5 | head -5

# CrystFEL stream files
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/*.stream | head -5
```

If no files exist yet, the workflow has not run or failed. Check the eLog run table
or run `/monitor-jobs` before continuing.

---

## Step R.2 — Inspect field names and verify assumptions

Open-ended inspection — ask the user to run and paste the output:

```python
import h5py

f = h5py.File('<smd_path>', 'r')

def print_tree(name, obj):
    if isinstance(obj, h5py.Dataset):
        print(f'{name:60s} {obj.shape}  {obj.dtype}')
f.visititems(print_tree)
```

Or for targeted checks:

```python
# Beam monitor field
print(f['<alias>/totalIntensityJoules'][:10])

# Scan variable field
print(f['<scan_var>'][:10])

# Detector ROI shape
print(f['<det_alias>/ROI_0_area'][:3].shape)
```

Confirm with the user:
1. **`ipm_var` path** — does the field exist and contain non-trivial values?
2. **`scan_var` path** — does the field exist and step as expected?
3. **ROI coverage** — does the saved area capture the spectral/diffraction signal?

---

## Step R.3 — Edit the YAML

Config file location:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/{hutch}_lute.yaml
```

Common adjustments after first inspection:

| What's wrong | Field to change | Location in YAML |
|---|---|---|
| `scan_var` field not found in HDF5 | `scan_var` | `AnalyzeSmallDataXES` / `XSS` / `XAS` block |
| `ipm_var` values all near zero or negative | `ipm_var`, `min_ipm` | downstream task block |
| ROI misses the signal stripe | `ROI` under `getROIs` | `SubmitSMD.producer_parameters` |
| Spectrum projected along wrong axis | `invert_xes_axes` | `AnalyzeSmallDataXES` block |
| Too many shots rejected | `min_ipm`, `min_Iscat` | `intensity_thresholds` block |
| Indexing rate too low (SFX) | `min_peaks`, `pushres`, `indexing` | `IndexCrystFEL` block |
| Wrong unit cell / space group (SFX) | `unit_cell`, `symmetry` | `IndexCrystFEL` / `MergePartialator` |

For SFX-specific adjustments (low indexing rate, poor CC*, wrong unit cell), delegate
to `@ask-cctbx-xfel` before editing. Cross-check against
`analyze-data/references/sfx-analysis-defaults.md` for MFX typical values.

Make the edit directly in the YAML, then set permissions:
```bash
chmod 666 /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/{hutch}_lute.yaml
```

**No need to re-run `install_lute.py`** for YAML-only changes — the DAG and eLog
registration are unchanged. The next workflow trigger picks up the new config automatically.

---

## Step R.4 — Re-trigger for a representative run

Re-run the workflow for a single run to validate before the next beamtime.

**Option A — Manual trigger via eLog UI**
```
https://pswww.slac.stanford.edu/lgbk/lgbk/{experiment}/
→ Run table → select run → trigger lute_{wf_name}
```

**Option B — Submit directly via SLURM**
```bash
{lute_path}/install/bin/submit_launch_slurm.sh \
  -c {config_path} \
  -t SmallDataProducer2 \
  --run {run_number} \
  --experiment {experiment}
```

Use `/monitor-jobs` to track the re-triggered job.

---

## Step R.5 — Iterate

Repeat R.2 → R.3 → R.4 until:
- `scan_var` bins the signal correctly across the expected range
- `ipm_var` filters bad shots without over-rejecting
- ROI contains the full spectral stripe with no clipping
- Downstream analysis plots look physically reasonable
- (SFX) Indexing rate and merge statistics are within acceptable range
  (see `analyze-data/references/sfx-analysis-defaults.md`)

Once satisfied, the config is production-ready — the eLog workflow trigger applies
it to all subsequent runs automatically.

---

## Quick Reference — SmallData HDF5 field name patterns

| Source | Typical HDF5 path | Notes |
|---|---|---|
| Area detector ROI sum | `{det_alias}/ROI_0_sum` | Sum of ROI pixels per shot |
| Area detector ROI image | `{det_alias}/ROI_0_area` | Full 2D ROI frame (if `writeArea: true`) |
| bmmon intensity | `{det_alias}/totalIntensityJoules` | Pre-computed by firmware |
| EPICS PV (per-shot) | `epics/{pv_alias}` | Saved via `epicsPV` in producer_parameters |
| Scan motor | `{motor_alias}` or `epicsUser/{alias}` | Depends on PV registration |
| Timing tool | `tt/ttCorr`, `tt/AMPL` | Only if `ttCalib` block present |
| Shot flags | `lightStatus/xray`, `lightStatus/laser` | Always present |

For full field name conventions, read `ask-lute/references/result-passing.md`.
