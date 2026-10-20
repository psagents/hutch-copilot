# hutch-copilot — state of play

A Claude Code skill for running an LCLS experiment: beam readiness, data collection, and
reading back what the analysis produced.

It runs **on** a `{hutch}-daq` or `{hutch}-console` machine. That is the design target,
not a preference: `/cds`, the DAQ log tree, the pcds environments and Channel Access are
all local there, so there is no jump host and no remote hop. Tools return JSON and judge
nothing; the skills carry the judgment.

---

## Status

| Sub-skill | State |
|---|---|
| **`are-we-ready`** | **Works**, verified against MFX hardware: 29 happi devices, 21 of them on the lightpath, 4 motor readbacks, `settled: true`, the impediment named, and 4 unreadable diagnostics reported as unknown rather than guessed. `check_daq` reads DAQ state off the control port, so it no longer needs a bridge; an unreachable control port still returns unknown (rc=4), which is the correct answer, not a failure |
| **`analyze-data`** | **Written, untested.** Calls only `hutch-lute` and `hutch-state`. Needs one real LUTE run to exercise |
| **`take-run`** | **Unblocked (Q1 resolved).** Its hard gate reads `bin/hutch-daq --hutch $H state`, and the run commands now go through `bin/hutch-python run`, which loads devices in a fresh process and needs no session or socket. `get_run()` is gone — it was an elog HTTP GET, not a DAQ read. Needs one real run at MFX to exercise the write side |
| **`sync-log`** | **Written, hook verified live.** `bin/hutch-stop` blocks once per experiment-touching turn; 16 unit tests cover the loop guard and every silent path. It fired on the turn that wrote it — a false positive, since developing the copilot had put `--experiment` in a test fixture; a checkout-cwd guard now suppresses that. Needs one real shift to exercise the write side |
| **`coordinate-experiment`** | **Partly subsumed.** `sync-log` gives ambient events and facts; shift-handoff summaries and condition tracking are still unbuilt |

---

## The tools

**One tool, one backend** — so which tool to call follows from which source answers the
question, and there is never a choice to make about where a fact comes from.

```
hutch-state      path get set unset audit dump
hutch-happi      list device pv preset
hutch-epics      caget
hutch-lightpath  path
hutch-python     ping install exec conf
hutch-daq        state config logs [--sessions] (read-only)
hutch-data       detectors xtc2 cell cat
hutch-skills     list
hutch-lute       sbatch status watch peds readiness kerberos render
                 plan workspace dags envs register all
hutch-log        init append
```

Two shell hooks sit beside them, and are not CLIs: `bin/hutch-session-start` restores
context at session start, `bin/hutch-stop` asks for the log to be synced at turn end.

`--help` on any of them lists its flags. They work from a bare checkout with no install
step; `pip install -e .` only adds the same commands to PATH.

Three rules the tools enforce and that must not be worked around:

1. **Every value carries provenance.** `hutch-state set` requires `--source`, and
   `--kind` is one of `resolved` (read from an authoritative source), `operator` (a human
   said so), `inferred` (reasoning only).
2. **Stale reads fail.** `hutch-state get` refuses a value past its per-field TTL unless
   `--stale-ok` is passed, which also reports the age.
3. **A resolver that cannot reach its source raises.** Never substitute a remembered or
   documented value.

A **readiness** question composes several backends, so it is not a tool call. Those live
with the skill that asks them: `are-we-ready/scripts/check_{beam,hutch,daq}.py`.

External paths are declared once, in `bin/hutch-env`, and read back by
`common.external_path()`. `hutch_tools/` holds no absolute path outside its own checkout.

---

## Next — the MFX demo

The loop to run end to end with an operator:

```
/are-we-ready   → beam path, hutch devices, DAQ state
/take-run       → autorun() under confirmation; XTC2 confirmed on disk;
                  LUTE job fires
  (LUTE runs, triggered by the eLog ARP)
/analyze-data   → reads the merge log from hutch-lute status; reports CC½,
                  Rsplit, completeness, multiplicity; names the limiting
                  factor, or recommends stopping
```

No parser code: `hutch-lute status` returns the log path, the skill reads the file, and
the agent reasons from the text.

These acceptance tests run every shift, regardless of what else is cut. They launch
nothing and need no beam, no DAQ and no session:

