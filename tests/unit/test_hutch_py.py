"""hutch-py: run in a fresh hutch-python load, and conf. The approval is Claude Code's prompt, not here."""

from __future__ import annotations

import argparse
import io

from hutch_copilot.tools import common as _cli
from hutch_copilot.tools import session as py


def _fake_run(monkeypatch, rc=0, stdout=b"3\nHUTCH_NS_SIZE: 402\n"):
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, input=kw["input"].decode(), env=kw["env"])
        return py.subprocess.CompletedProcess(argv, rc, stdout, b"")
    monkeypatch.setattr(py.subprocess, "run", run)
    return seen


def test_the_surface_is_run_and_conf():
    """exec, ping and install went with the live-session bridge; --confirm with the safety check."""
    sub = next(a for a in py.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub.choices) == {"run", "conf"}
    assert "--confirm" not in {o for a in py.build_parser()._actions for o in a.option_strings}


def test_run_puts_hutch_engineering_scripts_first_on_path(monkeypatch):
    """takepeds needs daqutils/get_info from there; without it it records nothing."""
    seen = _fake_run(monkeypatch)
    monkeypatch.setenv("HUTCH_ENG_TOOLS", "/eng")
    py.run_script("MFX", "print(1)")
    assert seen["env"]["PATH"].startswith("/eng/mfx/scripts:")


def test_run_dash_reads_the_script_from_stdin_and_reports_the_namespace(monkeypatch, capsys):
    seen = _fake_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("a = 1\nb = 2\nprint(a + b)\n"))
    assert py.main(["--hutch", "mfx", "run", "-"]) == _cli.OK
    assert "a = 1\nb = 2\nprint(a + b)\n" in seen["input"]
    assert '"namespace_size": 402' in capsys.readouterr().out


def test_a_snippet_that_raised_is_rc_1_not_unreachable(monkeypatch):
    _fake_run(monkeypatch, rc=1, stdout=b"")
    assert py.main(["--hutch", "mfx", "run", "x()"]) == 1


def test_no_launcher_is_unreachable(monkeypatch):
    def missing(*a, **kw):
        raise FileNotFoundError("no mfxpython")
    monkeypatch.setattr(py.subprocess, "run", missing)
    assert py.main(["--hutch", "mfx", "run", "print(1)"]) == _cli.UNREACHABLE
