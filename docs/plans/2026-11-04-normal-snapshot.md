# Normal snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/are-we-ready` and `take-run` judge the hutch against an operator-blessed "normal" snapshot (this experiment's, or else the hutch's latest from another experiment), so known-benign lightpath blockers stop being reported and real changes since last beamtime are.

**Architecture:** One new module `hutch_tools/normal.py` + `bin/hutch-normal` (`take | show | diff`). The snapshot is one field, `normal`, in the experiment's existing state file (`--kind operator`, no TTL). Rules are data in that field, not code: each names devices by glob and says how to judge them. `diff` is pure (snapshot + current readings → findings), so it is tested without a machine; `check_beam.py` / `check_daq.py` call it when a snapshot exists.

**Tech Stack:** Python 3.11+ stdlib (`fnmatch`, `json`), existing `hutch_tools.{state,lightpath,daq,epics,happi,common}`. Tests: pytest under `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python`.

**Spec:** this conversation (2026-11-04), summarised in *Decisions* below.

## Decisions (from the operator, 2026-11-04)

- Snapshot is **per experiment**, stored in that experiment's state file. No hutch-wide file.
- With no snapshot of its own, an experiment falls back to the **newest `normal` among other experiments of the same hutch**, ordered by `retrieved_at`, never by name. Unreadable directories are reported, never silently skipped.
- A borrowed snapshot's rules apply but every finding says `borrowed from <exp> (<date>)` until the operator re-takes it for this experiment.
- **No hutch knowledge in the repo.** Device names, limits and classifications live only in state, attributed to a person. The repo ships mechanism and tests with fixture data.
- Rule kinds:
  - `ignore` — never a blocker (ipm diodes).
  - `required_in` — must be inserted; flag if not (`mr1l4_homs`).
  - `idle_closed` — closed while idle is normal; still a blocker when about to take data (pulse picker, stoppers).
  - `min_gap` — slit: flag (warning, not blocker) if `:BLOCKED` is YES or X/Y gap below `min_mm`.
- DAQ: snapshot records the idle state (`running`, recording off at MFX); `take-run` accepts it instead of stopping.
- Seed for mfx102095027 from the 2026-11-01 23:39:57Z readiness run + operator rules + James Baxter's slit limits (Slack, 2026-11-04): dg1 0.25, dg2 US 0.1, MS 0.1, DS 0.01 mm.

## Global Constraints

- Every value written carries `source` and `kind` (`hutch-state` rule 1). Snapshot and rules are `kind=operator`.
- Unknown is not no: an unreadable PV in a rule yields finding `unknown`, never `ok`.
- A tool that cannot reach its source raises (rule 3). `diff` never substitutes a remembered value.
- One summary-line docstrings; comments only for non-obvious why.
- No device names or numbers in `hutch_tools/` or SKILL.md files except as clearly-labelled examples.

## File Structure

- Create `hutch_tools/normal.py` — snapshot shape, `latest()` lookup, pure `diff()`, `take()`, CLI.
- Create `bin/hutch-normal` — 5-line shim like `bin/hutch-state`.
- Create `tests/unit/test_normal.py`.
- Modify `are-we-ready/scripts/check_beam.py`, `check_daq.py` — attach `normal_diff` when a snapshot exists.
- Modify `are-we-ready/SKILL.md`, `take-run/SKILL.md`, `SKILL.md` (tool table), `README.md` (tool table).
- Modify `tests/unit/test_doc_commands.py` — add `"normal": "normal"` to `BINARIES`.

## Snapshot shape (the contract every task uses)

