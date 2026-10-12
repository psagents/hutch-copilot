# Running the tests

```bash
/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q
```

**Use that interpreter, not bare `python`.** The psconda `base` environment that S3DF puts on
`PATH` (`/sdf/group/lcls/ds/ana/sw/conda2-v4/inst/bin/python`) has no pytest, so `python -m
pytest` fails with `No module named pytest` and looks like a broken checkout rather than a
missing dependency. Any psana env under `conda2/inst/envs/` ships pytest; `ps_20260504` is the
one these tests are verified against.

`pyproject.toml` sets `pythonpath = ["."]`, so `hutch_tools` imports from the checkout with no
install step and there is no `conftest.py`. `pip install -e .` stays optional, by design.

## Conventions

- **The suite is in-process.** No test shells out to `bin/hutch-*`. CLI *shape* is checked by
  introspecting `build_parser()` (`test_doc_commands.py`), behaviour by calling module functions
  directly. That is why every module keeps `build_parser()` separate from `main()`.
- **Filesystem fixtures go through `monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))`** —
  and paths under it are built with the `paths.*` helpers (`paths` is the tests' alias for
  `hutch_tools.common`) rather than written as literals. A literal lets a test assert the
  same wrong directory the code produces: both agree and neither matches the filesystem.

  `setenv`, not `setattr`: every external path is declared in `bin/hutch-env` and read per
  call by `common.external_path()`, so one override mechanism works everywhere — including
  in a `multiprocessing` worker, which `setattr` cannot reach (see `_writer` in
  `test_log.py`).
- **SSH is not mocked anywhere.** Nothing that requires `psdev` or a DAQ machine has behavioural
  coverage; `hutch-daq status` and `logs` are deliberately in that category until there is a live
  experiment to verify them against.
- `test_doc_commands.py` scrapes every `bin/hutch-*` invocation out of every `*.md` in the skill
  and checks it against the real parser. Adding or renaming a binary means updating its
  `BINARIES` dict in the same commit, or its documented invocations go silently unchecked.
