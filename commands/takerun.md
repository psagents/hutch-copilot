# /takerun — Start a DAQ Run

Triggered by: `/takerun`, "take a run", "start collecting", "begin a run", "take data".

---

## Phase 1: Resolve Run Parameters

Pull what you already know from the session state. Ask only for what is missing.

**Required parameters:**
- `duration` (seconds or minutes) OR `n_events` (shot count) — ask if neither known
- `sample_name` — from state, or ask if absent
- `run_label` — optional free-text label (suggest `{sample_name}_{concentration}`)

**Never ask for parameters already in state.** If the user says "/takerun for 3 minutes"
and the sample is already in context, do not ask about the sample again.

If both `duration` and `n_events` are provided, prefer `duration`.

---

## Phase 2: Confirm

Show a summary before starting. This is a write operation — always confirm.

```
Proposed run:
  Duration  : 3 min (180 s)
  Sample    : FeNO6, 10mM, 50µm Rayleigh jet
  Label     : FeNO6_10mM
  Hutch     : MFX  /  Experiment: mfxl1013621

Shall I start the run?
```

Wait for explicit approval before proceeding.

---

## Phase 3: Configure and Begin

If live bridge is available, execute in sequence. All DAQ commands are **write** class —
confirmation has already been given in Phase 2.

```python
# Configure
daq.configure(
    record=True,
    # duration OR events — use whichever was specified
)
```

Then start with the appropriate call:

```python
# By duration (seconds):
daq.begin(duration=180, wait=False)

# Or by event count:
daq.begin(events=54000, wait=False)
```

Use `wait=False` so monitoring can proceed in parallel.

---

## Phase 4: Monitor

Poll status and data arrival. Issue the status check every ~15s while the run is active.

```python
# DAQ status
daq.status()
```

Parse the response for:
- `run_number` — report it to the user ("Run 47 started")
- `state` — `Running`, `Configured`, `Open`
- Any error messages

**Simultaneously, verify data is landing on S3DF** (check after ~30s):

```bash
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | head -5
```

If XTC2 files are not growing within 60s of run start, warn the user:
> "No new XTC2 files detected at `/sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/`.
> The run may not be recording. Check DAQ status or run `/fixdaq`."

Report run number and estimated progress to the user while waiting.

---

## Phase 5: End Run

When duration has elapsed or the user says "stop" / "end run" / "done":

```python
daq.end_run()
```

**This must be called even if an error occurred.** Use try/finally semantics — if any
prior step raised an exception, still call `daq.end_run()` before surfacing the error.

---

## Phase 6: Verify Data

After end_run, confirm the data arrived:

```bash
ls -lh /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | grep "r{run_number:04d}"
```

Report the file size and count. If files are absent or zero-size, warn the user and
suggest `/fixdaq`.

**Update session state:**
```json
{ "last_run": <run_number> }
```

---

## Error Handling

| Symptom | Response |
|---|---|
| `daq.begin()` throws exception | Call `daq.end_run()` anyway; then suggest `/fixdaq` |
| No XTC2 files after 60s | Warn; check `daq.status()`; suggest `/fixdaq` |
| Run ends unexpectedly | Check `daq.status()` for error state; document in elog |
| Bridge not connected | Provide commands for user to run manually; no auto-execution |

---

## Bridge Not Available

If the bridge is not connected, provide the commands for the user to run manually
in their hutch-python session:

```python
# In your hutch-python session:
daq.configure(record=True)
daq.begin(duration=180, wait=True)
daq.end_run()
```

Guide them through each step, waiting for them to confirm before proceeding to the next.
