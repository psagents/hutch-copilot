"""hutch-setup: link the skills, install the hooks, register hutch-<hutch>, check the rest.

Idempotent: run it again after a pull, or with --check to see what it would change.
Every Claude config on this account is covered: $CLAUDE_CONFIG_DIR (claude-lcls), ~/.claude
(plain claude) and each Cogito host overlay under ~/.cogito/hosts.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time

from .tools.common import skill_root

REPO = skill_root()
HOOKS = {"SessionStart": ("hutch-session-start", 5), "Stop": ("hutch-stop", 10)}
HOME = pathlib.Path(os.path.expanduser(f"~{os.environ.get('USER', '')}"))


def targets() -> list[dict]:
    """Each Claude config: its dir, and the environment that makes `claude` use it."""
    base = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    out = []
    lcls = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude-lcls")
    if lcls.is_dir():
        out.append({"name": "claude-lcls", "dir": lcls, "cogito": False,
                    "env": {**base, "CLAUDE_CONFIG_DIR": str(lcls)}})
    if (HOME / ".claude").is_dir():
        out.append({"name": "claude", "dir": HOME / ".claude", "cogito": False,
                    "env": {**base, "HOME": str(HOME)}})
    for host in sorted((HOME / ".cogito" / "hosts").glob("*/")):
        out.append({"name": f"cogito:{host.name}", "dir": host / ".claude", "cogito": True,
                    "env": {**base, "HOME": str(host)}})
    return out


def link_skills(root: pathlib.Path, check: bool) -> list[str]:
    """hutch-copilot and each skills/<name> linked into ``root``; never replaces a stranger."""
    msgs = []
    for name, src in [("hutch-copilot", REPO)] + [(p.name, p) for p in sorted((REPO / "skills").iterdir())]:
        dst = root / name
        if dst.resolve() == src.resolve():
            continue
        if dst.exists() or dst.is_symlink():
            msgs.append(f"✗ {dst} is not this checkout's ({os.path.realpath(dst)}); left alone")
            continue
        msgs.append(f"→ link {dst} -> {src}")
        if not check:
            root.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src)
    return msgs


def install_hooks(settings_path: pathlib.Path, check: bool) -> list[str]:
    """SessionStart and Stop point at this checkout's bin/; a hook from another checkout is replaced."""
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    msgs = []
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
        msgs.append(f"→ {settings_path} {event}: "
                    f"{', '.join(h['command'] for h in mine) or '(none)'} -> {want}")
    if msgs and not check:
        if settings_path.exists():
            shutil.copy2(settings_path, f"{settings_path}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.chmod(0o600)
        os.replace(tmp, settings_path)
    return msgs


def on_hutch(hutch: str) -> bool:
    return socket.gethostname().split(".")[0] in (f"{hutch}-daq", f"{hutch}-control")


def mcp_command(hutch: str, sif: str, cogito: bool) -> list[str]:
    """The server's argv: local on the hutch itself, else over the user's own ssh.

    A Cogito session takes ssh even on the hutch: its agent container has neither
    apptainer nor /cds.
    """
    local = [str(REPO / "bin" / "hutch-mcp"), sif]
    if on_hutch(hutch) and not cogito:
        return local
    return ["ssh", "-T", "-o", "BatchMode=yes", "-J", "psdev", f"{hutch}-daq", *local]


def register_mcp(t: dict, hutch: str, sif: str, check: bool) -> list[str]:
    """hutch-<hutch> in this config runs exactly mcp_command(); anything else is replaced."""
    name, want = f"hutch-{hutch}", mcp_command(hutch, sif, t["cogito"])
    cfg = (pathlib.Path(t["env"]["CLAUDE_CONFIG_DIR"]) if "CLAUDE_CONFIG_DIR" in t["env"]
           else pathlib.Path(t["env"]["HOME"])) / ".claude.json"
    have = json.loads(cfg.read_text()).get("mcpServers", {}).get(name) if cfg.exists() else None
    if have and [have.get("command"), *have.get("args", [])] == want:
        return []
    if not check:
        claude = shutil.which("claude")
        if not claude:
            return [f"✗ {t['name']}: `claude` is not on PATH; cannot register {name}"]
        if have:
            subprocess.run([claude, "mcp", "remove", "-s", "user", name], env=t["env"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        p = subprocess.run([claude, "mcp", "add", "-s", "user", name, "--", *want], env=t["env"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if p.returncode:
            return [f"✗ {t['name']}: claude mcp add {name} failed: {p.stderr.strip()}"]
    return [f"→ {t['name']}: {name} = {' '.join(want)}"]


def _run(argv: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 255, "", str(exc))


def checks(hutch: str, sif: str) -> list[str]:
    """What setup cannot do for you: the Kerberos ticket, key-based ssh, the image, the advisors."""
    msgs = []
    ticket = f"FILE:{HOME}/krb5cc.ticket"
    if _run(["klist", "-s", "-c", ticket]).returncode:
        msgs.append(f"✗ no valid Kerberos ticket; in your own terminal: "
                    f"kinit -c {ticket} {os.environ.get('USER', '$USER')}@SLAC.STANFORD.EDU")
    reach = ([] if on_hutch(hutch)
             else ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-J", "psdev",
                   f"{hutch}-daq"])
    p = _run([*reach, "test", "-r", sif])
    if p.returncode == 255 and reach:
        msgs.append(f"✗ ssh to {hutch}-daq needs a password or failed: {p.stderr.strip()[-200:]}")
    elif p.returncode:
        msgs.append(f"✗ no image at {sif} on {hutch}-daq; pull it there with "
                    f"apptainer pull {sif} docker://<image>:<tag>")
    adv = _run([str(REPO / "scripts" / "fetch-advisors.sh"), "--report"], timeout=120)
    if adv.returncode:
        msgs.append(f"✗ advisors: {(adv.stdout + adv.stderr).strip().splitlines()[-1:]} "
                    f"(scripts/fetch-advisors.sh installs them)")
    return msgs


def build_parser() -> argparse.ArgumentParser:
    """Construct this command's parser (kept separate so it can be introspected)."""
    ap = argparse.ArgumentParser(prog="hutch-setup", description=__doc__.splitlines()[0])
    ap.add_argument("--hutch", required=True, help="e.g. mfx")
    ap.add_argument("--sif", default=str(HOME / "hutch-mcp.sif"),
                    help="the hutch-mcp image, as a path on the hutch machine")
    ap.add_argument("--check", action="store_true", help="report what would change; change nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    """hutch-setup: 0 when nothing is left to do, 1 otherwise."""
    args = build_parser().parse_args(argv)
    hutch = args.hutch.strip().lower()
    ts = targets()
    msgs: list[str] = []
    for root in {(t["dir"] / "skills").resolve() for t in ts}:
        msgs += link_skills(root, args.check)
    for settings in {(t["dir"] / "settings.json").resolve() for t in ts}:
        msgs += install_hooks(settings, args.check)
    marker = pathlib.Path(os.environ.get("HUTCH_SETUP_FILE")
                          or HOME / ".config" / "hutch-copilot" / "hutch")
    if not marker.exists() or marker.read_text().strip() != hutch:
        msgs.append(f"→ {marker} = {hutch}")
        if not args.check:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(hutch + "\n")
    for t in ts:
        msgs += register_mcp(t, hutch, args.sif, args.check)
    msgs += checks(hutch, args.sif)
    if not args.check and shutil.which("claude"):
        for t in ts:
            line = next((ln for ln in _run([shutil.which("claude"), "mcp", "list"], 120, t["env"])
                         .stdout.splitlines() if ln.startswith(f"hutch-{hutch}:")), "")
            if "Connected" not in line:
                msgs.append(f"✗ {t['name']}: hutch-{hutch} does not connect: {line[-120:] or 'absent'}")
    print("\n".join(msgs) if msgs else f"✓ hutch-copilot is set up for {hutch}")
    if any(m.startswith("→") for m in msgs) and not args.check:
        print("start a new session for the skills, hooks and server to load")
    pending = [m for m in msgs if m.startswith("✗") or (args.check and m.startswith("→"))]
    return 1 if pending else 0
