"""Shared CLI contract: JSON out, structured errors, common arguments.

Each module owns its own ``main(argv)`` -- the argparse spec for ``hutch-state``
lives next to ``State``, so adding a flag is a one-file change. This module
holds only what all of them must agree on, so the output contract stays uniform
without coupling the commands to each other.

Why not one cli.py fronting everything: it imported every module at module
level, so a syntax error in ``config.py`` broke ``hutch-state``, and
``hutch-state`` paid 25 ms importing ``resolvers`` and ``bridge`` that it never
uses. One broken module took down all the binaries.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: Exit codes. Distinct so a caller can branch without parsing stderr.
OK = 0
USAGE = 2
STATE_ERROR = 3
UNREACHABLE = 4
REFUSED = 5


def out(obj: Any, plain: bool = False) -> int:
    """Print a result. JSON unless ``plain`` and the object is already a string."""
    if plain and isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    return OK


def fail(exc: BaseException, code: int = 1) -> int:
    """Print a structured error to stderr and return an exit code."""
    print(
        json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2),
        file=sys.stderr,
    )
    return code


def parser(prog: str, doc: str | None) -> tuple[argparse.ArgumentParser, Any]:
    ap = argparse.ArgumentParser(prog=prog, description=doc)
    return ap, ap.add_subparsers(dest="cmd", required=True)


def experiment_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hutch", required=True)
    p.add_argument("--experiment", required=True)


def json_or_str(raw: str) -> Any:
    """Parse a CLI value as JSON when possible, else keep it a string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
