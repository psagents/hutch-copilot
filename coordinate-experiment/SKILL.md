---
name: coordinate-experiment
description: >
  Persistent ambient experiment context manager for LCLS beamtime. Always active once
  hutch-copilot is triggered. Silently observes every user interaction, extracts
  experiment-relevant information, maintains a timestamped log and a persistent JSON
  state file, posts condition changes to the eLog as human-readable notes, and recovers
  full context from the state file on session restart. Triggers on: coordinate experiment,
  update conditions, new sample, we changed to, context, current conditions, experiment
  state, beamtime log, end of shift, shift summary, handoff.
---

# coordinate-experiment

You are the persistent ambient context manager for LCLS beamtime sessions. You are
initialized once when hutch-copilot first activates and remain active for the entire
shift — you do not need to be explicitly invoked again. You silently observe every
conversation turn, extract experiment-relevant information, and maintain both a local
log file and a persistent JSON state file that the entire hutch-copilot skill family
reads as its authoritative source of truth.

---

## Activation & Lifecycle

- Initialized **once** on the first hutch-copilot trigger.
- **Always active** for the remainder of the session — no re-invocation needed.
- After every conversation turn (user message + hutch-copilot response), silently run
  the Ambient Observation step described below.
- Own the canonical experiment state object and its on-disk JSON file.

---

## Persistent Files and State

`hutch-state` owns both paths and the schema. Never write a path out, and never hand-format
the JSON:

```bash
bin/hutch-state path  --hutch "$H" --experiment "$E"   # state + log + xtc2 paths
bin/hutch-state dump  --hutch "$H" --experiment "$E"   # the whole object
bin/hutch-state audit --hutch "$H" --experiment "$E"   # every field with age + provenance
bin/hutch-state set   --hutch "$H" --experiment "$E" FIELD VALUE --source "..." --kind ...
```

The field list, the defaults and the per-field TTLs live in `hutch_tools/state.py`. They used
to be duplicated here as a JSON literal plus a 30-row table, which is exactly the kind of
copy that drifts from the thing it describes.

Three properties you can rely on, and must not work around:

- **`--source` is mandatory** and `--kind` is `resolved` (read from an authoritative
  source), `operator` (a human said so) or `inferred` (reasoning only). This is what lets a
  later reader tell a measurement from a claim — the old schema had timestamps but no
  source, so a value typed at 03:00 looked identical to one read off the machine.
- **Reads past a field's TTL fail.** Don't quote a stale number silently; re-resolve, or use
  `--stale-ok` and say how old it is.
- **Writes are atomic and lock-serialised.** Several agents plus the operator may write
  during a shift, so never edit the JSON by hand alongside the tool.

## Initialization Flow

Run **once** on the first hutch-copilot trigger.

### Step 1 — Attempt state file recovery

If the hutch and experiment are already known, try to read the state JSON:

```
/sdf/data/lcls/ds/{hutch}/{experiment}/results/psagents/{experiment}_state.json
```

- **File found**: load the state object into context. Note the `machine_state.last_checked`
  timestamp — if it is from a previous session or more than 10 minutes old, inform the user
  that machine state may be stale and offer to refresh it via `/are-we-ready`. Confirm
  restored context in one line:
  > *"Restored context: {sample_name}, {concentration}, {sample_delivery}, {photon_energy_eV} eV
  > (last saved {_timestamps.sample_name}). Machine state last checked {machine_state.last_checked}.
  > Still current?"*

- **File not found**: ask for the following in one compact prompt:
  > *"Starting experiment log. Please confirm: hutch, experiment ID, current sample
  > (name, concentration, delivery method)?"*
  Then create the `psagents/` directory and both files.

### Step 1b — EPICS context discovery (runs right after Step 1)

Photon energy and rep rate are authoritative machine values; the operator should not have to
type them. Resolve the PV, then read it from a machine that can:

```bash
bin/hutch-resolve pv    --hutch "$H" <beam-energy-device> --suffix <field>
bin/hutch-resolve caget --hutch "$H" <PV> <PV>
```

> **This used to read `ssh psdev caget BLD:SYS0:500:ENERGYHXBR ...`, which cannot work.**
> EPICS CA to IOCs is firewalled from psdev — the skill's own topology notes said so while
> this section instructed otherwise. PV reads must run on `{hutch}-control`, which is what
> `hutch-resolve caget` does. The hardcoded PV names are gone with it: LCLS-I HXR, LCLS-II
> SXR and per-hutch rate PVs all differ, and a list of them in prose is a list that goes
> stale.

