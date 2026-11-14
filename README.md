# hutch-copilot

A Claude Code skill for running an LCLS/LCLS-II experiment from a chat: is beam reaching the
hutch, take a run, set up and read the analysis. It asks before anything moves, and says where
every number came from.

How it works inside, and how to change it: [`docs/DEVELOPING.md`](docs/DEVELOPING.md).

## Set up

Once per account, from an S3DF terminal:

```bash
git clone https://github.com/psagents/hutch-copilot.git ~/.claude/skills/hutch-copilot
~/.claude/skills/hutch-copilot/bin/hutch-setup --hutch mfx
```

It sets up every Claude you use — `claude-lcls`, plain `claude`, and your Cogito hosts — then
says what it did and what is left for you:

```
hutch-copilot setup for MFX:
  → ~/.claude-lcls/skills: linked hutch-copilot, are-we-ready, assess-analysis, debug-daq, ...
  → ~/.claude-lcls/settings.json: SessionStart and Stop hooks run this checkout's bin/
  → ~/.claude-lcls/skills: linked 21 advisors
  → ~/.config/hutch-copilot/hutch = mfx
  ...

You still need to:
  1. get a Kerberos ticket for the eLog, in your own terminal (not with `!`): kinit -c FILE:...

Then start a new Cogito session, or Claude Code on mfx-daq, and type /are-we-ready.
```

It does what a script can: links the skills and the advisors into each Claude, installs two
hooks, and records which hutch is yours. Only you can get the Kerberos ticket (`kinit`, in
your own terminal). To take runs from Cogito, your SLAC username must also be granted in the
hutch server's policy, which its owner keeps on the hutch machine
(`~/cogito-rmcp-hutch-mfx/policy.yaml`; [`authz/policy.yaml`](authz/policy.yaml) is the example).

Run it again after a `git pull`, or whenever something seems missing. `--check` shows what it
would change and changes nothing. Then start a new session: Claude reads its skills only when
a session starts.

## Where to use it

The hutch's own tools (beam path, devices, live PVs, the archiver, the DAQ, hutch-python) run
only on the hutch machine, so where you open the session decides what it can do:

| Session | What it says first | The hutch | Use it for |
|---|---|---|---|
| Claude Code on `mfx-daq`, or Cogito | "hutch-copilot is ready to help you at MFX. Which experiment is this session for?" | all of it: read it, and take runs once you approve | online and offline work |
| Claude Code on S3DF | nothing | not reachable | offline work: analysis, data on disk, the beamtime log |

On `mfx-daq` it runs the hutch's tools directly; in Cogito it reaches the same tools through
Cogito's `sdf-mcp` gateway. That is the only difference.

Before any hutch work it asks which experiment the session is for, once. Then ask in your own
words, or use a command:

| Command | For | Where |
|---|---|---|
| `/are-we-ready` | one verdict: is beam reaching the hutch, are the devices placed, can the DAQ record | `mfx-daq`, Cogito |
| `/take-run` | a DARK, GEOM or DATA run, confirmed with you first | `mfx-daq`, Cogito |
| `/debug-daq` | what is wrong with the DAQ, from its state, config and logs | `mfx-daq`, Cogito |
| `/setup-analysis` | a LUTE analysis workflow, set up and registered with the eLog | anywhere |
| `/assess-analysis` | what a finished analysis job says, and the one thing to change | anywhere |
| `/sync-log` | write what was just established into the beamtime log (it also does this by itself) | anywhere |

Asked for online work where it cannot reach the hutch, it says so and tells you where to go.

## What it is made of

A session starts with what is set up for it, and nothing else:

| Kind | Where it comes from | For hutch-copilot |
|---|---|---|
| **skills**: instructions loaded when they apply | the folders in that Claude's `skills/` | `hutch-copilot`, the six commands, the advisors |
| **MCP servers**: tools it can call | registered for that Claude, or brought by Cogito | the hutch's tools, through Cogito's gateway |
| **hooks**: scripts at fixed moments | that Claude's `settings.json` | the greeting above, and the log sync at the end of a turn |

**Advisors** are other skills that hold the science: what a LUTE parameter means, whether an
SFX merge is good, which part of the DAQ a fault is in. hutch-copilot holds none of that, so
four are required (`ask-lute`, `ask-cctbx-xfel`, `ask-smalldata`, `psana-daq`). Without one, it
reports what it read and says it cannot judge it, rather than guess. `hutch-setup` installs
and links them; `scripts/fetch-advisors.sh --report` lists them.

**Other MCP servers** in a session (Cogito's gateway also serves `epics-mfx`, for example) are
fine for a quick question. The commands above never use them, so they behave the same
everywhere.

## What it will ask you

- **The experiment**, once per session.
- **Your approval for anything that moves hardware or starts a run.** It says what will happen
  first, and runs it only once you approve: a yes in the chat, or typing `write` (or
  `beam-critical`) in the form it shows. It cannot approve anything itself.
- **A Kerberos ticket**, when the eLog needs one: run the `kinit` line it gives you in your own
  terminal, not with `!`, which has no prompt for your password.
- **What only you know**: a sample name, which run is the calibrant. Never what it can read.

## When something's wrong

| You see | Do |
|---|---|
| no greeting in Cogito or on `mfx-daq` | run `bin/hutch-setup --hutch mfx`, then start a new session |
| no greeting in Claude Code on S3DF | nothing: that is expected, it stays quiet there |
| it cannot reach the hutch, in Claude Code on S3DF | expected: use Cogito, or Claude Code on `mfx-daq` |
| it cannot reach the hutch, in Cogito | the session's facility must be LCLS, which is what brings the `sdf-mcp` gateway |
| it says you have no grant, in Cogito | ask the hutch server's owner to add you to its `policy.yaml` |
| an advisor is missing | re-run `hutch-setup`; `scripts/fetch-advisors.sh --report` shows where it looked |
| eLog steps fail, no valid Kerberos ticket | run the `kinit` line, in your own terminal |
