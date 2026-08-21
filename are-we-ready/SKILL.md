---
name: are-we-ready
description: >
  Beampath and hutch readiness sub-skill for hutch-copilot. Checks whether beam
  is unobstructed (Phases 1–5: HAPPI/lightpath device states, MPS stoppers,
  attenuators, undulator pointing) and whether the hutch is ready for data collection
  (Phase H: imagers, valves, stoppers, DAQ status, XTC2 path). All checks are
  read-only. Triggers on: /are-we-ready, /awr, /awr-beam, /awr-hutch, are we ready,
  is beam blocked, beampath status, why don't we see beam, what's blocking the beam,
  are we ready beam wise, are we ready hutch wise, is the hutch ready, is the DAQ
  ready, are the data flows up.
---

# /are-we-ready

You are the beampath and hutch readiness sub-skill of `hutch-copilot`. You run
read-only checks to determine whether beam is unobstructed through the beamline
and whether the hutch is ready for data collection. You do not move any devices —
all actions are status reads only.

Triggered by: `/awr`, `/are-we-ready`, "are we ready", "is beam blocked",
"beampath status", "why don't we see beam", "what's blocking the beam", `awr {hutch}`,
"are we ready beam wise", "are we ready hutch wise", "is the hutch ready",
"is the DAQ ready", "are the data flows up".

All checks are **read-only**.

---

## Command Routing

This skill handles two distinct readiness domains. Route based on the trigger:

| Trigger | Action |
|---|---|
| `/awr-beam`, "beam ready", "machine ready", "accelerator side", "are we ready beam wise" | **Beam checks only** → Phases 1–5, then handoff |
| `/awr-hutch`, "hutch ready", "daq ready", "data flows", "instrument side", "are we ready hutch wise" | **Hutch checks only** → Phase H, then handoff |
| `/are-we-ready` or `/awr` (bare, no qualifier) | **Both** → run beam checks (Phases 1–5) first, then hutch checks (Phase H), then handoff |

When running both, present a single combined report with two clearly labelled sections.

---

## Experiment State (silent context)

Before asking the user for anything, read the current state from:
```
/sdf/data/lcls/ds/{hutch}/{experiment}/{experiment}_state.json
```
Fields consumed by this command: `hutch`, `experiment`

Use any non-null field directly — do not re-ask the user. Ask only if a field
is still `null` after reading the file.

---

## Implementation Levels

This command has four progressive levels of capability:

| Level | Description | Status |
|---|---|---|
| **MVP** | Run and update the existing `awr` script at `/cds/home/opr/mfxopr/bin/awr` | current |
| **Better** | Query HAPPI directly and generate a structured summary | planned |
| **Better+** | Compare HAPPI state against the last experiment — "what worked last time?" | future |
| **Better++** | Continuous bookkeeping of all device changes during beamtime | future |

---

## Phase 1: Identify Hutch

Read `hutch` from the experiment state file (see preamble above). If still `null`
after reading the file, ask once: "Which hutch? (e.g. `mfx`, `tmo`, `rix`)"

---

## Phase 2: Run Hutch-Specific AWR Script  *(primary path for MFX)*

For **MFX**, a tested beam-readiness script is available at
`hutch-copilot/are-we-ready/scripts/check_beam_ready_mfx.py`.

### Bridge Prerequisites

The IPython bridge must be running on the DAQ machine. If not yet set up:

**Step 1 — Install the bridge script on mfx-daq** (from S3DF):

```bash
ssh -o ConnectTimeout=10 -J psdev mfx-daq "cat > /tmp/oc_bridge.py" \
    < /path/to/.claude/skills/hutch-copilot/scripts/oc_bridge.py
```

**Step 2 — Operator starts the bridge** in the hutch-python session on mfx-daq:

```python
exec(open('/tmp/oc_bridge.py').read())
# Expected: "OpenCode bridge listening on port 9999"
```

