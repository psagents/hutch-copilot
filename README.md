# hutch-copilot

A Claude Code skill for running an LCLS/LCLS-II experiment: beam readiness, data
collection, and reading back what the analysis produced.

Two halves. The **skills** (`SKILL.md` and the four sub-skill directories) carry
judgment — what to ask the operator, when to confirm, how to sequence a shift. The
**tools** (`hutch_tools/`) carry mechanism. The split exists because an agent must
never hand-write a path, compose a PV name, or improvise an `ssh` command: every
such fact comes from a tool call that either answers or fails loudly.

## Where this runs, and where to put it

**Run it on a `{hutch}-daq` machine** — `mfx-daq`, `rix-daq`, and so on. That is
the design target, not a preference. Everything the tools need is native there:
`/cds`, `/sdf/data/lcls`, the `{hutch}opr` DAQ log tree, the pcds Python
environments, working EPICS Channel Access, and a reachable Slurm controller
(`lcls-drp`).

`{hutch}-control` works for most things, but it has no Slurm controller, so DAQ
state is not visible from there.

**An S3DF login node is not supported.** It has no `/cds` and no Channel Access,
and the `psdev` hop that used to bridge that gap is gone (it is also no longer
reachable). A checkout on S3DF will fail on most `hutch-resolve` subcommands —
that is expected, not a bug. The one exception runs the other way: LUTE
submission still needs an S3DF hop, because the Slurm controller at the hutch is
the DAQ cluster, not the analysis cluster.

**Where the checkout must live.** Claude Code only finds a skill inside a
directory it scans, so the checkout has to be one of:

| launched as | put it at |
|---|---|
| `claude` | `~/.claude/skills/hutch-copilot` |
| `claude-lcls` (sets `CLAUDE_CONFIG_DIR`) | `~/.claude-lcls/skills/hutch-copilot` |

A checkout anywhere else is invisible to the `Skill` tool no matter how correct
the code is — the symptom is `Unknown skill: hutch-copilot`, with `SKILL.md`
readable by hand. To serve both from one checkout, symlink the second:

```bash
ln -s ~/.claude/skills/hutch-copilot ~/.claude-lcls/skills/hutch-copilot
```

Then put the tools on PATH. No install step, and `PYTHONPATH` is not needed —
each `bin/hutch-*` finds the package itself:

```bash
source bin/hutch-env     # sets PATH and $HUTCH, finds caget
```

## The five tools

| tool | subcommands | owns |
|---|---|---|
| `hutch-state` | `path get set unset audit dump` | the experiment state file |
| `hutch-daq` | `ping install exec config logs` | the live session, the DAQ config, the DAQ logs |
| `hutch-resolve` | 13, grouped by source (below) | machine truth |
| `hutch-lute` | 13, grouped by what it touches (below) | LUTE: jobs, pedestals, config, workspace |
| `hutch-log` | `init append` | the beamtime log |

`hutch-resolve` reads from four sources, and which one a subcommand uses determines what
can go wrong with it:

| source | reached via | subcommands |
|---|---|---|
| happi — device database | `db.json` on `/cds`, read locally | `devices` `device` `pv` `preset` |
| data — DataSource / XTC2 / PDB | local filesystem | `detectors` `xtc2` `cell` |
| epics — Channel Access | `caget`, locally | `caget` |
| control — DAQ configuration | `conf.yml` on `/cds`, read locally | `conf` |
| session — live hutch-python | loopback socket, port 9999 | `beampath` `daq` |
| local — this checkout | no network | `skills` |

Only the last-but-one needs anything running: `beampath` and `daq` read live
objects out of the hutch-python session, so they need the operator to have
started it and the bridge inside it. Everything else is a file read or a CA
read and works whether or not a session exists.

`hutch-lute` groups by what it touches, and by whether it is safe to run mid-shift:

| what | subcommands | during a shift? |
|---|---|---|
| observe — Slurm | `watch` `status` | yes |
| calibration — `makepeds` | `peds` | yes, after a DARK run (still needs psdev; see Status) |
| report — read-only | `readiness` `kerberos` `plan` | yes |
| submit — Slurm, directly | `sbatch` | only if the eLog ARP did not fire |
| configure — before a shift | `render` `workspace` `dags` `envs` `register` `all` | no |

