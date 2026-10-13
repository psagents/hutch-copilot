---
name: sync-log
description: >
  Record what a turn established into the beamtime log and the experiment state file.
  Invoked by the Stop hook (bin/hutch-stop) at the end of any turn that touched the
  experiment, or by the operator as /sync-log. Reads the session transcript from the
  watermark forward, never whole.
---

# Sync Log (`sync-log`)

You are writing the record a later shift reads. Two destinations, and the split is
already drawn in the tools: **`state` stores facts; `log` records events.**

This runs at the end of a turn, so you usually already know what happened — you did it.
Read the transcript only to recover what has fallen out of your context.

---

## 1. Find your place

```bash
bin/hutch-state get sync_log.last_uuid --hutch $H --experiment $E --value-only
```

Missing (rc=3) means nothing has ever been synced for this experiment. Then sync only
the current turn, not the whole session — a first sync that reconstructs a week of
history from a transcript invents more than it records.

**Never read the transcript whole.** A one-month session file measured 1.8 MB; loading it
destroys the context this pass exists to preserve. Read forward from the watermark only,
and extract text and tool calls rather than raw JSON — e.g.

```bash
awk '/"uuid":"<last_uuid>"/{found=1; next} found' <transcript> | tail -40
```

---

## 2. Open the log if it is not open

`append` refuses without a header, by design — a log with no date or experiment is not a
log. So on the first entry of a beamtime:

```bash
bin/hutch-log init --hutch $H --experiment $E --shift-date $(date +%F)
```

Idempotent; it never overwrites an existing log.

---

## 3. Events → the log

One bullet per thing that happened, machine-timestamped:

```bash
bin/hutch-log append --hutch $H --experiment $E --reconstructed \
    "Run 43 (DARK) complete, 12k events, XTC2 confirmed on disk"
```

`--reconstructed` marks the entry as written after the fact, which is exactly what this
pass is. A reader can then tell it from a note typed in real time.

What is an event: a run started, finished or failed; a sample changed; beam lost or
restored; a DAQ fault diagnosed and what fixed it; an analysis submitted or its verdict;
a decision the operator made and why. **What is not:** that you ran a readiness check and
everything was normal. A log of non-events is a log nobody reads.

Write for the person arriving at 03:00 who was not here. Name the run, the number, the
consequence. "Deadtime high" is not an entry; "Run 51 aborted, 40% deadtime traced to
epix10k2m, recovered after reconfigure" is.

---

## 4. Facts → the state file

Anything *resolved* during the turn, with provenance that is not optional:

```bash
bin/hutch-state set last_run_number 43 --hutch $H --experiment $E \
    --source "hutch-daq state --hutch mfx" --kind resolved
```

`--kind` follows the rule in the parent `SKILL.md` without exception:

- `resolved` — a tool read it from its source. `--source` names the invocation precisely
  enough to re-run.
- `operator` — a human said so. Intent, not measurement.
- `inferred` — you reasoned it out. **A value you read off your own earlier prose with no
  tool behind it is `inferred`**, and labelling it `resolved` because it looks like a
  reading is the one failure that makes the whole state file untrustworthy.

Do not re-resolve by re-reading the transcript when a tool can answer now. If a value
matters and you are unsure of it, call the tool again.

---

## 5. Advance the watermark

Last, and only after the writes above succeeded:

```bash
bin/hutch-state set sync_log.last_uuid <uuid of the last line you read> \
    --hutch $H --experiment $E --source "sync-log: transcript <session_id>" --kind resolved
```

This is what makes the pass idempotent: the next Stop hook finds nothing unsynced and
stays silent. Skipping it means re-logging the same events next turn.

---

## Two prohibitions

- **Never log a conclusion the transcript does not support.** An ambient logger that
  invents detail is worse than none, because it is read as a record. If the turn
  established nothing, write nothing and say so in one line.
- **Never restructure the log.** It is append-only and the tool enforces it. The history
  of a shift is not improved by tidying.

Then end the turn. The hook will not ask twice.
