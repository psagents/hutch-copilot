"""bin/hutch-stop: the Stop hook that makes logging ambient.

Shell, so pytest shells out. What is pinned here is the whole reason the hook is
safe to install: it blocks exactly once, it never guesses an experiment, and every
malformed input leaves the turn alone. A bookkeeping hook that can wedge a shift is
worse than no bookkeeping, so each `exit 0` path gets a test.

`hostname` is faked on PATH rather than the host being trusted: the hook only fires
on a `*-daq`/`*-control` machine, so without this the block test passes at MFX and
fails everywhere else -- and the off-hutch guard could never be tested at all.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from hutch_tools.common import script

STOP = script("bin", "hutch-stop")


@pytest.fixture()
def fake_host(tmp_path):
    """Put a `hostname` on PATH that reports whatever a test asks for."""
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "hostname").write_text('#!/bin/bash\nprintf "%s\\n" "$FAKE_HOST"\n')
    (shim / "hostname").chmod(0o755)

    def run(payload: dict, host: str = "mfx-daq") -> subprocess.CompletedProcess:
        import os
        env = {**os.environ, "FAKE_HOST": host,
               "PATH": f"{shim}:{os.environ['PATH']}"}
        return subprocess.run([str(STOP)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    return run


@pytest.fixture()
def transcript(tmp_path):
    """A transcript naming an experiment, as the hook expects to find one."""
    p = tmp_path / "session.jsonl"
    p.write_text(
        '{"uuid":"a1","type":"user"}\n'
        '{"uuid":"a2","type":"assistant","message":'
        '"ran bin/hutch-state audit --hutch mfx --experiment mfx101609126"}\n'
    )
    return p


def _payload(transcript, **over) -> dict:
    base = {
        "hook_event_name": "Stop",
        "session_id": "s1",
        "stop_hook_active": False,
        "transcript_path": str(transcript),
        "cwd": "/sdf/data/lcls/ds/mfx/mfx101609126",
        "last_assistant_message": "ran bin/hutch-daq state, DAQ is configured",
    }
    return {**base, **over}


# --------------------------------------------------------------------------
# The block -- the one path that produces output
# --------------------------------------------------------------------------
def test_a_turn_that_touched_the_experiment_is_asked_to_sync(fake_host, transcript):
    res = fake_host(_payload(transcript))
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["decision"] == "block"
    assert "sync-log/SKILL.md" in out["reason"]
    assert "mfx101609126" in out["reason"]
    assert "mfx101609126" in out["systemMessage"]


def test_the_experiment_comes_from_the_transcript_not_a_guess(fake_host, tmp_path):
    """Two experiments in one session: the most recent one wins."""
    p = tmp_path / "t.jsonl"
    p.write_text("--experiment mfx101609126\n--experiment mfx101624926\n")
    out = json.loads(fake_host(_payload(p)).stdout)
    assert "mfx101624926" in out["reason"]
    assert "mfx101609126" not in out["reason"]


def test_output_is_valid_json_even_with_an_odd_experiment_name(fake_host, tmp_path):
    """jq builds the payload, so no name or path can break the JSON."""
    p = tmp_path / "t.jsonl"
    p.write_text('--experiment mfx101609126"; rm -rf /\n')
    res = fake_host(_payload(p))
    assert res.returncode == 0
    if res.stdout.strip():
        json.loads(res.stdout)


# --------------------------------------------------------------------------
# Every silent path. None of these may emit anything.
# --------------------------------------------------------------------------
def test_stop_hook_active_is_the_loop_guard(fake_host, transcript):
    """The invocation after our own block. Blocking here is the infinite loop."""
    res = fake_host(_payload(transcript, stop_hook_active=True))
    assert (res.returncode, res.stdout.strip()) == (0, "")


def test_off_hutch_the_hook_stays_silent(fake_host, transcript):
    for host in ("s3dflogin", "psana101", "someones-laptop", ""):
        res = fake_host(_payload(transcript), host=host)
        assert (res.returncode, res.stdout.strip()) == (0, ""), host


def test_a_transcript_with_no_experiment_is_never_guessed(fake_host, tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"uuid":"a1","type":"assistant","message":"hello"}\n')
    res = fake_host(_payload(p))
    assert (res.returncode, res.stdout.strip()) == (0, "")


def test_a_turn_that_touched_nothing_is_not_asked(fake_host, transcript):
    res = fake_host(_payload(transcript, last_assistant_message="Here is a haiku."))
    assert (res.returncode, res.stdout.strip()) == (0, "")


def test_developing_the_copilot_is_not_operating_the_hutch(fake_host, transcript):
    """The false positive this hook produced on the turn that wrote it.

    Writing `--experiment mfx101609126` into a test fixture is indistinguishable
    from running it against the DAQ, so a session in this checkout would log its
    own unit tests to a real beamtime.
    """
    for cwd in (str(STOP.parent.parent), str(STOP.parent), str(STOP.parent.parent / "sync-log")):
        res = fake_host(_payload(transcript, cwd=cwd))
        assert (res.returncode, res.stdout.strip()) == (0, ""), cwd


def test_a_path_merely_sharing_the_checkout_prefix_still_syncs(fake_host, transcript):
    """`hutch-copilot-notes/` is not inside `hutch-copilot/`."""
    res = fake_host(_payload(transcript, cwd=f"{STOP.parent.parent}-other"))
    assert json.loads(res.stdout)["decision"] == "block"


def test_a_missing_cwd_does_not_suppress_the_sync(fake_host, transcript):
    """Absent cwd must fail toward logging; the guard is narrow, not a catch-all."""
    payload = _payload(transcript)
    del payload["cwd"]
    assert json.loads(fake_host(payload).stdout)["decision"] == "block"


def test_an_unreadable_transcript_is_not_an_error(fake_host, tmp_path):
    for path in (str(tmp_path / "missing.jsonl"), ""):
        res = fake_host(_payload(tmp_path / "x", transcript_path=path))
        assert (res.returncode, res.stdout.strip()) == (0, ""), path


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '{"a":'])
def test_malformed_stdin_leaves_the_turn_alone(stdin, tmp_path):
    """The hook runs on every turn end; it must never be the thing that breaks one."""
    res = subprocess.run([str(STOP)], input=stdin, capture_output=True, text=True)
    assert (res.returncode, res.stdout.strip()) == (0, "")