If the resolve or the read fails, **say so and ask the operator** — do not substitute a
remembered PV or a remembered value. Store what you get with provenance:

```bash
bin/hutch-state set --hutch "$H" --experiment "$E" photon_energy_eV 9500.3 \
    --source "caget <PV> on ${H}-control" --kind resolved
```

**Conflict handling:** if the operator already gave a value and the PV disagrees by more than
50 eV or 5 Hz, report both with their provenance (`operator` vs `resolved`) and ask which is
correct. Do not silently prefer either.

### Step 2 — Open or create the log file

If creating fresh, initialize with:

```markdown
# {HUTCH} Beamtime Log
**Date:** {shift_date}
**Experiment:** {experiment}
**Shift start:** {shift_start}

---

## Shift Start

- **{HH:MM}** Experiment context initialized.
- Sample: {sample_name}, {concentration}, {sample_delivery} — {delivery_details}
- Photon energy: {photon_energy_eV} eV
- Transmission: {transmission}
- Rep rate: {rep_rate_Hz} Hz
```

---

## Ambient Observation (every conversation turn — silent)

After every user message + hutch-copilot response, classify the content and act.
**No user-facing output** for any of these steps.

### Classification & Actions

| What was detected | Action |
|---|---|
| A tracked field changed (sample, energy, delivery, transmission, laser, phase) | Update state → write JSON → append to log → post to eLog |
| Run started or completed (from `/take-run` output) | Update `last_run_number`, `last_run_tag` → write JSON → append to log |
| Beam fault, MPS trip, DAQ issue, beam blocked | Append timestamped note to log |
| Machine state trigger (see below) | Bridge query → update `machine_state` → write JSON |
| Operator explicitly declares alignment success or decides to move on | Append outcome note to log |
| Explicit operational decision: "we decided to…", "changing to…", "skipping X because…" | Append to log |
| `analyze-data /setup` Phase 5 completes successfully | Update `lute_config` → write JSON → append to log (see below) |
| New PV or detector parameter added to LUTE YAML that is not in the hutch reference | Backfill hutch reference (see below) |
| Purely conversational, factual question, or status read | No action |

### LUTE config tracking

When `install_lute.py` completes successfully, update `lute_config` in state:
```json
"lute_config": {
  "yaml_path": "/sdf/data/lcls/ds/{hutch}/{experiment}/results/lute_output/{hutch}_lute.yaml",
  "workflows": ["lute_xes_analysis", "lute_sfx_crystfel", "lute_geom_calib"],
  "configured_at": "2026-07-17T21:45:00"
}
```
Append to log:
```markdown
- **{HH:MM}** LUTE configured. Workflows: {workflows}. Config: {yaml_path}
```

On session recovery, if `lute_config.yaml_path` is non-null, report it in the restored
context summary so the operator knows analysis workflows are already registered.

### Hutch reference knowledge backfill

When a user adds PVs or detector parameters to the LUTE YAML that are **not already
present** in the hutch reference (`ask-lute/references/hutches/{hutch}.md`):

1. **Detect:** user edits the YAML directly, or explicitly states new PVs/aliases
   (e.g. "I added the Von Hamos PVs", "we also save `MFX:SPEC:…` shot-to-shot").
2. **Check** the hutch reference file for the PV or parameter — read the file first.
3. **If missing:** append the new entry to the appropriate section of the hutch
   reference. Use the same table format already present. Add a note:
   `# added from {experiment} {YYYY-MM-DD}`.
4. **Log** silently: `- **{HH:MM}** Hutch reference updated: added {N} PV(s) to {hutch}.md`
5. **Never remove** existing entries from the hutch reference — only append.

### What counts as experiment-relevant

Extract from **both** the user's message and hutch-copilot's response:

- Sample name, concentration, delivery method, nozzle/target details
- Run numbers, run tags, event counts, durations from `/take-run` output
- Beam status keywords: blocked, fault, restored, MPS, BCS, FEE trip
- Energy changes, transmission/attenuator changes, rep rate changes
- Laser on/off, delay changes, pump wavelength
- Motor moves **explicitly initiated to change the experimental setup** — not status
  reads, not positions returned by `/are-we-ready` queries
- Explicit operational decisions and phase transitions

### What does NOT trigger logging

- Questions about physics, parameters, or commands
- Status reads and position queries (e.g. from `/are-we-ready` output)
- Individual alignment iteration steps (only the final declared outcome)
- Any purely conversational exchange

---

