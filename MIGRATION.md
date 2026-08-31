# Refactor: mechanism into tools, knowledge into resolvers

Branch: `ENH/tool-layer-refacto`

## The diagnosis

hutch-copilot was ~2,300 lines of markdown procedure and ~1,100 lines of Python, and the
ratio was backwards. The markdown was doing three jobs that need different homes:

1. **Mechanism** — how to reach a machine, where a file lives, how to parse a log.
2. **Policy** — what needs confirmation, what is beam-critical, what gets logged.
3. **Judgment** — is this reading physical, which knob next, is the sample dying.

Only (3) needs a model. Because all three were prose, (1) drifted and could not be tested,
(2) was restated inconsistently per sub-skill, and (3) was buried under the other two.

The concrete evidence, all of it live on `main` before this branch:

| Symptom | Consequence |
|---|---|
| The state-file path was written out in prose in 6 places and **3 were wrong** (`{exp}/{exp}_state.json` instead of `{exp}/results/psagents/{exp}_state.json`) | `are-we-ready`, `analyze-data` and `refine` read a file nothing wrote, so "do not re-ask the user" silently became "re-ask for everything" |
| The SSH+socket bridge blob was copy-pasted verbatim in **6 places**, 2 of them hardcoding `/sdf/home/f/fpoitevi/...` | The skill was broken on arrival for its second user |
| `/align-spectrometer` dispatched to `commands/align-spectrometer.md`, which does not exist. `@daq-logs` and `@confluence-doc` do not exist either | Dead ends mid-operation |
| DAQ state, XTC2 arrival and job polling each described twice, in different voices | Two implementations to keep in sync, by hand, in prose |
| `are-we-ready` Phase 3 embedded ~75 lines of Python **in markdown** for the model to write out and `exec` | Never tested, guaranteed to rot |
| `references/hutches/` had exactly one file; `sfx-analysis-defaults.md` opened by telling the reader not to trust it | Knowledge files that everyone already knew were stale |

Note the pattern: every one of those is the same bug class — something accepted
permissively and then silently wrong. Same class as the phil template accepting any
`phil_parameters` key and dropping the ones it does not reference.

## Why knowledge became resolvers, not data files

The first draft of this plan proposed per-hutch YAML for device lists, PV names and pass
conditions. That was the same mistake one format down: a hand-maintained cache of something
authoritative, which goes stale silently.

Campaign evidence (`sfx-hutch-copilot-integration`, 10 iterations on mfx101624926 r36):
**every value read from its source was right first time** — detector address from
`psana2 detinfo`, unit cell from `CRYST1`. **Every value a human had pre-encoded into text
was wrong** — the mask path was missing its `.mask` extension, and the unit-cell filter
target was 2.2%/2.7% off the observed cell for the entire campaign.

So `hutch-resolve` asks the authoritative source and returns `{value, source, retrieved_at}`.
Three rules make it work rather than just moving the problem:

1. **Provenance is the deliverable.** `--kind resolved|operator|inferred` distinguishes a
   measurement from a claim. The old schema had `_timestamps` but no source field, so a
   value typed at 03:00 looked identical to one read off the machine.
2. **Per-field TTL, and stale reads fail.** A device list is good for hours, a detector
   alias for one run, a PV value for seconds, an operator-declared sample until it changes.
   One static file cannot carry four lifetimes.
3. **No silent fallback to a literal.** A resolver that cannot reach its source raises.

---

## What landed

### `hutchlib/` — the tool layer (stdlib only, Python 3.9+)

| Module | Responsibility |
|---|---|
| `paths.py` | the ONE definition of every hutch-copilot path; `skill_root()` self-resolves |
| `state.py` | provenance + per-field TTL, atomic writes, lock-serialised concurrent writers |
| `safety.py` | action classification; understating a class is a refusal, not a downgrade |
| `bridge.py` | one implementation of the ipython / psdev / controls bridges |
| `resolvers.py` | devices, device, pv, beampath, detectors, daq, conf, preset, cell |
| `traps.py` | executable incident register + the phil-template whitelist extractor |
| `lute.py` | task-agnostic LUTE submission adapter, Kerberos scoping, job status |
| `cli.py` | `hutch-state`, `hutch-bridge`, `hutch-resolve`, `hutch-lute` — JSON out |

Entry points in `bin/`, no install step required.

### Bugs fixed

- State path unified behind `hutch-state path`; the three wrong prose copies are gone.
- Bridge blob replaced by `hutch-bridge`; the three `fpoitevi` paths are gone.
- Safety classification is enforced before transmit instead of being a table to remember.
- Kerberos is scoped: `hutch-lute kerberos` asks whether the config actually touches the
  eLog. `setup.md`'s unconditional gate would have blocked the whole campaign.
- `run_type` uppercase is an assertion (`hutch-lute traps --scope run-params`).
- Dangling `/align-spectrometer`, `@daq-logs`, `@confluence-doc` references corrected.

### Files deleted

