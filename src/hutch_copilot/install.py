"""hutch-setup: link the skills and advisors, install the hooks, record the hutch, check the ticket.

Idempotent: run it again after a pull, or with --check to see what it would change.
Every Claude config on this account is covered: $CLAUDE_CONFIG_DIR (claude-lcls), ~/.claude
(plain claude) and each Cogito host overlay under ~/.cogito/hosts. No config registers a hutch
server: Cogito reaches the hutch through its own sdf-mcp gateway, and a terminal on the hutch
machine runs the CLIs.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

from .tools.common import skill_root
from .tools.skills import ADVISORS, required, skill_roots

REPO = skill_root()
HOOKS = {"SessionStart": ("hutch-session-start", 5), "Stop": ("hutch-stop", 10)}
HOME = pathlib.Path(os.path.expanduser(f"~{os.environ.get('USER', '')}"))


def targets() -> list[dict]:
    """Each Claude config: its dir, and the environment that makes `claude` use it."""
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    out = []
    lcls = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude-lcls")
    if lcls.is_dir():
        out.append({"name": "claude-lcls", "dir": lcls,
                    "env": {**base, "CLAUDE_CONFIG_DIR": str(lcls)}})
    if (HOME / ".claude").is_dir():
        out.append({"name": "claude", "dir": HOME / ".claude",
                    "env": {**base, "HOME": str(HOME)}})
    for host in sorted((HOME / ".cogito" / "hosts").glob("*/")):
        out.append({"name": f"cogito:{host.name}", "dir": host / ".claude",
                    "env": {**base, "HOME": str(host)}})
    return out


def link_skills(root: pathlib.Path, check: bool) -> list[str]:
    """hutch-copilot and each skills/<name> linked into ``root``; never replaces a stranger."""
    msgs, linked = [], []
    for name, src in [("hutch-copilot", REPO)] + [(p.name, p) for p in sorted((REPO / "skills").iterdir())]:
        dst = root / name
        if dst.resolve() == src.resolve():
            continue
        if dst.exists() or dst.is_symlink():
            msgs.append(f"✗ {dst} links to another copy ({os.path.realpath(dst)}): "
                        f"remove it to use this checkout, then re-run")
            continue
        linked.append(name)
        if not check:
            root.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src)
    return ([f"→ {root}: linked {', '.join(linked)}"] if linked else []) + msgs


def link_advisors(roots: list[pathlib.Path], check: bool) -> list[str]:
    """Every advisor into each root lacking it, from the first root or shared root that holds it."""
    # A session loads only its own config's skills/: one fetched into ~/.claude is not claude-lcls's.
    need = required()
    msgs: list[str] = []
    linked: dict[pathlib.Path, list[str]] = {}
    for name in sorted(ADVISORS):
        # os.path.isfile, not Path.is_file: a shared root this host cannot read must read as absent.
        src = next((r / name for r in [*roots, *skill_roots()]
                    if os.path.isfile(r / name / "SKILL.md")), None)
        if src is None:
            if name in need:
                msgs.append(f"✗ install the required advisor {name}: it is in no skills folder "
                            f"(scripts/fetch-advisors.sh --report shows where it looked)")
            continue
        for root in roots:
            dst = root / name
            if os.path.isfile(dst / "SKILL.md"):
                continue
            if dst.exists() or dst.is_symlink():
                if name in need:
                    msgs.append(f"✗ {dst} is not a usable copy of {name}: remove it, then re-run")
                continue
            linked.setdefault(root, []).append(name)
            if not check:
                root.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src.resolve())
    return [f"→ {root}: linked {len(n)} advisors" for root, n in linked.items()] + msgs


def install_hooks(settings_path: pathlib.Path, check: bool) -> list[str]:
    """SessionStart and Stop point at this checkout's bin/; a hook from another checkout is replaced."""
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    events = []
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
        hooks[event].append({"hooks": [{"type": "command", "command": want, "timeout": timeout}]})
        events.append(event)
    if events and not check:
        if settings_path.exists():
            shutil.copy2(settings_path, f"{settings_path}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.chmod(0o600)
        os.replace(tmp, settings_path)
    return [f"→ {settings_path}: {' and '.join(events)} hooks run this checkout's bin/"] if events else []


def remove_mcp(t: dict, hutch: str, check: bool) -> list[str]:
    """An old hutch-<hutch> registration (the server over ssh) removed from this config."""
    name = f"hutch-{hutch}"
    cfg = (pathlib.Path(t["env"]["CLAUDE_CONFIG_DIR"]) if "CLAUDE_CONFIG_DIR" in t["env"]
           else pathlib.Path(t["env"]["HOME"])) / ".claude.json"
    if not (cfg.exists() and name in json.loads(cfg.read_text()).get("mcpServers", {})):
        return []
    if not check:
        claude = shutil.which("claude")
        if not claude:
            return [f"✗ put `claude` on PATH, then re-run: {t['name']} needs it to remove {name}"]
        p = subprocess.run([claude, "mcp", "remove", "-s", "user", name], env=t["env"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if p.returncode:
            return [f"✗ {t['name']}: `claude mcp remove -s user {name}` failed: {p.stderr.strip()}"]
    return [f"→ {t['name']}: removed {name}; the hutch is reached from Cogito or {hutch}-daq"]


def _run(argv: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 255, "", str(exc))


def checks() -> list[str]:
    """What setup cannot do for you: the Kerberos ticket the eLog reads."""
    ticket = f"FILE:{HOME}/krb5cc.ticket"
    if _run(["klist", "-s", "-c", ticket]).returncode == 0:
        return []
    return [f"✗ get a Kerberos ticket for the eLog, in your own terminal (not with `!`): "
            f"kinit -c {ticket} {os.environ.get('USER', '$USER')}@SLAC.STANFORD.EDU"]


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap = argparse.ArgumentParser(prog="hutch-setup", description=__doc__.splitlines()[0])
    ap.add_argument("--hutch", required=True, help="e.g. mfx")
    ap.add_argument("--check", action="store_true", help="report what would change; change nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-setup: 0 when nothing is left to do, 1 otherwise."""
    args = build_parser().parse_args(argv)
    hutch = args.hutch.strip().lower()
    ts = targets()
    roots = list(dict.fromkeys((t["dir"] / "skills").resolve() for t in ts))
    msgs: list[str] = []
    for root in roots:
        msgs += link_skills(root, args.check)
    for settings in dict.fromkeys((t["dir"] / "settings.json").resolve() for t in ts):
        msgs += install_hooks(settings, args.check)
    if not args.check:
        print("fetching the advisors...", flush=True)
        fetched = re.search(r"linked: \d+ new, \d+ updated", _run(
            [str(REPO / "scripts" / "fetch-advisors.sh")], timeout=600).stdout)
        if fetched and fetched.group() != "linked: 0 new, 0 updated":
            msgs.append(f"→ advisors from GitHub, {fetched.group()}")
    msgs += link_advisors(roots, args.check)
    marker = pathlib.Path(os.environ.get("HUTCH_SETUP_FILE")
                          or HOME / ".config" / "hutch-copilot" / "hutch")
    if not marker.exists() or marker.read_text().strip() != hutch:
        msgs.append(f"→ {marker} = {hutch}")
        if not args.check:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(hutch + "\n")
    for t in ts:
        msgs += remove_mcp(t, hutch, args.check)
    msgs += checks()
    # ~ shortens what was done; never what to run: `FILE:~/` is not expanded by a shell.
    done = [m.replace(f"{HOME}/", "~/") for m in msgs if m.startswith("→")]
    todo = [m[2:] for m in msgs if m.startswith("✗")]
    print(f"hutch-copilot setup for {hutch.upper()}" + (", what it would change:" if args.check else ":"))
    print("\n".join(f"  {m}" for m in done) or "  ✓ nothing to change")
    if todo:
        print("\nYou still need to:\n" + "\n".join(f"  {i}. {m}" for i, m in enumerate(todo, 1)))
    if not args.check:
        print(f"\n{'Then s' if todo else 'S'}tart a new Cogito session, or Claude Code on {hutch}-daq, "
              f"and type /are-we-ready.")
    return 1 if todo or (args.check and done) else 0
