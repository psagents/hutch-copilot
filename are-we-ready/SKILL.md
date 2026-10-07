---
name: are-we-ready
description: >
  Beampath and hutch readiness sub-skill for hutch-copilot. Checks whether beam
  is unobstructed (beam side: lightpath device states, stoppers, attenuators,
  pointing, energy) and whether the hutch is ready for data collection (hutch
  side: imagers, valves, stoppers, DAQ, XTC2 path). All checks are read-only.
  Triggers on: /are-we-ready, /awr, /awr-beam, /awr-hutch, are we ready,
  is beam blocked, beampath status, why don't we see beam, what's blocking the
  beam, are we ready beam wise, are we ready hutch wise, is the hutch ready,
  is the DAQ ready, are the data flows up.
---

# /are-we-ready

Read-only readiness checks. You move nothing.

**All mechanism is in `hutch-tools`** — see the Tool Layer section of `../SKILL.md`. This
file contains only routing, what to look at, and how to judge it. It deliberately ships
no device lists, no PV names and no pass-condition constants: those are resolved.

---

## Routing

| Trigger | Run |
|---|---|
| `/awr-beam`, "beam ready", "machine ready", "accelerator side" | Beam side, then handoff |
| `/awr-hutch`, "hutch ready", "daq ready", "data flows", "instrument side" | Hutch side, then handoff |
| `/are-we-ready`, `/awr` (bare) | Both, one combined report with two labelled sections |

---

## Step 1 — Context and connectivity

```bash
bin/hutch-state get --hutch "$H" --experiment "$E" hutch --value-only   # confirm, don't re-ask
bin/hutch-daq --hutch "$H" ping
```

If `hutch` is unknown, ask once. If `ping` reports `"mode": "offline"`, you cannot check
anything live: say so, offer the setup steps from `../SKILL.md`, and if you quote any state
value use `--stale-ok` and **report its age**. Never present a remembered value as current.

---

## Step 2 — Beam side

```bash
bin/hutch-resolve beampath --hutch "$H"
```

Returns `{blocking: [...], devices: [{name, state, transmission}], method}`. `blocking` is
lightpath's own determination where available; `method` says whether the fallback was used —
report it if it was.

If a device name in the report is unfamiliar, or you need the beamline inventory ordered
along the beam, resolve it — do not recall it:

```bash
bin/hutch-resolve devices --hutch "$H"          # full inventory, sorted by z
bin/hutch-resolve device  --hutch "$H" <name>   # one happi entry
```

Then the two CRITICAL machine values. Resolve the PV rather than typing one:

```bash
bin/hutch-resolve pv --hutch "$H" <device> --suffix <field>   # -> composed PV + provenance
bin/hutch-daq --hutch "$H" exec 'beam_status.pulse_energy.get()'
```

### Beam destination — where the mirror is pointing

```bash
bin/hutch-resolve beampath --hutch "$H"                 # lightpath's verdict (Step 2 above)
bin/hutch-resolve preset  --hutch mfx mr1l4_homs        # the setpoint, from its own file
```

Read the setpoint; do not carry one in your head. The MFX script this replaced hardcoded
`MFX_PITCH = -562.035`, and a hand-encoded setpoint that has drifted reports a correctly
pointed beam as a failure — or worse, the reverse. If `preset` has no entry, say the setpoint
is unverified and ask the operator; do not fall back to a literal.

### Undulator pointing and stoppers — EPICS, one call

```bash
bin/hutch-resolve caget \
    BPMS:UNDH:4690:XOFF.D BPMS:UNDH:4690:YOFF.D \
    MFX:PPS:MMS:ST1:STATE MFX:PPS:MMS:ST2:STATE
```

Read locally: CA reaches the IOCs from the hutch machines — the script this replaced read
these from the DAQ machine.

### Imagers, valves, slits, pulse energy — one session round-trip each

No resolver owns these yet, so batch them:

```bash
bin/hutch-daq --hutch mfx exec '[(d, mfx_dg1_valve_1.state.get()) for d in [1]]'   # pattern
bin/hutch-daq --hutch mfx exec 'beam_status.get()'
```

Compose one snippet per group rather than one per device; each `exec` is a round-trip. Report
an unreadable device as an ERROR, never as OUT — see Step 4.

---

## Step 3 — Hutch side

```bash
bin/hutch-resolve daq --hutch "$H"     # state, config_alias, experiment, recording, run
bin/hutch-daq --hutch "$H" config      # the DAQ config file, verbatim
```

Has the last run's data landed?

```bash
bin/hutch-resolve xtc2 --hutch "$H" --experiment "$E" --run <r>   # present, count, size
```

If a DAQ process is down and you need to know why, the log is readable even after a crash:

```bash
bin/hutch-daq --hutch "$H" logs --process <name>      # newest files first
bin/hutch-daq --hutch "$H" logs --process <name> -n 50
```

Cross-check `daq.experiment` against the state `experiment`. A mismatch is a warning to the
operator, never something to silently accept.

---

## Step 4 — Report and judge

Group by consequence, not by check order:

```
AWR — {HUTCH} — {timestamp}      [bridge: live | offline, values age {n}s]
══════════════════════════════════════════════════════════════
BLOCKERS      {device}  {state}  {pv}
EXPECTED IN   {device}  {state}
OUT / OK      {n} devices
ERRORS        {device}  (PV unreachable — not the same as OUT)
──────────────────────────────────────────────────────────────
{one-line verdict}
```

Judgment — this is the part that is yours, not the tool's:

- **Stopper or MPS device in beam** — hard block, operator + PPS authorization.
- **Attenuator at T=0%** — decide whether it is deliberate protection or a misconfiguration;
  say which you think it is and why.
- **Diagnostic in beam** — usually fine; most are non-intercepting or thin foils.
- **Everything out, T≈100%, still no beam** — escalate to machine level. Resolve the
  beam-presence PV for the hutch rather than recalling one.
- **`ERROR` is not `OUT`.** A device whose PV did not answer is unknown. Say so.
- This is a point-in-time snapshot, and device classification needs human judgment —
  cross-check with the operator before anything moves.

---

## → coordinate-experiment handoff (mandatory)

Write only what this invocation actually checked, with provenance:

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" machine_state.beam_present true \
    --source "lightpath via $(hostname) beampath resolver" --kind resolved
bin/hutch-state set --hutch "$H" --experiment "$E" machine_state.daq_status Ready \
    --source "daq.state via mfx-daq" --kind resolved
```

`machine_state.*` carries a 600 s TTL, so a later reader is told when these have gone stale
rather than trusting them silently.

Log an entry only for operational events — beam blocked, stopper in, DAQ disconnected. All
checks passing is a status read, not an event, and gets no log entry.
