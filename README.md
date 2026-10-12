# hutch-copilot

A Claude Code skill for running an LCLS/LCLS-II experiment: beam readiness, data
collection, and reading back what the analysis produced.

Two halves. The **skills** (`SKILL.md` and the four sub-skill directories — `take-run`,
`are-we-ready`, `setup-analysis`, `assess-analysis`) carry
judgment — what to ask the operator, when to confirm, how to sequence a shift. The
**tools** (`hutch_tools/`) carry mechanism.

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

### Advisors

**This repo holds no science knowledge, so the advisor skills hold all of it** — every
threshold, parameter meaning and symptom→knob judgment. They are separate skills and are
installed separately:

```bash
scripts/fetch-advisors.sh --report   # the two tiers, and what is missing
scripts/fetch-advisors.sh            # clone/update them, then report
```

Two tiers, both derived — there is no third list to maintain:

| tier | what it means | absent |
|---|---|---|
| **required** | a `SKILL.md` cites it by name, so a judgment routes through it | the judgment is refused; the script exits 4 |
| **available** | declared in `DECLARED_ADVISORS`, cited nowhere | reported and ignored |

The script clones every `psagents` repo that contains a `SKILL.md` plus the LCLS-II DAQ
diagnostic suite from `slac-lcls/lcls2` into `~/.claude/advisor-src`, and symlinks each
skill directory into `~/.claude/skills`. Both paths are overridable with
`$HUTCH_ADVISOR_CACHE` and `$CLAUDE_SKILLS_DIR` — point the latter at
`~/.claude-lcls/skills` to populate that root instead. An existing non-symlink directory
is left alone, so a clone you maintain yourself is never overwritten.

Seven advisors are **not on GitHub** — `ask-lcls2`, `ask-smalldata`, `ask-ami`,
`elog-copilot`, `experimental-hutch-python`, `xpm-seq` and `lcls-catalog` live only on
`/sdf/group/lcls/ds/dm/apps/dev/claude/skills`. The script cannot fetch them and they are
unreachable off S3DF and the hutches. None is currently required. See *Status* — whether
the org is the shared home for all of them is an open question.

The tiers are derived from the docs rather than from a GitHub topic (`knowledge` or
similar) for that reason: a topic query cannot see those seven, so it would report a
required advisor as absent, or omit it entirely. Revisit if they move to the org.

**Restart Claude Code afterwards** — the skill list is read once at session start, so a
freshly linked advisor is invisible until then.

## The tools

**One tool, one backend.** The tool to call follows from which source answers the
question, so there is never a choice to make about where a fact comes from.

| tool | owns |
|---|---|
| `hutch-state` | the experiment state file — facts, with provenance and TTL |
| `hutch-happi` | the device database — what exists, where, under which PV prefix |
| `hutch-epics` | Channel Access — live PV reads; and PV history from the archiver |
| `hutch-lightpath` | the beam path to a hutch, and what blocks it |
| `hutch-python` | the live hutch-python session, and the safety gate in front of it |
| `hutch-daq` | the DAQ — state, logs, config. Read-only |
| `hutch-data` | what landed on disk — psana2 detectors, XTC2, PDB cells, `/cds` files |
| `hutch-skills` | which skills are installed, and where |
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
bin/hutch-python --hutch mfx exec 'mfx_stopper_1.remove()'

# declaring a weaker class than the code needs is a refusal, not a downgrade
bin/hutch-python --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write   # rc=5
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
/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q
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
- **Is `github.com/psagents` the shared home for skills?** Not settled, and the advisor
  tiers depend on the answer. Today the advisors live in three places: 14 on the org, 7
  only on `/sdf/group/lcls/ds/dm/apps/dev/claude/skills` (`ask-lcls2`, `ask-smalldata`,
  `ask-ami`, `elog-copilot`, `experimental-hutch-python`, `xpm-seq`, `lcls-catalog`, all
  owned by `cwang31`, no git remote), and the 6 LCLS-II DAQ skills on a `slac-lcls/lcls2`
  feature branch. Consequences of leaving it:
  - `fetch-advisors.sh` cannot install the filesystem-only 7 at all, so they are
    unavailable off S3DF and the hutches — and unavailable to anyone outside `ps-users`.
    None is required today; citing one in a `SKILL.md` would make it required and
    immediately unsatisfiable for those users.
  - The DAQ suite is pinned to `features/psana-daq-monitor`. A feature branch is not a
    release: it can be rebased or merged away under us.
  - Each is a few KB of markdown, so size is not the obstacle. Several do depend on
    shared-storage artifacts (`.code-index.db` under `apps/dev/software/`, the
    `elog-copilot` SQLite DB), which pushing the skill does **not** make portable — that
    split is the real design question, not the hosting.

  Moving them to the org would also let the two tiers be checked without a filesystem:
  see the rejected `knowledge` topic note under *Advisors*, which only fails today
  because a topic query cannot see those 7.
- **A terminal API instead of the session bridge.** `hutch-python` takes a script
  argument (`hutch-python --cfg <conf.yml> script.py`), which may remove the need for
  a live interactive session and the socket entirely. Worth measuring before
  investing further in `oc_bridge.py`: does a script run get the same loaded
  namespace, and what does it cost per invocation.