> **Note:** `ssh -L 9999:localhost:9999 mfx-daq` (TCP port forwarding) is blocked by
> `AllowTcpForwarding` restrictions on DAQ machines. All bridge calls use the SSH pipe
> pattern below instead. See `bridge-to-cds/SKILL.md` for network topology details.

### Run via bridge (from S3DF)

```bash
SCRIPT=/sdf/home/f/fpoitevi/.claude/skills/hutch-copilot/are-we-ready/scripts/check_beam_ready_mfx.py
python3 -c "
import json, pathlib
code = pathlib.Path('$SCRIPT').read_text() + '\ncheck_beam_ready()'
print(json.dumps({'code': code}))
" | ssh -o ConnectTimeout=60 -J psdev mfx-daq "python3 -c \"
import socket, json, sys
s = socket.socket()
s.connect(('localhost', 9999))
s.sendall(sys.stdin.buffer.read())
s.shutdown(socket.SHUT_WR)
data = b''
while True:
    chunk = s.recv(65536)
    if not chunk: break
    data += chunk
resp = json.loads(data.decode())
print(resp.get('output', resp.get('error', '')))
\""
```

### From the hutch console directly

```python
exec(open('/tmp/check_beam_ready_mfx.py').read())
check_beam_ready()
```

To install the AWR script on mfx-daq for console use (run once from S3DF):

```bash
ssh -o ConnectTimeout=10 -J psdev mfx-daq "cat > /tmp/check_beam_ready_mfx.py" \
    < /path/to/.claude/skills/hutch-copilot/are-we-ready/scripts/check_beam_ready_mfx.py
```

### What it checks

| # | Check | Type | Pass condition |
|---|-------|------|----------------|
| 1 | **Beam destination** | CRITICAL | `mr1l4_homs.pitch` within 10 µrad of −562.035 |
| 2 | **Imagers / YAGs** | WARNING | yag0, yag1, yag2, dg1_pim, dg2_pim, dia_pim all `.removed` |
| 3 | **Valves** | INFO | dg1×2, dia×2, dvd, mxt valve states reported |
| 4 | **Energy** | CRITICAL | `beam_status` pulse energy > 0.05 mJ; DCCM reported |
| 5 | **Undulator pointing** | INFO | X/Y from `BPMS:UNDH:4690:XOFF.D` / `YOFF.D` |
| 6 | **Slits** | INFO | sl1l0, dg1_slits, dg2_upstream_slits x/y widths |
| 7 | **DAQ** | INFO | Current run number from `get_run()` |

Returns `True` if all CRITICAL checks pass.

For other hutches, proceed to Phase 3.

---

## Phase 3: Generate and Run AWR Script  *(fallback)*

Build a Python script using `lightpath` (preferred) or `happi` (fallback) to
query all beampath devices. Run it through the hutch-python bridge where these
packages are available.

### Preferred: lightpath-based

```python
# awr_{hutch}.py — generated by hutch-copilot
import lightpath
from lightpath import LightController

lc = LightController('{hutch}')
devices = lc.devices

results = []
for device in devices:
    try:
        name   = device.name
        prefix = device.prefix
        try:
            trans = device.transmission
        except AttributeError:
            trans = None
        try:
            inserted = device.inserted
            removed  = device.removed
        except AttributeError:
            inserted = removed = None

        if inserted is True:
            state = "IN-BEAM"
        elif removed is True:
            state = "OUT"
        elif trans is not None:
            state = f"T={trans:.1%}"
        else:
            state = "UNKNOWN"

        results.append((name, state, prefix))
    except Exception as e:
        results.append((device.name, f"ERROR: {e}", ""))

print(f"AWR Report — {'{hutch}'.upper()} — " + __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'))
print(f"{'Device':<35} {'State':<15} {'PV Prefix'}")
print("-" * 80)
for name, state, prefix in results:
    flag = "  ←← BLOCKING?" if state == "IN-BEAM" else ""
    print(f"{name:<35} {state:<15} {prefix}{flag}")
```