```json
{
  "taken_at": "2026-11-01T23:39:57+00:00",
  "taken_by": "lconreux",
  "beam_path": {"<device>": "<lightpath state>", "...": "..."},
  "daq_idle": {"state": "running", "recording": false, "config_alias": "BEAM"},
  "env": {"hutch_python_env": "pcds-5.9.1"},
  "rules": [
    {"match": "*_ipm", "kind": "ignore", "why": "diode", "by": "lconreux 2026-11-01"},
    {"match": "mr1l4_homs", "kind": "required_in", "why": "MFX branch mirror", "by": "..."},
    {"match": "sh45", "kind": "idle_closed", "why": "safety stopper", "by": "..."},
    {"match": "mfx_dg1_slits", "kind": "min_gap", "min_mm": 0.25,
     "pvs": {"x": "MFX:DG1:JAWS:ACTUAL_XWIDTH", "y": "MFX:DG1:JAWS:ACTUAL_YWIDTH",
             "blocked": "MFX:DG1:JAWS:BLOCKED"},
     "by": "James Baxter (Slack, 2026-11-04)"}
  ]
}
```

PV names in `min_gap` rules are resolved once, at rule-writing time, from `hutch-happi pv <name> <suffix>`, and stored so the rule is auditable.

---

### Task 1: `diff()` — judge current readings against a snapshot (pure)

**Files:**
- Create: `hutch_tools/normal.py`
- Test: `tests/unit/test_normal.py`

**Interfaces:**
- Produces: `diff(snapshot: dict, blocking: list[str], devices: dict[str, str], pv_values: dict[str, str], *, taking_data: bool) -> dict` returning `{"findings": [ {device, verdict, why} ], "blocking": [...], "changed": [ {device, was, now} ]}` where `verdict ∈ {"ignored","ok","expected_idle","blocker","warning","unknown"}`.

- [ ] **Step 1: Write the failing tests**

```python
"""Normal snapshot: judge the hutch against what an operator called normal."""

from __future__ import annotations

import pytest

from hutch_tools import normal

SNAP = {
    "taken_at": "2026-11-01T23:39:57+00:00",
    "beam_path": {"d1_ipm": "Inserted", "mirror": "Inserted", "stop": "Inserted",
                  "slit": "Inserted", "valve": "Removed"},
    "rules": [
        {"match": "*_ipm", "kind": "ignore"},
        {"match": "mirror", "kind": "required_in"},
        {"match": "stop", "kind": "idle_closed"},
        {"match": "slit", "kind": "min_gap", "min_mm": 0.1,
         "pvs": {"x": "S:X", "y": "S:Y", "blocked": "S:B"}},
    ],
}
NOW = {"d1_ipm": "Inserted", "mirror": "Inserted", "stop": "Inserted",
       "slit": "Inserted", "valve": "Removed"}
OPEN = {"S:X": "0.5", "S:Y": "0.5", "S:B": "NO"}


def verdicts(res):
    return {f["device"]: f["verdict"] for f in res["findings"]}


def test_benign_blockers_drop_out_while_idle():
    res = normal.diff(SNAP, ["d1_ipm", "mirror", "stop", "slit"], NOW, OPEN, taking_data=False)
    assert verdicts(res) == {"d1_ipm": "ignored", "mirror": "ok",
                             "stop": "expected_idle", "slit": "ok"}
    assert res["blocking"] == []


def test_idle_closed_stopper_blocks_when_taking_data():
    res = normal.diff(SNAP, ["stop"], NOW, OPEN, taking_data=True)
    assert verdicts(res)["stop"] == "blocker"
    assert res["blocking"] == ["stop"]


def test_required_mirror_out_is_a_blocker():
    res = normal.diff(SNAP, [], {**NOW, "mirror": "Removed"}, OPEN, taking_data=False)
    assert verdicts(res)["mirror"] == "blocker"


@pytest.mark.parametrize("pvs", [
    {"S:X": "2.8e-17", "S:Y": "0.3", "S:B": "NO"},   # 2026-11-04 mfx_dg2_midstream_slits
    {"S:X": "0.5", "S:Y": "0.5", "S:B": "YES"},
])
def test_a_closed_slit_is_a_warning(pvs):
    res = normal.diff(SNAP, ["slit"], NOW, pvs, taking_data=False)
    assert verdicts(res)["slit"] == "warning"


def test_an_unreadable_slit_is_unknown_not_ok():
    res = normal.diff(SNAP, ["slit"], NOW, {}, taking_data=False)
    assert verdicts(res)["slit"] == "unknown"


def test_an_unruled_blocker_stays_a_blocker():
    res = normal.diff(SNAP, ["valve"], {**NOW, "valve": "Inserted"}, OPEN, taking_data=False)
    assert verdicts(res)["valve"] == "blocker"
    assert res["changed"] == [{"device": "valve", "was": "Removed", "now": "Inserted"}]


def test_a_device_new_or_gone_since_the_snapshot_is_a_change():
    now = {k: v for k, v in NOW.items() if k != "valve"} | {"newdev": "Removed"}
    res = normal.diff(SNAP, [], now, OPEN, taking_data=False)
    assert {"device": "valve", "was": "Removed", "now": None} in res["changed"]
    assert {"device": "newdev", "was": None, "now": "Removed"} in res["changed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_normal.py -q`
