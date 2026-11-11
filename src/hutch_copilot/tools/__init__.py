"""hutch_copilot.tools -- one module per backend and binary, plus ``common`` for the output contract and paths.

Every command prints JSON on stdout and exits with a code from ``common``.

Stdlib only: no module here may import a third-party package, so the tools run
from a bare checkout under psconda with no install step.
"""