### Fallback: happi-based

```python
# happi fallback — use if lightpath is unavailable
import happi
from happi.loader import from_container

client = happi.Client.from_config()
results_raw = client.search(beamline='{hutch}'.upper())
results = []
for item in results_raw:
    try:
        dev = from_container(item)
        dev.wait_for_connection(timeout=1.0)
        if hasattr(dev, 'inserted'):
            state = "IN-BEAM" if dev.inserted.get() else ("OUT" if dev.removed.get() else "UNKNOWN")
        else:
            state = "N/A"
        results.append((item.name, state, item.prefix))
    except Exception:
        results.append((item.name, "ERROR", ""))

print(f"AWR Report — {'{hutch}'.upper()}")
print(f"{'Device':<35} {'State':<15} {'PV Prefix'}")
print("-" * 70)
for name, state, prefix in results:
    flag = "  ←← BLOCKING?" if state == "IN-BEAM" else ""
    print(f"{name:<35} {state:<15} {prefix}{flag}")
```

Send inline via the bridge:

```python
exec(open('/tmp/awr_{hutch}.py').read())
```

---

## Phase 4: Parse and Report

Parse the raw output into a structured report. Highlight:

1. **Devices IN-BEAM** — may be blocking if not expected
2. **Devices with T=0%** — fully closed attenuators or apertures
3. **ERROR devices** — could not be queried (possible PV communication issue)
4. **Devices with low transmission** — attenuators at partial insertion

**Example output:**

```
AWR Report — MFX — 2026-07-17 09:14
══════════════════════════════════════════════════════════════
POTENTIAL BLOCKERS (inserted / T=0):
  mfx_stopper_1       IN-BEAM    MFX:PPS:MMS:ST1       ←← BLOCKING?
  mfx_att_1           T=0.0%     MFX:ATT:COM:A1        ←← BLOCKING?

IN BEAM (expected):
  mfx_lens_1          IN-BEAM    MFX:MMS:LENS:1

OUT of beam (OK):
  mfx_target          OUT        MFX:MMS:TGT

Transmission summary: 0% (stopper in, attenuator at 0%)
══════════════════════════════════════════════════════════════
Beam is blocked. Check: mfx_stopper_1 (state: IN-BEAM).
```

---

## Phase 5: Diagnose

Interpret the results:

- **Stopper or MPS device IN-BEAM** — hard block; must be resolved by the
  operator with appropriate authorization.
- **Attenuator at T=0%** — check whether intentional (beam protection) or a
  misconfiguration.
- **Diagnostic IN-BEAM** — usually normal; most diagnostics are non-intercepting
  or thin foils.
- **All devices OUT, T~100%, beam still missing** — escalate to machine-level PVs
  (see `references/beam-status-pvs.md`).

Always cross-reference with the hutch operator before moving any device.

---

## Phase H: Hutch Readiness Check  *(hutch-side path)*

Checks the instrument-side state: optics out of beam, valves open, stoppers cleared,
DAQ configured and ready, and the data-flow path (XTC2 directory) accessible.
Run this phase for `/awr-hutch` or as the second half of a bare `/are-we-ready`.

### H.1 — Run hutch-readiness script (MFX)

For **MFX**, a dedicated hutch-readiness script is available at
`hutch-copilot/are-we-ready/scripts/check_hutch_ready_mfx.py`.

```bash
SCRIPT=/sdf/home/f/fpoitevi/.claude/skills/hutch-copilot/are-we-ready/scripts/check_hutch_ready_mfx.py
python3 -c "
import json, pathlib
code = pathlib.Path('$SCRIPT').read_text() + '\ncheck_hutch_ready()'
print(json.dumps({'code': code}))
" | ssh -o ConnectTimeout=60 -J psdev mfx-daq "python3 -c \"
import socket, json, sys
s = socket.socket()
s.connect(('localhost', 9999))
s.sendall(sys.stdin.buffer.read())
s.shutdown(socket.SHUT_WR)
data = b''
while True:
    chunk = s.recv(65536)
    if not chunk: break
    data += chunk
resp = json.loads(data.decode())
print(resp.get('output', resp.get('error', '')))
\""
```

