# hutch-copilot

A Claude Code skill for running an LCLS/LCLS-II experiment: beam readiness, data
collection, and reading back what the analysis produced.

Two halves. The **skills** (`SKILL.md` and the four sub-skill directories) carry
judgment — what to ask the operator, when to confirm, how to sequence a shift. The
**tools** (`hutch_tools/`) carry mechanism. The split exists because an agent must
never hand-write a path, compose a PV name, or improvise an `ssh` command: every
such fact comes from a tool call that either answers or fails loudly.

## The five tools

| tool | subcommands | owns |
|---|---|---|
| `hutch-state` | `path get set unset audit dump` | the experiment state file |
| `hutch-daq` | `ping install exec status logs` | the DAQ machine |
| `hutch-resolve` | 13, grouped by source (below) | machine truth |
| `hutch-lute` | `watch status peds` | Slurm jobs and pedestals |
| `hutch-log` | `init append` | the beamtime log |

`hutch-resolve` reads from four sources, and which one a subcommand uses determines what
can go wrong with it:

| source | reached via | subcommands |
|---|---|---|
| happi — device database | `psdev` | `devices` `device` `pv` `preset` `beampath` |
| data — DataSource / XTC2 / PDB | local S3DF filesystem | `detectors` `xtc2` `cell` |
| epics — Channel Access | `{hutch}-control` | `caget` |
| control — DAQ configuration | `psdev` | `daq` `conf` |
| local — this checkout | no network | `skills` |

They run from a bare checkout with no install step. The package is stdlib-only and
Python 3.9-compatible, because psconda ships 3.9 and the bridge listener runs inside
a live hutch-python session. `pip install -e .` is a convenience for CI, never a
requirement — a tool that needs setting up is a tool that is broken at 03:00.

Every command prints JSON on stdout, a structured error on stderr, and exits with a
code a caller can branch on:

| code | meaning |
|---|---|
| 0 | ok |
| 2 | called wrong (argparse usage) |
| 3 | state error — missing field, or stale past its TTL |
| 4 | unreachable — psdev down, no bridge listener, no Slurm record |
| 5 | **refused** — the safety gate did not transmit |

`5` is the one that matters. It is how a refusal becomes machine-checkable instead of
a sentence an agent can talk itself out of.

## Three rules `hutch-state` enforces

The state file is a **cache with provenance**, not a config file.

1. **Every value carries where it came from.** `set` requires `--source`, and `--kind`
   is one of `resolved` (read from an authoritative source), `operator` (a human said
   so), or `inferred` (reasoning only). Without this, a number a tired operator typed
   at 03:00 is indistinguishable from one read off the machine.
2. **Stale reads fail.** `get` refuses a value past its per-field TTL — 10 s for a PV,
   30 s for the beampath, an hour for a device list, never for an operator-declared
   sample name. Pass `--stale-ok` to accept one, and it reports `age_s` so quoting it
   without saying how old it is becomes awkward.
3. **A resolver that cannot reach its source raises.** It never substitutes a
   remembered or documented value. The caller chooses between explicitly-stale state
   and asking the operator.

`hutch-state audit` returns every field with its age, TTL and provenance, stale
first. That is what makes "which of these numbers did we actually measure" answerable.

## Network topology — why the tools reach out the way they do

This table is load-bearing. The tools are unusable without it.

| environment | hosts | filesystem | EPICS CA to IOCs |
|---|---|---|---|
| S3DF (batch) | `s3dflogin`, `sdf-login` | `/sdf` | no (firewalled) |
| CDS (controls-adjacent) | `psdev`, `psusr`, `pslogin` | `/cds` | no (read gateway) |
| controls network | `mfx-control`, `cxi-control` | `/cds` | yes |

Consequences, each of which has bitten:

- **`/sdf` is not mounted on psdev, and `/cds` is not mounted on S3DF.** The only
  bridge is ssh, so remote scripts must arrive on stdin and must not name `/sdf`
  paths. `psdev_read` refuses a `/sdf` path rather than failing obscurely.
- **`/cds` and `/reg/neh` are the same filesystem.** `/cds` is canonical; `/reg` is a
  deprecated alias. Never probe both.
- **`caget` is on PATH on psdev, but CA to the IOCs (172.21.x.x) is blocked there.**
  PV reads must run on `{hutch}-control`.
- **TCP forwarding is disabled on DAQ machines**, so `ssh -L 9999:localhost:9999` does
  not work. `hutch-daq` instead runs a client *on* the DAQ machine that connects to
  `localhost:9999` there, passed to `ssh` as a single shell-quoted argument — no
  nested-quoting hazard.
- **Beamline names are UPPERCASE in happi, lowercase on the filesystem.** `common` and
  `resolve` normalise in opposite directions.
- **psdev is shared.** Keep remote scripts short and non-blocking.

## The safety gate

`hutch_tools/daq.py` classifies every snippet before transmitting: `read-only`,
`write`, or `beam-critical`. Unrecognised code is classified as `write`, never
read-only.

```bash
# no --confirm: prints the classification, transmits nothing, exits 5
bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()'

# declaring a weaker class than the code needs is a refusal, not a downgrade
bin/hutch-daq --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write   # rc=5
```

This is defence in depth, not a proof — patterns cannot capture every unsafe
expression, and human confirmation is still required. It only makes skipping one
impossible by accident.

## Tests

```bash
/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q
```

See `tests/README.md` for why that interpreter and not bare `python`.

## Status

See `PLAN.md`. M1 (desk work) is done; M2 is a live MFX demo, currently blocked on
batch-mode ssh access to psdev.
