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

### `hutch-tools/` — the tool layer (stdlib only, Python 3.9+)

| Module | Responsibility |
|---|---|
| `paths.py` | the ONE definition of every hutch-copilot path; `skill_root()` self-resolves |
| `state.py` | provenance + per-field TTL, atomic writes, lock-serialised concurrent writers |
| `safety.py` | action classification; understating a class is a refusal, not a downgrade |
| `bridge.py` | one implementation of the ipython / psdev / controls bridges |
| `resolvers.py` | devices, device, pv, beampath, detectors, daq, conf, preset, cell |
| `traps.py` | executable incident register + the phil-template whitelist extractor |
| `lute.py` | task-agnostic LUTE submission adapter, Kerberos scoping, job status |
| `_cli.py` | the shared output contract only: `out`, `fail`, exit codes, common args |

Internal imports name what they use -- `from .traps import check as check_traps`, not
`from . import traps` plus `traps.check(...)` at the call site. The dependency is then visible
in the import block rather than buried in call sites, and it made the `build_sbatch` trap test
a plain `monkeypatch.setattr(lute, "check_traps", ...)` instead of reaching into
`build_sbatch.__globals__["traps"]` and restoring it by hand. A test asserts no
`from . import` form comes back.

`__init__.py` exposes a curated public API (`from hutch_tools import State, classify, build`)
via PEP 562 `__getattr__`. It **must** stay lazy: importing any submodule executes
`__init__.py`, so an eager `from .config import build` there would pull jinja2 and PyYAML into
`import hutch_tools.state` and a syntax error in `config.py` would break `hutch-state` again. A
test pins the exact module set loaded by `import hutch_tools.state` to
`{_cli, paths, state}`.

Each module also exposes `build_parser()` separately from `main()`, which makes the CLI
surface introspectable. That exists to support a doc-drift guard: every `bin/hutch-*`
invocation in the markdown is parsed and its subcommand and flags checked against the real
parsers. It immediately caught two drifts introduced during this refactor --
`hutch-resolve cell --pdb F` when the CLI takes a positional path, and `hutch-lute config`
after config became its own binary.

Each module owns its own `main(argv)`, so the argparse spec for `hutch-state` sits next to
`State` and adding a flag is a one-file change. There is no central `cli.py`: it imported
every module at module level, so **a syntax error in `config.py` broke `hutch-state`**, and
`hutch-state` paid ~25 ms importing `resolvers` and `bridge` it never uses. Verified after
the split: breaking `config.py` leaves `hutch-state` and `hutch-lute` at rc=0 and fails only
`hutch-config`.

Config rendering is its own binary (`hutch-config`) rather than a `hutch-lute` subcommand. It is a
different lifecycle phase from submission, and it is the only module needing jinja2 and
PyYAML — isolating it keeps the other four stdlib-only.

