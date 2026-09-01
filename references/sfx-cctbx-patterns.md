# SFX / CCTBX configuration patterns

Replaces `sfx-analysis-defaults.md`. That file carried tables of "typical values" for peak
finding, indexing rate and merge statistics, and opened by telling the reader not to trust
them ("use as sanity checks … not as pre-fills. Always confirm with the user"). Those tables
are gone. Parameter values are resolved or measured:

| Was a table here | Get it from |
|---|---|
| detector aliases | `hutch-resolve detectors --experiment E --run N` |
| unit cell, space group | `hutch-resolve cell F` (CRYST1 — never hand-typed) |
| device / PV names | `hutch-resolve pv --hutch H <device>` |
| geometry, mask paths | the experiment's `results/common/` tree; `ls` it, do not recall it |
| indexing yield, CC1/2, Rsplit, completeness | the scale and merge logs |

**Why the tables were actively harmful, not merely stale.** The old failure-mode table said
"indexing rate < 5% → geometry wrong (beam center or distance off by > 5%)". In the
`sfx-hutch-copilot-integration` campaign the baseline yield was 3.1% of spotfound events and
geometry was *fine*: `dispatch_hit_finder_minimum_number_of_reflections` was 40, which
discarded 407 frames carrying ≥ 20 spots — 2.5× more candidate frames than ever reached the
indexer. Lowering it to 20 tripled per-event yield (5.4% → 17.4%) and carried CC1/2 from
79.5% to 90.6%. A table lookup would have sent the operator hunting geometry for hours.

Diagnose from the funnel instead: aggregate the per-event debug CSVs under
`{work_dir}/r{run:04d}/index/debug/` and count `start`, `not_enough_spots_*`, `index_start`,
`integrate_ok_*`. **Report how many files you scanned** — a truncated scan produces a
plausible wrong total silently.

---

## Canonical SFX DAG — `!run_type` branching

This is a config pattern, not retrievable knowledge, so it stays written. `DARK` is always
empty: `takepeds`/`makepeds` happen outside LUTE.

```yaml
!LUTE_DAG
- !run_type
  DARK: []
  GEOM:
    task_name: "SmallDataProducer2"
    slurm_params: "--nodes=4 --ntasks-per-node=50 --partition=milano --account=lcls:{experiment}"
    next:
    - task_name: "BayFAIOptimizer2"
      slurm_params: "--nodes=1 --ntasks=120 --partition=milano --account=lcls:{experiment}"
      next: []
  DATA:
    task_name: "CCTBXIndexer"
    slurm_params: "--nodes=16 --ntasks-per-node=50 --partition=milano --account=lcls:{experiment}"
    next:
    - task_name: "CCTBXScaler"
      slurm_params: "--nodes=8 --ntasks-per-node=50 --partition=milano --account=lcls:{experiment}"
      next:
      - task_name: "CCTBXMerger"
        slurm_params: "--nodes=4 --ntasks-per-node=50 --partition=milano --account=lcls:{experiment}"
        next: []
```

- Use **managed** task names (`CCTBXIndexer`, not `IndexCCTBXXFEL`).
- `GEOM`: the `.poni` output must be deployed to the psana calibration DB before DATA runs.
- `DATA`: `SmallDataProducer2` is not needed for pure SFX — `CCTBXIndexer` reads psana directly.
- `wf_name = sfx_cctbx`, `--trigger END_OF_RUN`.
- `slurm_params` carries no `--time`. Nothing in LUTE does. If a wall-clock limit matters,
  submit through `hutch-lute sbatch --time`, which refuses to emit a script without one.

---

## d_min: a ranking hazard, stated up front

`d_min` appears in **both** `ScaleCCTBXXFEL` and `MergeCCTBXXFEL` and the two must never
disagree. Set it in one place and write both.

Overall CC1/2, Rsplit and completeness are all monotone in `d_min`, so **cutting `d_min`
improves all three even when the underlying data are identical.** Campaign evidence:
iteration 10 differed from iteration 9 only in `d_min` 2.0 → 2.2 Å, at identical coverage
(33551 events) and statistically identical lattices (6008 vs 6019). CC1/2 went 91.7% →
93.8% — computed on 6205 common indices instead of 8195. That is a re-cut, not an
improvement, and it must not be presented as one.

Judge `d_min` on the **per-shell** table: the deepest shell with CC1/2 above ~0.3 and merged
⟨I/σ(I)⟩ above ~0.5–1.0 and no incomplete-last-shell warning. Never rank two runs with
different `d_min` against each other on overall statistics.

---

## Under a fixed wall clock, `n_indexed` is not a parameter response

It is `coverage × yield`. Iterations 8 and 9 of the campaign had **identical** index-side
parameters, yet `n_indexed` went 4460 → 6019 because the job happened to attempt 33551
events instead of 22839. Always report `events_attempted` and `yield = images/events`
alongside `n_indexed`, and compare on yield.


---

## Symptom → knob

The one table worth keeping from the retired `/refine` command. **Check the funnel before
touching geometry** — that ordering is the campaign's most expensive lesson.

| Symptom | First thing to change | Where |
|---|---|---|
| **Indexing yield too low (CCTBX)** | `dispatch_hit_finder_minimum_number_of_reflections` — read the funnel first; a low rate is *not* reliably a geometry problem | `IndexCCTBXXFEL.phil_parameters` |
| Wall clock exhausted before the run ends | `radial_average_enable`→false; `output_composite_output`→false so a kill keeps partial results | `IndexCCTBXXFEL.phil_parameters` |
| An override appears to do nothing | the key is not referenced by the phil template, so it is silently dropped | `hutch-config --whitelist-dir …` |
| Job reported COMPLETED but produced nothing | `run_task.py` exits 0 on Task failure | `hutch-lute status <job_id>` |
| Wrong unit cell / space group | never hand-type it | `hutch-resolve cell <pdb>` |
| High Rsplit with many patterns | wrong symmetry, too few patterns, or partiality mismatch — check multiplicity before blaming parameters | scale/merge logs |
| Indexing rate too low (CrystFEL) | `min_peaks`, `pushres`, `indexing` | `IndexCrystFEL` block |

Inspecting output is a tool's job, not the operator's: the retired `/refine` asked the user
to run `h5py` and paste the result. That becomes `lute-stats` (MIGRATION.md item 1); until it
exists, read the scale and merge logs directly rather than asking for a paste.
