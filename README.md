# hutch-copilot

A Claude Code skill for running an LCLS/LCLS-II experiment: beam readiness, data
collection, and reading back what the analysis produced.

Two halves. The **skills** (`SKILL.md` and the four sub-skill directories) carry
judgment — what to ask the operator, when to confirm, how to sequence a shift. The
**tools** (`hutch_tools/`) carry mechanism. The split exists because an agent must
never hand-write a path, compose a PV name, or improvise an `ssh` command: every
such fact comes from a tool call that either answers or fails loudly.

## Installing

Run it on a `{hutch}-daq` machine — `mfx-daq`, `rix-daq`, and so on. That is the
design target, not a preference: `/cds`, the DAQ log tree, the pcds environments and
Channel Access are all native there.

Claude Code only finds a skill inside a directory it scans, so the checkout has to be
one of:

| launched as | put it at |
|---|---|
| `claude` | `~/.claude/skills/hutch-copilot` |
| `claude-lcls` (sets `CLAUDE_CONFIG_DIR`) | `~/.claude-lcls/skills/hutch-copilot` |

A checkout anywhere else is invisible to the `Skill` tool no matter how correct the
code is — the symptom is `Unknown skill: hutch-copilot`, with `SKILL.md` readable by
hand. To serve both from one checkout, symlink the second:

```bash
ln -s ~/.claude/skills/hutch-copilot ~/.claude-lcls/skills/hutch-copilot
```

Then put the tools on PATH. No install step, each `bin/hutch-*` finds the package itself:

```bash
source bin/hutch-env     # sets PATH and $HUTCH, finds caget
```

At a hutch, `bin/hutch-session-start` announces the host, the hutch and this path at
every session start. Off-hutch it stays silent, by design.

## The five tools

| tool | owns |
|---|---|
| `hutch-state` | the experiment state file — facts, with provenance and TTL |
| `hutch-daq` | the live hutch-python session, the safety gate, the DAQ config and logs |
| `hutch-resolve` | machine truth: happi, lightpath, EPICS, DataSource, `/cds` |
| `hutch-lute` | LUTE: jobs, pedestals, config rendering, workspace setup |
| `hutch-log` | the beamtime log — events, for humans |

`--help` on any of them lists its subcommands. `SKILL.md` carries the
contract the agent must obey — provenance on every write, stale reads fail, a
resolver that cannot reach its source raises rather than guessing.

They run from a bare checkout with no install step. The package is stdlib-only at
module scope and Python 3.9-compatible, because psconda ships 3.9 and the bridge
listener runs inside a live hutch-python session. `pip install -e .` is a convenience
for CI, never a requirement — a tool that needs setting up is a tool that is broken
at 03:00.

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

The session itself is a loopback socket: `scripts/oc_bridge.py` runs
`exec(code, globals())` inside the operator's hutch-python session, which is the only
way to reach its loaded device objects. It has **no access control** — any local user
can connect to port 9999 and exec in that namespace. The classifier is a discipline
on our own calls, not an enforcement boundary.

## Tests

```bash
/cds/group/pcds/pyps/conda/py312/envs/pcds-6.1.0/bin/python -m pytest tests/unit -q
```

Any interpreter with pytest will do — the package is stdlib-only — but the bare
`python3` at the hutch does not have it. See `tests/README.md`.

## Status

The tools run directly on `{hutch}-daq`. Outstanding:

- **`hutch-lute sbatch|watch|status`** call Slurm locally, which at the hutch is the
  DAQ cluster. They need a hop to `psana`/`s3dflogin` to reach the analysis cluster.
- **`hutch-lute peds`** is the last `psdev` caller and will fail until it either runs
  `makepeds` locally or hops to a host that can.
- **`hutch-daq logs` filter interface** — process names, date range and retention
  are unconfirmed. Needs a live experiment to verify against.
- **Publication-quality thresholds** for the `analyze-data` verdict (CC½,
  completeness, multiplicity, Rsplit) are plausible working numbers, not reference
  values. Needs a crystallographer.
- **CCTBX merge-log layout** — whether completeness and multiplicity sit in the same
  section as CC½ and Rsplit. Needs a real merge output.
- **A terminal API instead of the session bridge.** `hutch-python` takes a script
  argument (`hutch-python --cfg <conf.yml> script.py`), which may remove the need for
  a live interactive session and the socket entirely. Worth measuring before
  investing further in `oc_bridge.py`: does a script run get the same loaded
  namespace, and what does it cost per invocation.
