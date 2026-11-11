"""bin/hutch-setup: links, hooks and the hutch-<h> registration, all in a temporary home."""

from __future__ import annotations

import json
import subprocess

import pytest

from hutch_copilot import install

SKILLS = sorted(p.name for p in (install.REPO / "skills").iterdir())


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "HOME", tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HUTCH_SETUP_FILE", str(tmp_path / "hutch"))
    (tmp_path / ".claude").mkdir()
    return tmp_path


def test_links_every_skill_once_and_then_does_nothing(home):
    root = home / ".claude" / "skills"
    msgs = install.link_skills(root, check=False)
    assert len(msgs) == 1 + len(SKILLS)
    assert (root / "are-we-ready").resolve() == install.REPO / "skills" / "are-we-ready"
    assert (root / "hutch-copilot").resolve() == install.REPO
    assert install.link_skills(root, check=False) == []


def test_a_stranger_in_the_skills_dir_is_left_alone(home):
    root = home / ".claude" / "skills"
    root.mkdir()
    (root / "take-run").symlink_to("/sdf/home/x/someone/hutch-copilot/skills/take-run")
    msgs = install.link_skills(root, check=False)
    assert any(m.startswith("✗") and "take-run" in m for m in msgs)
    assert str((root / "take-run").readlink()).startswith("/sdf/home/x/someone")


def test_check_changes_nothing(home):
    root = home / ".claude" / "skills"
    assert install.link_skills(root, check=True)
    assert not root.exists()
    assert install.install_hooks(home / ".claude" / "settings.json", check=True)
    assert not (home / ".claude" / "settings.json").exists()


def test_hooks_replace_another_checkout_and_keep_the_rest(home):
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/sdf/home/f/fpoitevi/hutch-copilot/bin/hutch-stop"},
        {"type": "command", "command": "/usr/bin/other"}]}]}}))
    assert install.install_hooks(path, check=False)
    s = json.loads(path.read_text())
    commands = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
    assert commands == ["/usr/bin/other", str(install.REPO / "bin" / "hutch-stop")]
    assert s["model"] == "x"
    assert install.install_hooks(path, check=False) == []


@pytest.mark.parametrize("host,cogito,local", [
    ("sdfiana025", False, False), ("mfx-daq", False, True), ("mfx-daq", True, False),
])
def test_the_server_is_local_only_for_a_plain_session_on_the_hutch(monkeypatch, host, cogito,
                                                                   local):
    monkeypatch.setattr(install.socket, "gethostname", lambda: host)
    argv = install.mcp_command("mfx", "/x/hutch-mcp.sif", cogito)
    assert argv[-2:] == [str(install.REPO / "bin" / "hutch-mcp"), "/x/hutch-mcp.sif"]
    assert (argv[0] == "ssh") is not local
    if not local:
        assert argv[argv.index("-J") + 1:argv.index("-J") + 3] == ["psdev", "mfx-daq"]


def _target(home, cogito=False):
    return {"name": "t", "dir": home / ".claude", "cogito": cogito, "env": {"HOME": str(home)}}


def test_a_matching_registration_is_left_alone(home, monkeypatch):
    monkeypatch.setattr(install.socket, "gethostname", lambda: "sdfiana025")
    want = install.mcp_command("mfx", "/x.sif", False)
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"hutch-mfx": {"command": want[0], "args": want[1:]}}}))
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: pytest.fail("ran claude"))
    assert install.register_mcp(_target(home), "mfx", "/x.sif", check=False) == []


def test_a_stale_registration_is_removed_then_added(home, monkeypatch):
    monkeypatch.setattr(install.socket, "gethostname", lambda: "sdfiana025")
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"hutch-mfx": {"command": "ssh", "args": ["old/server/hutch-mcp.sh"]}}}))
    calls = []
    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(install.subprocess, "run", lambda argv, **k: calls.append((argv, k["env"]))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    msgs = install.register_mcp(_target(home), "mfx", "/x.sif", check=False)
    assert [c[0][1:3] for c in calls] == [["mcp", "remove"], ["mcp", "add"]]
    assert calls[1][0][calls[1][0].index("--") + 1:] == install.mcp_command("mfx", "/x.sif", False)
    assert calls[1][1]["HOME"] == str(home)
    assert msgs[0].startswith("→")


def test_check_reports_pending_work_and_writes_nothing(home, monkeypatch, capsys):
    monkeypatch.setattr(install, "checks", lambda hutch, sif: [])
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: pytest.fail("ran something"))
    assert install.main(["--hutch", "mfx", "--check"]) == 1
    assert not (home / "hutch").exists() and not (home / ".claude" / "skills").exists()
    assert "hutch-mfx" in capsys.readouterr().out
