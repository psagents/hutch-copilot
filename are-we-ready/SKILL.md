---
name: are-we-ready
description: >
  Readiness sub-skill for hutch-copilot. One dispatch running three checks:
  check-beam (is beam reaching the hutch, and what blocks it), check-hutch
  (are the hutch's own devices placed), check-daq (is the DAQ able to record).
  Analyzes all three and reports one verdict. All checks are read-only.
---

# /are-we-ready

Read-only readiness. You move nothing.

Run all three, then report one verdict:

```bash
bin/hutch-resolve check-beam  --hutch "$H"
bin/hutch-resolve check-hutch --hutch "$H"
bin/hutch-resolve check-daq   --hutch "$H" --experiment "$E"
```

Run all three even when the question names only one — "beam is fine" is not readiness
if the DAQ is unallocated. A failure in one does not skip the others.

`check-daq` is the only one needing a live session; the other two read Channel Access
directly and work with nothing running.

Three things the JSON does not say on its face:

- `impediment` is the first blocker along z, the one to act on. Everything downstream
  is blocked by consequence, so list length is not severity.
- **Unknown is not OUT.** `unreadable`, `needs_session` and `can_record: null` mean the
  value was not read. Never report one as a confident no, and say which.
- **`settled: false` means undetermined, not a fault.** Re-run with a longer
  `--settle-s` before reporting anything — passing an unsettled read on as a fault
  looks machine-wide.

Anything else unexpected: investigate with the tools before reporting it, and say what
you checked. For a `needs_session` device, read it in-session rather than guessing a PV
suffix.

Report by consequence — impediment first, then blockers, then what is unknown, then
the DAQ. Name the one thing to act on. This is a point-in-time snapshot; cross-check
with the operator before anything moves.

**Write nothing from an unsettled or unknown result.** If `settled` was false or
`can_record` was null there is no resolved value, and recording one launders a non-answer
into state a later reader trusts.
