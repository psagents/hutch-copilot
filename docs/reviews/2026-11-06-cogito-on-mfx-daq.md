# Review, 2026-11-06: hutch-copilot in a Cogito session on mfx-daq

Phase 2 of the Cogito plan. A Cogito host (`/sdf/sw/cogito/current/cogito-host`, image 0.20.0-host30)
runs on mfx-daq since 2026-11-05 11:16; a session opened on it. Branch `ENH/cogito-on-mfx-daq`.

## What the session reported

The session started and asked for the experiment, but `hutch-daq state` failed before it could
reach the DAQ: `/sdf/home/m/mfxopr/daq/scripts/mfx.py` did not exist, `ls /sdf/home` showed only
`l`, and `/cds` was absent. The agent then treated an eLog lead (`mfx102390727`, scheduled
11-25/26) as untrusted and asked for the experiment. Both are the right behaviour (rule 3; never
guess the experiment).

## Root cause: the session runs in an Apptainer container

`cogito-host` runs the host, and every session's agent, in `cogito.sif` (Debian 13, glibc 2.41),
not on the node. A path outside the bind list does not exist inside. The bind list is
`/sdf/sw/cogito/0.20.0-host30/binds` plus the working folder:

```
/sdf/data /sdf/group /sdf/scratch /sdf/sw /lscratch   + $PWD (here /sdf/home/l/lconreux)
```

So on mfx-daq the session sees neither `/cds` nor `/reg/g` nor `/sdf/home/m/mfxopr`. That is why
the hostname says mfx-daq while the filesystem says S3DF.

## Measured: what extra binds fix

`APPTAINER_BIND` is honoured on top of the wrapper's list (`cogito-host exec`, mfx-daq, 10:24 PST):

| Tool, inside the container | default binds | `+ /cds,/reg/g,/sdf/home` |
|---|---|---|
| `hutch-happi list` | rc 4 (`/cds`) | **rc 0**, 29 devices |
| `hutch-epics moved` (archiver) | — | **rc 0** (network reaches pscaa01) |
| `hutch-lightpath path` | rc 4 (`/cds`) | **rc 0**, 34 s |
| `hutch-daq state` | rc 4 (`mfx.py` missing) | rc 4, **no reply from the control port**: same as the node (DAQ down) |
| `hutch-epics caget` | rc 4 | rc 4: the rhel9 `caget` needs `libncurses.so.6`, absent in Debian |
| conda `caget` (`pcds-6.1.0/epics/bin/linux-x86_64`) | — | **reads `MFX:ATT:COM:R_CUR`** |

Binding `/sdf/home/m/mfxopr` alone is not enough: the DAQ release named in `mfx.py` lives in
`/sdf/home/s/seshu/...`, so `/sdf/home` is the bind that works.

Not measured yet: `hutch-py run` (needs `/reg/g`, the pcds conda env and `engineering_tools`, all
under the binds above, but its interpreter is a rhel9 build in a Debian container).

## To do on this branch

1. **Launch environment for a host on a hutch machine**, owned in this repo, not in Cogito:
   `APPTAINER_BIND=/cds,/reg/g,/sdf/home` and `HUTCH_EPICS_BIN=<pcds env>/epics/bin/linux-x86_64`.
   `HUTCH_EPICS_BIN` is already the one override point for `caget`, so no code change.
2. Restart the mfx-daq host with it, and re-run `/are-we-ready` in a session.
3. `hutch-py run` inside the container, read-only, by the operator.
4. Ask the Cogito admins whether hutch nodes should get these binds in the shared `binds` file,
   rather than every user exporting them.

## Verdict so far

Phase 2 is **blocked on the container, not on hutch-copilot**: the skill, the hooks and the
experiment rule all behaved. With three extra binds and the conda `caget`, four of the five hutch
tools work inside the container; the fifth (`daq state`) fails the same way it does on the node.
