"""`post()` sends text as a literal form field, and a refusal is not an unreachable eLog."""

from __future__ import annotations

import json

import pytest

from hutch_copilot.tools import elog


def _fake(monkeypatch, reply):
    seen = {}
    monkeypatch.setattr(elog, "elog_ticket", lambda: (True, "FILE:/h/krb5cc.ticket"))

    def run(argv, **kw):
        seen.update(argv=argv, env=kw["env"])
        return elog.subprocess.CompletedProcess(argv, 0, json.dumps(reply).encode(), b"")
    monkeypatch.setattr(elog.subprocess, "run", run)
    return seen


def test_post_sends_literal_text_with_the_ticket_and_returns_the_id(monkeypatch):
    seen = _fake(monkeypatch, {"success": True, "value": {"_id": "abc"}})
    rec = elog.post("mfx101948627", "@not-a-file", tags=["jet"], run=7, attach=["/p.png"])
    assert rec["value"] == "abc" and seen["env"]["KRB5CCNAME"] == "FILE:/h/krb5cc.ticket"
    argv = seen["argv"]
    assert argv[argv.index("log_text=@not-a-file") - 1] == "--form-string"
    assert "files=@/p.png" in argv and argv[-1].endswith("/mfx101948627/ws/new_elog_entry")


def test_a_refusal_is_rc_refused(monkeypatch):
    _fake(monkeypatch, {"success": False, "error_msg": "no such experiment"})
    with pytest.raises(elog.ElogRefused):
        elog.post("x", "t")
    assert elog.main(["post", "--experiment", "x", "--text", "t"]) == elog.REFUSED
