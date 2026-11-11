#!/bin/sh
# Moved to bin/hutch-mcp; kept for the hutch-<h> registrations until bin/hutch-setup re-registers them.
exec "$(dirname -- "$0")/../bin/hutch-mcp" "$@"
