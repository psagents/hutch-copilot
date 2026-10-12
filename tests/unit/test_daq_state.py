"""Reading DAQ state off the control port: the release, the env, the 9-tuple.

The bug these replace: state was read through hutch-python's bridge, which is
usually not running, so a perfectly reachable DAQ read as unknown and both
readiness and the take-run gate failed closed.

Everything here is faked -- a stub `psdaq` on a stub release tree -- so no live
DAQ is needed. What is not faked is the shape: the tuple below is the one a
real `DaqControl.getStatus()` returned from mfx-daq on 2026-10-12.
"""

from __future__ import annotations

import pytest

from hutch_tools import daq

#: Verbatim from a live mfx read. Nine positional fields, no names.
LIVE_STATUS = ("reset", "reset", "BEAM", False, {}, False, "mfx101937027", 0, 93)

_STUB_DAQCONTROL = """\
class DaqControl:
    def __init__(self, host, platform, timeout):
        assert isinstance(platform, int), platform
        self.seen = (host, platform)
    def getStatus(self):
        return %r
"""


@pytest.fixture()
def daq_release(tmp_path, monkeypatch):
    """A stub release tree whose header points at this interpreter."""
    site = tmp_path / "rel" / "lib" / "python3.9" / "site-packages" / "psdaq" / "control"
    site.mkdir(parents=True)
    (site.parent / "__init__.py").touch()
    (site / "__init__.py").touch()
    (site / "DaqControl.py").write_text(_STUB_DAQCONTROL % (LIVE_STATUS,))

    import sys
    conda = tmp_path / "conda" / "bin"
    conda.mkdir(parents=True)
    (conda / "python").symlink_to(sys.executable)

    log = tmp_path / "2026" / "10" / "12_13:40:47_h:control.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"# HOST:h\n# TESTRELDIR:{tmp_path}/rel\n"
                   f"# CONDA_PREFIX:{tmp_path}/conda\nbody\n")
    monkeypatch.setattr(daq, "_log_dir", lambda hutch, when=None: str(log.parent))
    monkeypatch.setattr(daq, "config_summary", lambda hutch: {
        "path": "mfx.py", "collect_host": "drp-srcf-cmp014", "platform": "0"})
    return tmp_path


def test_state_names_every_field_of_the_tuple(daq_release):
    v = daq.state("mfx")["value"]
    assert v == dict(zip(daq._STATUS_FIELDS, LIVE_STATUS))
    assert v["experiment"] == "mfx101937027" and v["last_run_number"] == 93


def test_the_source_names_what_was_queried(daq_release):
    assert "drp-srcf-cmp014" in daq.state("mfx")["source"]


def test_an_all_error_status_raises_rather_than_reading_as_a_state(daq_release):
    """getStatus() answers 'error' in every field instead of raising when the
    control port does not reply -- which is how the port being down would
    otherwise arrive as a DAQ state the operator is told about."""
    stub = daq_release / "rel/lib/python3.9/site-packages/psdaq/control/DaqControl.py"
    stub.write_text(_STUB_DAQCONTROL % (("error",) * 9,))
    with pytest.raises(daq.DaqError, match="no reply from the DAQ control port"):
        daq.state("mfx")


def test_a_config_without_a_collect_host_does_not_query_localhost(daq_release, monkeypatch):
    """DaqControl defaults host to localhost, which on a DAQ machine answers
    nothing and looks like an outage rather than an unparsed config."""
    monkeypatch.setattr(daq, "config_summary", lambda hutch: {
        "path": "mfx.py", "collect_host": None, "platform": "0"})
    with pytest.raises(daq.DaqError, match="no collect_host"):
        daq.state("mfx")


def test_a_release_without_a_header_raises(daq_release, monkeypatch):
    """Never a guessed release: several lcls2_<MMDDYY> trees live side by side
    per hutch, and the running one is not the newest by mtime."""
    for log in (daq_release / "2026" / "10").iterdir():
        log.write_text("no header here\n")
    with pytest.raises(daq.DaqError, match="TESTRELDIR"):
        daq.state("mfx")
