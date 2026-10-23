#!/usr/bin/env python3
"""Register this checkout's SessionStart/Stop hooks in a Claude settings.json.

    scripts/install-hooks.py                 # $CLAUDE_CONFIG_DIR, else ~/.claude-lcls
    scripts/install-hooks.py --config-dir ~/.claude
    scripts/install-hooks.py --check         # report only, exit 1 if a change is needed

Idempotent. A hutch-session-start/hutch-stop hook that points at a different
checkout -- another user's, copied from their settings -- is replaced, so a
session never runs someone else's mid-edit copy. Every other key is kept, and
the previous file is saved beside it.
"""
import argparse
import json
import os
import pathlib
import shutil
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOKS = {"SessionStart": ("hutch-session-start", 5), "Stop": ("hutch-stop", 10)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR")
                    or str(pathlib.Path.home() / ".claude-lcls"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.config_dir).expanduser() / "settings.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    changes = []
    for event, (name, timeout) in HOOKS.items():
        want = str(REPO / "bin" / name)
        groups = hooks.setdefault(event, [])
        mine = [h for g in groups for h in g.get("hooks", [])
                if h.get("command", "").endswith(f"/bin/{name}")]
        if [h["command"] for h in mine] == [want]:
            continue
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if h not in mine]
        hooks[event] = [g for g in groups if g["hooks"]]
        hooks[event].append({"hooks": [{"type": "command", "command": want,
                                        "timeout": timeout}]})
        changes.append(f"{event}: {', '.join(h['command'] for h in mine) or '(none)'} -> {want}")

    if not changes:
        print(f"{path}: hooks already point at {REPO}")
        return 0
    print("\n".join(changes))
    if args.check:
        return 1
    if path.exists():
        shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d%H%M%S')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    print(f"wrote {path}; start a new session for the hooks to load")
    return 0


if __name__ == "__main__":
    sys.exit(main())
