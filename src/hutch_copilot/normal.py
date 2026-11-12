"""hutch-normal: an operator-blessed snapshot of the hutch, and what differs from it.

The knowledge -- which devices are benign, which slit gap is too small -- lives in
each experiment's state file, attributed to a person. This module only applies it.
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import json
import pathlib

from .tools import daq, epics, lightpath
from .tools.common import OK, STATE_ERROR, UNREACHABLE, experiment_args, fail, out, parser, utcnow, \
    ds_root, state_file
from .tools.state import MissingValue, OPERATOR, State, StateError

#: Lightpath states that count as "in the beam".
_IN = {"Inserted", "Blocked"}

#: Lightpath states that count as unreadable.
_UNREADABLE = {"Disconnected", "Unknown", "Error"}


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
        return "warning", f"gap {x:g} x {y:g} mm, BLOCKED={blocked}, " \
               f"min {rule['min_mm']} mm"
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
            if state is None or state in _UNREADABLE:
                verdict, why = "unknown", f"state {state}"
            else:
                verdict, why = ("ok", "in, as required") if state in _IN \
                               else ("blocker", f"required in, is {state}")
        elif kind == "idle_closed":
            if state is None or state in _UNREADABLE:
                verdict, why = "unknown", f"state {state}"
            else:
                closed = name in blocking
                verdict = "blocker" if closed and taking_data \
                          else ("expected_idle" if closed else "ok")
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
               for d in sorted(set(was) | set(devices))
               if was.get(d) != devices.get(d)]
    return {"findings": findings,
            "blocking": [f["device"] for f in findings if f["verdict"] == "blocker"],
            "changed": changed}


def latest(hutch: str, experiment: str) -> dict:
    """This experiment's normal snapshot, else the newest one at this hutch."""
    unreadable = []
    try:
        own = State(hutch, experiment).get_entry("normal")
        return {"snapshot": own["value"], "from": experiment, "borrowed": False,
                "taken_at": own["value"].get("taken_at"), "unreadable": []}
    except MissingValue:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        unreadable.append(f"{state_file(hutch, experiment).parent}: {exc.__class__.__name__}")
    best = None
    hutch_dir = ds_root() / hutch.lower()
    if not hutch_dir.exists():
        return {"snapshot": None, "from": None, "borrowed": False, "taken_at": None,
                "unreadable": unreadable}
    for exp_dir in sorted(hutch_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        exp = exp_dir.name
        if exp == experiment:
            continue
        path = state_file(hutch, exp)
        try:
            snap = json.loads(path.read_text()).get("normal")
        except FileNotFoundError:
            continue
        except (PermissionError, json.JSONDecodeError) as exc:
            unreadable.append(f"{path.parent}: {exc.__class__.__name__}")
            continue
        if isinstance(snap, dict) and "taken_at" in snap:
            if best is None or snap["taken_at"] > best[1]["taken_at"]:
                best = (exp, snap)
        elif snap is not None:
            unreadable.append(f"{path.parent}: malformed normal")
    return {"snapshot": best and best[1], "from": best and best[0], "borrowed": bool(best),
            "taken_at": best and best[1]["taken_at"], "unreadable": unreadable}


def rule_pvs(snapshot: dict) -> list[str]:
    """Every PV named by this snapshot's rules."""
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
    rules = (json.loads(pathlib.Path(rules_file).read_text()) if rules_file
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