`pyproject.toml` declares metadata, the optional `config`/`dev` extras, pytest settings
(`pythonpath = ["."]`, which removed the `conftest.py` sys.path hack) and ruff settings.
**Installation stays optional**: `bin/hutch-*` work from a bare checkout, because a skill is
loaded by path and requiring `pip install` would recreate the "broken on arrival for the
second user" failure this refactor removed. `[project.scripts]` is a convenience for CI and
developers, not the only route. The ruff section is declared but unverified — ruff is not
present in psconda (a `.ruff_cache` from 0.12.5 exists, so someone runs it elsewhere).

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
bin/hutch-resolve cell /sdf/data/lcls/ds/mfx/mfx101624926/results/pam/1dpx.pdb
#   -> 77.05 77.05 37.21 / P 43 21 2, matching the campaign
bin/hutch-lute kerberos --yaml <campaign iter10 yaml>
#   -> needs_kerberos: false
bin/hutch-lute traps --yaml <campaign iter10 yaml>
#   -> [] (clean, correctly — that config ran)
bin/hutch-lute sbatch --task CCTBXIndexer --time 00:10:00 --nodes 4 --ntasks-per-node 60 ...
#   -> reproduces the campaign's hand-written submit_task.sh from parameters
```

---

### Skill restructure — applied

The test: every sub-skill gets four sections (routing / tool calls in order / **decision
points** / handoff), and **if the decision-points section is empty it is not a sub-skill.**

| Sub-skill | Before | After | Why |
|---|---|---|---|
| `are-we-ready` | 417 | 142 | judgment kept: ERROR is not OUT, deliberate-vs-misconfigured attenuator, when to escalate |
| `take-run` | 403 | 154 | judgment kept: the DAQ-state gate, the confirmation, the triage table. Phases 4–5 became `hutch-lute watch` |
| `analyze-data/commands/setup.md` | 293 | 146 | the field-by-field wizard and the heredoc writes became `hutch-config`; the DAG design and the detector-alias human gate stayed |
| `coordinate-experiment` | 588 | 462 | dropped the duplicated state schema and the context-propagation table; ambient classification, `/sync-log` and `/handoff` are irreducibly model work |
| **`bridge-to-cds`** | 313 | **deleted** | no decision points at all — pure mechanism, and it duplicated both `hutch-resolve` and the standalone `cds-bridge` skill. Its topology table now lives in `hutch_tools/bridge.py`, next to the code it explains |

Markdown total 2,303 -> 1,470 lines. `coordinate-experiment` lands well above the ~200 first
estimated, and should: `/sync-log`, `/handoff`, ambient classification and the note-taking
conventions are ~250 lines of genuine judgment. Cutting to a target number would have removed
content the tools cannot replace.

Root dispatch is now split by whether judgment is needed. `/context` is marked **Tool only**:
it is `hutch-state audit` plus formatting, and the audit is strictly better than the prose
template it replaces because it reports each field's age and provenance.

### Two more bugs found while restructuring

- **`coordinate-experiment` Step 1b instructed `ssh psdev caget BLD:SYS0:500:ENERGYHXBR ...`,
  which cannot work.** EPICS CA to IOCs is firewalled from psdev — the skill's own topology
  notes said so while this section instructed otherwise. PV reads must run on
  `{hutch}-control`. Now `hutch-resolve caget`, with the hardcoded PV names gone.
- **`resolvers.cell()` disagreed with `setup-cctbx-pipeline`'s `read_cryst1` on float
  formatting** (`90` vs `90.0`). That skill's validation greps the unit-cell string verbatim,
  so the spelling is load-bearing. Aligned and pinned with a test. Its docstring advertises a
  format its code does not produce.

Deleting hutch-copilot's `references/hutches/mfx.md` was right for a second reason:
`ask-lute/references/hutches/` carries all five hutches and is the proper owner, which the
deleted file itself said.

---

### Self-contained: nothing load-bearing outside this repo

Eleven of the fourteen sibling skills this skill referenced had been removed, and the
CCTBX jinja template lived in one of them (`setup-cctbx-pipeline`), which left
`hutch-config` with nothing to render.

| Was outside | Now |
|---|---|
| `setup-cctbx-pipeline/templates/cctbx_pipeline.yaml.j2` | `templates/cctbx_pipeline.yaml.j2`, and `--template` defaults to it |
| `setup-cctbx-pipeline/render.py` `read_cryst1` | `hutch-resolve cell` |
| `setup-cctbx-pipeline/render.py` `make_env` delimiters | `config.DELIMITERS["lute"]` |
| `setup-cctbx-pipeline/render.py` 7 validation assertions | `traps.py`, plus five more the campaign earned |
| `cds-bridge` recipes | `hutch-resolve cat / conf / preset / device / pv / caget` |
| eleven dead `@advisor` pointers | removed, replaced with in-repo guidance |

**The template was reconstructed, and the reconstruction is proven rather than asserted.**
The campaign's nine rendered configs survive, so `tests/unit/test_template.py` re-renders
each one from its recorded context and overrides and diffs against the file on disk. All
nine match. Two comment lines are normalised (the template now credits hutch-tools, not
skills that no longer exist); every key and value is compared strictly.

Two optional blocks are **reconstructed but unverified**, because the campaign passed
`None` for them and so no rendering in the corpus exercises them: the per-shot wavelength
calibration in `data_spec`, and `select_min_ct` / `select_max_ct`. Both are marked
`UNVERIFIED` in the template and a test asserts those markers stay.

One design detail the reproduction forced: the template must still emit
`MergeCCTBXXFEL.phil_parameters.tag`, so that the Kerberos route-around shows up as a
`deleted` override rather than `absent`, and so `hutch-lute kerberos` has something to
detect. Fidelity here is behavioural, not only textual — a test pins it for all nine
iterations. Relatedly, the explanatory comment about `tag` lives in the block header
rather than directly above the key: a comment above a commonly-deleted key is left
orphaned when the key goes, which is exactly how the first reconstruction attempt failed.

Three sibling skills remain, reclassified as **optional advisors** — `ask-lute`,
`ask-cctbx-xfel`, `ask-happi`. They are consultants, not dependencies: the skill must work
with none of them installed, and must never block on one or assume a value one would have
supplied. `tests/unit/test_self_contained.py` enforces the boundary:

- every at-prefixed name in the docs must be in `DECLARED_ADVISORS`;
- no code path may reference another skill directory;
- no code path may hardcode a home directory (docstrings documenting the historical
  `fpoitevi` bug are stripped via the AST first, so prose about the bug does not trip the
  check aimed at code);
- the default template must ship with the repo, render, parse, and pass the trap register.

Both guards were verified to actually fail on an injected violation, not merely to pass.

---

## What remains, in order

1. **`lute-stats --json`** — logs -> metrics with a declared schema, reporting files scanned.
   The anchors are fixed strings in CCTBX source. Must include the indexing funnel and must
   never report a total without saying what it counted: the campaign's own harness silently
   truncated two rows because `timeout 120 cat | awk` sends SIGTERM to `cat`, so `pipefail`
   never fired and `awk` printed a plausible partial. This is now the highest-value item.
2. **`refine.md` R.2** still asks the operator to run `h5py` and paste the output. Any
   instruction of that shape is a missing tool; it becomes `lute-stats` once (1) exists.
3. **`hutch-resolve runs --exp --tag`** — eLog run/tag resolution. Deliberately not stubbed:
   it needs Kerberos and belongs with `elog-search`, and a stub that looks like a resolver
   would be worse than its absence.
4. **Move the nominal pointing setpoint** out of `check_beam_ready_mfx.py` (where
   `mr1l4_homs.pitch ~= -562.035` is a literal) into `hutch-resolve preset`.
5. **Extend the scenario harness.** `tests/sfx_basic/` (13 steps, assertions + mocks) is the
   best thing in the repo and should keep testing what it is good at — dispatch, safety
   gating, handoff. Its `expected_lute_checks.yaml` needs updating for the tool calls, and
   `HUTCH_DS_ROOT` should point it at the fixture tree.
6. **Beamtime dry-run before merge.** The tools are unit-tested and verified against real
   campaign artifacts, but no sub-skill has been exercised end-to-end against a live bridge.

## Compatibility

- `state.py` mirrors every write into `_timestamps`, so pre-refactor readers keep working.
- Nothing in `hutch-tools` imports outside the standard library except `resolvers.detectors`,
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
