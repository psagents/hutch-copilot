# hutch-copilot: how it works, for whoever changes it

The operator's side (setup, first session, what it asks) is in [`../README.md`](../README.md).

Two halves. The **skills** (`SKILL.md`, and one per procedure under `skills/` — `/are-we-ready`,
`/take-run`, `/debug-daq`, `/setup-analysis`, `/assess-analysis`, `/sync-log`) carry
judgment — what to ask the operator, when to confirm, how to sequence a shift. The
**tools** (`src/hutch_copilot/`) carry mechanism.

## What `hutch-setup` does

`hutch-setup` is idempotent; run it again after a pull, and `--check` reports what it would
change without changing anything. For every Claude config on the account — `claude-lcls`
(`$CLAUDE_CONFIG_DIR`), plain `claude` (`~/.claude`) and each Cogito host overlay — it:

- **links the skills**: the checkout as `hutch-copilot`, and each `skills/<name>` beside it,
  so `/are-we-ready` and the rest are real commands. Claude Code only finds a skill in a
  directory it scans, and never inside another skill. A link that is not this checkout's —
  another user's copy — is reported and left alone.
- **installs the hooks**: `SessionStart` and `Stop` pointing at **this** checkout's `bin/`,
  replacing any that point at another checkout. Other keys are kept; the old file is saved
  as `settings.json.bak-<time>`.
- **fetches and links the advisors**: `scripts/fetch-advisors.sh`, then every advisor in
  `ADVISORS` (`src/hutch_copilot/tools/skills.py`) into each config's `skills/`, from the first
  config or shared root that has it. A session loads only its own config's skills, so an
  advisor in `~/.claude/skills` is invisible to `claude-lcls` until it is linked there too.
  A required advisor that is nowhere, or blocked by a broken copy, is reported.
- **registers no hutch server**, and removes an old `hutch-<hutch>` registration (the server
  over ssh). Off the hutch, only Cogito reaches the hutch, through its `sdf-mcp` gateway; on
  the hutch machine the CLIs do.
- **checks what it cannot do for you**: a Kerberos ticket (it prints the `kinit` line; run it
  in your own terminal).

It also records the hutch in `~/.config/hutch-copilot/hutch`, which Cogito sessions read to
know their hutch. `bin/hutch-session-start` greets the operator the same way on the hutch machine
and in Cogito, asking for the experiment, and tells the model the one route to the hutch's tools
(below): that route is the only difference between the two.
A Claude Code terminal off the hutch gets nothing, greeting or context. The tools
need no install step: each `bin/hutch-*` finds the package itself, and `source bin/hutch-env`
puts them on PATH.

`hutch-stop` blocks the turn once when it ended having touched the experiment, so
`sync-log` records it while the context is still live. It spawns nothing, writes nothing,
and exits silently where no hutch is set up, inside this checkout (developing the copilot is not
operating the hutch), with no experiment established, or on any malformed input.
Four things stop it looping: `stop_hook_active`, the harness's own
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8), the advancing `sync_log.last_uuid`
watermark, and the fact that it issues no tool call.

## Advisors

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
| **available** | declared in `ADVISORS` (`tools/skills.py`), cited nowhere | reported and ignored |

The script clones every `psagents` repo that contains a `SKILL.md` plus the LCLS-II DAQ
diagnostic suite from `slac-lcls/lcls2` into `~/.claude/advisor-src`, and symlinks each
skill directory into `~/.claude/skills`. Both paths are overridable with
`$HUTCH_ADVISOR_CACHE` and `$CLAUDE_SKILLS_DIR`. An existing non-symlink directory
is left alone, so a clone you maintain yourself is never overwritten. `hutch-setup` then
links them into the other configs.

Seven advisors are **not on GitHub** — `ask-lcls2`, `ask-smalldata`, `ask-ami`,
`elog-copilot`, `experimental-hutch-python`, `xpm-seq` and `lcls-catalog` live only on
`/sdf/group/lcls/ds/dm/apps/dev/claude/skills`. The script cannot fetch them and they are
unreachable off S3DF and the hutches. One is required — `ask-smalldata`, for SmallData
producer configs — so SmallData verdicts are refused off S3DF. See *Status* — whether
the org is the shared home for all of them is an open question.

The tiers are derived from the docs rather than from a GitHub topic (`knowledge` or
similar) for that reason: a topic query cannot see those seven, so it would report a
required advisor as absent, or omit it entirely. Revisit if they move to the org.

**Restart Claude Code afterwards** — the skill list is read once at session start, so a
freshly linked advisor is invisible until then.

## Three settings

