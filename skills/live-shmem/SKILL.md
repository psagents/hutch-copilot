---
name: live-shmem
description: >
  Live-data sub-skill for hutch-copilot. How to read the DAQ's monitoring shared memory
  (MEB) for any live analysis the operator asks for: find the shmem, write a psana2
  reader, test it offline, have the operator launch it on the MEB node, and watch its
  output. Knowledge only: it ships no monitor and no metric.
  Triggers on: live display, live monitor, online monitoring, shared memory, shmem, MEB,
  mebuser, watch the detector live, live metric, optimise on live data.
---

> Part of `hutch-copilot`: its rules apply here, and its commands run from that skill's
> directory (`bin/hutch-*`). If it is not loaded in this session, load it first.

# Live data from shared memory

The operator asks for something live: a ring intensity, a hit rate, a beam-position trend.
Nothing here decides *what* to compute. That is the operator's question, and `@ask-lcls2`
is the advisor for the psana2 API. This file covers the plumbing, which is the same for
every live analysis and was learned the hard way on 2026-11-20 (mfx101948627).

## 1. Find the shmem: resolve it, never write it out

```bash
bin/hutch-daq --hutch "$H" config      # the cnf the DAQ launched from
bin/hutch-daq --hutch "$H" state       # which processes are up, on which host
```

In the cnf, a user MEB is a `monReqServer` line (`meb_cmd`) with `-t <tag>`. The tag is
the shmem name and `host:` is its node: for example `id: "mebuser0"` on drp-srcf-mon011 with
`-t {hutch_meb}_mebuser0`. Expand the f-string variables from the same file.

- **Use a user MEB, never AMI's.** The `meb{N}_{instance}` processes (`-t {hutch_meb}{instance}`)
  feed AMI. Readers there take events from AMI.
- **It must be up.** `state` must show the MEB process alive and the DAQ past `connect`. A
  MEB that is not running is a question for the operator, not something to start.
- More than one user MEB, or none: ask the operator which to use.

## 2. Where the reader runs

- **On the MEB node only.** Shared memory is local to that host. mfx-daq cannot see it, and
  the `/sdf/data` xtc2 copy runs one run behind, so it is no substitute.
- **Through Slurm as the hutch's opr account:**
  `srun -p drpq -w <node> -n 1 -c 4 --time=02:00:00 <start script>`. Your own ssh and srun to
  that node are refused, so the **operator launches it**. Write the scripts, then hand them
  the exact command.
- In the start script, source `$HUTCH_PSCONDA` (bin/hutch-env) and `unset DISPLAY`. srun
  forwards the console's DISPLAY, but the node is headless.

## 3. The reader

```python
ds = DataSource(shmem=TAG, detectors=[...])   # live
ds = DataSource(exp=EXP, run=RUN, detectors=[...], max_events=N)   # the same code, offline
```

- **Test offline first**, on a recent run on disk, with the same code path behind one switch
  (`argv[1]`: `shmem` or a run number). Run it in the background, as the probe rule says.
- Pass `detectors=[...]`, and only what the metric needs.
- Every event carries its own timestamp. Report the lag (now minus event time) with each
  output so a stalled reader is visible.

## 4. Output contract

Write to a directory that both the console and this session can read (the opr account's
home):

- `latest.png`: write it to a temp name, then `os.replace`, so a viewer never reads half a
  file. The operator watches it with `display -update 10 latest.png` on the console. You view
  it with Read.
- `metrics.csv`: append-only, one row per window, flushed. Columns are `t`, `run`, `n_events`,
  `lag_s`, then the metric. Watch it with the Monitor tool (an `until`/`tail -f` filter on
  the column you care about) or `/loop`, never by polling in a turn.

## 5. Pitfalls

- **The shmem stalls about 2 min at every run change.** A gap means no data, never zero.
  Say so when a scan point falls in one, and re-take it.
- **Compare live with live only.** MEB events are a sample of what lands on disk, and the
  normalisation window differs, so a live number is not comparable with an offline one.
- **Metric-driven scans:** each move is a `bin/hutch-py run --devices <name>` (approved at
  the prompt), then a dwell, then the CSV rows from that dwell's window. A motor's `.VAL`
  lags `.RBV`, so record the readback.
- Stopping is the operator's: Ctrl-C in the srun terminal, then close the viewer.

## Worked example

`~lconreux/jet_monitor/` (2026-11-20, mfx101948627): a jungfrau solvent-ring metric over
`mfx_mebuser0`, plus the JET:Y scan it drove (`scan_1452/`). Read it for the shape, not the
metric. `jet_display.py` is the reader, `start_on_mon011.sh` the node-side script, `go.sh`
the operator's launcher.