Expected: FAIL, `ImportError: cannot import name 'normal'`

- [ ] **Step 3: Implement**

```python
"""hutch-normal: an operator-blessed snapshot of the hutch, and what differs from it.

The knowledge -- which devices are benign, which slit gap is too small -- lives in
each experiment's state file, attributed to a person. This module only applies it.
"""

from __future__ import annotations

import fnmatch

#: Lightpath states that count as "in the beam".
_IN = {"Inserted", "Blocked"}


def _rule(snapshot: dict, device: str) -> dict | None:
    return next((r for r in snapshot.get("rules", [])
                 if fnmatch.fnmatchcase(device, r["match"])), None)


def _gap(rule: dict, pv_values: dict[str, str]) -> tuple[str, str]:
    pvs = rule["pvs"]
    try:
        x, y = float(pv_values[pvs["x"]]), float(pv_values[pvs["y"]])
        blocked = pv_values[pvs["blocked"]]
    except (KeyError, ValueError):
        return "unknown", f"could not read {sorted(pvs.values())}"
    if blocked == "YES" or min(x, y) < rule["min_mm"]:
        return "warning", f"gap {x:g} x {y:g} mm, BLOCKED={blocked}, min {rule['min_mm']} mm"
    return "ok", f"gap {x:g} x {y:g} mm"


def diff(snapshot: dict, blocking: list[str], devices: dict[str, str],
         pv_values: dict[str, str], *, taking_data: bool) -> dict:
    """Judge current lightpath readings against a snapshot's rules and states."""
    findings = []
    for name in sorted(set(blocking) | {d for d in devices if _rule(snapshot, d)}):
        rule = _rule(snapshot, name) or {}
        kind, state = rule.get("kind"), devices.get(name)
        if kind == "ignore":
            verdict, why = "ignored", rule.get("why", "")
        elif kind == "required_in":
            verdict, why = ("ok", "in, as required") if state in _IN else ("blocker", f"required in, is {state}")
        elif kind == "idle_closed":
            closed = name in blocking
            verdict = "blocker" if closed and taking_data else ("expected_idle" if closed else "ok")
            why = rule.get("why", "")
        elif kind == "min_gap":
            verdict, why = _gap(rule, pv_values)
        elif name in blocking:
            verdict, why = "blocker", "no rule in the normal snapshot"
        else:
            continue
        findings.append({"device": name, "verdict": verdict, "why": why})

    was = snapshot.get("beam_path", {})
    changed = [{"device": d, "was": was.get(d), "now": devices.get(d)}
               for d in sorted(set(was) | set(devices)) if was.get(d) != devices.get(d)]
    return {"findings": findings,
            "blocking": [f["device"] for f in findings if f["verdict"] == "blocker"],
            "changed": changed}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_normal.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add hutch_tools/normal.py tests/unit/test_normal.py
git commit -m "ENH normal: judge lightpath readings against an operator snapshot"
```

