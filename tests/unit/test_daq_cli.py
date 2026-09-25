"""The hutch-daq CLI: exit codes, and that a refusal transmits nothing.

test_safety.py covers `require_confirmation` as a function. Nothing covered the
wiring from a refusal to the process exit code, which is what PLAN.md's standing
safety gate actually runs -- so the gate could have been weakened to an argparse
usage error without a single test going red. It nearly was: hoisting --confirm to
the top-level parser to fix a broken documented invocation turned
`exec '...' --confirm write` into `error: unrecognized arguments`, i.e. rc 2
instead of the refusal's rc 5.

These tests need no DAQ machine: every refusal is decided before any ssh, and the
fake below asserts that by failing loudly if it is ever called.
"""

from __future__ import annotations

import pytest

from hutch_tools import _cli, daq


@pytest.fixture
def no_ssh(monkeypatch):
    """Make any ssh attempt a test failure, and record that none happened."""
    calls = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("ssh was attempted; nothing should have been transmitted")

    monkeypatch.setattr(daq, "_ssh", _boom)
    return calls


# --------------------------------------------------------------------------
# Refusals -- rc 5, and nothing leaves the machine
# --------------------------------------------------------------------------
def test_beam_critical_understated_as_write_is_refused(no_ssh, capsys):
    """PLAN.md M1.a.5 / M2.2. Runs every shift; never de-scoped."""
    rc = daq.main(["--hutch", "mfx", "exec", "mfx_stopper_1.remove()",
                   "--confirm", "write"])
    assert rc == _cli.REFUSED
    assert "beam-critical" in capsys.readouterr().err
    assert no_ssh == []


def test_confirm_is_accepted_before_the_subcommand_too(no_ssh):
    """take-run/SKILL.md documents the leading form; it died with
    `invalid choice: '300'` while --confirm/--timeout were subcommand-only."""
    rc = daq.main(["--hutch", "mfx", "--timeout", "300", "--confirm", "write",
                   "exec", "mfx_stopper_1.remove()"])
    assert rc == _cli.REFUSED
    assert no_ssh == []


def test_unrecognised_code_confirmed_read_only_is_refused(no_ssh):
    """PLAN.md M2.3 -- unrecognised code errs upward to write, so a read-only
    confirmation is understating it."""
    rc = daq.main(["--hutch", "mfx", "exec", "frobnicate_the_thing()",
                   "--confirm", "read-only"])
    assert rc == _cli.REFUSED
    assert no_ssh == []


def test_no_confirmation_prints_the_classification_and_sends_nothing(no_ssh, capsys):
    """`classify` folded into `exec`: without --confirm you get the verdict on
    stdout and a refusal, and the snippet is not transmitted."""
    rc = daq.main(["--hutch", "mfx", "exec", "daq.begin()"])
    assert rc == _cli.REFUSED
    assert no_ssh == []

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
    monkeypatch.setattr(daq, "_ssh",
                        lambda *a, **kw: '{"status": "ok", "output": "True"}')
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.OK


def test_a_remote_exception_does_not_exit_zero(monkeypatch):
    """`exec` returned OK regardless of res["ok"], so rc could not tell a
    traceback in the session from a result."""
    monkeypatch.setattr(
        daq, "_ssh",
        lambda *a, **kw: '{"status": "error", "error": "NameError: daq"}')
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.UNREACHABLE


# --------------------------------------------------------------------------
# The surface itself
# --------------------------------------------------------------------------
def test_run_and_classify_are_gone():
    """Both were removed in M1.a: `run` shipped whole scripts into the session
    (and its callers are resolver calls now), `classify` folded into `exec`."""
    choices = _subcommands()
    assert choices == {"ping", "install", "exec", "status", "logs"}


def _subcommands() -> set:
    import argparse
    for action in daq.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("no subparsers found")


def test_unreachable_bridge_is_not_a_silent_zero(monkeypatch):
    def _down(*a, **kw):
        raise daq.BridgeUnavailable("ssh to mfx-daq failed (rc=255)")

    monkeypatch.setattr(daq, "_ssh", _down)
    assert daq.main(["--hutch", "mfx", "exec", "daq.state"]) == _cli.UNREACHABLE
    # ping reports offline rather than raising, but must still not exit 0
    assert daq.main(["--hutch", "mfx", "ping"]) == _cli.UNREACHABLE