`bin/hutch-session-start` tells a session how it reaches what only the hutch can read, by
whether the hutch's own files are readable where Claude runs, and whether it is Cogito:

| Where Claude runs | Hook says | The hutch-only tools |
|---|---|---|
| a terminal on the hutch machine (`claude` on `mfx-daq`) | `MODE: local` | `bin/hutch-*` directly; writes after the operator's yes |
| Cogito, on S3DF **or** on the hutch machine (its container has no `/cds`) | `MODE: mcp` | `mcp__sdf-mcp__hutch<hutch>_<tool>`, through the gateway; writes per the bundle's `policy.yaml` |
| a terminal anywhere else (S3DF) | nothing | none: offline work only (analysis, data on disk, state, log) |

Cogito is told apart by `$HOME`: a Cogito host sets it to its overlay, away from the passwd
home. The skills name each step both ways, and the tools return the same JSON either way.

## Off the hutch: `hutch-mcp`

A Cogito session (whose agent container mounts neither `/cds` nor the operator homes)
reaches the hutch-only tools through the gateway, which serves
`src/hutch_copilot/server.py`: an MCP server built like epics-mcp (FastMCP, stdio or streamable HTTP
on a Unix socket, the caller from `X-Authenticated-User`), with fourteen tools, each a
fixed argv over an existing `bin/hutch-*`. Only `hutch_py_run` can write: the operator must
type the action class into an MCP elicitation form, and the model cannot.

