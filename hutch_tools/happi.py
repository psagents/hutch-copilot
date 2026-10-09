"""hutch-happi: the device database -- what exists, where, and under which PV prefix.

Reads ``db.json`` directly rather than importing the ``happi`` package: it is
plain JSON, so this needs nothing beyond the standard library and works in any
interpreter. (A submodule named ``happi`` does not shadow the real package --
Python 3 imports are absolute.)

Device *state* is not here. Whether something blocks the beam is lightpath's
answer (``hutch-lightpath``) and a live readback is Channel Access's
(``hutch-epics``); this module reports only what the database records.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow


class HappiError(RuntimeError):
    """The database was reachable but could not answer. Never silently defaulted."""


def _record(value: Any, source: str, **extra: Any) -> dict:
    rec = {"value": value, "source": source, "retrieved_at": utcnow()}
    rec.update(extra)
    return rec


def db_path() -> str:
    """Path to the happi database, declared by bin/hutch-env."""
    return external_path("HAPPI_DB")


def read_local(path: str) -> str:
    """Read a file that is authoritative in itself. Raises rather than defaulting."""
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except OSError as exc:
        raise HappiError(f"cannot read {path}: {exc}") from exc


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except OSError as exc:
        raise HappiError(f"cannot read happi db {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HappiError(f"happi db {path} is not valid JSON: {exc}") from exc


def rows(db: str, beamline: str) -> list[dict]:
    """Devices on one beamline, ordered along the beam (z)."""
    entries = _load(db)
    beamline = beamline.strip().upper()
    found = []
    for entry in entries.values():
        if (entry.get("beamline") or "").upper() != beamline:
            continue
        try:
            z = float(entry.get("z"))
        except (TypeError, ValueError):
            z = None
        found.append({
            "name": entry.get("name"),
            "prefix": entry.get("prefix"),
            "device_class": entry.get("device_class"),
            "z": z,
        })
    # Unknown z sorts last rather than at the origin: a device with no recorded
    # position is not at the front of the beamline.
    found.sort(key=lambda r: (r["z"] is None, r["z"] if r["z"] is not None else 0))
    return found


def lightpath_names(db: str, beamline: str) -> set[str]:
    """Names on this beamline that lightpath tracks, so a caller can defer to it."""
    entries = _load(db)
    want = beamline.strip().upper()
    return {
        entry.get("name")
        for entry in entries.values()
        if (entry.get("beamline") or "").upper() == want and entry.get("lightpath")
    }


#: happi device_class suffixes whose prefix addresses an EPICS motor record, so
#: `prefix + ".RBV"` is a real PV. Anything else is a composite device.
_MOTOR_CLASSES = ("IMS", "EpicsMotor", "Newport", "DelayNewport", "BeckhoffAxis", "PCDSMotorBase")


def has_motor_record(row: dict) -> bool:
    """Is this device's prefix an EPICS motor record, so `prefix + '.RBV'` is real?"""
    return (row.get("device_class") or "").rsplit(".", 1)[-1] in _MOTOR_CLASSES


def inventory(hutch: str) -> list[dict]:
    """The hutch's happi rows, or a HappiError naming the likely mistake."""
    found = rows(db_path(), hutch)
    if not found:
        raise HappiError(
            f"happi has no devices with beamline={hutch.upper()!r}. "
            f"Beamline names in db.json are UPPERCASE; check the hutch name."
        )
    return found


def device(hutch: str, name: str, **kw: Any) -> dict:
    """One happi entry by name."""
    found = inventory(hutch)
    match = next((d for d in found if d["name"] == name), None)
    if match is None:
        raise HappiError(f"no happi device named {name!r} on {hutch.upper()}")
    return _record(
        match,
        f"resolved:happi {db_path()} beamline={hutch.upper()} name={name}",
    )


def pv(hutch: str, name: str, suffix: str = "", **kw: Any) -> dict:
    """PV name for a device: the happi prefix plus a caller-supplied suffix.

    No device-class -> suffix table ships here; the suffix is echoed back so
    the composed name stays auditable.
    """
    rec = device(hutch, name, **kw)
    prefix = rec["value"].get("prefix")
    if not prefix:
        raise HappiError(f"happi entry {name!r} has no prefix")
    return _record(
        prefix + suffix,
        rec["source"] + (f" suffix={suffix}" if suffix else ""),
        prefix=prefix,
        device_class=rec["value"].get("device_class"),
    )


def preset(hutch: str, device_name: str, experiment: str | None = None) -> dict:
    """A motor's saved positions. Nominal setpoints come from here, never a literal.

    Presets live in ``presets/beamline/`` for the standing beamline ones and in
    ``presets/<experiment>/`` for per-experiment ones -- not directly under
    ``presets/``, which is where this used to look and never found anything.
    """
    root = f"{external_path('PYTHON_APPS')}/{hutch.strip().lower()}/presets"
    subdirs = ["beamline"] + ([experiment] if experiment else [])
    tried = [f"{root}/{sub}/{device_name}.yml" for sub in subdirs]
    for path in tried:
        if pathlib.Path(path).is_file():
            return _record(read_local(path), f"resolved:{path}")
    raise HappiError(
        f"no preset for {device_name!r}: looked in {', '.join(tried)}. "
        f"Many devices have none -- ask the operator for the setpoint rather "
        f"than using a literal."
    )


def listing(hutch: str) -> dict:
    """Every happi device on this beamline, with no live reads at all."""
    found = inventory(hutch)
    return _record(
        found,
        f"resolved:happi {db_path()} beamline={hutch.upper()}",
        count=len(found),
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-happi: list | device | pv | preset."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-happi", _DOC)

    list_parser = sub.add_parser("list", help="every device on this beamline")
    list_parser.add_argument("--hutch", required=True)

    device_parser = sub.add_parser("device", help="one happi entry by name")
    device_parser.add_argument("--hutch", required=True)
    device_parser.add_argument("name")

    pv_parser = sub.add_parser("pv", help="compose a PV name from the happi prefix")
    pv_parser.add_argument("--hutch", required=True)
    pv_parser.add_argument("name")
    pv_parser.add_argument("--suffix", default="")

    preset_parser = sub.add_parser("preset", help="a motor's saved positions")
    preset_parser.add_argument("--hutch", required=True)
    preset_parser.add_argument("device")
    preset_parser.add_argument("--experiment", default=None,
                               help="also look in that experiment's preset dir")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-happi: list | device | pv | preset."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "list":
            rec = listing(args.hutch)
        elif args.cmd == "device":
            rec = device(args.hutch, args.name)
        elif args.cmd == "pv":
            rec = pv(args.hutch, args.name, args.suffix)
        elif args.cmd == "preset":
            rec = preset(args.hutch, args.device, args.experiment)
        else:
            return USAGE
        return out(rec)
    except (HappiError, OSError) as exc:
        return fail(exc, UNREACHABLE)
