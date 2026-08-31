"""Provenance-carrying experiment state store.

The state file is a *cache with provenance*, not a config file. Every value
carries where it came from and when, and reads past a per-field TTL fail
loudly instead of returning something stale.

Why provenance: the old schema had ``_timestamps`` but no source field, so a
value a tired operator typed at 03:00 was indistinguishable from one read off
the machine. That made the "use any non-null field, do not re-ask" rule unsafe
-- the skill could not tell a discovered value from an assumed one.

Why TTL per field: a device list is good for hours, a detector alias for one
run, a PV value for seconds, an operator-declared sample name until it
changes. A single staleness rule (or a single static data file) cannot carry
four different lifetimes.

Writes are atomic and lock-serialised: during a shift several agents and the
operator may write concurrently.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import pathlib
import tempfile
import time
from typing import Any, Iterator

from . import paths

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
}

LOCK_TIMEOUT_S = 10.0


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


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


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
        self.path = paths.state_file(hutch, experiment)

    # -- io ---------------------------------------------------------------
    def load(self) -> dict:
        if not self.path.exists():
            return {"hutch": self.hutch, "experiment": self.experiment, "_provenance": {}}
        with self.path.open() as fh:
            data = json.load(fh)
        data.setdefault("_provenance", {})
        return data

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """Serialise writers with an O_EXCL lockfile.

        Several agents plus the operator may write during a shift; two
        read-modify-write cycles interleaving would silently drop one update.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        fd = None
        while fd is None:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                # Break a lock left behind by a killed process.
                with contextlib.suppress(OSError):
                    if _age_seconds(
                        _dt.datetime.fromtimestamp(
                            lock.stat().st_mtime, _dt.timezone.utc
                        ).isoformat()
                    ) > LOCK_TIMEOUT_S:
                        lock.unlink()
                        continue
                if time.monotonic() > deadline:
                    raise StateError(f"timed out waiting for {lock}")
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            with contextlib.suppress(OSError):
                lock.unlink()

    def _write_atomic(self, data: dict) -> None:
        """Write via a temp file in the same directory, then os.replace().

        Never truncate the real file: a crash mid-write during a shift would
        lose the whole shift's context.
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
        with self._lock():
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
        with self._lock():
            data = self.load()
            with contextlib.suppress(KeyError):
                parent_path, _, leaf = field.rpartition(".")
                parent = _dig(data, parent_path) if parent_path else data
                parent.pop(leaf, None)
            data.get("_provenance", {}).pop(field, None)
            data.get("_timestamps", {}).pop(field, None)
            self._write_atomic(data)

    def audit(self) -> list[dict]:
        """Every field that carries provenance, most stale first.

        This is the report a skill should show instead of silently trusting
        the file: it makes 'which of these numbers did we actually measure'
        answerable.
        """
        data = self.load()
        rows = []
        for field in sorted(data.get("_provenance", {})):
            with contextlib.suppress(StateError):
                rows.append(self.get_entry(field))
        rows.sort(key=lambda r: (not r["stale"], -(r["age_s"] or 0)))
        return rows
