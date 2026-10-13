"""Provenance-carrying experiment state store."""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import tempfile
from typing import Any

from .common import (
    STATE_ERROR,
    USAGE,
    experiment_args,
    fail,
    json_or_str,
    log_file,
    out,
    parser,
    state_file,
    utcnow,
    xtc2_dir,
)

# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------
#: A value read from an authoritative source by a resolver. ``detail`` should
#: name the source precisely enough to re-run it, e.g.
#: ``resolved:psana2 detinfo exp=mfx101624926 run=36``.
RESOLVED = "resolved"
#: A human told us. Trustworthy for intent, not for machine truth.
OPERATOR = "operator"
#: Derived by reasoning from other values. Never treat as measurement.
INFERRED = "inferred"

VALID_KINDS = (RESOLVED, OPERATOR, INFERRED)

#: Default time-to-live in seconds, longest matching key prefix wins.
#: ``None`` means "does not expire" -- correct for operator-declared intent.
DEFAULT_TTL: dict[str, float | None] = {
    "beampath": 30.0,
    "pv": 10.0,
    "machine_state": 600.0,  # matches the pre-existing 10-minute staleness rule
    "daq": 300.0,
    "detectors": 300.0,
    "devices": 3600.0,
    "presets": 3600.0,
    "photon_energy_eV": 900.0,
    "rep_rate_Hz": 900.0,
    "pulse_energy_uJ": 300.0,
    "last_run_number": None,
    "lute_config": None,
    "sample_name": None,
    "concentration": None,
    "sample_delivery": None,
    "transmission": None,
    "current_phase": None,
    "sync_log": None,  # the /sync-log watermark: a position in a transcript, not a reading
}


class StateError(RuntimeError):
    pass


class StaleValue(StateError):
    """Raised when a cached value is older than its TTL and no fallback was allowed."""

    def __init__(self, field: str, age_s: float, ttl_s: float, entry: dict):
        self.field, self.age_s, self.ttl_s, self.entry = field, age_s, ttl_s, entry
        super().__init__(
            f"{field!r} is stale: {age_s:.0f}s old, TTL {ttl_s:.0f}s "
            f"(source {entry.get('source')!r}). Re-resolve it, or pass "
            f"--stale-ok to accept it with its age reported."
        )


class MissingValue(StateError):
    """Raised when a field has never been set. Ask, or resolve -- do not guess."""


def _age_seconds(iso: str) -> float:
    try:
        then = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - then).total_seconds()


def ttl_for(field: str) -> float | None:
    """Longest matching prefix in DEFAULT_TTL; unknown fields do not expire."""
    best: tuple[int, float | None] = (-1, None)
    for key, ttl in DEFAULT_TTL.items():
        if (field == key or field.startswith(key + ".")) and len(key) > best[0]:
            best = (len(key), ttl)
    return best[1]


