"""hutch-daq: the DAQ itself -- its state, its log files, its config. Read-only.

Runs on a ``{hutch}-daq`` machine, where everything it needs is reachable: the
``{hutch}opr`` log and config trees, and the DAQ's own control port. State is
read with ``psdaq``'s ``DaqControl``, in the DAQ's release and interpreter --
not through hutch-python's bridge, which is usually not running and would make
a reachable DAQ read as unknown.

Nothing here writes. Beginning or configuring a run is a write that goes
through ``hutch-python exec`` with an explicit confirmation.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import subprocess
from typing import Any

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow


class DaqError(RuntimeError):
    """The DAQ was reachable but could not answer. Never silently defaulted."""


# --------------------------------------------------------------------------
# DAQ state
# --------------------------------------------------------------------------
#: Every DAQ state, from psdaq ControlDef.states. Anything outside this set is
#: not a state we failed to reach readiness in -- it is a string we could not
#: interpret (an 'ERROR: ...', a renamed state), which must read as unknown
#: rather than as a confident no.
DAQ_STATES = ("reset", "unallocated", "allocated", "connected",
              "configured", "starting", "paused", "running")
#: Of those, the ones a run can be taken from. 'configured' is ready-to-begin;
#: starting/paused/running mean a run is already underway, also not "blocked".
DAQ_READY_STATES = ("configured", "starting", "paused", "running")

#: Control-port timeout, milliseconds -- DaqControl's own unit.
T_CONTROL_MS = 5000

#: The read-only subset of DaqControl, from psana-daq-control's "may run"
#: table. That skill is the authority on what is safe to call against a live
#: DAQ; anything mutating (setState, selectPlatform, any set*/store*) is a
#: write and does not belong in this module. Do not widen this without
#: widening it there first.
_READ_ONLY_CALLS = ("getState", "getStatus", "getPlatform", "getInstrument",
                    "getJsonConfig")

#: getStatus() returns a bare 9-tuple; these are the keys it reads off the
#: reply, in order, from DaqControl.py. `experiment_name` is renamed to
#: `experiment` because that is what every reader here already calls it.
_STATUS_FIELDS = ("transition", "state", "config_alias", "recording", "platform",
                  "bypass_activedet", "experiment", "run_number", "last_run_number")

#: Runs in the DAQ's own py3.9 interpreter, not this one.
_STATE_CODE = (
    "import json, sys\n"
    "from psdaq.control.DaqControl import DaqControl\n"
    "c = DaqControl(host=sys.argv[1], platform=int(sys.argv[2]), timeout=int(sys.argv[3]))\n"
    "print(json.dumps(c.getStatus()))\n"
)


def _daq_env(hutch: str) -> tuple[str, dict[str, str]]:
    """The interpreter and environment the running DAQ was launched with.

    ponytail: read from the newest log session's header, so a DAQ moved to a
    different release mid-shift is read correctly only once it has logged. Ask
    the operator for TESTRELDIR if that bites.
    """
    files = _log_files(hutch)
    if not files:
        raise DaqError(f"no DAQ log to resolve the release from in {_log_dir(hutch)}")
    header = log_header(files[0])
    release, conda = header.get("TESTRELDIR"), header.get("CONDA_PREFIX")
    if not release or not conda:
        raise DaqError(f"{files[0].name} has no TESTRELDIR/CONDA_PREFIX header")

    python = pathlib.Path(conda) / "bin" / "python"
    # The release dir is version-stamped (lcls2_082726) and there are several
    # per hutch, so the running one can only come from the header -- and its
    # python minor version with it.
    site = next(iter(sorted(pathlib.Path(release).glob("lib/python3.*/site-packages"))), None)
    if not python.is_file() or site is None:
        raise DaqError(f"DAQ release is not usable: {python} / {release}/lib/python3.*")
    return str(python), {
        **os.environ,
        "PYTHONPATH": str(site),
        "LD_LIBRARY_PATH": f"{release}/lib:{conda}/lib",
    }


def state(hutch: str, *, timeout: int = T_CONTROL_MS) -> dict:
    """The DAQ's own status fields, verbatim, read off its control port.

    Judges nothing. Whether these add up to "ready to record" is a readiness
    question, and belongs to the layer that asks it -- see
    ``are-we-ready/scripts/check_daq.py``.
    """
    config = config_summary(hutch)
    host, platform = config["collect_host"], config["platform"]
    if not host or platform is None:
        raise DaqError(f"{config['path']} declares no collect_host/platform to query")

    python, env = _daq_env(hutch)
    res = subprocess.run(
        [python, "-c", _STATE_CODE, host, str(platform), str(timeout)],
        capture_output=True, text=True, env=env, timeout=timeout / 1000 + 30,
    )
    try:
        payload = dict(zip(_STATUS_FIELDS, json.loads(res.stdout.strip().splitlines()[-1])))
    except (json.JSONDecodeError, IndexError) as exc:
        raise DaqError(
            f"DaqControl returned no status: {(res.stderr or res.stdout)[-400:]!r}"
        ) from exc
    # getStatus() answers 'error' in every field rather than raising when the
    # control port does not reply. That is a source we could not reach, not a
    # DAQ that is not ready, so it must not reach a caller as a state.
    if payload["state"] == "error":
        raise DaqError(
            f"no reply from the DAQ control port on {host} platform {platform}"
            f"{': ' + res.stderr.strip()[-300:] if res.stderr.strip() else ''}"
        )
    return {
        "value": payload,
        "source": f"resolved:DaqControl.getStatus via {host} platform {platform}",
        "retrieved_at": utcnow(),
    }


# --------------------------------------------------------------------------
# DAQ log files
#
# Local reads: the {hutch}opr log root is mounted on the DAQ machine. Note the
# `<host>` in a log file name is the drp node that wrote it
# (drp-srcf-cmp0NN), never this machine -- which is why the process filter
# matches the `:<process>.log` field and not the whole name.
#
# Listing and reading only. Which `<E>`/`<C>` lines are real and which are
# noise is judgment, and lives in the psana-daq-logs advisor.
# --------------------------------------------------------------------------
#: The `DD_HH:MM:SS` a filename starts with. Every process of one daqmgr launch
#: shares it, so it is the de-facto session id.
_SESSION = re.compile(r"^(\d\d_\d\d:\d\d:\d\d)_")


def _log_dir(hutch: str, when: datetime.date | None = None) -> str:
    """Per-process DAQ log directory for a hutch's operator account."""
    d = when or datetime.date.today()
    root = external_path("OPR_LOG_ROOT").format(hutch=hutch.strip().lower())
    return f"{root}/{d.year:04d}/{d.month:02d}"


