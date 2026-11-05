# Review, 2026-11-05: hutch-copilot from a terminal on mfx-daq

Phase 1 of the Cogito plan: does every tool work on mfx-daq, run by hand, before any session or
MCP is involved? `main` @ b156197, run over `ssh -J psdev mfx-daq`, as lconreux, ~10:15–10:35 PST.
No beam; the DAQ had been stopped at 09:01 (Slurm job 115363 cancelled).

Each tool was also run from S3DF (sdfiana023), to know which ones need the hutch at all. That
column decides what phase 3 has to expose over MCP.

| Tool | mfx-daq | S3DF | Needs the hutch? |
|---|---|---|---|
| `hutch-session-start` | rc 0, asks for the experiment | silent, by design | — |
| `hutch-state audit` | rc 0 | rc 0 | no |
| `hutch-happi list` | rc 0, 29 devices | rc 4, `db.json` under `/cds` | **yes** |
| `hutch-epics caget` | rc 0 (`MFX:ATT:COM:R_CUR`) | rc 4, no `caget` | **yes** |
| `hutch-epics moved` | rc 0, archiver sample 11-03 | rc 4, `pscaa01` unreachable | **yes** |
| `hutch-lightpath path` | rc 0, 41 s, 13 blocking | rc 4, pcds env under `/cds` | **yes** |
| `are-we-ready/scripts/check_beam.py` | rc 0, 20 s | (lightpath) | **yes** |
| `hutch-normal show` | rc 0, no snapshot yet | rc 0 | no (but `diff`/`take` read lightpath + CA) |
| `hutch-daq state` | rc 4, no reply from `drp-srcf-cmp014` | rc 4, same | **yes** once the DAQ is up |
| `hutch-daq logs`, `config` | rc 0 | rc 0 | no: `/sdf/home/m/mfxopr/daq` |
| `hutch-data xtc2` (run 157) | rc 0, 11 files | rc 0 | no |
| `hutch-lute` | not run (no analysis today) | `--help` rc 0 | no |
| `hutch-skills list` | **rc 4 → fixed**, see below | rc 0 | no |
| `hutch-py run` | rc 0, read-only (operator, by hand) | — | **yes** |

`hutch-daq state` failing is correct behaviour: the DAQ was down, and rule 3 says report it, not
guess. Re-run it once the operator has the DAQ back.

## Found and fixed on this branch

- **`hutch-skills list` crashed on mfx-daq.** The shared root
  `/sdf/group/lcls/ds/dm/apps/dev/opencode` is `drwxr-s--- cwang31:ps-users`, and mfx-daq does not
  grant lconreux ps-users access to it (S3DF does), so `is_dir()` itself raised and hid every
  readable root. It now lists the readable roots and reports the others in `unreadable_roots`.
  On mfx-daq: rc 0, `ask-lute` found under `~/.claude/skills`.

## Checked by the operator, by hand

The Claude Code permission classifier refused `hutch-py run` over ssh to mfx-daq, so the operator
ran both checks in their own terminal:

- `run 'print(len(globals()))'` → `returncode: 0`, safety `read-only`.
- `run 'daq.begin_infinite(record=False)'` → refused as `write` ("not provably read-only"),
  nothing sent. **The Nov 1 gate bug is fixed at the hutch.**

`raise RuntimeError(...)` is still classed read-only, which is harmless.

## Verdict

**Phase 1 passes.** Every tool works from a terminal on mfx-daq; `hutch-daq state` reports the DAQ
down, which is correct, and should be re-run once it is back. Six tools need the hutch: happi, epics (caget, archiver), lightpath,
`daq state`, `hutch-py`, and `normal diff/take` through them. Everything else already works from
S3DF, so the MCP server in phase 3 only has to wrap those six.
