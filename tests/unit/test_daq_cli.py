"""The hutch-daq CLI: exit codes, and that a refusal transmits nothing.

test_safety.py covers `require_confirmation` as a function. Nothing covered the
wiring from a refusal to the process exit code, which is what the standing safety
gate actually runs -- so the gate could have been weakened to an argparse
usage error without a single test going red. It nearly was: hoisting --confirm to
the top-level parser to fix a broken documented invocation turned
`exec '...' --confirm write` into `error: unrecognized arguments`, i.e. rc 2
instead of the refusal's rc 5.

These tests need no live session: every refusal is decided before the bridge is
touched, and the fake below asserts that by failing loudly if it is ever called.
The chokepoint is `_bridge_request` -- the loopback socket that replaced the ssh
hop once the tools moved onto the DAQ machine.
"""

from __future__ import annotations

import pytest

from hutch_tools import common as _cli
from hutch_tools import session as daq


@pytest.fixture
def no_bridge(monkeypatch):
    """Make any bridge request a test failure, and record that none happened."""
    calls = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("the bridge was used; nothing should have been transmitted")

    monkeypatch.setattr(daq, "_bridge_request", _boom)
    # ssh is gone from this path entirely; if one is ever attempted, say so.
    monkeypatch.setattr(daq, "_ssh", _boom)
    return calls


# --------------------------------------------------------------------------
# Refusals -- rc 5, and nothing leaves the machine
# --------------------------------------------------------------------------
def test_beam_critical_understated_as_write_is_refused(no_bridge, capsys):
    """Standing acceptance test: runs every shift, never de-scoped."""
    rc = daq.main(["--hutch", "mfx", "exec", "mfx_stopper_1.remove()",
                   "--confirm", "write"])
    assert rc == _cli.REFUSED
    assert "beam-critical" in capsys.readouterr().err
    assert no_bridge == []


def test_confirm_is_accepted_before_the_subcommand_too(no_bridge):
    """take-run/SKILL.md documents the leading form; it died with
    `invalid choice: '300'` while --confirm/--timeout were subcommand-only."""
    rc = daq.main(["--hutch", "mfx", "--timeout", "300", "--confirm", "write",
                   "exec", "mfx_stopper_1.remove()"])
    assert rc == _cli.REFUSED
    assert no_bridge == []


def test_unrecognised_code_confirmed_read_only_is_refused(no_bridge):
    """Unrecognised code errs upward to write, so a read-only confirmation is
    understating it. Standing acceptance test."""
    rc = daq.main(["--hutch", "mfx", "exec", "frobnicate_the_thing()",
                   "--confirm", "read-only"])
    assert rc == _cli.REFUSED
    assert no_bridge == []


def test_no_confirmation_prints_the_classification_and_sends_nothing(no_bridge, capsys):
    """`classify` folded into `exec`: without --confirm you get the verdict on
    stdout and a refusal, and the snippet is not transmitted."""
    rc = daq.main(["--hutch", "mfx", "exec", "daq.begin()"])
    assert rc == _cli.REFUSED
    assert no_bridge == []

    import json
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["class"] == "write"
    assert verdict["reasons"]


# --------------------------------------------------------------------------
# Read-only passes through without demanding anything
# --------------------------------------------------------------------------
def test_read_only_code_needs_no_confirmation(monkeypatch):
    """A needless confirmation prompt at 03:00 trains operators to pass
    --confirm reflexively, which is how the gate stops meaning anything."""
    monkeypatch.setattr(daq, "_bridge_request",
                        lambda *a, **kw: '{"status": "ok", "output": "True"}')
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.OK


def test_a_remote_exception_does_not_exit_zero(monkeypatch):
    """`exec` returned OK regardless of res["ok"], so rc could not tell a
    traceback in the session from a result."""
    monkeypatch.setattr(
        daq, "_bridge_request",
        lambda *a, **kw: '{"status": "error", "error": "NameError: daq"}')
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.UNREACHABLE


# --------------------------------------------------------------------------
# `exec -` reads stdin -- the multi-line path, in place of a `run` subcommand
# --------------------------------------------------------------------------
def test_exec_dash_reads_the_script_from_stdin(monkeypatch, capsys):
    """This is why `run` is not needed: a whole script arrives on stdin.

    Undocumented and untested until now, which is how a `hutch-daq run` taking a
    file path kept looking necessary.
    """
    import io
    import json

    sent = {}

    def fake_bridge(payload, timeout):
        sent.update(json.loads(payload.decode()))
        return '{"status": "ok", "output": "3"}'

    monkeypatch.setattr(daq, "_bridge_request", fake_bridge)
    monkeypatch.setattr("sys.stdin", io.StringIO("a = 1\nb = 2\nprint(a + b)\n"))
    assert daq.main(["--hutch", "mfx", "exec", "-"]) == _cli.OK
    assert sent["code"] == "a = 1\nb = 2\nprint(a + b)\n"


def test_a_script_on_stdin_is_classified_as_one_unit(monkeypatch, capsys):
    """Every line is classified together, so a write buried in the middle of an
    otherwise read-only script still gates the whole thing."""
    import io

    monkeypatch.setattr(daq, "_bridge_request", _refuse_bridge)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("print(daq.state)\nmfx_stopper_1.remove()\nprint('done')\n"),
    )
    assert daq.main(["--hutch", "mfx", "exec", "-"]) == _cli.REFUSED
    assert "beam-critical" in capsys.readouterr().out


def _refuse_bridge(*a, **kw):
    raise AssertionError("the bridge was used; nothing should have been transmitted")


# --------------------------------------------------------------------------
# The surface itself
# --------------------------------------------------------------------------
def test_run_and_classify_are_gone():
    """Both were removed in M1.a: `run` shipped whole scripts into the session
    (and its callers are resolver calls now), `classify` folded into `exec`.

    `config` and `logs` left with the Oct 2026 split -- they read the DAQ's own
    files, not the session, so they are `hutch-daq`. What stays here is the
    session and the gate in front of it, plus `conf` (hutch-python's own
    conf.yml, which is session truth rather than DAQ truth)."""
    choices = _subcommands()
    assert choices == {"ping", "install", "exec", "conf"}


def _subcommands() -> set:
    import argparse
    for action in daq.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("no subparsers found")


def test_unreachable_bridge_is_not_a_silent_zero(monkeypatch):
    def _down(*a, **kw):
        raise daq.BridgeUnavailable("nothing is listening on localhost:9999")

    monkeypatch.setattr(daq, "_bridge_request", _down)
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.UNREACHABLE
    # ping reports offline rather than raising, but must still not exit 0
    assert daq.main(["--hutch", "mfx", "ping"]) == _cli.UNREACHABLE
