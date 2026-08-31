"""Machine-checkable action classification for anything sent to a live session.

Before this module the Read-Only / Write / Beam-Critical split lived only as a
prose table in ``SKILL.md``, and each sub-skill re-derived it. The single thing
standing between a misclassification and an unconfirmed ``.mv()`` on a
lightpath component was the model remembering that table.

Here the classification is code, and ``bridge.py`` consults it *before*
transmitting. A write cannot be sent unless the caller declares a matching
confirmation class, so a model that mislabels an operation as read-only gets a
refusal rather than a moved motor.

This is defence in depth, not a proof. Patterns cannot capture every unsafe
expression, so the default for unrecognised code is WRITE -- never read-only.
Human confirmation is still required; this only makes it impossible to skip
by accident.
"""

from __future__ import annotations

import re

READ_ONLY = "read-only"
WRITE = "write"
BEAM_CRITICAL = "beam-critical"

#: Increasing severity. A snippet is classified at its highest match.
SEVERITY = {READ_ONLY: 0, WRITE: 1, BEAM_CRITICAL: 2}

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------
# Positive evidence that a snippet only reads. Everything must match one of
# these for the snippet to be considered read-only.
_READ_ONLY_PATTERNS = [
    r"^\s*$",
    r"^\s*#",
    r"\.position\b",
    r"\.inserted\b",
    r"\.removed\b",
    r"\.get\(\s*\)",
    r"\.read\(\s*\)",
    r"\.describe\(\s*\)",
    r"\bdaq\.state\b",
    r"\bdaq\.status\(\s*\)",
    r"\bdaq\.config_info\(\s*\)",
    r"\bget_run\(\s*\)",
    r"\bcaget\b",
    r"\bcamonitor\s+-n\s*1\b",
    r"\bhappi\b",
    r"\blightpath\b",
    r"\bprint\(",
    r"^\s*(import|from)\s",
    r"^\s*(for|if|else|elif|try|except|finally|with|def|return|pass)\b",
    r"^\s*[A-Za-z_][\w.]*\s*$",  # bare attribute read, e.g. `daq.state`
    r"^\s*\w+\s*=\s*[^=]",  # local assignment (evaluated below on its RHS)
    r"\bTrue\b|\bFalse\b|\bNone\b",
]

# Operations that change the machine.
_WRITE_PATTERNS = {
    r"\.mv\(": "device move (.mv)",
    r"\.mvr\(": "relative device move (.mvr)",
    r"\.move\(": "device move (.move)",
    r"\.umv\(": "device move (.umv)",
    r"\.set\(": "setpoint write (.set)",
    r"\.put\(": "PV write (.put)",
    r"\bcaput\b": "PV write (caput)",
    r"\.insert\(": "device insert",
    r"\.remove\(": "device remove",
    r"\bdaq\.begin\(": "DAQ begin",
    r"\bdaq\.configure\(": "DAQ configure",
    r"\bdaq\.end_run\(": "DAQ end_run",
    r"\bdaq\.connect\(": "DAQ connect",
    r"\bdaq\.disconnect\(": "DAQ disconnect",
    r"\bautorun\(": "DATA run (autorun)",
    r"\bgeomrun\(": "GEOM run (geomrun)",
    r"\btakepeds\(": "DARK run (takepeds)",
    r"\bmakepeds\b": "pedestal deployment (makepeds)",
    r"\bRE\(": "Bluesky plan execution",
    r"\.stage\(": "device stage",
    r"\bopen\([^)]*['\"][wa]": "file write",
}

# Devices whose motion changes delivered beam. Matched on the whole snippet,
# so `mfx_att_1.set(...)` and `att1.mv(...)` both trip it.
#
# Note the boundaries: `\b` does NOT work here, because device names are
# underscore-joined (`mfx_stopper_1`, `mr1l4_homs`) and `_` is a word character,
# so `\bstopper\b` never matches the name it was written for. Underscore must
# count as a token separator.
_TOK_START = r"(?<![A-Za-z0-9])"
_TOK_END = r"(?![A-Za-z0-9])"


def _tok(core: str) -> str:
    return _TOK_START + core + _TOK_END


_BEAM_CRITICAL_DEVICES = {
    _tok(r"stopper\w*"): "stopper",
    _tok(r"pps\w*"): "PPS stopper",
    _tok(r"mps\w*"): "MPS device",
    _tok(r"att(en\w*)?\d*"): "attenuator",
    _tok(r"apert\w*"): "aperture",
    _tok(r"shutter\w*"): "shutter",
    _tok(r"gatt\d*"): "gas attenuator",
    _tok(r"lodcm\w*"): "LODCM",
    _tok(r"mr\d+[a-z]\d+\w*"): "mirror (lightpath component)",
    _tok(r"picker\w*"): "pulse picker",
}


def _lines(code: str) -> list[str]:
    return [ln for ln in code.splitlines() if ln.strip()]


def classify(code: str) -> dict:
    """Classify a snippet. Returns {class, reasons, unmatched_lines}.

    Errs upward: any line that is not positively recognised as read-only makes
    the snippet at least WRITE.
    """
    reasons: list[str] = []
    verdict = READ_ONLY

    for pattern, label in _WRITE_PATTERNS.items():
        if re.search(pattern, code):
            reasons.append(label)
            verdict = WRITE

    if verdict == WRITE:
        for pattern, label in _BEAM_CRITICAL_DEVICES.items():
            if re.search(pattern, code, re.IGNORECASE):
                reasons.append(f"beam-critical target: {label}")
                verdict = BEAM_CRITICAL

    unmatched = [
        ln
        for ln in _lines(code)
        if not any(re.search(p, ln) for p in _READ_ONLY_PATTERNS)
    ]
    if verdict == READ_ONLY and unmatched:
        # Not provably read-only -> treat as a write and say which lines.
        verdict = WRITE
        reasons.append(
            "not provably read-only: "
            + "; ".join(ln.strip()[:60] for ln in unmatched[:3])
        )

    return {"class": verdict, "reasons": reasons, "unmatched_lines": unmatched}


class SafetyRefusal(PermissionError):
    """Raised when a snippet's class outranks the confirmation offered."""


def require_confirmation(code: str, confirmed_as: str | None) -> dict:
    """Gate a snippet. Raise unless ``confirmed_as`` matches its class or higher.

    ``confirmed_as`` is the class a human has actually approved. Passing a
    weaker class than the snippet requires is a refusal, not a downgrade --
    that is the whole point.
    """
    result = classify(code)
    needed = result["class"]
    if SEVERITY[needed] == 0:
        return result

    if confirmed_as is None:
        raise SafetyRefusal(
            f"this is a {needed} operation and no confirmation was supplied.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  show the command to the operator, then re-send with "
            f"--confirm {needed}"
        )
    if confirmed_as not in SEVERITY:
        raise SafetyRefusal(f"unknown confirmation class {confirmed_as!r}")
    if SEVERITY[confirmed_as] < SEVERITY[needed]:
        raise SafetyRefusal(
            f"confirmed as {confirmed_as!r} but this is {needed!r}.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  a {needed} operation needs an explicit {needed} confirmation, "
            f"including a beam-risk warning to the operator."
        )
    return result