---

### Task 2: `latest()` — this experiment's snapshot, else the hutch's newest

**Files:**
- Modify: `hutch_tools/normal.py`
- Test: `tests/unit/test_normal.py`

**Interfaces:**
- Consumes: `state.State(hutch, experiment).get_entry("normal")`, `common.ds_root()`, `common.state_file()`.
- Produces: `latest(hutch: str, experiment: str) -> dict` returning `{"snapshot": dict | None, "from": str | None, "borrowed": bool, "taken_at": str | None, "unreadable": [str]}`.

- [ ] **Step 1: Write the failing tests**

```python
from hutch_tools import state


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    return tmp_path


def _seed(exp, taken_at):
    state.State("mfx", exp).set("normal", {**SNAP, "taken_at": taken_at},
                                source="test", kind="operator")


def test_own_snapshot_wins(root):
    _seed("mfxA", "2026-10-01T00:00:00+00:00")
    _seed("mfxB", "2026-11-01T00:00:00+00:00")
    got = normal.latest("mfx", "mfxA")
    assert (got["from"], got["borrowed"]) == ("mfxA", False)


def test_fallback_is_newest_by_time_not_name(root):
    _seed("mfx999", "2026-09-01T00:00:00+00:00")
    _seed("mfx100", "2026-11-01T00:00:00+00:00")
    got = normal.latest("mfx", "mfxNEW")
    assert (got["from"], got["borrowed"]) == ("mfx100", True)


def test_unreadable_experiments_are_reported(root):
    _seed("mfx100", "2026-11-01T00:00:00+00:00")
    locked = root / "mfx" / "mfxLOCK" / "results" / "psagents"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        got = normal.latest("mfx", "mfxNEW")
    finally:
        locked.chmod(0o755)
    assert any("mfxLOCK" in u for u in got["unreadable"])


def test_no_snapshot_anywhere_is_none_not_an_error(root):
    got = normal.latest("mfx", "mfxNEW")
    assert got["snapshot"] is None and got["from"] is None
```

- [ ] **Step 2: Run to verify fail**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_normal.py -q -k "own or fallback or unreadable or anywhere"`
Expected: FAIL, `AttributeError: module 'hutch_tools.normal' has no attribute 'latest'`

- [ ] **Step 3: Implement** (append to `normal.py`; add imports `json` and `from .common import ds_root, state_file` and `from .state import MissingValue, State`)

```python
def latest(hutch: str, experiment: str) -> dict:
    """This experiment's normal snapshot, else the newest one at this hutch."""
    try:
        own = State(hutch, experiment).get_entry("normal")
        return {"snapshot": own["value"], "from": experiment, "borrowed": False,
                "taken_at": own["value"].get("taken_at"), "unreadable": []}
    except (MissingValue, OSError):
        pass
    best, unreadable = None, []
    for exp_dir in sorted((ds_root() / hutch.lower()).iterdir()):
        exp = exp_dir.name
        if exp == experiment:
            continue
        path = state_file(hutch, exp)
        try:
            snap = json.loads(path.read_text()).get("normal")
        except FileNotFoundError:
            continue
        except (PermissionError, json.JSONDecodeError) as exc:
            # A group-only directory reads as absent to anyone outside the group;
            # say so, or "nothing changed" is claimed from a search that never ran.
            unreadable.append(f"{path.parent}: {exc.__class__.__name__}")
            continue
        if snap and (best is None or snap["taken_at"] > best[1]["taken_at"]):
            best = (exp, snap)
    return {"snapshot": best and best[1], "from": best and best[0], "borrowed": bool(best),
            "taken_at": best and best[1]["taken_at"], "unreadable": unreadable}
