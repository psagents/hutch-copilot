"""Filesystem primitives shared by the two things that mutate files on disk.

``state.py`` replaces a JSON document; ``log.py`` appends to a markdown one. Both
run during a shift while several agents and the operator may be writing, so both
need the same serialisation — hence one implementation rather than two.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import pathlib
import time
from typing import Iterator

LOCK_TIMEOUT_S = 10.0


class LockTimeout(RuntimeError):
    pass


def utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def local_hhmm() -> str:
    """Wall-clock HH:MM, 24h.

    The log is read by humans on shift, so entries are stamped in local time --
    and stamped by the machine. The prose convention used to be "get the time via
    `date +%H:%M`", which meant a model could omit it or copy a stale one.
    """
    return _dt.datetime.now().strftime("%H:%M")


@contextlib.contextmanager
def lock(target: pathlib.Path, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Serialise writers to ``target`` with an O_EXCL sidecar lockfile.

    Two read-modify-write cycles interleaving would silently drop one update.
    A lock left behind by a killed process is broken once it is older than the
    timeout, so a crash cannot wedge a shift.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lockfile = target.with_suffix(target.suffix + ".lock")
    deadline = time.monotonic() + timeout_s
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            with contextlib.suppress(OSError):
                age = time.time() - lockfile.stat().st_mtime
                if age > timeout_s:
                    lockfile.unlink()
                    continue
            if time.monotonic() > deadline:
                raise LockTimeout(f"timed out waiting for {lockfile}") from None
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            lockfile.unlink()


def append_atomic(target: pathlib.Path, text: str, mode: int = 0o664) -> None:
    """Append under lock, flushed to disk before the lock is released."""
    with lock(target):
        with target.open("a") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    with contextlib.suppress(OSError):
        os.chmod(target, mode)