# --------------------------------------------------------------------------
# Dotted access helpers
# --------------------------------------------------------------------------
def _dig(obj: dict, dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _plant(obj: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class State:
    """Read/write access to one experiment's state file."""

    def __init__(self, hutch: str, experiment: str):
        self.hutch = hutch
        self.experiment = experiment
        self.path = state_file(hutch, experiment)

    # -- io ---------------------------------------------------------------
    def load(self) -> dict:
        if not self.path.exists():
            return {"hutch": self.hutch, "experiment": self.experiment, "_provenance": {}}
        with self.path.open() as fh:
            data = json.load(fh)
        data.setdefault("_provenance", {})
        return data

    def _write_atomic(self, data: dict) -> None:
        """Write via a temp file in the same directory, then os.replace().

        Never truncates the real file, so a crash mid-write cannot lose the
        shift's context.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o664)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # -- api --------------------------------------------------------------
    def get_entry(self, field: str) -> dict:
        """Full record: {value, source, kind, retrieved_at, age_s, ttl_s, stale}."""
        data = self.load()
        try:
            value = _dig(data, field)
        except KeyError:
            raise MissingValue(
                f"{field!r} has never been set for {self.experiment}. "
                f"Resolve it from its source or ask the operator -- do not assume."
            ) from None
        if value is None:
            raise MissingValue(f"{field!r} is null for {self.experiment}.")

        prov = data.get("_provenance", {}).get(field, {})
        retrieved_at = prov.get("retrieved_at") or data.get("_timestamps", {}).get(field)
        ttl = prov.get("ttl_s", ttl_for(field))
        age = _age_seconds(retrieved_at) if retrieved_at else float("inf")
        return {
            "field": field,
            "value": value,
            "source": prov.get("source", "unknown"),
            "kind": prov.get("kind", "unknown"),
            "retrieved_at": retrieved_at,
            "age_s": None if age == float("inf") else round(age, 1),
            "ttl_s": ttl,
            "stale": bool(ttl is not None and age > ttl),
        }

    def get(self, field: str, stale_ok: bool = False) -> Any:
        entry = self.get_entry(field)
        if entry["stale"] and not stale_ok:
            raise StaleValue(field, entry["age_s"] or float("inf"), entry["ttl_s"], entry)
        return entry["value"]

    def set(
        self,
        field: str,
        value: Any,
        *,
        source: str,
        kind: str = RESOLVED,
        ttl_s: float | None = -1.0,
    ) -> dict:
        """Set a field together with its provenance.

        ``source`` is mandatory and must be specific enough to re-run or
        attribute -- ``'psana2 detinfo exp=... run=36'``, not ``'lookup'``.
        """
        if kind not in VALID_KINDS:
            raise StateError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
        if not source or not source.strip():
            raise StateError(
                f"refusing to set {field!r} without a source. An unattributed "
                f"value cannot be distinguished from a guess."
            )
        ttl = ttl_for(field) if ttl_s == -1.0 else ttl_s
        stamp = utcnow()
        data = self.load()
        _plant(data, field, value)
        data.setdefault("_provenance", {})[field] = {
            "source": source.strip(),
            "kind": kind,
            "retrieved_at": stamp,
            "ttl_s": ttl,
        }
        # Mirror into _timestamps so pre-refactor readers keep working.
        data.setdefault("_timestamps", {})[field] = stamp
        self._write_atomic(data)
        return self.get_entry(field)

    def unset(self, field: str) -> None:
        """Remove a field and its provenance."""
        data = self.load()
        with contextlib.suppress(KeyError):
            parent_path, _, leaf = field.rpartition(".")
            parent = _dig(data, parent_path) if parent_path else data
            parent.pop(leaf, None)
        data.get("_provenance", {}).pop(field, None)
        data.get("_timestamps", {}).pop(field, None)
        self._write_atomic(data)

    def audit(self) -> list[dict]:
        """Every field that carries provenance, most stale first."""
        data = self.load()
        rows = []
        for field in sorted(data.get("_provenance", {})):
            with contextlib.suppress(StateError):
                rows.append(self.get_entry(field))
        rows.sort(key=lambda r: (not r["stale"], -(r["age_s"] or 0)))
        return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-state: path | get | set | unset | audit | dump."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""

    ap, sub = parser("hutch-state", _DOC)

    for name in ("path", "audit", "dump"):
        experiment_args(sub.add_parser(name))

    get_parser = sub.add_parser("get", help="read one field with its provenance")
    experiment_args(get_parser)
    get_parser.add_argument("field")
    get_parser.add_argument("--stale-ok", action="store_true",
                            help="return a value past its TTL, with its age reported")
    get_parser.add_argument("--value-only", action="store_true")

    set_parser = sub.add_parser("set", help="write one field; --source is mandatory")
    experiment_args(set_parser)
    set_parser.add_argument("field")
    set_parser.add_argument("value", help="JSON if parseable, else a string")
    set_parser.add_argument("--source", required=True,
                            help="specific enough to re-run, e.g. 'psana2 detinfo exp=.. run=36'")
    set_parser.add_argument("--kind", default=OPERATOR, choices=VALID_KINDS)
    set_parser.add_argument("--ttl", type=float, default=None)

    unset_parser = sub.add_parser("unset")
    experiment_args(unset_parser)
    unset_parser.add_argument("field")

    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-state: path | get | set | unset | audit | dump."""
    args = build_parser().parse_args(argv)
    st = State(args.hutch, args.experiment)
    try:
        if args.cmd == "path":
            return out({
                "state": str(st.path),
                "log": str(log_file(args.hutch, args.experiment)),
                "xtc2": str(xtc2_dir(args.hutch, args.experiment)),
                "exists": st.path.exists(),
            })
        if args.cmd == "dump":
            return out(st.load())
        if args.cmd == "audit":
            return out(st.audit())
        if args.cmd == "get":
            entry = st.get_entry(args.field)
            if entry["stale"] and not args.stale_ok:
                raise StaleValue(args.field, entry["age_s"] or 0, entry["ttl_s"], entry)
            return out(entry["value"] if args.value_only else entry,
                            plain=args.value_only)
        if args.cmd == "set":
            return out(st.set(
                args.field, json_or_str(args.value), source=args.source,
                kind=args.kind, ttl_s=args.ttl if args.ttl is not None else -1.0))
        if args.cmd == "unset":
            st.unset(args.field)
            return out({"unset": args.field})
    except StateError as exc:
        return fail(exc, STATE_ERROR)
    return USAGE
