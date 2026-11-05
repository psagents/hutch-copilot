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
            verdict, why = ("ok", "in, as required") if state in _IN \
                           else ("blocker", f"required in, is {state}")
        elif kind == "idle_closed":
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