```bash
bin/hutch-python --hutch mfx run 'mfx_stopper_1.remove()'                  # MUST be rc=5
bin/hutch-python --hutch mfx run 'mfx_stopper_1.remove()' --confirm write   # MUST be rc=5
bin/hutch-python --hutch mfx run 'x()' --confirm read-only                  # MUST be rc=5
bin/hutch-python --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write  # MUST be rc=5
bin/hutch-python --hutch mfx exec 'x()' --confirm read-only                 # MUST be rc=5
```

Understating a beam-critical action is a refusal, never a downgrade; unrecognised code
errs upward. All five verified 2026-10-13. A no-beam maintenance window is sufficient
and preferable — the transports, the resolvers and the safety gate all validate
without beam.

---

## Open questions

### Q1 — RESOLVED (2026-10-13): `bin/hutch-python run`

Shipped. `run` stages a snippet to the hutch's own `{hutch}python` wrapper on
`/dev/stdin`; `load()` runs before hutch_python's interactive/script branch, so the
snippet gets the identical namespace a session would. No listener, no socket, fresh
state per call, and authorisation becomes the OS's job — the Unix account and
`{hutch}opr` membership, as with every other beamline tool. `exec` and the bridge
remain for one shift of evidence, then go (see *Removing the bridge* below).

What the question left open, answered:

- **Startup cost.** `from hutch_python.load_conf import load` alone is **24.5 s cold /
  ~9 s warm** on mfx-daq, before any device instantiation; `/u1/mfxopr/conda_envs` does
  not exist there, so `mfxenv` falls back to NFS. A full device load is unmeasured —
  it needs `mfxopr`, and `load()` writes `MFX:SCAN` PVs (`load_conf.py:427-432`), so it
  is not a read-only probe. No plausible number changes the design: runs take minutes.
  `T_LOAD = 180 s`. If it ever hurts, the answer is batching several operations into one
  snippet, **not a daemon** — a daemon reopens the authorisation question. If a
  persistent executor is genuinely wanted, `start-re-manager`/`qserver` already exist in
  these envs and are authenticated.
- **No `-c`.** Not needed: `/dev/stdin` staging costs nothing upstream, and because
  `cli.py` reads the script to EOF before exec'ing, an `input()` prompt in a snippet
  raises `EOFError` instead of hanging. Non-interactivity is enforced by construction.
- **Structured output.** Still stdout verbatim, as the bridge was. `run` adds
  `returncode` and `namespace_size`; add more when a caller needs a value `hutch-daq
  state` cannot give.
- **Exit codes.** rc **1** = the snippet raised (traceback in `error`); rc **4** =
  launcher or hutch unreachable. Collapsing them would leave a caller unable to tell
  "no launcher" from "the run failed".
- **Untested at the beamline.** Still true for the loading path: refusals, staging, exit
  codes and timeouts are verified, but nothing has yet *launched*. First real use is one
  `autorun()` at MFX.

Two traps found while resolving this, worth not rediscovering:

- **A near-empty namespace fails silently.** Every `conf.yml` points `db:` at
  `/reg/g/pcds/...`, which is unreadable by a non-opr account while the
  `/cds/group/pcds/...` spelling of the same file is fine. The happi load sits inside
  `safe_load('database')`, so the result is **rc=0 with no devices**, not an exception.
  `run` prepends an assertion keyed on `elog` and namespace size — not on `daq`, which
  MEC/CXI/XCS (no `daq_type`, so lcls1) and `las` (`nodaq`) never get.
- **`get_run()` is an elog HTTP GET** (`mfx/macros.py:619-622`), despite a docstring
  claiming it queries the DAQ. `hutch-daq state` already carries `run_number` and
  `last_run_number` from `DaqControl.getStatus()`. `take-run` now uses that.

Not agent-specific: any calibration routine, alignment scan or test harness faced the
same choice. Original write-up, now historical:
`~/.claude/handoffs/hutch-python-cli-ask.md`.

#### Removing the bridge

Gate: the acceptance tests above, plus one real `autorun()` through `run` at MFX that
produced XTC2 and an elog post. Then delete `exec`, `exec_code`, `ping`,
`install_listener`, `_bridge_request`, `BRIDGE_PORT`, `_start_hint`, `BridgeError`,
`scripts/oc_bridge.py`, and `tests/unit/test_paths.py`'s `oc_bridge.py` assertion. Grep
`oc_bridge`, `9999` and `bridge` across `*.md` first; `tests/unit/test_doc_commands.py`
fails on any surviving doc reference.

