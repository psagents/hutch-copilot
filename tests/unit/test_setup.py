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
    assert len(msgs) == 1 and all(name in msgs[0] for name in ["hutch-copilot", *SKILLS])
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


def _target(home):
    return {"name": "t", "dir": home / ".claude", "env": {"HOME": str(home)}}


def test_an_old_hutch_server_is_removed_and_none_is_added(home, monkeypatch):
    """Off the hutch only Cogito's gateway reaches it; on it, the CLIs do."""
    calls = []
    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(install.subprocess, "run", lambda argv, **k: calls.append(argv)
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    assert install.remove_mcp(_target(home), "mfx", check=False) == []
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"hutch-mfx": {"command": "ssh", "args": ["mfx-daq"]}}}))
    assert install.remove_mcp(_target(home), "mfx", check=True)[0].startswith("→")
    assert calls == []
    install.remove_mcp(_target(home), "mfx", check=False)
    assert [c[1:3] for c in calls] == [["mcp", "remove"]]


def test_check_reports_pending_work_and_writes_nothing(home, monkeypatch, capsys):
    monkeypatch.setattr(install, "checks", lambda *a: [])
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: pytest.fail("ran something"))
    assert install.main(["--hutch", "mfx", "--check"]) == 1
    assert not (home / "hutch").exists() and not (home / ".claude" / "skills").exists()
    assert "~/hutch = mfx" in capsys.readouterr().out


def test_advisors_reach_every_config_and_a_stranger_is_left_alone(home, monkeypatch):
    """claude-lcls once lacked three required advisors that ~/.claude/skills had."""
    a, b, shared = home / "a" / "skills", home / "b" / "skills", home / "shared"
    for root, name in ((shared, "ask-lute"), (a, "psana-daq")):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text("x")
    b.mkdir(parents=True)
    (b / "psana-daq").symlink_to(home / "gone")
    monkeypatch.setenv("HUTCH_SKILL_ROOTS", str(shared))
    monkeypatch.setattr(install, "required",
                        lambda: {"ask-lute": ["x"], "psana-daq": ["y"], "ask-smalldata": ["z"]})
    msgs = install.link_advisors([a, b], check=False)
    assert (a / "ask-lute").resolve() == (b / "ask-lute").resolve() == (shared / "ask-lute").resolve()
    assert (b / "psana-daq").readlink() == home / "gone"
    assert any(m.startswith("✗") and str(b / "psana-daq") in m for m in msgs)
    assert any(m.startswith("✗") and "ask-smalldata" in m for m in msgs)
    assert not [m for m in install.link_advisors([a, b], check=False) if m.startswith("→")]


def test_only_what_needs_the_operator_is_listed_for_them(home, monkeypatch, capsys):
    monkeypatch.setattr(install, "_run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(install, "link_advisors", lambda roots, check: [])
    monkeypatch.setattr(install, "remove_mcp", lambda *a: [])
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    monkeypatch.setattr(install, "checks", lambda *a: ["✗ get a Kerberos ticket"])
    assert install.main(["--hutch", "mfx"]) == 1
    out = capsys.readouterr().out
    assert "You still need to:\n  1. get a Kerberos ticket" in out and "Then start a new" in out
    monkeypatch.setattr(install, "checks", lambda *a: [])
    assert install.main(["--hutch", "mfx"]) == 0
    assert "You still need to" not in capsys.readouterr().out
