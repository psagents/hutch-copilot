"""hutch-mcp: the hutch-only tools as a stdio MCP server, run on the hutch machine.

A session that is not on the hutch (a Cogito agent container, an S3DF node) reaches the
hutch through this, over the user's own ssh: ``ssh -T <hutch>-daq .../bin/hutch-mcp``.
Each tool runs the existing ``bin/hutch-*`` with a fixed argv; none of them writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, TextIO

from .common import skill_root

#: name -> (bin, argv template, description, {param: (json type, description)}).
#: "{p}" is replaced by the argument; a nested list is dropped unless all its params are given;
#: a boolean param keeps only its flag when true.
TOOLS: dict[str, tuple[str, list, str, dict[str, tuple[str, str]]]] = {
    "happi_list": ("hutch-happi", ["list", "--hutch", "{hutch}"],
                   "Every device happi records for this hutch, with its PV prefix.",
                   {"hutch": ("string", "hutch name, e.g. mfx")}),
    "happi_device": ("hutch-happi", ["device", "--hutch", "{hutch}", "{name}"],
                     "One happi device entry by name.",
                     {"hutch": ("string", "hutch name"), "name": ("string", "happi device name")}),
    "happi_pv": ("hutch-happi", ["pv", "--hutch", "{hutch}", "{name}", ["--suffix", "{suffix}"]],
                 "The PV prefix of a happi device, optionally with a suffix appended.",
                 {"hutch": ("string", "hutch name"), "name": ("string", "happi device name"),
                  "suffix": ("string", "PV suffix, e.g. :RBV")}),
    "epics_caget": ("hutch-epics", ["caget", "{pv}"],
                    "Live Channel Access values of one or more PVs, read now.",
                    {"pv": ("array", "PV names")}),
    "epics_history": ("hutch-epics", ["history", "{pv}", "--from", "{start}", ["--to", "{end}"]],
                      "Archived samples of one PV between two times.",
                      {"pv": ("string", "PV name"), "start": ("string", "ISO time"),
                       "end": ("string", "ISO time, default now")}),
    "epics_moved": ("hutch-epics", ["moved", "{pv}", "--from", "{start}", ["--to", "{end}"]],
                    "Did a motor move between two times? Reads .MSTA from the archiver; null means unknown.",
                    {"pv": ("string", "motor PV prefix"), "start": ("string", "ISO time"),
                     "end": ("string", "ISO time, default now")}),
    "lightpath_path": ("hutch-lightpath", ["path", "--hutch", "{hutch}"],
                       "The beam path to the hutch and every device blocking it. Takes ~40 s.",
                       {"hutch": ("string", "hutch name")}),
    "daq_state": ("hutch-daq", ["--hutch", "{hutch}", "state"],
                  "The DAQ's own state (transition, recording, config alias, experiment), off its control port.",
                  {"hutch": ("string", "hutch name")}),
    "normal_diff": ("hutch-normal", ["diff", "--hutch", "{hutch}", "--experiment", "{experiment}",
                                     ["--taking-data", "{taking_data}"]],
                    "Judge the current beam path and DAQ against the operator's normal snapshot.",
                    {"hutch": ("string", "hutch name"), "experiment": ("string", "experiment, e.g. mfx102095027"),
                     "taking_data": ("boolean", "about to take data: idle-closed devices become blockers")}),
    "data_cat": ("hutch-data", ["cat", "{path}"],
                 "Read a file under /cds, which only the hutch machines mount.",
                 {"path": ("string", "absolute /cds path")}),
}

#: lightpath settles for ~40 s; anything far past that is hung.
T_TOOL = 300


def _names(template: list) -> list[str]:
    return [t[1:-1] for t in template if isinstance(t, str) and t.startswith("{")]


def _argv(template: list, args: dict) -> list[str]:
    out: list[str] = []
    for item in template:
        if isinstance(item, list):
            if all(args.get(n) not in (None, False) for n in _names(item)):
                out += _argv(item, args)
        elif item.startswith("{"):
            value = args[item[1:-1]]
            if value is True:
                continue
            for v in value if isinstance(value, list) else [value]:
                if str(v).startswith("-"):
                    raise ValueError(f"{item[1:-1]}={v!r} looks like a flag")
                out.append(str(v))
        else:
            out.append(item)
    return out


def _schema(template: list, params: dict) -> dict:
    props = {p: ({"type": "array", "items": {"type": "string"}} if t == "array" else {"type": t})
             | {"description": d} for p, (t, d) in params.items()}
    return {"type": "object", "properties": props, "required": _names(template)}


def _text(text: str, error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def call(name: str, args: dict) -> dict:
    """Run one tool and return its MCP result; a nonzero rc is an error result, not a protocol error."""
    binary, template, _, _ = TOOLS[name]
    try:
        argv = [str(skill_root() / "bin" / binary), *_argv(template, args)]
    except KeyError as exc:
        return _text(f"missing required argument {exc}", True)
    except ValueError as exc:
        return _text(str(exc), True)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=T_TOOL)
    except subprocess.TimeoutExpired:
        return _text(f"{binary} timed out after {T_TOOL} s", True)
    text = p.stdout.strip() or p.stderr.strip()
    return _text(f"rc {p.returncode}\n{text}" if p.returncode else text, p.returncode != 0)


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, its response out; None for a notification."""
    if "id" not in msg:
        return None
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        result: Any = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "hutch-mcp", "version": "0.1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": _schema(t, p)}
                            for n, (_, t, d, p) in TOOLS.items()]}
    elif method == "tools/call" and params.get("name") in TOOLS:
        result = call(params["name"], params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": msg["id"],
                "error": {"code": -32601, "message": f"unknown method or tool: {method}"}}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Newline-delimited JSON-RPC on stdio, until stdin closes."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    return argparse.ArgumentParser(prog="hutch-mcp", description=__doc__.splitlines()[0],
                                   epilog="tools: " + ", ".join(TOOLS))


def main(argv: list[str] | None = None) -> int:
    """hutch-mcp: serve the hutch-only tools over stdio."""
    build_parser().parse_args(argv)
    serve()
    return 0