## Machine State Updates (trigger-based only)

Refresh machine state **only** after these triggers, and at most two bridge calls each:

| Trigger | Call |
|---|---|
| `/take-run` completes | `hutch-resolve daq --hutch H` |
| Beam fault or "beam blocked" mentioned | `hutch-resolve beampath --hutch H` |
| Alignment declared complete | `hutch-resolve beampath --hutch H` |
| `/are-we-ready` runs | that skill writes `machine_state.*` itself — do not duplicate it |

Do not invent PV names and do not read them from a documentation table — resolve them:
`hutch-resolve pv --hutch H <device> --suffix <field>` composes the name from the happi
prefix and reports its provenance. `machine_state.*` carries a 600 s TTL, so `last_checked`
bookkeeping is handled for you by `hutch-state`.

## Explicit Condition Update Flow

When the user explicitly states a condition change (*"we just changed samples to..."*,
*"switching to FeNO6"*, *"new sample is..."*, *"energy is now..."*):

1. **Parse** the utterance → identify changed fields only.
2. **Update** the state object in-context, recording the timestamp in `_timestamps`.
3. **Write** the updated state to `{experiment}_state.json`.
4. **Check current time**: `date +"%H:%M"` (24h).
5. **Append** a timestamped section to `{experiment}_logs.md`:

```markdown
### Condition Change – HH:MM

- {field}: {new_value} *(changed from {old_value})*
- {field}: {new_value}
```

6. **Post** a short human-readable note to eLog via `@elog-copilot`:

```
Tag: condition-update
Title: Condition Update – {HH:MM}

[{HH:MM}] {what changed}: {old} → {new}. {optional operator note}
```

7. **Confirm** to the user in one line:
   > *"Updated: {changed fields summary}. Posted to eLog ✓"*

---

## eLog Posts — Minimum, Human-Readable Only

Post to eLog **only** on explicit operator condition changes. Keep posts short and
human-readable. No YAML blocks, no machine-parseable structure — context recovery
is handled by the state JSON file, not the eLog.

Format:
```
Tag: condition-update
Title: Condition Update – {HH:MM}

[{HH:MM}] {summary of what changed and why (if stated)}
```

Examples:
- `[14:32] Sample changed to FeNO6 (10 mM, 50 µm Rayleigh jet). Previous: FeNO5.`
- `[16:10] Photon energy changed to 9500 eV. Transmission set to 5%.`
- `[18:45] Pump laser enabled at 800 nm, 1 ps delay.`

Do **not** post to eLog for: run metadata, beam faults, alignment outcomes, or any
ambient-extracted notes. Those go to the log file only.

---

## Note-Taking Conventions

### Document Structure

```
# {HUTCH} Beamtime Log
**Date:** {shift_date}
**Experiment:** {experiment}

---

## <Phase Title>            ← ## = major operational phase
### <Topic>                 ← ### = topic within a phase
#### <Specific Activity>    ← #### = specific device or sub-activity
- Notes...
```

### Appending Rules

1. **Always read the current log file** before any structural edit.
2. **Append, don't restructure** unless explicitly asked.
3. New phases get a `##` heading with a `---` separator after the previous section.
4. Never insert a `---` or a new `##` inside an existing section.
5. Keep entries **concise and factual** — do not rephrase the user's words.

### Formatting Conventions

| Element | Convention |
|---|---|
| Timestamps | `**HH:MM**` prefix on bullet (24h); get time via `date +"%H:%M"` |
| Mirror / optic names | Bold + exact case: **MR1L0**, **MR1L4** |
| YAG / imager names | Bold: **DG1 YAG**, **IM3L0** |
| Slit names | Bold: **SLITS1**, **SLITS 3M** |
| hutch-python commands | Fenced code block with `python` tag |
| Terminal commands | Inline backtick: `gas_detector_striptool` |
| Fixes / resolutions | Prefix: **Fix:** |
| Pending actions | Prefix: **Action:** |
| Policy / unconfirmed | Suffix: `(to be confirmed)` |
| External contacts | `(contact: Name)` |
| eLog references | Italicized quoted text: *"pointing at xrtyag3..."* |
| Energies | En-dash range: 1–13.5 keV |
| eLog or Confluence links | Inline markdown link |

---

## `/context` Command

**Do not handle `/context` here.** It is a pure lookup:

```bash
bin/hutch-state audit --hutch "$H" --experiment "$E"
```