For other hutches, generate an equivalent script using `happi` or `lightpath` device
objects available in that hutch's Python session.

### H.2 — What it checks

| # | Check | Type | Pass condition |
|---|-------|------|----------------|
| 1 | **Imagers / YAGs** | WARNING | All imagers (yag0, yag1, yag2, dg1_pim, dg2_pim, dia_pim) `.removed` |
| 2 | **Valves** | INFO | dg1×2, dia×2, dvd, mxt valve states reported |
| 3 | **Stoppers** | CRITICAL | `MFX:PPS:MMS:ST1:STATE` and `ST2:STATE` both `OUT` |
| 4 | **DAQ status** | CRITICAL | `daq.status()` → state not `Disconnected`; `daq.config_info()` → detector listed |
| 5 | **XTC2 path** | INFO | Data path `/sdf/data/lcls/ds/{hutch}/{experiment}/xtc2/` exists and is accessible |

Returns `True` if all CRITICAL checks pass.

### H.3 — Report format

```
Hutch Readiness Report — MFX — {timestamp}
══════════════════════════════════════════════════════════════
[1] Imagers:  yag0 OUT  yag1 OUT  yag2 OUT  ✓
[2] Valves:   dg1_v1 OPEN  dia_v1 OPEN  mxt OPEN  ✓
[3] Stoppers: ST1 OUT  ST2 OUT  ✓
[4] DAQ:      state=Ready  Detector: {detector_name} ({drp_nodes})  ✓
[5] XTC2:     {xtc2_path}  accessible  ✓
══════════════════════════════════════════════════════════════
PASS — hutch is ready for data collection.
```

### H.4 — Diagnose blockers

- **Stopper IN-BEAM** — hard block; requires operator + PPS authorization.
- **DAQ Disconnected** — the DAQ must be connected before data collection.
  Inform the user; do not attempt `daq.connect()` without explicit confirmation.
- **DAQ configured but no detector listed** — misconfigured DAQ session.
  Operator should reconfigure before running.
- **XTC2 path missing** — GPFS not mounted or experiment directory not provisioned.
  Escalate to LCLS computing support.
- **Imager IN-BEAM** — usually removable by operator; check if intentional.

---

## Known Limitations

- Device classification (blocking vs. diagnostic) may not be perfect — human
  judgment is required.
- `lightpath` may not have a full entry for every hutch.
- Devices in maintenance or with disconnected PVs will appear as `ERROR`.
- This is a point-in-time snapshot — states can change rapidly.

---

## → coordinate-experiment handoff (mandatory — runs after all checks complete)

After every AWR report is delivered, update the coordinate-experiment state JSON.
Update only the fields that were checked in this invocation.

### After beam checks (Phases 1–5):

```json
"machine_state": {
  "beam_present": {true|false},
  "last_checked": "{ISO timestamp}"
}
```

### After hutch checks (Phase H):

```json
"machine_state": {
  "daq_status": "{Ready | Running | Disconnected | …}",
  "detector_name": "{detector name from daq.config_info(), e.g. 'Jungfrau 16M'}",
  "hutch_ready": {true|false},
  "last_checked": "{ISO timestamp}"
}
```

### Log entry rules

- **Beam blocked or MPS fault**: append timestamped note to log.
- **Stopper IN-BEAM**: append note — stopper state, operator notified.
- **DAQ disconnected**: append note.
- **All checks pass**: no log entry (status read, not an operational event).

```markdown
- **{HH:MM}** AWR check: {beam blocked — {device} IN-BEAM | stopper ST1 IN | DAQ disconnected}. Operator notified.
```