```

- [ ] **Step 4: Run to verify pass**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_normal.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add hutch_tools/normal.py tests/unit/test_normal.py
git commit -m "ENH normal: fall back to the hutch's newest snapshot from another experiment"
```

---

### Task 3: CLI `bin/hutch-normal show | diff | take`

**Files:**
- Modify: `hutch_tools/normal.py`
- Create: `bin/hutch-normal`
- Modify: `tests/unit/test_doc_commands.py` (`BINARIES` gets `"normal": "normal"`)
- Test: `tests/unit/test_normal.py`

**Interfaces:**
- Consumes: `latest()`, `diff()`, `lightpath.beam_path(hutch)`, `daq.state(hutch)`, `epics.caget(pvs)`, `State.set()`.
- Produces: `current(hutch: str) -> dict` `{"blocking", "devices", "daq"}`; `rule_pvs(snapshot) -> list[str]`; `main(argv)`; CLI:
  - `hutch-normal show --hutch H --experiment E` → `latest()` output.
  - `hutch-normal diff --hutch H --experiment E [--taking-data]` → `diff()` plus `from`, `borrowed`, `taken_at`, `unreadable`, `daq_changed`.
  - `hutch-normal take --hutch H --experiment E --source S [--rules FILE]` → reads now, keeps existing rules (own or borrowed) unless `--rules` given, writes `normal` with `kind=operator`.

- [ ] **Step 1: Write the failing tests**

```python
from hutch_tools import daq, epics, lightpath


@pytest.fixture()
def machine(monkeypatch):
    monkeypatch.setattr(lightpath, "beam_path", lambda hutch, **kw: {"value": {
        "blocking": ["stop"], "devices": [{"name": k, "state": v} for k, v in NOW.items()]}})
    monkeypatch.setattr(daq, "state", lambda hutch, **kw: {"value": {
        "state": "running", "recording": False, "config_alias": "BEAM"}})
    monkeypatch.setattr(epics, "caget", lambda pvs, **kw: {p: OPEN[p] for p in pvs})


def test_take_keeps_borrowed_rules_and_records_the_operator(root, machine, capsys):
    _seed("mfxOLD", "2026-11-01T00:00:00+00:00")
    assert normal.main(["take", "--hutch", "mfx", "--experiment", "mfxNEW",
                        "--source", "operator lconreux: this is normal"]) == 0
    entry = state.State("mfx", "mfxNEW").get_entry("normal")
    assert entry["kind"] == "operator"
    assert entry["value"]["rules"] == SNAP["rules"]
    assert entry["value"]["daq_idle"]["state"] == "running"


def test_diff_reports_where_the_snapshot_came_from(root, machine, capsys):
    import json as _j
    _seed("mfxOLD", "2026-11-01T00:00:00+00:00")
    assert normal.main(["diff", "--hutch", "mfx", "--experiment", "mfxNEW"]) == 0
    res = _j.loads(capsys.readouterr().out)
    assert res["borrowed"] and res["from"] == "mfxOLD"
    assert res["blocking"] == []


def test_diff_with_no_snapshot_says_so(root, machine, capsys):
    import json as _j
    assert normal.main(["diff", "--hutch", "mfx", "--experiment", "mfxNEW"]) == 0
    assert _j.loads(capsys.readouterr().out)["snapshot"] is None
```

- [ ] **Step 2: Run to verify fail**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_normal.py -q -k "take or diff_"`
Expected: FAIL, `AttributeError: ... 'main'`

- [ ] **Step 3: Implement** (append; imports `argparse`, `getpass`, `from . import daq, epics, lightpath`, `from .common import OK, STATE_ERROR, UNREACHABLE, experiment_args, fail, out, parser, utcnow`, `from .state import OPERATOR, StateError`)

```python
def rule_pvs(snapshot: dict) -> list[str]:
    return [pv for r in snapshot.get("rules", []) for pv in r.get("pvs", {}).values()]


