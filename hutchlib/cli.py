"""Command-line surface. JSON out by default, so skills consume data not prose.

  hutch-state    path | get | set | audit
  hutch-bridge   ping | install | exec | run
  hutch-resolve  devices | device | pv | beampath | detectors | daq | conf | preset | cell
  hutch-lute     sbatch | submit | status | traps | kerberos
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import bridge, lute, paths, resolvers, safety, state, traps


def _out(obj: Any, plain: bool = False) -> int:
    if plain and isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    return 0


def _fail(exc: BaseException, code: int = 1) -> int:
    print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2), file=sys.stderr)
    return code


def _exp_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hutch", required=True)
    p.add_argument("--experiment", required=True)


# --------------------------------------------------------------------------
def state_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hutch-state", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("path", "audit", "dump"):
        sp = sub.add_parser(name)
        _exp_args(sp)

    g = sub.add_parser("get", help="read one field with its provenance")
    _exp_args(g)
    g.add_argument("field")
    g.add_argument("--stale-ok", action="store_true",
                   help="return a value past its TTL, with its age reported")
    g.add_argument("--value-only", action="store_true")

    s = sub.add_parser("set", help="write one field; --source is mandatory")
    _exp_args(s)
    s.add_argument("field")
    s.add_argument("value", help="JSON if parseable, else a string")
    s.add_argument("--source", required=True,
                   help="specific enough to re-run, e.g. 'psana2 detinfo exp=.. run=36'")
    s.add_argument("--kind", default=state.OPERATOR, choices=state.VALID_KINDS)
    s.add_argument("--ttl", type=float, default=None)

    u = sub.add_parser("unset")
    _exp_args(u)
    u.add_argument("field")

    args = ap.parse_args(argv)
    st = state.State(args.hutch, args.experiment)
    try:
        if args.cmd == "path":
            return _out({
                "state": str(st.path),
                "log": str(paths.log_file(args.hutch, args.experiment)),
                "xtc2": str(paths.xtc2_dir(args.hutch, args.experiment)),
                "exists": st.path.exists(),
            })
        if args.cmd == "dump":
            return _out(st.load())
        if args.cmd == "audit":
            return _out(st.audit())
        if args.cmd == "get":
            entry = st.get_entry(args.field)
            if entry["stale"] and not args.stale_ok:
                raise state.StaleValue(args.field, entry["age_s"] or 0, entry["ttl_s"], entry)
            return _out(entry["value"] if args.value_only else entry,
                        plain=args.value_only)
        if args.cmd == "set":
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError:
                value = args.value
            return _out(st.set(args.field, value, source=args.source, kind=args.kind,
                               ttl_s=args.ttl if args.ttl is not None else -1.0))
        if args.cmd == "unset":
            st.unset(args.field)
            return _out({"unset": args.field})
    except state.StateError as exc:
        return _fail(exc, 3)
    return 2


# --------------------------------------------------------------------------
def bridge_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hutch-bridge", description=__doc__)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--host", default=None, help="override the DAQ host")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("install", help="copy oc_bridge.py to the DAQ machine")

    e = sub.add_parser("exec", help="run python in the live hutch-python session")
    e.add_argument("code", help="python source, or - to read stdin")
    e.add_argument("--confirm", choices=[safety.READ_ONLY, safety.WRITE, safety.BEAM_CRITICAL],
                   help="action class a human has approved")
    e.add_argument("--timeout", type=int, default=bridge.T_QUERY)

    r = sub.add_parser("run", help="ship a script into the session and call into it")
    r.add_argument("script")
    r.add_argument("--call", default=None, help="e.g. 'check_beam_ready()'")
    r.add_argument("--confirm", choices=[safety.READ_ONLY, safety.WRITE, safety.BEAM_CRITICAL])
    r.add_argument("--timeout", type=int, default=bridge.T_SCAN)

    c = sub.add_parser("classify", help="show the safety class of a snippet without sending it")
    c.add_argument("code")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "ping":
            res = bridge.ping(args.hutch, host=args.host)
            return _out(res, plain=False) or (0 if res["ok"] else 4)
        if args.cmd == "install":
            return _out(bridge.install_listener(args.hutch, host=args.host), plain=True)
        if args.cmd == "classify":
            return _out(safety.classify(args.code))
        if args.cmd == "exec":
            code = sys.stdin.read() if args.code == "-" else args.code
            return _out(bridge.exec_code(code, hutch=args.hutch, confirm=args.confirm,
                                         timeout=args.timeout, host=args.host))
        if args.cmd == "run":
            return _out(bridge.run_script(args.script, hutch=args.hutch, call=args.call,
                                          confirm=args.confirm, timeout=args.timeout))
    except safety.SafetyRefusal as exc:
        return _fail(exc, 5)
    except bridge.BridgeError as exc:
        return _fail(exc, 4)
    return 2


# --------------------------------------------------------------------------
def resolve_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hutch-resolve", description=__doc__)
    ap.add_argument("--persist-to", nargs=2, metavar=("HUTCH", "EXPERIMENT"),
                    help="also write the result into that experiment's state")
    ap.add_argument("--field", default=None, help="state field to persist under")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("devices", "beampath", "daq", "conf"):
        sp = sub.add_parser(name)
        sp.add_argument("--hutch", required=True)

    d = sub.add_parser("device")
    d.add_argument("--hutch", required=True)
    d.add_argument("name")

    p = sub.add_parser("pv")
    p.add_argument("--hutch", required=True)
    p.add_argument("name")
    p.add_argument("--suffix", default="")

    det = sub.add_parser("detectors")
    det.add_argument("--experiment", required=True)
    det.add_argument("--run", required=True, type=int)

    pr = sub.add_parser("preset")
    pr.add_argument("--hutch", required=True)
    pr.add_argument("device")

    ce = sub.add_parser("cell")
    ce.add_argument("pdb")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "devices":
            rec = resolvers.devices(args.hutch)
        elif args.cmd == "device":
            rec = resolvers.device(args.hutch, args.name)
        elif args.cmd == "pv":
            rec = resolvers.pv(args.hutch, args.name, args.suffix)
        elif args.cmd == "beampath":
            rec = resolvers.beampath(args.hutch)
        elif args.cmd == "daq":
            rec = resolvers.daq(args.hutch)
        elif args.cmd == "conf":
            rec = resolvers.hutch_conf(args.hutch)
        elif args.cmd == "detectors":
            rec = resolvers.detectors(args.experiment, args.run)
        elif args.cmd == "preset":
            rec = resolvers.preset(args.hutch, args.device)
        elif args.cmd == "cell":
            rec = resolvers.cell(args.pdb)
        else:
            return 2

        if args.persist_to:
            field = args.field or args.cmd
            st = state.State(*args.persist_to)
            rec["persisted"] = resolvers.persist(st, field, rec)
        return _out(rec)
    except (resolvers.ResolveError, bridge.BridgeError, OSError) as exc:
        return _fail(exc, 4)


# --------------------------------------------------------------------------
def lute_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hutch-lute", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sb = sub.add_parser("sbatch", help="render a submission script")
    sb.add_argument("--task", required=True)
    sb.add_argument("--config", required=True)
    sb.add_argument("--log-dir", required=True)
    sb.add_argument("--lute-path", required=True)
    sb.add_argument("--experiment", required=True)
    sb.add_argument("--run", required=True, type=int)
    sb.add_argument("--time", dest="time_limit", required=True,
                    help="Slurm walltime, e.g. 00:10:00 -- mandatory, LUTE sets none")
    sb.add_argument("--nodes", type=int, default=1)
    sb.add_argument("--ntasks-per-node", type=int, default=1)
    sb.add_argument("--no-mpi", action="store_true")
    sb.add_argument("--submit", action="store_true")
    sb.add_argument("--dependency", default=None)
    sb.add_argument("--script-path", default=None)

    stt = sub.add_parser("status")
    stt.add_argument("job_id")

    tr = sub.add_parser("traps", help="list the incident register, or check a file")
    tr.add_argument("--scope", default=None,
                    choices=["sbatch", "lute-yaml", "run-params", "phil-overrides"])
    tr.add_argument("--yaml", default=None)

    kb = sub.add_parser("kerberos", help="does this config actually need a ticket?")
    kb.add_argument("--yaml", required=True)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "sbatch":
            text = lute.build_sbatch(
                task=args.task, config=args.config, log_dir=args.log_dir,
                lute_path=args.lute_path, experiment=args.experiment, run=args.run,
                time_limit=args.time_limit, nodes=args.nodes,
                ntasks_per_node=args.ntasks_per_node, mpi=not args.no_mpi,
            )
            if not args.submit:
                return _out(text, plain=True)
            path = args.script_path or f"{args.log_dir}/sbatch_{args.task}.sh"
            return _out({"job_id": lute.submit(text, script_path=path,
                                               dependency=args.dependency)})
        if args.cmd == "status":
            return _out(lute.job_status(args.job_id))
        if args.cmd == "traps":
            if args.yaml:
                text = open(args.yaml).read()
                return _out(traps.check(args.scope or "lute-yaml", {"yaml_text": text}))
            reg = traps.registry()
            return _out([e for e in reg if not args.scope or e["applies_to"] == args.scope])
        if args.cmd == "kerberos":
            needed, why = lute.needs_kerberos(open(args.yaml).read())
            return _out({"needs_kerberos": needed, "why": why,
                         "have_ticket": lute.have_ticket()})
    except (lute.LuteError, OSError) as exc:
        return _fail(exc, 4)
    return 2
