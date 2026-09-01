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
bin/hutch-bridge --hutch "$H" ping
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
bin/hutch-bridge --hutch "$H" exec 'beam_status.pulse_energy.get()'
```

For MFX there is a tested script that bundles pointing, imagers, valves, energy, undulator
pointing, slits and run number in one round-trip — prefer it, it is one bridge call
instead of many:

```bash
bin/hutch-bridge --hutch mfx run are-we-ready/scripts/check_beam_ready_mfx.py \
    --call 'check_beam_ready()' --timeout 60
```

> The nominal pointing setpoint inside that script is a hardcoded literal. It should come
> from `hutch-resolve preset --hutch mfx <mirror>` or from last-known-good state. Until it
> does, treat a pointing failure as "verify with the operator", not as ground truth.

---

## Step 3 — Hutch side

```bash
bin/hutch-resolve daq --hutch "$H"     # state, config_alias, experiment, recording, run
bin/hutch-state path --hutch "$H" --experiment "$E"   # includes the xtc2 path to stat
```

For MFX, the equivalent bundled script:

```bash
bin/hutch-bridge --hutch mfx run are-we-ready/scripts/check_hutch_ready_mfx.py \
    --call 'check_hutch_ready()' --timeout 60
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