def current(hutch: str) -> dict:
    """Lightpath and DAQ readings, now."""
    path = lightpath.beam_path(hutch)["value"]
    return {"blocking": path["blocking"],
            "devices": {d["name"]: d["state"] for d in path["devices"]},
            "daq": daq.state(hutch)["value"]}


def _judge(hutch: str, experiment: str, taking_data: bool) -> dict:
    found = latest(hutch, experiment)
    snap = found["snapshot"]
    if snap is None:
        return found
    now = current(hutch)
    try:
        pvs = epics.caget(rule_pvs(snap))
    except epics.EpicsError:
        pvs = {}  # every min_gap rule then reports `unknown`, never `ok`
    res = diff(snap, now["blocking"], now["devices"], pvs, taking_data=taking_data)
    idle = snap.get("daq_idle", {})
    res["daq_changed"] = {k: {"was": v, "now": now["daq"].get(k)}
                          for k, v in idle.items() if now["daq"].get(k) != v}
    return {**found, **res}


def _take(hutch: str, experiment: str, source: str, rules_file: str | None) -> dict:
    now = current(hutch)
    rules = (json.loads(open(rules_file).read()) if rules_file
             else (latest(hutch, experiment)["snapshot"] or {}).get("rules", []))
    snap = {"taken_at": utcnow(), "taken_by": getpass.getuser(),
            "beam_path": now["devices"],
            "daq_idle": {k: now["daq"].get(k) for k in ("state", "recording", "config_alias")},
            "rules": rules}
    return State(hutch, experiment).set("normal", snap, source=source, kind=OPERATOR, ttl_s=None)


_DOC = """hutch-normal: show | diff | take."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-normal", _DOC)
    experiment_args(sub.add_parser("show", help="the snapshot in force, and where it came from"))
    d = sub.add_parser("diff", help="what differs from normal, now")
    experiment_args(d)
    d.add_argument("--taking-data", action="store_true",
                   help="idle-closed devices (stoppers, picker) count as blockers")
    t = sub.add_parser("take", help="record now as normal; operator-attributed")
    experiment_args(t)
    t.add_argument("--source", required=True, help="who said this is normal, and when")
    t.add_argument("--rules", help="JSON file of rules; default: keep the snapshot in force")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-normal: show | diff | take."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "show":
            return out(latest(args.hutch, args.experiment))
        if args.cmd == "diff":
            return out(_judge(args.hutch, args.experiment, args.taking_data))
        return out(_take(args.hutch, args.experiment, args.source, args.rules))
    except StateError as exc:
        return fail(exc, STATE_ERROR)
    except (lightpath.LightpathError, daq.DaqError, OSError) as exc:
        return fail(exc, UNREACHABLE)
```

`bin/hutch-normal`:

```python
#!/usr/bin/env python3
"""hutch-normal entry point. Works from a checkout with no install step."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from hutch_tools.normal import main
raise SystemExit(main())
```

Then `chmod +x bin/hutch-normal`, and in `tests/unit/test_doc_commands.py` add `"normal": "normal",` to `BINARIES`.

- [ ] **Step 4: Run full suite**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add hutch_tools/normal.py tests/unit/test_normal.py tests/unit/test_doc_commands.py bin/hutch-normal
git commit -m "ENH bin/hutch-normal: show, diff and take the normal snapshot"
```

---

### Task 4: Wire into `/are-we-ready` and `take-run`

**Files:**
- Modify: `are-we-ready/scripts/check_beam.py`
- Modify: `are-we-ready/SKILL.md`, `take-run/SKILL.md`, `SKILL.md`, `README.md`
- Test: `tests/unit/test_readiness.py`

**Interfaces:**
- Consumes: `normal.latest()`, `normal.diff()`, `normal.rule_pvs()`.
- Produces: `check_beam.py --experiment E` adds `"normal": {from, borrowed, taken_at, findings, blocking, changed, unreadable}` to its output. Without `--experiment` output is unchanged.

