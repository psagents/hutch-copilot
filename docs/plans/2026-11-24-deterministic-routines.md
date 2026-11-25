# hutch-copilot: from sub-skills to deterministic processes

## Context
The operator wants routine checks that run on their own, and wants more of each sub-skill
done by code than by the LLM. Today every sub-skill is prose that Claude re-executes step by
step each time, including:
- fixed sequences: verify XTC2, then watch for the LUTE job, then read sacct, then set state;
- gate tables, such as the DAQ state that allows a run;
- verdict rules computed from JSON (`are-we-ready`'s yes/no/unknown, plus "retry settle once").

Prose is slower, costs tokens and can be followed wrongly. Nothing watches the hutch between
questions, so a run ending, a DAQ fault or a finished analysis job goes unnoticed until
someone asks. Live example: on 2026-11-24 the DAQ was recording run 420 on mfx101923927 and
nothing in the copilot knew.

The decisions are made:
- Routines run **in the shift session**.
- Findings are reported **in the session and in the beamtime log**.
- The routines are **deterministic, not more LLM**, so no Workflow tool and no per-tick
  prompts.

**Principle.** Code owns sequences, gates, polling, and any verdict computable from tool JSON.
Prose keeps only interpretation:
- what an advisor's criteria say about a metric;
- turning operator words into run parameters;
- confirmations.

Every write still goes through its approval prompt. The watcher's only write is the beamtime
log, which needs no approval.

## What moves where
| Sub-skill | Becomes code | Stays judgment (prose) |
|---|---|---|
| `are-we-ready` | `hutch-ready all`: the three checks, the settle retry, `verdict` yes/no/unknown, `act_on` and the `unknown` list | investigating an unexpected reading |
| `take-run` | `hutch-run gate`: the DAQ-state gate table plus the experiment mismatch. `hutch-run verify --run N --since T`: XTC2 landed, LUTE job appeared and its state, then `last_run_number` set `--kind resolved` | run_type from the operator's words, parameters, confirmation, per-hutch run verbs |
| `assess-analysis` | `hutch-lute check --job ID` (Phase 3): sacct state, `output_exists`, `hit_wall_clock`, kill reason (UNRESPONSIVE vs `task_timeout`, grepped from the log), CPU time, and the parser recorded as `lute_parse`, run on the outputs | good enough / one knob / failed, from the advisor criteria; merge iteration choices |
| `sync-log` | the watcher appends machine events (runs, DAQ faults, beam, jobs) itself | decisions and conclusions only |
| `debug-daq`, `setup-analysis`, `live-shmem` | unchanged now; the watcher triggers `debug-daq`. Backlog: a `hutch-lute preflight` for the 4 submit blockers | as today |

## Phase 1: the watcher and `hutch-ready all`

### `hutch-ready all` (`src/hutch_copilot/readiness.py`)
- A new subcommand. It runs `beam`, `hutch` and `daq` via the existing functions and retries
  an unsettled beam read once with `settle_s=30`, as the skill prescribes today.
- It returns the same JSON plus `verdict`, `act_on` and `unknown`. The rules move verbatim
  from `skills/are-we-ready/SKILL.md`:
  - `no` on a settled blocker, on `can_record: false`, or on `experiment_mismatch`;
  - `yes` only when beam is clear and `can_record: true`;
  - anything else is `unknown`.
- MCP: `ready_all` in `server.py`, the same shape as `ready_daq`.
- `are-we-ready/SKILL.md` shrinks to: call `all`, relay it in the existing report shape, and
  keep the "unknown is not OUT" and "write nothing from unknown" rules.

### `bin/hutch-watch --hutch H --experiment E` (new `src/hutch_copilot/watch.py`)
- A plain loop. Its cadences are measured costs:

| Source | Cadence | Cost |
|---|---|---|
| `check_daq` | 30 s | 2.8 s per read |
| `beam_path` + `normal.diff` | 10 min | about 40 s |
| `watch_for_jobs` / `job_status` (`tools/lute.py:398,442`) for `{hutch}opr` since start | 2 min | — |

- Each tick reduces its reads to a small snapshot:
  - daq: `state`, `recording`, `run_number`, `last_run_number`, `can_record`, `config_alias`,
    `experiment_mismatch`;
  - beam: `impediment`, `settled`;
  - normal: the set of deviating device names;
  - lute: `{job_id: state}`.
- A pure function `changes(old, new) -> list[event]` produces the events. On each event the
  watcher:
  - prints one JSON line to stdout, `{"at","kind","detail","run"}`;
  - appends one plain-words line through `log.append` (not `--reconstructed`; a machine wrote
    it in real time).
- An unreadable source emits `unreachable` once and `reachable` on recovery. It never counts
  as a state change.
- **Restart-safe.** The last snapshot is persisted next to the state file (path from
  `common`'s psagents helpers, never written out). A restart emits only real differences, so
  re-arming never re-logs.
- It runs in-session under the **Monitor** tool with the maximum timeout, re-armed on expiry.
  Quiet ticks cost no tokens.
  - ponytail: local mode only. Cogito (MCP mode) would need a server-side watcher. Add it when
    Cogito shifts need it.

### Reactions: a short "Shift watch" section in `SKILL.md`
| Event | Action |
|---|---|
| `run_ended` | `hutch-run verify` (Phase 2; until then, the current `take-run` §4 steps) |
| `daq_fault` (`state: error`, or `can_record` true→false) | `/debug-daq` |
| `job_done` | `/assess-analysis` |
| `beam_changed` | report one line |
| `experiment_mismatch` | tell the operator first |
| anything else | nothing |

Start the watcher after `audit` at session start, once the experiment is known.

## Phase 2: `bin/hutch-run gate | verify` (new `src/hutch_copilot/runcheck.py`)
- **`gate`** returns `proceed`, or `stop` with a reason, from `check_daq`. The rule is the
  `take-run` §1 table: `configured` and `connected` proceed; `running`, `error` and
  `unallocated`/`allocated`/`reset` stop; `experiment_mismatch` or a null `config_alias` add a
  warning.
- **`verify`** composes `hutch-data xtc`, `watch_for_jobs`, `job_status` and `hutch-state set`
  into one JSON: XTC2 files and size, job id and state, and whether it recorded
  `last_run_number`. It never submits anything; a missing job is reported, not retried.
- The run itself stays `bin/hutch-py run` with its approval prompt. `take-run/SKILL.md`
  becomes: gate → parameters (judgment) → confirm and run → verify → report.

## Phase 3: `hutch-lute check --job ID` (`src/hutch_copilot/tools/lute.py`)
- One call returns:
  - from `job_status`: the sacct state and `hit_wall_clock`;
  - `output_exists`, checked on the paths status returns;
  - `kill_reason`, from regexes for the two LUTE kill messages already quoted in
    `assess-analysis` §2;
  - CPU time;
  - the parsed output from the recorded `lute_parse` command.
- `assess-analysis` §1–3 collapse into that call. §4/§4b (verdict, knob, merges) stay with
  the advisors.

## Critical files
- `src/hutch_copilot/readiness.py`, `src/hutch_copilot/server.py`;
- new `src/hutch_copilot/watch.py`, `src/hutch_copilot/runcheck.py`, `bin/hutch-watch`,
  `bin/hutch-run` (copy an existing 5-line shim);
- `tools/lute.py` (Phase 3);
- `SKILL.md`, and `skills/{are-we-ready,take-run,assess-analysis}/SKILL.md` (shrink);
- the registries `pyproject.toml`, `tests/unit/test_package_api.py` and
  `tests/unit/test_doc_commands.py`; `PLAN.md`.

Reuse rather than rewrite: `check_hutch`/`check_daq`/`beam_path`, `normal.diff`,
`watch_for_jobs`/`job_status`, `log.append`, `state` set/path, and `out`/`fail`/the exit codes
in `tools/common.py`.

## Verification
- **Unit tests:**
  - `verdict` truth table (`no`/`yes`/`unknown`, mismatch outranks green);
  - `changes()` (run start/end, fault, unreachable emitted once, restart with a persisted
    snapshot emits nothing);
  - `gate` table;
  - `check` kill-reason regexes.
- Run with `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q`.
- **Live on mfx-daq:**
  - `hutch-ready all --hutch mfx --experiment <E>` agrees with today's `/are-we-ready`
    output.
  - Start `hutch-watch` under Monitor while the DAQ is running. A run end should produce one
    `run_ended` event and one beamtime-log line. Killing and restarting the watcher should add
    no duplicate line.
  - `bin/hutch-run verify --run <last run>` reports XTC2 and the LUTE job, the same as the
    manual §4 steps.
