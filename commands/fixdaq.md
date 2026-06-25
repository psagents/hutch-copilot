# /fixdaq — DAQ-II Troubleshooting

Triggered by: `/fixdaq`, "fix daq", "daq not working", "daq crashed", "daq error",
"daq is broken", "daq won't start", "data not recording".

This command diagnoses common DAQ-II problems and guides the user through targeted
fixes. It operates on a strict allowlist — it never runs arbitrary shell commands
and always shows the exact command before executing anything.

---

## Phase 1: Gather Symptoms

Ask the user to describe what they observe. Key questions (ask only those not already
answered):

1. "What symptoms do you see?" — error message, missing data, DAQ not starting, etc.
2. "What were you doing when it happened?" — just started, mid-run, after restart
3. "Have you already tried restarting anything?"

Use `@daq-logs` to pull recent error entries for the experiment:

```
Search DAQ logs for errors in the last 30 minutes for experiment {experiment}
```

Review the log output for known error patterns (see Error Patterns below).

---

## Phase 2: Classify the Problem

Match symptoms to a category:

| Category | Symptoms |
|---|---|
| **A: DAQ not starting** | Can't begin a run, DAQ in error state, control process unreachable |
| **B: Data not recording** | Run starts but no XTC2 files appear on S3DF |
| **C: Detector missing** | Run data missing one or more expected detectors |
| **D: DAQ frozen mid-run** | Run started but hangs indefinitely |
| **E: Run number stuck** | Run number not incrementing across successive runs |
| **F: Network/DRP issue** | Data recording process not connecting |

---

## Phase 3: Diagnose

Run read-only diagnostics first (no confirmation required):

### Check XTC2 file arrival
```bash
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | head -10
```

### Check disk space
```bash
df -h /sdf/data/lcls/ds/{hutch}/{experiment}/
```

### Check DAQ status via bridge (read-only)
```python
daq.status()
daq.config_info()
```

### Check platform/process status

**NOTE: The exact commands for checking and restarting DAQ-II processes depend on
the DAQ-II deployment configuration at your hutch. The generic patterns below are
starting points — verify with your DAQ operator or consult the LCLS DAQ team.**

Common status checks (confirm exact commands with DAQ team):
```bash
# These are placeholder patterns — fill in with actual commands for your hutch:
# kubectl get pods -n {daq_namespace}   (if Kubernetes-managed)
# prodmgr status                        (if production manager)
# kconsole {hutch} status               (if kconsole-managed)
```

---

## Phase 4: Propose Fix

Based on the diagnosis, propose a targeted fix from the allowed actions below.
Always show the exact command and wait for confirmation before executing.

### Allowed Actions

#### Read-only (no confirmation needed)
```bash
# Check XTC2 files
ls -lt /sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/ | head -10

# Check disk space
df -h /sdf/data/lcls/ds/{hutch}/{experiment}/

# Read DAQ status
daq.status()
daq.config_info()

# Query DAQ logs
# (via @daq-logs agent)
```

#### DAQ control via hutch-python bridge (require confirmation)
```python
# Disconnect and reconnect DAQ
daq.disconnect()
daq.connect()

# End a stuck run
daq.end_run()

# Reset DAQ state
daq.reset()
```

#### Process-level restarts (require confirmation + FILL IN)

> **These commands need to be filled in with the actual mechanism for your hutch.**
> Common patterns at LCLS-II — verify with the DAQ team before use:

```bash
# PLACEHOLDER: Replace with actual hutch restart command
# kconsole {hutch} restart
# hstart / hstop
# kubectl rollout restart deployment/{daq_process_name} -n {namespace}
# prodmgr restart {process_name}
```

**NEVER execute outside this allowlist.** If the fix requires commands not listed here,
describe them to the user and ask them to run manually.

---

## Error Patterns

Common DAQ-II errors and their likely fixes:

| Error / Symptom | Likely Cause | Suggested Action |
|---|---|---|
| No XTC2 files, run starts fine | DRP not running or not connected | Check DRP process; reconnect DAQ |
| `daq.begin()` raises `TimeoutError` | DAQ platform not started | Platform restart (ask DAQ operator) |
| Run stuck at "Configured" | Partition not allocated | Check partition; platform restart |
| Missing detector in data | Detector DRP not connected | Restart DRP for that detector |
| Run number not incrementing | DAQ database issue | Contact DAQ operator |
| Disk full | S3DF storage full | Check disk; contact LCLS ops |
| `ConnectionRefusedError` | DAQ manager not running | Full DAQ restart needed (operator) |

---

## Escalation

If the issue is not resolved after the allowed diagnostic + restart steps:

1. Post a summary to the eLog (via `@elog-copilot`) with:
   - Symptoms observed
   - Diagnostics run
   - Actions taken
   - Current state

2. Recommend contacting the LCLS DAQ team or on-call operator:
   - **LCLS DAQ Elog**: post to the current experiment's elog
   - **Slack**: `#lcls-daq` or hutch-specific channel
   - **On-call operator**: hutch phone / control room

---

## Notes on DAQ-II Architecture

DAQ-II (psdaq) at LCLS-II uses:
- **DRP (Data Recording Process)** — one per detector, collects and records data
- **Control process** — manages run state and configuration
- **XPM** — timing master (use `/awr` or `@xpm-seq` for timing issues)
- **Platform** — coordinates all DAQ processes for a hutch

This is different from LCLS-I, which used `psana1` and platform processes on DAQ nodes.
If the experiment uses `.xtc` files (not `.xtc2`), it's LCLS-I — DAQ-I troubleshooting
is out of scope for this command; contact the on-call operator directly.