- [ ] **Step 1: Write the failing test** (in `test_readiness.py`)

```python
def test_check_beam_attaches_the_normal_diff(tmp_path, monkeypatch, capsys):
    from hutch_tools import epics, normal, state
    monkeypatch.setenv("HUTCH_DS_ROOT", str(tmp_path))
    state.State("mfx", "mfxOLD").set("normal", {
        "taken_at": "2026-11-01T00:00:00+00:00", "beam_path": {"d_ipm": "Inserted"},
        "rules": [{"match": "*_ipm", "kind": "ignore"}]}, source="t", kind="operator")
    monkeypatch.setattr(lightpath, "beam_path", lambda hutch, **kw: {
        "value": {"blocking": ["d_ipm"], "devices": [{"name": "d_ipm", "state": "Inserted"}]},
        "blocking": ["d_ipm"], "settled": True, "source": "fake", "retrieved_at": "now"})
    monkeypatch.setattr(epics, "caget", lambda pvs, **kw: {})
    assert check_beam.main(["--hutch", "mfx", "--experiment", "mfxNEW"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["normal"]["borrowed"] and res["normal"]["blocking"] == []
```

- [ ] **Step 2: Run to verify fail**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit/test_readiness.py -q -k normal_diff`
Expected: FAIL, `unrecognized arguments: --experiment`

- [ ] **Step 3: Implement** in `check_beam.py`

```python
from hutch_tools import epics, lightpath, normal
...
    ap.add_argument("--experiment", default=None,
                    help="judge against this experiment's normal snapshot, else the hutch's latest")
    args = ap.parse_args(argv)
    try:
        res = lightpath.beam_path(args.hutch, settle_s=args.settle_s)
    except (lightpath.LightpathError, OSError) as exc:
        return fail(exc, UNREACHABLE)
    if args.experiment:
        found = normal.latest(args.hutch, args.experiment)
        if found["snapshot"]:
            v = res["value"]
            try:
                pvs = epics.caget(normal.rule_pvs(found["snapshot"]))
            except epics.EpicsError:
                pvs = {}
            found.update(normal.diff(found.pop("snapshot"), v["blocking"],
                                     {d["name"]: d["state"] for d in v["devices"]},
                                     pvs, taking_data=False))
        res["normal"] = found
    return out(res)
```

Docs:
- `are-we-ready/SKILL.md`: call `check_beam.py --hutch "$H" --experiment "$E"`. Add a bullet: *`normal.blocking` replaces `blocking` when present. Report `ignored`/`expected_idle` once in a line, `warning` (a slit near-closed) with its gap, and every `changed` entry. If `normal.borrowed`, say "rules from `<from>` (`<taken_at>`), not yet confirmed for `$E`". `normal.snapshot: null` → no snapshot: report raw lightpath and offer `hutch-normal take`. Non-empty `unreadable` → say which experiments could not be searched.*
- `take-run/SKILL.md` DAQ gate: replace the `running` row with *`running` — if `hutch-normal diff` shows no `daq_changed` (this is the hutch's normal idle) proceed; else stop, "DAQ is already in a run. End it first?"*. Before sending the run, `bin/hutch-normal diff --hutch "$H" --experiment "$E" --taking-data`; any `blocking` → stop and name them.
- `SKILL.md` and `README.md` tool tables: add `| bin/hutch-normal | The operator's "normal" snapshot, and what differs from it now |`.

- [ ] **Step 4: Run full suite**

Run: `/sdf/group/lcls/ds/ana/sw/conda2/inst/envs/ps_20260504/bin/python -m pytest tests/unit -q`
Expected: all pass (including `test_doc_commands` on the new invocations)

- [ ] **Step 5: Commit**

```bash
git add are-we-ready take-run SKILL.md README.md tests/unit/test_readiness.py
git commit -m "ENH are-we-ready, take-run: judge against the normal snapshot"
```

