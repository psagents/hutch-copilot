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
| **`take-run`** | **Blocked on Q1.** Its hard gate reads `bin/hutch-daq --hutch $H state`, which needs no bridge; the run commands themselves still transit the bridge socket |
| **`coordinate-experiment`** | **Not implemented.** No directory, and `hutch-log` has no caller anywhere in the skill tree |

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

Two acceptance tests run every shift, regardless of what else is cut:

```bash
bin/hutch-python --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write   # MUST be rc=5
bin/hutch-python --hutch mfx exec 'x()' --confirm read-only                  # MUST be rc=5
```

Understating a beam-critical action is a refusal, never a downgrade; unrecognised code
errs upward. A no-beam maintenance window is sufficient and preferable for this — the
bridge, the resolvers and the safety gate all validate without beam.

---

## Open questions

### Q1 — Use `hutch_python.cli` instead of the bridge socket

The copilot reaches a live session through a listener on `localhost:9999`
(`scripts/oc_bridge.py`), which exec()s in the operator's loaded-device namespace. Launch
is authenticated; **use is not** — any local account on that machine can connect and move
hardware. The client-side safety classifier constrains the copilot's own calls and
nothing else, so it is defence in depth, not a boundary.

Most of the alternative already exists. `hutch_python.cli` takes a positional script
argument and `load()` runs *before* the interactive/script branch, so
`hutch-python --cfg <hutch>/conf.yml script.py` already has identical device access to a
session. Moving to it makes authorisation the OS's job — the Unix account and
`{hutch}opr` membership decide who may move hardware, as with every other beamline tool.
No listener, no socket to protect, fresh state per call.

Unresolved: **per-invocation startup cost** (every call pays a full device load — if it is
slow the answer is batching, not a daemon, since a daemon reopens the authorisation
question); no `-c` for a one-liner; no structured output; unclear non-zero exit on a
failed device operation; and no beamline use of this path today, so it is effectively
untested.

Not agent-specific: any calibration routine, alignment scan or test harness faces the
same choice — drive a human's session, or reimplement device loading. Full write-up in
`~/.claude/handoffs/hutch-python-cli-ask.md`.

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

### Q3 — How to build the context manager

The gap is the **trigger, not the storage**. `hutch-state audit` already returns value,
source, kind, age and staleness for every field, and a `SessionStart` hook is already
wired and working (`bin/hutch-session-start`). Possible shapes, undecided:

- **SessionStart hook + SKILL.md** — the hook injects `audit` output so restoring context
  is automatic, and SKILL.md carries the write-back discipline.
- **Add a Stop hook** so logging is ambient too. But a Stop hook has no judgment: it
  knows the turn ended, not whether a sample changed or a run mattered, so it may need
  the agent to stage the entry anyway.
- **SKILL.md only** — nothing to install, and nothing to enforce it.

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