**`_ssh` and `BridgeUnavailable` are not bridge code** despite living in `session.py` —
`lute.py:87-88` and `daq.py` both depend on them for the `makepeds` hop, and
`test_peds.py`/`test_daq_cli.py` patch them. Move them to `common.py`, renaming
`BridgeUnavailable` to `RemoteError`. Rename `session.py` → `python.py` in the same
commit (it is the only `bin/hutch-X` ↔ `hutch_tools/X` mismatch left), updating
`pyproject.toml:37`, `bin/hutch-python`, `lute.py:87-88`, and the misleading
`from hutch_tools import session as daq` alias in `test_daq_cli.py:22`.

### Q1b — the per-hutch run vocabulary

`run` is a transport and must stay one. There are **16 hutch-python deployments** (`abl
amo cxi det icl las mec mfx rix sxr tmo tst txi ued xcs xpp`), every one with the same
`{hutch}python` + `{hutch}env` + `conf.yml` shape, and every launcher outside MFX is
byte-identical. The CLI contract is stable across all four envs in use (`pcds-5.9.0`,
`5.9.1` on py39; `6.0.1`, `6.1.0` on py312): `script` positional, `exec(code, objs,
objs)`. So the transport generalises. The **vocabulary does not**:

| hutch | data-taking vocabulary |
|---|---|
| **MFX** | `autorun()`, `geomrun()`, `takepeds()`, `makepeds()`, plus `alignment_scan`, `attenuator_scan`, `focus_scan`, `delay_scan`, XAS/EXAFS lists |
| **CXI** | `autorun()` — **same name, incompatible signature**, plus `cycle_sc3`, `safe_samplex`, `shift_start`/`shift_end` |
| **XCS** | `ascan_wimagerh5[_slow]`, `delay_scan`, `infinite_scan`, `knife_edge_scan` |
| **RIX** | no run verb — an energy-scan family: `energy_scan`, `energy_fly_scan[_nd][_grid][_list]`, `continuous_scan`, `vernier_scan` |
| **TMO** | bluesky step plans (`one_1d_step_no_wait`, `fly_scan`) plus alignment/fiducial tooling |
| **MEC**, **XPP** | none (MEC laser modules only) |
| others | not surveyed; `las` is `nodaq` |

The trap is CXI, not the hutches with nothing:

```python
# MFX  mfx/autorun.py:242
_autorun(sample='?', tag=None, run_length=60.0, inspire=, record=, run_type=, ...)
# CXI  cxi/macros.py:1205
autorun(sample_name=None, run_length=300, record=False, runs=1, delay=5, pulsepicker=None)
```

`run_length` is the only shared parameter, so an MFX snippet raises `TypeError`
part-way through a run at CXI. `take-run` is therefore MFX-only and says so; adopting
it elsewhere needs a per-hutch signature, not a copy. RIX/TMO/XCS are *scan*-shaped, so
a run skill for them is a different skill. If a facility-wide abstraction is ever
wanted, the portable layer is `RE`/`daq`/the `re`/`bp`/`bps` namespaces, available
everywhere via `load()`/`initialize_wrapper_namespaces` — not any hutch module.

#### Other CLIs in these envs

Surveyed while answering Q1; recorded so the next reader does not re-survey. **Already
wrapped:** `happi` (`hutch-happi`), `caget`/`caproto-get`/`caproto-put`
(`hutch-epics`). **Not ours:** `lightpath` (GUI launcher only — `hutch-lightpath` uses
the library), `LogBookPost` (the namespace already has `elog`), `whatrecord`, `getpvs`,
`applypvs`, `pmgrUtils.sh`, `typhos`. **Candidates, each a separate decision:** `atef
check <json>` — declarative passive/active checkouts, and MFX already ships
`mfx/atef/MFX{Passive,Active}Checkout.json`, a no-beam readiness check `are-we-ready`
could use; `qserver` + `start-re-manager` — bluesky queueserver, an authenticated
stateful non-interactive executor, i.e. what the bridge was badly imitating, but it
reopens the authorisation question; `epicsarch-qs`.

One behavioural difference to know before driving a bluesky plan through `run`:
script mode never calls `configure_ipython_session`, whose `exec_files` is what
subscribes nabs' `ELogPoster` to `RE`. So **`RE(...)` plans do not auto-post to the
elog** under `run`. `autorun`/`geomrun` are unaffected — they post themselves via the
`elog` object built inside `load()`.

### Q2 — `hutch-epics history`

`caget` answers "what is this PV now". It cannot answer "what changed between two times",
which is what an operator actually asks after a shift. The EPICS Archiver Appliance
serves it, reachable from S3DF:

```
http://pscaa01.slac.stanford.edu:17668/retrieval/data/getData.json
    ?pv=MFX:DG1:PIC:01.RBV&from=<ISO8601>&to=<ISO8601>
```

Shape would be `hutch-epics history --hutch H <pv> --from T1 --to T2`. Three constraints
worth encoding rather than rediscovering:

- **Query `.MSTA`, not `.RBV`, to decide whether a motor moved.** `.RBV` is archived with
  a deadband, so sub-deadband motion is invisible. `.MSTA` bit 1 (`DONE_MOVING`) is
  archived on change, so every commanded move appears. `.MOV`/`.DMOV` are not archived.
- **IOC restarts masquerade as motion.** On reconnect the appliance re-posts the
  unchanged value and `MSTA` reads with the encoder and gains bits clear — a state a
  working motor record never holds. Filter on `MSTA & 2048` before trusting the DONE bit,
  and surface `cnxlostepsecs`/`cnxregainedepsecs` so a reconnect is labelled rather than
  silently plotted.
- **The appliance host is unverified.** `pscaa01` serves `getData.json`, but
  `/retrieval/bpl/getAllPVs` and `getApplianceInfo` both 404, so "PV not archived" is
  inferred from a per-PV 404 rather than asked directly.

### Q3 — How to build the context manager — **settled**

The gap was the **trigger, not the storage**, and the trigger is now `bin/hutch-stop`: a
`Stop` hook that blocks once per turn that touched the experiment, so `sync-log` writes
the entry from the session that still has the context. The concern that "a Stop hook has
no judgment" is resolved by splitting it — the hook decides only *whether to ask*
(`last_assistant_message` matches a hutch tool, an experiment is known, the session is
not in this checkout), and the sub-skill decides *what to record*.

**The filter cannot tell "operated on" from "mentioned."** A transcript that writes
`--experiment E` into a test file reads the same as one that ran it against the DAQ, and
prose *about* `hutch-state` matches the same pattern as a call to it. The cwd guard covers
the case that actually bit (developing the skill); distinguishing the general case means
parsing tool-call structure out of the transcript, which is the parser this design
deliberately does not have. A false block costs one turn and the agent is told to write
nothing — the failure is cheap, which is why the coarse filter is the right trade.

Rejected: `entr` or any watchdog over a copied transcript. `entr` is not installed at the
hutches, the harness already fires at turn end with `transcript_path` in hand, and a
headless `claude -p` would write the beamtime log with no operator in the loop.

One thing to re-check on upgrade: the Stop payload and the `{"decision":"block"}`
contract were read out of the shipping binary (2.1.285) because the docs site is
unreachable from the hutches. They are not a published API. `tests/unit/test_stop_hook.py`
pins the behaviour we depend on, so a contract change should surface as a test failure
rather than a silent no-op.

### Q4 — `/take-run` is sequenced behind Q1

Its gate works. Its writes do not have a boundary yet, so exercise Q1 first.

### Also open

- `hutch-daq logs` retention: how far back the tree is kept. The listing/judgment split is
  settled — `@psana-daq-logs` owns which log lines matter.
- Publication-quality thresholds for the `analyze-data` verdict — CC½, completeness,
  multiplicity, Rsplit. Needs a crystallographer.
- Whether the CCTBX merge log keeps completeness and multiplicity in the same section as
  CC½ and Rsplit. Needs a real merge output.

One dependency worth stating plainly: **`hutch-lute peds` runs `makepeds` over ssh to
`psdev`.** It is the only tool that reaches off the hutch machine.

---

## Verification

Bare `python3` on these hosts has no pytest; use the pcds interpreter.

```bash
PY=/cds/group/pcds/pyps/conda/py312/envs/pcds-6.1.0/bin/python3.12
$PY -m pytest tests/unit -q

# every tool resolves
for b in state happi epics lightpath python daq data skills lute log; do
    bin/hutch-$b --help >/dev/null || echo "MISSING: $b"
done

# one declaration per external path, and none in the package
grep -rnE '"/cds|"/sdf/group' hutch_tools/      # expect nothing
grep -rn "R7.0.2-2.0" bin/ hutch_tools/         # expect bin/hutch-env only

# readiness, against real hardware
are-we-ready/scripts/check_hutch.py --hutch mfx
are-we-ready/scripts/check_beam.py  --hutch mfx
are-we-ready/scripts/check_daq.py   --hutch mfx --experiment <exp>
```
