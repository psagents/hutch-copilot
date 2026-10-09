"""hutch-daq: the DAQ itself -- its state, its log files, its config. Read-only.

Runs on a ``{hutch}-daq`` machine, where everything it needs is local: the
``{hutch}opr`` log tree and the config dir under ``/cds``. Reading DAQ state
goes through the live session, so it borrows ``hutch-python``'s bridge -- but
the session, the safety gate and Channel Access are not this module's job.

Nothing here writes. Beginning or configuring a run is a write that goes
through ``hutch-python exec`` with an explicit confirmation.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
from typing import Any

from .common import UNREACHABLE, USAGE, external_path, fail, out, parser, utcnow
from .session import T_QUERY, BridgeError, exec_code


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

#: A dict literal on ONE line, not a loop over eval(). The classifier checks
#: line by line and `out[key] = str(eval(expr))` is not provably read-only, so
#: the old snippet refused itself; spread over several lines, the closing `}))`
#: is not a recognised read either. Keeping it to one line means one line to
#: recognise -- and the gate stays strict for everything else.
_STATE_CODE = (
    "import json\n"
    "print(json.dumps({"
    "'state': str(daq.state), "
    "'config_alias': str(daq.config_alias_sig.get()), "
    "'experiment': str(daq.experiment_name_sig.get()), "
    "'recording': str(daq.recording_sig.get()), "
    "'run_number': str(get_run())"
    "}))\n"
)


def state(hutch: str, *, timeout: int = T_QUERY) -> dict:
    """The DAQ's own signals, verbatim: state, config alias, experiment, recording, run.

    Judges nothing. Whether these add up to "ready to record" is a readiness
    question, and belongs to the layer that asks it -- see
    ``are-we-ready/scripts/check_daq.py``.
    """
    res = exec_code(_STATE_CODE, timeout=timeout)
    if not res["ok"]:
        raise DaqError(f"DAQ query failed: {res.get('error') or res['output'][:200]}")
    try:
        payload = json.loads(res["output"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise DaqError(f"DAQ query returned non-JSON: {res['output'][:200]!r}") from exc
    return {
        "value": payload,
        "source": "resolved:daq signals via the hutch-python session",
        "retrieved_at": utcnow(),
    }


# --------------------------------------------------------------------------
# DAQ log files
#
# Local reads: the {hutch}opr log root is mounted on the DAQ machine. Note the
# `<host>` in a log file name is the drp node that wrote it
# (drp-srcf-cmp0NN), never this machine -- which is why the process filter
# matches the `:<process>.log` field and not the whole name.
# --------------------------------------------------------------------------
def _log_dir(hutch: str, when: datetime.date | None = None) -> str:
    """Per-process DAQ log directory for a hutch's operator account."""
    d = when or datetime.date.today()
    root = external_path("OPR_LOG_ROOT")
    return f"{root}/{hutch.strip().lower()}opr/{d.year:04d}/{d.month:02d}"


def logs(
    hutch: str,
    *,
    process: str | None = None,
    lines: int = 0,
    count: int = 20,
    when: datetime.date | None = None,
) -> dict:
    """List the most recent DAQ log files, or tail the newest when ``lines`` > 0.

    Files are named ``<DD_HH:MM:SS>_<host>:<process>.log``.
    """
    hutch = hutch.strip().lower()
    d = _log_dir(hutch, when)
    directory = pathlib.Path(d)
    if not directory.is_dir():
        raise DaqError(f"DAQ log directory does not exist: {d}")

    pattern = f"*:{process}.log" if process else "*.log"
    newest_first = sorted(
        directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )

    if lines > 0:
        if not newest_first:
            raise DaqError(f"no log matching {pattern!r} in {d}")
        newest = newest_first[0]
        tail = newest.read_text(errors="replace").splitlines()[-int(lines):]
        return {
            "directory": d,
            "process": process,
            "tail_lines": lines,
            "file": newest.name,
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
            re.findall(r'["\'](/cds/[^"\']+\.(?:txt|json|ya?ml))["\']', text)):
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
    ap.add_argument("--timeout", type=int, default=T_QUERY)

    sub.add_parser("state", help="the DAQ's own signals, verbatim; judges nothing")
    sub.add_parser("config", help="dump the DAQ config file verbatim; interprets nothing")

    logs_parser = sub.add_parser("logs", help="list or tail per-process DAQ log files")
    logs_parser.add_argument("--process", default=None,
                             help="filter the :<process>.log field, e.g. drp, teb, control")
    logs_parser.add_argument("-n", "--lines", type=int, default=0,
                             help="tail this many lines of the newest match; 0 lists instead")
    logs_parser.add_argument("--count", type=int, default=20, help="how many names to list")
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
            return out(logs(args.hutch, process=args.process, lines=args.lines,
                            count=args.count))
        return USAGE
    except (DaqError, BridgeError, OSError) as exc:
        return fail(exc, UNREACHABLE)
