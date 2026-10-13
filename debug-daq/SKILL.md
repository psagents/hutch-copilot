---
name: debug-daq
description: >
  DAQ fault sub-skill for hutch-copilot. Collects the readings a DAQ diagnosis
  starts from -- state, the config that actually launched, the log sessions --
  and hands them to the @psana-daq advisor, which owns the diagnosis. Holds no
  diagnostic knowledge of its own. Read-only.
---

# /debug-daq

You collect readings and hand them over. **You do not diagnose.** Which angle a fault
belongs to, what an error line means, whether a deadtime number is high — all of that is
`@psana-daq` and the leaves it routes to. This file exists so the advisor does not have to
ask for what this session already knows.

## 1. Collect

```bash
bin/hutch-daq --hutch "$H" state
bin/hutch-daq --hutch "$H" config
bin/hutch-daq --hutch "$H" logs --sessions      # when the scope is a past window
```

`state` reads the DAQ's control port directly — no hutch-python session needed. It returns
`transition`, `state`, `config_alias`, `recording`, `bypass_activedet`, `experiment`,
`run_number` and `last_run_number`.

Ask for **scope** once if the operator has not already said it: live, or a past date/time
window. Past scope means picking a session from `--sessions`: the `DD_HH:MM:SS` prefix is
the session id, and the newest is not necessarily the one being asked about — show the
list and let the operator choose.

A `DaqError` here is a reading you could not take, not a fault you have found. Report which
one failed and hand over anyway: the control leg `@psana-daq` routes to is built to work
when everything else is down, and an unreachable control port is itself the most useful
thing you know.

## 2. Hand over

Invoke `@psana-daq`, stating hutch and scope explicitly — that is the form its router asks
for — and pass on:

- `hutch`, and `$E` as the experiment the operator named.
- every field from `state`, verbatim, with `source` and `retrieved_at`.
- the `path` from `config` — the leaf config that launched this DAQ. The advisor otherwise
  has to ask the operator, because `daqmgr` takes it as an argument and is not resident.
- the session prefix, if the scope is a past window.
- `experiment_mismatch`, if `state`'s `experiment` is not `$E`. That outranks most faults:
  the DAQ can look healthy and still write to the previous shift's experiment.

If `@psana-daq` cannot be loaded, the required-advisor contract in `SKILL.md` applies
unchanged: report every reading with its provenance, say plainly that the advisor is
missing, give the operator `scripts/fetch-advisors.sh` and the need to restart afterwards,
and **do not produce a verdict**. No remembered threshold, no error line you interpreted
yourself.

## 3. Write nothing

A diagnosis is the advisor's conclusion, not a resolved reading of yours — recording it
would launder it into state a later reader trusts. If the operator wants it kept,
`@psana-daq` offers its own persistence through `psana-daq-history`.
