---
name: are-we-ready
description: >
  Readiness sub-skill for hutch-copilot. One dispatch running three checks:
  check-beam (is beam reaching the hutch, and what blocks it), check-hutch
  (are the hutch's own devices placed), check-daq (is the DAQ able to record).
  Analyzes all three and reports one verdict. All checks are read-only.
  Triggers on: /are-we-ready, are we ready, is beam blocked, beam path status,
  can we take data.
---

> Part of `hutch-copilot`: its rules apply here, and its commands run from that skill's
> directory (`bin/hutch-*`). If it is not loaded in this session, load it first.

# /are-we-ready

Read-only readiness. You move nothing.

Make exactly these three checks, then report one verdict. Which form follows the session's
`MODE:` (the hutch-copilot skill):

| `MODE: local` | `MODE: mcp`, in parallel |
|---|---|
| `bin/hutch-ready beam --hutch "$H"` | `ready_beam()` |
| `bin/hutch-ready hutch --hutch "$H"` | `ready_hutch()` |
| `bin/hutch-ready daq --hutch "$H" --experiment "$E"` | `ready_daq(experiment="$E")` |

Both forms return the same JSON. Nothing else: no other `bin/hutch-*`, no raw `caget`s,
no reading the tools' source, no switching mode. The verdict is in that JSON. Run all three
even when the question names only one — "beam is fine" is not readiness if the DAQ is
unallocated. One failing does not skip the others: it is that check's answer (rule 3), and
the report says so.

None needs a live hutch-python session: the DAQ check reads the DAQ's control port, the
other two Channel Access.

Four things the JSON does not say on its face:

- `impediment` is the first blocker along z, the one to act on. Everything downstream
  is blocked by consequence, so list length is not severity.
- **Unknown is not OUT.** `unreadable`, `needs_session` and `can_record: null` mean the
  value was not read. Never report one as a confident no, and say which.
- **`settled: false` means undetermined, not a fault.** Call `ready_beam(settle_s=30)`
  (`--settle-s 30` locally) before reporting anything — passing an unsettled read on as a fault looks machine-wide.
- **`experiment_mismatch` outranks a green DAQ.** It means the DAQ has a different
  experiment open than the one the operator named, and it deliberately does not change
  `can_record`. So this is the one case where every signal reads ready and the data still
  lands in the wrong experiment — usually a session left over from the previous shift.
  Report it with the impediment, never as a footnote.

Anything else unexpected: investigate with the hutch tools before reporting it, and say what
you checked. For a `needs_session` device, say it was not read; never guess a PV suffix.

Report by consequence, in this shape and this order:

```
**Ready: yes | no | unknown** — $H, $E, <retrieved_at>
Act on:   <impediment, or the DAQ fault, or "nothing">
Beam:     <impediment> at z=<z>; <n> blocked downstream by consequence | clear | unsettled
Hutch:    <n> devices: <n> read, <n> on the lightpath, <n> unknown (<names>)
DAQ:      <state>, can_record <true|false|unknown>, config <alias>, experiment <exp>
Unknown:  <every value not read, and why — or "none">
```

`no` when any check is a confident no: a settled blocker, `can_record: false`, or an
`experiment_mismatch` (which goes on the `Act on` line). `yes` only when beam is clear and
`can_record: true`. Anything else — an error, unsettled, `can_record: null` — is `unknown`,
never `yes`. This is a point-in-time snapshot; cross-check with the operator before
anything moves.

**Write nothing from an unsettled or unknown result.** If `settled` was false or
`can_record` was null there is no resolved value, and recording one launders a non-answer
into state a later reader trusts.
