"""hutchlib -- the tool layer for hutch-copilot.

Mechanism lives here so that skills can carry only judgment. Every entry point
returns JSON on stdout and a non-zero exit status with a structured error on
stderr.

Layers:
  paths      one definition of every hutch-copilot path
  state      provenance-carrying cache with per-field TTL, atomic writes
  safety     machine-checkable action classification, consulted before send
  bridge     the one implementation of the ipython / psdev / controls bridges
  resolvers  ask the authoritative source; never fall back to a literal
  traps      executable incident register (the only written knowledge)
  lute       task-agnostic LUTE submission adapter
"""

__all__ = ["paths", "state", "safety", "bridge", "resolvers", "traps", "lute"]
__version__ = "0.1.0"