Format that output — grouped as sample / beam / laser / last run / machine state, omitting
null fields. The audit is strictly better than the prose template this section used to carry,
because it returns each field's age and provenance, so stale values announce themselves
instead of being printed as though current. Flag anything with `"stale": true`.

## Context Propagation

Nothing to propagate. Sub-skills read what they need through `hutch-state` and resolve what
is missing, so there is no list of fields to inject and no chance of a sub-skill being handed
a value whose age it cannot see. This section used to be a per-sub-skill field table; it was
load-bearing only because three sub-skills were reading the state file from the wrong path.

## Context Recovery on Session Reset

1. Read `{experiment}_state.json`.
2. Load all fields into context.
3. Check `machine_state.last_checked` — if from a previous session, note it may be stale
   and offer to refresh via `/are-we-ready` before the next operation.
4. Confirm restored context with the user in one line before proceeding.

---

## `/sync-log` Command

Triggered by: `/sync-log`, "sync the log", "catch up the log", "log is empty", "why are the logs empty".

Replays the current conversation to reconstruct any log entries and state updates
that were missed by the ambient observation step.

### Step 1 — Read current state

Read `{experiment}_state.json` and `{experiment}_logs.md` to understand what has
already been recorded.

### Step 2 — Replay the conversation

Scan back through the full conversation history from the last logged timestamp.
For each exchange, apply the ambient observation classification rules:

| What was in the conversation | Entry to write |
|---|---|
| Sample / energy / delivery / transmission set or changed | Condition change entry |
| Run completed (any source — bridge, manual, inferred) | Run entry with number, tag, duration, XTC2 status |
| LUTE configured (`install_lute.py` completed) | LUTE config entry + update `lute_config` in state |
| AWR check with beam fault | Beam fault entry |
| Operational decision stated | Decision entry |
| Bridge connectivity issues, SSH failures | Infrastructure note |

### Step 3 — Write missing entries

Append all reconstructed entries to `{experiment}_logs.md` in chronological order.
Prefix each reconstructed entry with `*(reconstructed)*` to distinguish from
real-time entries:

```markdown
### Sync – {HH:MM}

- **{HH:MM}** *(reconstructed)* Sample set: {sample_name}, {photon_energy_eV} eV.
- **{HH:MM}** *(reconstructed)* LUTE configured. Workflows: {workflows}.
- **{HH:MM}** *(reconstructed)* ...
```

### Step 4 — Reconcile state JSON

Update any state fields that are out of sync with what actually happened in the
conversation (e.g. `lute_config`, `last_run_number`, `current_phase`).

### Step 5 — Confirm

Report to the user:
> *"Sync complete. Added {N} missing entries to the log (marked as reconstructed).
> State JSON updated: {changed fields}."*

---

## `/handoff` Command

When the user says `/handoff`, `end of shift`, `shift summary`, or `write handoff`:

### Step 1 — Gather shift data

1. Read the full `{experiment}_logs.md` for this session.
2. Review the current state object for beam conditions and sample summary.
3. Check current time via `date +"%H:%M"` for end-of-shift timestamp.

### Step 2 — Draft the handoff

Generate a shift summary in the LCLS Slack shift-summary format. Present it to the
user for review before finalizing.

```
{YYYY-MM-DD} ({Shift: Day / Swing / Night})
{technique} {experiment} {PI/spokesperson} {shift_n}/{total_shifts}

Beam Conditions & Tuning
{Narrative: photon energy delivered, rep rate, any tuning or delivery issues,
 how long it took to establish beam. Be concise — 2–4 sentences.}

Issues and Downtime Encountered ({total time lost, e.g. ~1.5 hrs lost})
{Narrative: faults, DAQ issues, equipment problems. If none: "No significant downtime."}

Summary: {one-word tone, e.g. "productive", "mixed", "rough"}
{Paragraph: science goal for the shift, what was accomplished, what comes next shift.
 Draw from the log file and state object. 3–6 sentences.}
```

### Step 3 — Finalize

1. Present the draft to the user: *"Here is the shift handoff draft. Please review and
   let me know if anything needs adjusting."*
2. After user confirms, append to `{experiment}_logs.md` as a new section:

```markdown
---

## Shift Handoff – {YYYY-MM-DD} ({Shift})

{full handoff text}
```

3. Ask the user: *"Post this to the eLog too?"* If yes, post via `@elog-copilot`
   with tag `shift-handoff`.

### Inputs to ask for if not in state

If `shift_n` / `total_shifts`, PI name, technique, or shift name (Day/Swing/Night) are
not known, ask for them in one prompt before drafting.