They run from a bare checkout with no install step. The package is stdlib-only at module
scope and Python 3.9-compatible, because psconda ships 3.9 and the bridge listener runs
inside a live hutch-python session. `pip install -e .` is a convenience for CI, never a
requirement — a tool that needs setting up is a tool that is broken at 03:00.

Four third-party packages are used, all imported *inside* the function that needs them:
jinja2 and PyYAML by `hutch-lute render`, requests and krtc by `hutch-lute register`. That
is why `hutch-lute watch` keeps working on a machine without them, and why
`test_package_api.py` checks module-scope imports separately from deferred ones — moving an
import inside a function must not become a way to add a dependency quietly.

Every command prints JSON on stdout, a structured error on stderr, and exits with a
code a caller can branch on:

| code | meaning |
|---|---|
| 0 | ok |
| 2 | called wrong (argparse usage) |
| 3 | state error — missing field, or stale past its TTL |
| 4 | unreachable — no bridge listener, missing file, no Slurm record |
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

## Network topology — why there is almost nothing to reach out to

Running on `{hutch}-daq` is what makes the tools simple: the things they read are
all local there, so there is no transport to get wrong.

| environment | hosts | filesystem | EPICS CA to IOCs | Slurm |
|---|---|---|---|---|
| hutch DAQ | `mfx-daq`, `rix-daq` | `/cds` + `/sdf/data/lcls` | yes | `lcls-drp` (DAQ) |
| hutch control | `mfx-control` | `/cds` + `/sdf/data/lcls` | yes | none |
| S3DF (batch) | `s3dflogin`, `psana` | `/sdf` | no (firewalled) | analysis |

Consequences, each of which has bitten:

- **`/reg/g/pcds` is missing on the DAQ machines**, while `/cds/group/pcds` is the
  same tree. Always write the `/cds` form. This is also why the stock DAQ scripts
  (`daq_utils.py`, `wheredaq`) break there — they hardcode `/reg`.
- **`caget` is on PATH on the control machines but not on the DAQ machines**, where
  only the absolute rhel9 build exists. `hutch-env` prepends it; `caget_binary()`
  resolves PATH first, then that build.
- **Do not set `EPICS_CA_ADDR_LIST`.** It is unset on the DAQ machines and CA works
  by broadcast. Pinning it is a guess that can break a host that was working.
- **The session bridge is a loopback socket, not an ssh hop.** `oc_bridge.py`
  `exec()`s in the hutch-python session's own `globals()`, which is the only way to
  see its loaded device objects. Running on the same machine does not substitute for
  it, and no ssh is involved any more.
- **The bridge has no access control.** Any local user can connect to port 9999 and
  exec in the operator's namespace. The safety classifier is client-side — a
  discipline on our own calls, not an enforcement boundary.
- **`/cds` and `/reg/neh` are the same filesystem.** `/cds` is canonical; `/reg` is a
  deprecated alias. Never probe both.
- **Beamline names are UPPERCASE in happi, lowercase on the filesystem.** `common` and
  `resolve` normalise in opposite directions.
- **Slurm at the hutch is the DAQ cluster (`drpq`), not the analysis cluster.** LUTE
  submission still needs a hop to `psana` or `s3dflogin`.

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
/cds/group/pcds/pyps/conda/py312/envs/pcds-6.1.0/bin/python -m pytest tests/unit -q
```

Any interpreter with pytest will do — the package is stdlib-only — but the bare
`python3` at the hutch does not have it. See `tests/README.md`.

## Status

See `PLAN.md`. M1 (desk work) is done. The tools now run directly on
`{hutch}-daq`, so the psdev hop that blocked M2 is gone.

Still outstanding:

- **`hutch-lute sbatch|watch|status`** call Slurm locally, which at the hutch is
  the DAQ cluster. They need a hop to `psana`/`s3dflogin` to reach the analysis
  cluster.
- **`hutch-lute peds`** is the last `psdev` caller and will fail until it either
  runs `makepeds` locally or hops to a host that can.
- **A terminal API instead of the session bridge.** `hutch-python` takes a script
  argument (`hutch-python --cfg <conf.yml> script.py`), which may remove the need
  for a live interactive session and the socket entirely. Worth measuring before
  investing further in `oc_bridge.py`: does a script run get the same loaded
  namespace, and what does it cost per invocation.
