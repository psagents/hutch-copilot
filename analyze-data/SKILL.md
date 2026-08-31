---
name: analyze-data
description: >
  LCLS data analysis setup and refinement sub-skill for hutch-copilot. Use for
  configuring and running LUTE analysis workflows after data collection: SFX pipeline
  setup (/setup) and parameter refinement after first output (/refine). Calibration
  runs (GEOM, DARK) are handled by /take-run with a run_type tag — no separate
  calibrate command. Wizard logic lives here; consults ask-lute as a reference brain
  and ask-cctbx-xfel for SFX parameters and ask-smalldata for SmallData parameters.
  No bridge required — works entirely offline from S3DF. Triggers on: set up analysis,
  configure lute, sfx pipeline, refine parameters, check output, analyze data, 
  lute setup, lute wizard.
---

# analyze-data

You are the data analysis sub-skill of `hutch-copilot`. You own the offline
LUTE workflow setup and parameter refinement lifecycle for LCLS experiments.
Calibration runs (GEOM, DARK) are handled upstream via `/take-run` run_type tags
and Maestro DAG branching — you do not run calibration. You do not control live hardware.

You are invoked from `hutch-copilot` when the user asks to set up or monitor analysis.

---

## Experiment State (silent context)

Read state through the tool — never interpolate the path (this file used to name the
wrong one, so every field silently came back missing and the wizard re-asked everything):

```bash
bin/hutch-state get --hutch "$H" --experiment "$E" <field>
bin/hutch-state audit --hutch "$H" --experiment "$E"    # what do we actually know, and how old
```

Fields consumed by this skill: `hutch`, `experiment`, `photon_energy_eV`,
`last_run_number`, `sample_name`, `sample_delivery`.

A `MissingValue` error means ask the operator. A `StaleValue` error means re-resolve, or
pass `--stale-ok` and report the age. Do not assume a value in either case.

---

## Command Dispatch

| Command / Intent | Action |
|---|---|
| `/setup` or "set up analysis", "configure lute", "sfx pipeline", "run lute", "lute wizard" | Read `commands/setup.md` |
| `/refine` or "check output", "refine parameters", "bad ROI", "field not found", "re-run" | Read `commands/refine.md` |

---

## Execution Context

- All commands run from S3DF — no hutch-python bridge required.
- Data lives at `/sdf/data/lcls/ds/{hutch}/{experiment}/`.
- LUTE output lives at `/sdf/data/lcls/ds/{hutch}/{experiment}/results/<optional_dir>/lute_output/`.
- Jobs are submitted by the specified eLog trigger.

---

## Reference Resources

| Need | Where to look |
|---|---|
| LUTE task catalog, YAML syntax, DAG structure, result passing | `@ask-lute` (reference brain) |
| SFX crystallography parameters (CrystFEL, CCTBX.XFEL) | `@ask-cctbx-xfel` |
| CCTBX DAG pattern, d_min hazard, funnel diagnosis | `references/sfx-cctbx-patterns.md` |
| LUTE submission, wall clock, known traps, Kerberos scoping | `bin/hutch-lute` |
| SmallData detector parameters (ROI, droplet/photon, azimuthal integration) | `@ask-smalldata` |
| SLURM job submission and monitoring | `@ask-lute/references/slurm-submission.md` |
| File finding, XTC2 inventory, HDF5 verification | `@lcls-catalog` |
| psana2 data inspection, detector calibration check | `@ask-lcls2` |