Through the gateway (HTTP), the server is one process run by whoever runs the tunnel, so every
write would run as that account. A policy, modelled on epics-mcp's, decides who may ask, and
it lives where epics-mcp's does: in the tunnel bundle, as `policy.yaml` beside `mcp.sock`
(`~/cogito-rmcp-hutch-<hutch>/` on the hutch machine), kept by the server's owner and passed by
`bin/hutch-mcp` as `AUTHZ_POLICY_FILE`. [`authz/policy.yaml`](../authz/policy.yaml) is only the
example to copy; nothing reads it. The caller is the gateway's `X-Authenticated-User`, a bare
username (`user=lconreux` in the server's log), trusted because only agentgateway's certificate
reaches the socket; a name with a domain matches no grant. It is default-deny and fails closed:
no `AUTHZ_POLICY_FILE`, or a policy that cannot be read or evaluated, refuses every gateway
write. It is re-read on every write, so an edit applies without a restart. A granted write is
then shown to the operator, and runs as the tunnel's account; the log records who asked. Over ssh (stdio) the policy is not read, because
the server runs as the caller.

Not verified yet: whether agentgateway relays the elicitation form to Cogito. If it does not, a
granted write is refused with "this client cannot ask the operator", which fails safe. The
account running the tunnel should be the hutch's operator account (or a service account), not
a person's, before anyone else is granted.

It ships as an image, built by `.github/workflows/hutch-mcp-image.yml` on Rocky 9 and pushed to
GHCR. The image holds only Python and the MCP SDK; `bin/hutch-mcp` binds in what the
tools wrap (`/cds`, `/reg/g`, `/sdf/home`, `/sdf/group`, `/sdf/data`). On the hutch:

```bash
apptainer pull hutch-mcp.sif docker://ghcr.io/psagents/hutch-copilot:sha-<commit>
bin/hutch-mcp hutch-mcp.sif                 # stdio, for a client over ssh
bin/hutch-mcp hutch-mcp.sif $D/mcp.sock     # for the SAGE tunnel, before run.sh
```

Or let cron keep the tunnel's server on the newest `:main`, and bring both back after a reboot
(`crontab -e` on the hutch, `D` spelled out):

```
*/15 * * * * $HOME/.claude/skills/hutch-copilot/bin/hutch-mcp-update $D/mcp.sock >> $D/update.log 2>&1
@reboot sleep 30; $HOME/.claude/skills/hutch-copilot/bin/hutch-mcp-update $D/mcp.sock >> $D/update.log 2>&1; $D/run.sh >> $D/run.log 2>&1
```

Nothing registers it per user. To try the server by hand over your own ssh, in one config:

```bash
claude mcp add -s user hutch-mfx -- ssh -T -o BatchMode=yes \
    -J psdev mfx-daq ~/.claude/skills/hutch-copilot/bin/hutch-mcp <path>/hutch-mcp.sif
```

## If epics-mcp becomes the standard

[`slaclab/epics-mcp`](https://github.com/slaclab/epics-mcp) serves `pv_get`, `pv_info`,
`archiver_get` and more, read-only, with per-user authentication and per-PV authorisation.
Today the procedures use only this repo's tools, and other servers in a session are extras
(`SKILL.md`). If epics-mcp is mandated, the switch is contained, because every EPICS read goes
through one module:

1. **What the model calls.** Swap the two rows of `SKILL.md`'s table to `pv_get` and
   `archiver_get`, drop `epics_caget` and `epics_history` from `server.py`, and give
   `test_every_mcp_tool_a_skill_names_exists` a declared list of external tools. Keep
   `epics_moved`, or contribute it upstream: its `.MSTA` filtering is code, not reasoning.
   Served through the same gateway, it needs nothing from `hutch-setup`.
2. **What the composite checks read in-process** (`ready_hutch`, `normal_diff`): swap the
   body of `tools/epics.py` (`caget`, `history`) to call epics-mcp. Nothing above it changes.
   Settle first which identity this server presents to it: the user's token over the gateway,
   epics-mcp in stdio mode as that user over ssh.
3. **What cannot move without other people's code**: `lightpath` opens its own Channel Access
   inside the pcds library, and `hutch-py run` is a full hutch-python load. They stay direct
   unless those libraries gain an epics-mcp backend.
4. **One branch, no toggle**: two live routes is what the design avoids. The doc drift tests
   catch a doc still naming the old tools; verify as on 11-11, `/are-we-ready` in each setting
   making exactly the named calls.

## The tools

**One tool, one backend.** The tool to call follows from which source answers the
question, so there is never a choice to make about where a fact comes from.

| tool | owns |
|---|---|
| `hutch-state` | the experiment state file — facts, with provenance and TTL |
| `hutch-happi` | the device database — what exists, where, under which PV prefix |
| `hutch-epics` | Channel Access — live PV reads; and PV history from the archiver |
| `hutch-lightpath` | the beam path to a hutch, and what blocks it |
| `hutch-py` | the hutch-python device namespace — via the CLI (`run`), or a live session (`exec`, deprecated) — and the safety gate in front of both |
| `hutch-daq` | the DAQ — state (via its control port, no session needed), logs, config. Read-only |
| `hutch-data` | what landed on disk — `daq` (LCLS-I or LCLS-II), psana1/psana2 detectors, XTC/XTC2, PDB cells, `/cds` files |
| `hutch-skills` | which skills are installed, and where |
| `hutch-lute` | LUTE: jobs (`run-task`, `run-workflow`), pedestals, config rendering, workspace setup. Slurm calls go over `ssh psana` when the local cluster is not S3DF |
| `hutch-log` | the beamtime log — events, for humans |

Two shell hooks sit beside them and are not CLIs: `hutch-session-start` and `hutch-stop`.

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

`src/hutch_copilot/tools/session.py` classifies every snippet before transmitting: `read-only`,
`write`, or `beam-critical`. Unrecognised code is classified as `write`, never
read-only.

```bash
# no --confirm: prints the classification, transmits nothing, exits 5
bin/hutch-py --hutch mfx exec 'mfx_stopper_1.remove()'

# declaring a weaker class than the code needs is a refusal, not a downgrade
bin/hutch-py --hutch mfx exec 'mfx_stopper_1.remove()' --confirm write   # rc=5
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

- **LCLS-I** — `hutch-data daq|xtc|detectors` and `hutch-lute run-task` handle it
  (checked on cxi101933627). `hutch-lute peds` still checks for `.xtc2` only and will
  refuse an LCLS-I DARK run.
- **`hutch-lute peds`** is the last `psdev` caller and will fail until it either runs
  `makepeds` locally or hops to a host that can.
- **`hutch-daq logs` retention** — verified live at MFX on 2026-10-12 (88 sessions in one
  month, `.log.zst` once rotated), but how far back the tree is kept is unconfirmed. The
  split is settled: listing sessions and reading a file is here, deciding which `<E>`/`<C>`
  lines matter is `@psana-daq-logs`.
- **Is `github.com/psagents` the shared home for skills?** Not settled, and the advisor
  tiers depend on the answer. Today the advisors live in three places: 14 on the org, 7
  only on `/sdf/group/lcls/ds/dm/apps/dev/claude/skills` (`ask-lcls2`, `ask-smalldata`,
  `ask-ami`, `elog-copilot`, `experimental-hutch-python`, `xpm-seq`, `lcls-catalog`, all
  owned by `cwang31`, no git remote), and the 6 LCLS-II DAQ skills on a `slac-lcls/lcls2`
  feature branch. Consequences of leaving it:
  - `fetch-advisors.sh` cannot install the filesystem-only 7 at all, so they are
    unavailable off S3DF and the hutches — and unavailable to anyone outside `ps-users`.
    `ask-smalldata` is now required (cited by the root `SKILL.md` for SmallData
    producer configs), so for those users a SmallData verdict is refused.
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