---

### Task 5: Seed mfx102095027 (operator data, not code)

Writes to the experiment state file. **Show the JSON to the operator and get an OK before writing.**

- [ ] **Step 1: Resolve slit PVs from happi** (not typed by hand)

```bash
for d in mfx_dg1_slits mfx_dg2_upstream_slits mfx_dg2_midstream_slits mfx_dg2_downstream_slits; do
  for s in :ACTUAL_XWIDTH :ACTUAL_YWIDTH :BLOCKED; do bin/hutch-happi pv --hutch mfx $d $s; done
done
```

- [ ] **Step 2: Write `/tmp/mfx_rules.json`** from the resolved PVs:

```json
[
  {"match": "*_ipm", "kind": "ignore", "why": "diode, inserted for diagnostics", "by": "lconreux 2026-11-04"},
  {"match": "mr1l4_homs", "kind": "required_in", "why": "mirror that sends beam to MFX", "by": "lconreux 2026-11-01"},
  {"match": "mfx_pulsepicker", "kind": "idle_closed", "why": "closed when idle, open for beam", "by": "lconreux 2026-11-04"},
  {"match": "sh45", "kind": "idle_closed", "why": "safety stopper", "by": "lconreux 2026-11-01"},
  {"match": "mfx_dg1_slits", "kind": "min_gap", "min_mm": 0.25, "pvs": {"x": "<resolved>", "y": "<resolved>", "blocked": "<resolved>"}, "by": "James Baxter (Slack, 2026-11-04)"},
  {"match": "mfx_dg2_upstream_slits", "kind": "min_gap", "min_mm": 0.1, "pvs": {"...": "..."}, "by": "James Baxter (Slack, 2026-11-04)"},
  {"match": "mfx_dg2_midstream_slits", "kind": "min_gap", "min_mm": 0.1, "pvs": {"...": "..."}, "by": "James Baxter (Slack, 2026-11-04)"},
  {"match": "mfx_dg2_downstream_slits", "kind": "min_gap", "min_mm": 0.01, "pvs": {"...": "..."}, "by": "James Baxter (Slack, 2026-11-04)"}
]
```

(`<resolved>` is filled from Step 1's output at execution time, not a placeholder in the design.)

- [ ] **Step 3: At the next shift, with beam and the DAQ in its normal idle, operator OK:**

```bash
bin/hutch-normal take --hutch mfx --experiment mfx102095027 --rules /tmp/mfx_rules.json \
  --source "operator lconreux <date>: normal idle; rules per lconreux 11-01/11-04 and James Baxter (Slack 11-04)"
bin/hutch-normal diff --hutch mfx --experiment mfx102095027
```

Expected: `blocking: []`; `mfx_dg2_midstream_slits` → `warning` if still at 0 gap (open item for MFX staff).

Until then, a seed from Sunday's reading is possible: the 23:39:57Z blocker list and DAQ state from the session transcript, `source` naming that transcript. Its `beam_path` is partial (transcript truncated), so `changed` would be noisy — prefer the live take.

- [ ] **Step 4: Log it**

```bash
bin/hutch-log append --hutch mfx --experiment mfx102095027 "normal snapshot taken; <diff summary>"
```

---

## Self-review

- Coverage: per-experiment storage (T2/T3), fallback newest-by-time + unreadable reported (T2), borrowed labelled (T2/T4 docs), no knowledge in repo (rules only in state; T5 is data), four rule kinds (T1), slit `:BLOCKED` or gap < min (T1), warning not blocker (T1), DAQ idle accepted (T3 `daq_changed`, T4 take-run), seed with James Baxter attribution (T5). Archiver "when did it move" for a `changed` motor: existing `hutch-epics moved`, referenced from the are-we-ready docs bullet — no code.
- Types: `diff` / `latest` / `rule_pvs` / `current` signatures consistent across T1–T4.