| Deleted | Replaced by |
|---|---|
| `references/beam-status-pvs.md` (133 lines) | `hutch-resolve pv` — happi *is* the PV registry |
| `references/hutches/mfx.md` (144 lines) | `hutch-resolve devices/conf/preset`; the file itself said `@ask-lute` held the real copy |
| `analyze-data/references/sfx-analysis-defaults.md` (166 lines) | `analyze-data/references/sfx-cctbx-patterns.md` |

The replacement file keeps only what is genuinely written knowledge — the `!run_type` DAG
pattern, the `d_min` ranking hazard, and the coverage-vs-yield warning — and drops the
tables of "typical values". One of those tables was not merely stale but actively
misleading: it said an indexing rate below 5% means geometry is wrong, whereas the
campaign's 3.1% baseline was caused by a hit-finder floor set to 40, and geometry was fine.

### `are-we-ready` — the exemplar thin skill

417 lines → 142. Routing, what to look at, how to judge it. The 75 lines of inline Python
are gone; `hutch-resolve beampath` uses `lightpath`'s own `blocking_devices` and reports
when it fell back instead of falling back silently.

### Tests

`tests/unit/` — 67 tests, all passing:

```bash
source /sdf/group/lcls/ds/ana/sw/conda2/manage/bin/psconda.sh
python -m pytest tests/unit -q
```

They cover: the canonical path, provenance requirements, TTL/staleness, atomicity, 4-way
concurrent writers, every safety class including the refusal-on-understatement, and every
trap firing on the configuration that caused it.

Verified against real artifacts, not only fixtures:

```bash
bin/hutch-resolve cell --pdb /sdf/data/lcls/ds/mfx/mfx101624926/results/pam/1dpx.pdb
#   -> 77.05 77.05 37.21 / P 43 21 2, matching the campaign
bin/hutch-lute kerberos --yaml <campaign iter10 yaml>
#   -> needs_kerberos: false
bin/hutch-lute traps --yaml <campaign iter10 yaml>
#   -> [] (clean, correctly — that config ran)
bin/hutch-lute sbatch --task CCTBXIndexer --time 00:10:00 --nodes 4 --ntasks-per-node 60 ...
#   -> reproduces the campaign's hand-written submit_task.sh from parameters
```

---

## What remains, in order

1. **`lute-config`** — render + `--set TASK.block.key=value` + validate. Today
   `setup-cctbx-pipeline/render.py` has no override facility, which is why the campaign
   needed 250 lines of YAML text surgery (`render_iter.py`). Wire
   `traps.template_whitelist()` in so an unknown phil key is a config-time error. This is
   the highest-value remaining item.
2. **`lute-stats --json`** — logs → metrics with a declared schema, reporting files scanned.
   The anchors are fixed strings in CCTBX source. Must include the indexing funnel and must
   never report a total without saying what it counted: the campaign's own harness silently
   truncated two rows because `timeout 120 cat | awk` sends SIGTERM to `cat`, so `pipefail`
   never fired and `awk` printed a plausible partial.
3. **`hutch-jobs watch`** — supersedes `take-run` Phase 5's prose squeue/sacct polling.
4. **`hutch-resolve runs --exp --tag`** — eLog run/tag resolution. Deliberately not stubbed:
   it needs Kerberos and belongs with `elog-search`, and a stub that looks like a resolver
   would be worse than its absence.
5. **Thin the remaining skills** against the tools: `take-run` (395), `analyze-data` +
   commands (514), `coordinate-experiment` (585). Target 80–120 lines each. In particular
   `coordinate-experiment`'s context-propagation table becomes unnecessary — if sub-skills
   resolve what they need, nothing needs injecting.
6. **`refine.md` R.2** still asks the user to run `h5py` and paste the output. Any
   instruction of that shape is a missing tool; it becomes `lute-stats` once (2) exists.
7. **De-duplicate `bridge-to-cds/` against the standalone `cds-bridge` skill**, and move the
   nominal pointing setpoint out of `check_beam_ready_mfx.py` into
   `hutch-resolve preset`.
8. **Extend the scenario harness.** `tests/sfx_basic/` (13 steps, assertions + mocks) is the
   best thing in the repo and should keep testing what it is good at — dispatch, safety
   gating, handoff. Its `expected_lute_checks.yaml` will need updating for the tool calls.

## Compatibility

- `state.py` mirrors every write into `_timestamps`, so pre-refactor readers keep working.
- Nothing in `hutchlib` imports outside the standard library except `resolvers.detectors`,
  which shells out to psana2 under psconda.
- `HUTCH_DS_ROOT` overrides the data root, for tests and for the scenario harness.

## Try it

```bash
bin/hutch-state  path   --hutch mfx --experiment mfxl1013621
bin/hutch-state  audit  --hutch mfx --experiment mfxl1013621
bin/hutch-bridge --hutch mfx classify 'mfx_stopper_1.remove()'
bin/hutch-bridge --hutch mfx ping
bin/hutch-resolve devices --hutch mfx
bin/hutch-lute   traps
```