def _log_files(hutch: str, pattern: str = "*", when: datetime.date | None = None
               ) -> list[pathlib.Path]:
    """Logs matching ``pattern``, newest first. Rotated ``.zst`` included."""
    d = _log_dir(hutch, when)
    directory = pathlib.Path(d)
    if not directory.is_dir():
        raise DaqError(f"DAQ log directory does not exist: {d}")
    found = [p for suffix in (".log", ".log.zst") for p in directory.glob(pattern + suffix)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def read_log(path: pathlib.Path) -> str:
    """A log's text, decompressing a rotated ``.zst`` in memory."""
    if path.suffix != ".zst":
        return path.read_text(errors="replace")
    # `zstd -dc`, never `zstdcat`: the latter is `zstd -dcf`, and -f passes
    # non-zstd input straight through at exit 0 -- so a corrupt file would read
    # as a successful empty one.
    res = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise DaqError(f"cannot decompress {path.name}: {res.stderr.strip()[-200:]}")
    return res.stdout


def log_header(path: pathlib.Path) -> dict[str, str]:
    """The `#`-prefixed header a DAQ log opens with, as fields.

    Carries SLURM_JOB_ID, ID, PLATFORM, HOST, CMDLINE, CONDA_PREFIX and
    TESTRELDIR -- which is how the running release and the limits a process was
    actually launched with are recovered.
    """
    head = "".join(read_log(path).splitlines(keepends=True)[:40])
    return dict(re.findall(r"^# (\w+):\s*(.+?)\s*$", head, re.M))


def sessions(hutch: str, when: datetime.date | None = None) -> dict:
    """Every daqmgr session in a month, newest first, with its file count.

    Present these and let the operator choose. The newest prefix is not
    necessarily the session being asked about.
    """
    counts: dict[str, int] = collections.Counter(
        m.group(1) for p in _log_files(hutch, when=when)
        if (m := _SESSION.match(p.name))
    )
    return {
        "directory": _log_dir(hutch, when),
        "count": len(counts),
        "sessions": [{"session": s, "files": n} for s, n in sorted(counts.items(), reverse=True)],
    }


def logs(
    hutch: str,
    *,
    process: str | None = None,
    lines: int = 0,
    count: int = 20,
    session: str | None = None,
    when: datetime.date | None = None,
) -> dict:
    """List the most recent DAQ log files, or tail the newest when ``lines`` > 0.

    Files are named ``<DD_HH:MM:SS>_<host>:<process>.log``.
    """
    hutch = hutch.strip().lower()
    d = _log_dir(hutch, when)
    # No trailing `*` after a bare `*`: pathlib rejects `**` mid-component.
    pattern = (session + "*" if session else "*") + (f":{process}" if process else "")
    newest_first = _log_files(hutch, pattern, when)

    if lines > 0:
        if not newest_first:
            raise DaqError(f"no log matching {pattern!r} in {d}")
        newest = newest_first[0]
        tail = read_log(newest).splitlines()[-int(lines):]
        return {
            "directory": d,
            "process": process,
            "tail_lines": lines,
            "file": newest.name,
            "header": log_header(newest),
            "content": "\n".join(tail),
        }

    files = [p.name for p in newest_first[: int(count)]]
    return {
        "directory": d,
        "process": process,
        "count": len(files),
        "files": files,
    }


# --------------------------------------------------------------------------
# DAQ configuration -- dumped verbatim, never interpreted
#
# Judging whether a config is safe belongs to a layer above this one, so this
# parses nothing. The LCLS-II config is `<hutch>.py`; the `.cnf` files beside
# it are LCLS-I era and reference rhel7 paths that no longer exist.
#
# ponytail: `<hutch>.py` is a convention. The leaf config daqmgr was launched
# with is an argument, not discoverable at rest -- several live beside it. If
# the operator used another, psana-daq resolves which, and a run's own
# `CMDLINE` header is the cross-check.
# --------------------------------------------------------------------------
def daq_config(hutch: str) -> dict:
    """The live DAQ config file for a hutch, verbatim, with its path."""
    hutch = hutch.strip().lower()
    config_dir = external_path("DAQ_CONFIG_DIR")
    path = pathlib.Path(config_dir.format(hutch=hutch)) / f"{hutch}.py"
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        raise DaqError(f"cannot read DAQ config {path}: {exc}") from exc
    return {
        "path": str(path),
        "source": f"resolved:{path}",
        "lines": text.count("\n") + 1,
        "content": text,
    }


#: Detector DRP processes, by the `-D <type>` their command carries. The ids
#: themselves are hutch-chosen names, so match on the driver instead.
_DRP_DETECTORS = ("jungfrau", "epix100", "epix10k", "ts", "wave8", "opal", "archon")


def config_summary(hutch: str) -> dict:
    """Readiness-relevant fields from the DAQ config, not its 200 lines."""
    rec = daq_config(hutch)
    text = rec["content"]
    # Commented-out alternatives are everywhere in these files -- MFX carries
    # three dead feespecImg_0 variants and two qadc_0. Counting them would
    # overstate what is meant to be running.
    live = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]

    def scalar(name: str) -> str | None:
        m = re.search(rf'^{name}\s*=\s*["\']([^"\']+)', text, re.M)
        return m.group(1) if m else None

    summary: dict[str, Any] = {
        "path": rec["path"],
        "lines": rec["lines"],
        "collect_host": scalar("collect_host"),
        # Quoted in every config seen ("0"), but psdaq takes it as an int.
        "platform": scalar("platform"),
        "data_dir": scalar("data_dir"),
        "station": None,
        "user": None,
    }
    m = re.search(r'^hutch,\s*station,\s*user\s*=\s*\(([^)]*)\)', text, re.M)
    if m:
        parts = [p.strip().strip('"\'') for p in m.group(1).split(",")]
        if len(parts) == 3:
            summary["station"], summary["user"] = parts[1], parts[2]

    # f-string templates (ami-node_{N}_{instance}) are reported verbatim:
    # expanding them means reimplementing the config's own loops, and a wrong
    # expansion is worse than an obviously-templated name.
    ids: list[str] = []
    dets: list[str] = []
    for line in live:
        m = re.search(r'id:\s*f?"([^"]+)"', line)
        if not m:
            continue
        ids.append(m.group(1))
        driver = re.search(r'-D\s*(\w+)', line)
        if driver and driver.group(1) in _DRP_DETECTORS:
            dets.append(m.group(1))
    summary["processes"] = len(ids)
    summary["process_ids"] = ids
    summary["detectors"] = dets

    # Files the config reads at startup. An unreadable epicsArch.txt is a real
    # readiness problem that no DAQ state field reports.
    summary["also_reads"] = {}
    for ref in dict.fromkeys(
            re.findall(r'["\'](/sdf/[^"\']+\.(?:txt|json|ya?ml))["\']', text)):
        p = pathlib.Path(ref)
        try:
            summary["also_reads"][ref] = {
                "readable": True, "lines": p.read_text(errors="replace").count("\n") + 1}
        except OSError as exc:
            summary["also_reads"][ref] = {"readable": False, "error": str(exc)}
    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-daq: state | config | logs."""


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap, sub = parser("hutch-daq", _DOC)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--timeout", type=int, default=T_CONTROL_MS,
                    help="control-port timeout in ms")

    sub.add_parser("state", help="the DAQ's own signals, verbatim; judges nothing")
    sub.add_parser("config", help="dump the DAQ config file verbatim; interprets nothing")

    logs_parser = sub.add_parser("logs", help="list or tail per-process DAQ log files")
    logs_parser.add_argument("--process", default=None,
                             help="filter the :<process>.log field, e.g. drp, teb, control")
    logs_parser.add_argument("-n", "--lines", type=int, default=0,
                             help="tail this many lines of the newest match; 0 lists instead")
    logs_parser.add_argument("--count", type=int, default=20, help="how many names to list")
    logs_parser.add_argument("--session", default=None,
                             help="one daqmgr session, as DD_HH:MM:SS")
    logs_parser.add_argument("--sessions", action="store_true",
                             help="list the sessions in this month instead of files")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-daq: state | config | logs."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "state":
            return out(state(args.hutch, timeout=args.timeout))
        if args.cmd == "config":
            return out(daq_config(args.hutch))
        if args.cmd == "logs":
            if args.sessions:
                return out(sessions(args.hutch))
            return out(logs(args.hutch, process=args.process, lines=args.lines,
                            count=args.count, session=args.session))
        return USAGE
    except (DaqError, OSError, subprocess.SubprocessError) as exc:
        return fail(exc, UNREACHABLE)
