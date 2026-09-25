"""hutch_tools -- the tool layer for hutch-copilot.

One module per binary, plus ``common`` for the output contract and paths.
Every command prints JSON on stdout and exits with a code from ``common``.

Stdlib only: no module here may import a third-party package, so the tools run
from a bare checkout under psconda with no install step.
"""

from __future__ import annotations

__version__ = "0.2.0"
