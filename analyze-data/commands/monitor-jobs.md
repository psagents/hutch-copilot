# /monitor-jobs — Monitor LUTE Analysis Jobs

Triggered by: `/monitor-jobs`, "check jobs", "jobs running", "slurm status",
"did lute finish", "is the analysis done", "check the pipeline".

This command checks the status of running or recently completed LUTE workflows
for the current experiment. It combines SLURM job status, LUTE database state,
and output file verification.

---

## Phase 1: SLURM Job Status

Use `@ask-slurm-s3df` to query active and recent jobs:

```bash
squeue -u {username} --format="%.18i %.9P %.30j %.8T %.10M %.6D %R" | grep {experiment}
```

Or a broader recent history:
```bash
sacct -u {username} --starttime=now-1days \
  --format=JobID,JobName,State,Elapsed,ExitCode | grep {experiment}
```

Report: job ID, task name, state (RUNNING / COMPLETED / FAILED), elapsed time.

---

## Phase 2: LUTE Database Status

The LUTE SQLite database tracks per-task completion and result passing.

```bash
sqlite3 /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/lute.db \
  "SELECT taskname, status, start_time, end_time FROM tasks ORDER BY start_time DESC LIMIT 20;"
```

Status values:
- `SUBMITTED` — job queued
- `RUNNING` — job active
- `COMPLETED` — finished successfully; result registered in DB
- `FAILED` — task exited with error; check logs

If the DB is locked (another process writing), retry after 5s.

---

## Phase 3: Output File Verification

Confirm expected outputs exist and are non-empty:

```bash
# SmallData HDF5
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/hdf5/smalldata/*.h5 | tail -5

# SFX stream files (CrystFEL)
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/*.stream | tail -5

# Merged HDF5 / MTZ (post-partialator)
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/*.hkl \
        /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/*.mtz 2>/dev/null
```

Use `@lcls-catalog` for a richer inventory query if the experiment has many runs.

---

## Phase 4: Summary Report

```
LUTE job status — {experiment} — {timestamp}
══════════════════════════════════════════════
SLURM jobs:
  {job_id}  {task_name}  {state}  {elapsed}

LUTE DB (last 5 tasks):
  {taskname}  {status}  {start} → {end}

Output files:
  SmallData : {N} files, latest {size} ({filename})
  Stream    : {N} files, latest {filename}
  Merged    : {present / absent}
══════════════════════════════════════════════
{Summary: pipeline at step N/M. Next: ... OR: all steps complete.}
```

---

## Error Diagnosis

If a task shows `FAILED`:

```bash
# Find the task log
ls /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/logs/ | grep {taskname}
tail -50 /sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/logs/{logfile}
```

Common failure patterns:

| Error in log | Likely cause | Action |
|---|---|---|
| `ModuleNotFoundError` | Wrong LUTE environment | Check LUTE version; re-run `install_lute.py` |
| `FileNotFoundError: *.geom` | Geometry file path wrong | Fix `geom_file` in YAML; re-trigger |
| `No peaks found` | Peak threshold too high | Lower `min_snr`; run `/refine` |
| `SLURM: out of memory` | Job needs more RAM | Increase `--mem` in DAG `slurm_params` |
| `CalibStoreError` | Missing calibration | Run `/calibrate` first |

For SLURM-specific issues (fairshare, node availability, partition limits), delegate
to `@ask-slurm-s3df`.
