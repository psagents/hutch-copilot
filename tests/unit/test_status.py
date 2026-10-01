"""hutch-lute status: the output paths Act 3 reads the merge log from.

`analyze-data` reads the CCTBX merge log itself rather than shipping a parser, so
`status` returning `stdout`/`stderr` is the whole interface between the two. A row
that parses but drops the paths breaks the demo silently.
"""

from __future__ import annotations

import subprocess

from hutch_tools import lute


def _fake_sacct(monkeypatch, stdout: str):
    def run(cmd, **kw):
        assert "StdOut" in cmd[-1], "status must ask sacct for the output paths"
        return subprocess.CompletedProcess(cmd, 0, stdout.encode(), b"")

    monkeypatch.setattr(lute.subprocess, "run", run)


def test_status_returns_the_output_paths(monkeypatch):
    _fake_sacct(monkeypatch, "41|merge|COMPLETED|00:12:03|01:00:00|0:0|/w/o.log|/w/e.log\n")
    got = lute.job_status("41")
    assert got["stdout"] == "/w/o.log"
    assert got["stderr"] == "/w/e.log"
    assert got["state"] == "COMPLETED"


def test_step_rows_do_not_shadow_the_job_row(monkeypatch):
    """Only the main row carries the paths; .batch/.extern leave them empty."""
    _fake_sacct(
        monkeypatch,
        "41|merge|TIMEOUT|12:00:16|12:00:00|0:0|/w/o.log|/w/e.log\n"
        "41.batch|batch|CANCELLED|12:00:17||0:15||\n"
        "41.extern|extern|COMPLETED|12:00:47||0:0||\n",
    )
    got = lute.job_status("41")
    assert got["stdout"] == "/w/o.log"
    assert got["hit_wall_clock"] is True


def test_no_sacct_record_reports_unknown_without_inventing_paths(monkeypatch):
    _fake_sacct(monkeypatch, "")
    got = lute.job_status("41")
    assert got["state"] == "UNKNOWN"
    assert "stdout" not in got
