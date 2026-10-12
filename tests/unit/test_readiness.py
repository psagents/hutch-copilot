"""The three readiness verbs: check_beam, check_hutch, check_daq.

Each test here pins a failure that actually happened on mfx-daq, because every
one of them produces a plausible-looking wrong answer rather than an error:

- `check_daq` read state through hutch-python's bridge, which is usually not
  running, so a reachable DAQ read as unknown.
- `check_beam` reports every device as blocking while Channel Access is still
  connecting, which reads as a facility-wide fault.
- `can_record` guessed False from a state string it did not recognise.
- `preset` looked in a directory that has never existed.

No live machine: every subprocess is faked, so these run anywhere.
"""

from __future__ import annotations

import json
import re

import pytest

from hutch_tools import daq, happi, lightpath
from hutch_tools.epics import EpicsError

import check_beam, check_daq, check_hutch


def _fake_state(monkeypatch, **over):
    """Make ``daq.state`` return a DaqControl status with ``over`` applied."""
    status = {"transition": "configure", "state": "configured", "config_alias": "BEAM",
              "recording": False, "platform": {}, "bypass_activedet": False,
              "experiment": "mfx101609126", "run_number": 7, "last_run_number": 7}
    status.update(over)
    monkeypatch.setattr(daq, "state", lambda hutch, timeout=None: {
        "value": status, "source": "resolved:fake", "retrieved_at": "now"})


# --------------------------------------------------------------------------
# check_daq
# --------------------------------------------------------------------------
def test_the_daq_query_is_valid_python():
    """It is assembled by string concatenation, so a stray comma is invisible."""
    compile(daq._STATE_CODE, "<check_daq>", "exec")


def test_the_daq_query_calls_only_read_only_daqcontrol():
    """psana-daq-control owns the read-only allowlist; a mutating call here is
    a write dressed as a state read, and nothing else would catch it."""
    called = set(re.findall(r"c\.(\w+)\(", daq._STATE_CODE))
    assert called and called <= set(daq._READ_ONLY_CALLS), called


def test_the_status_fields_match_what_getstatus_returns():
    """getStatus() answers a bare 9-tuple, so a name dropped from this list
    silently shifts every field after it onto the wrong value."""
    assert len(daq._STATUS_FIELDS) == 9
    assert daq._STATUS_FIELDS.index("state") == 1


@pytest.mark.parametrize("state,expected", [
    ("configured", True),
    ("starting", True),
    ("paused", True),
    ("running", True),
    ("connected", False),
    ("allocated", False),
    ("unallocated", False),
    ("reset", False),
])
def test_can_record_follows_the_daq_state(monkeypatch, state, expected):
    _fake_state(monkeypatch, state=state)
    assert check_daq.check_daq("mfx")["value"]["can_record"] is expected


@pytest.mark.parametrize("state", ["", "None", "ERROR: no daq object", "frobnicated"])
def test_an_uninterpretable_state_is_unknown_not_a_no(monkeypatch, state):
    """A string we cannot interpret must not read as "the DAQ is not ready".

    "Not ready" sends an operator to fix the DAQ; "unknown" sends them to find
    out why we cannot see it. Guessing False conflates the two.
    """
    _fake_state(monkeypatch, state=state)
    assert check_daq.check_daq("mfx")["value"]["can_record"] is None


#: A DAQ config in miniature: a live process, a commented-out alternative for
#: the same id, a templated id, a detector, and a referenced file.
CONFIG_FIXTURE = '''\
collect_host = "drp-srcf-cmp014"
data_dir = "/cds/data/drpsrcf"
hutch, station, user = ("mfx", 0, "mfxopr")
ea_cfg = "/sdf/home/m/mfxopr/daq/epics/epicsArch.txt"
procmgr_config = [
 { host: collect_host, id: "teb0", cmd: f"{teb_cmd}"},
 #{ host: "old-host",  id: "teb0", cmd: f"{teb_cmd} --dead-variant"},
 { host: "cmp033", id: "jungfrau_0", cmd: f"{drp_cmd0} -D jungfrau -l 0x77"},
 { host: w, id: f"ami-node_{N}_{instance}", cmd: f"ami-node -p {base_port}"},
]
'''


@pytest.fixture()
def daq_cfg(tmp_path, monkeypatch):
    path = tmp_path / "mfx.py"
    path.write_text(CONFIG_FIXTURE)
    monkeypatch.setattr(daq, "daq_config", lambda hutch: {
        "path": str(path), "lines": CONFIG_FIXTURE.count("\n") + 1,
        "content": CONFIG_FIXTURE, "source": f"resolved:{path}"})
    return path


def test_the_config_summary_skips_commented_out_processes(daq_cfg):
    """These files carry dead alternatives -- MFX has three unused feespecImg_0
    variants and two qadc_0. Counting them overstates what should be running."""
    c = daq.config_summary("mfx")
    assert c["processes"] == 3, c["process_ids"]
    assert c["process_ids"].count("teb0") == 1


def test_the_config_summary_reports_templates_verbatim(daq_cfg):
    """Expanding `ami-node_{N}_{instance}` means reimplementing the config's own
    loops; a wrong expansion is worse than an obviously-templated name."""
    assert "ami-node_{N}_{instance}" in daq.config_summary("mfx")["process_ids"]


def test_the_config_summary_finds_the_scalars_and_the_detector(daq_cfg):
    c = daq.config_summary("mfx")
    assert c["collect_host"] == "drp-srcf-cmp014"
    assert c["data_dir"] == "/cds/data/drpsrcf"
    assert (c["station"], c["user"]) == ("0", "mfxopr")
    # Matched on the -D driver, not the id: ids are hutch-chosen names.
    assert c["detectors"] == ["jungfrau_0"]


def test_the_config_summary_reports_referenced_file_readability(daq_cfg):
    """An unreadable epicsArch.txt breaks a run while the DAQ still reads
    `configured`, so readiness has to see it."""
    reads = daq.config_summary("mfx")["also_reads"]
    entry = reads["/sdf/home/m/mfxopr/daq/epics/epicsArch.txt"]
    assert set(entry) >= {"readable"}


def test_the_config_summary_stays_small(daq_cfg):
    """The point of summarising: the real file is 204 lines / 16 KB, which would
    dominate every readiness report."""
    assert len(json.dumps(daq.config_summary("mfx"))) < 2000


def test_a_missing_scalar_is_null_not_absent(tmp_path, monkeypatch):
    """`null` means the config does not set it; a missing key would mean the
    parser never looked. The caller needs to tell those apart."""
    monkeypatch.setattr(daq, "daq_config", lambda hutch: {
        "path": "x", "lines": 1, "content": "# nothing here\n", "source": "s"})
    c = daq.config_summary("mfx")
    assert c["collect_host"] is None and c["user"] is None
    assert c["processes"] == 0


def test_an_unreadable_config_does_not_change_the_verdict(monkeypatch):
    """A config we cannot parse is worth reporting but is not a readiness
    failure -- it must not be able to turn can_record from True to False."""
    _fake_state(monkeypatch)
    def boom(hutch):
        raise daq.DaqError("cannot read DAQ config")
    monkeypatch.setattr(check_daq.daq, "config_summary", boom)
    v = check_daq.check_daq("mfx")["value"]
    assert v["can_record"] is True
    assert "cannot read DAQ config" in v["config"]["error"]


def test_check_daq_carries_the_config_summary(monkeypatch, daq_cfg):
    _fake_state(monkeypatch)
    v = check_daq.check_daq("mfx")["value"]
    assert v["config"]["collect_host"] == "drp-srcf-cmp014"
    assert "content" not in v["config"], "the summary must not inline the file"


def test_a_different_experiment_is_surfaced(monkeypatch):
    _fake_state(monkeypatch, experiment="mfxOTHER")
    rec = check_daq.check_daq("mfx", experiment="mfx101609126")
    assert rec["value"]["experiment_mismatch"] == {
        "daq": "mfxOTHER", "expected": "mfx101609126"}


def test_the_matching_experiment_raises_no_alarm(monkeypatch):
    _fake_state(monkeypatch)
    rec = check_daq.check_daq("mfx", experiment="mfx101609126")
    assert "experiment_mismatch" not in rec["value"]


# --------------------------------------------------------------------------
# check_beam
# --------------------------------------------------------------------------
def _fake_proc(stdout: str, rc: int = 0):
    class P:
        returncode = rc
        def __init__(self):
            self.stdout = stdout.encode()
            self.stderr = b""
    return lambda *a, **kw: P()


def test_the_beam_snippet_is_percent_formattable():
    """The snippet is %-formatted, so a bare `%` anywhere in it -- including in
    a comment -- raises TypeError at call time rather than at import. Writing
    "50%" in a comment broke check-beam exactly this way."""
    rendered = lightpath._PATH_SNIPPET % {
        "db": "/tmp/db.json", "end": "MFX", "settle": 1.0}
    compile(rendered, "<check_beam>", "exec")


def test_the_beam_snippet_waits_once_then_judges():
    """A flat wait, not convergence detection. Watching for a stable
    Disconnected count reads as settled during the first seconds, when nothing
    has connected and the count is stable at its worst value. Source check,
    because the snippet runs in another interpreter."""
    src = lightpath._PATH_SNIPPET
    assert src.count("time.sleep(") == 1, "one flat wait, not a poll loop"
    assert "while " not in src, "the settle loop should be gone"
    assert 'settled = states["Disconnected"] <= len(bp.path) // 2' in src


def test_the_default_settle_is_longer_than_the_measured_convergence():
    """Measured on mfx-daq: ~55/59 disconnected to 2/59 in under 6 s, twice.
    The default has to clear that with margin, or every call reports unsettled."""
    import inspect
    default = inspect.signature(lightpath.beam_path).parameters["settle_s"].default
    assert default >= 8.0, f"{default}s is under the measured convergence"


def test_an_unsettled_read_is_reported_as_unsettled(monkeypatch):
    """Channel Access connects asynchronously and lightpath counts Disconnected
    as blocking, so reading too early reports ~every device as blocking. That
    must stay visibly undetermined rather than becoming a beam fault."""
    payload = {
        "settled": False,
        "states": {"Disconnected": 57, "Inserted": 1, "Removed": 1},
        "devices": [{"name": "d1", "z": 1.0, "state": "Disconnected", "blocking": True}],
        "blocking": ["d1"], "impediment": "d1", "unreadable": ["d1"],
        "minimum_transmission": 0.1, "method": "m",
    }
    monkeypatch.setattr(lightpath.subprocess, "run",
                        _fake_proc("HUTCHLIB_JSON:" + json.dumps(payload)))
    rec = lightpath.beam_path("mfx", settle_s=0)
    assert rec["settled"] is False
    assert rec["value"]["settled"] is False


def test_the_payload_survives_logging_on_stdout(monkeypatch):
    """lightpath logs to stdout, so the JSON is not necessarily the last line."""
    payload = {"settled": True, "states": {}, "devices": [], "blocking": [],
               "impediment": None, "unreadable": [], "minimum_transmission": 0.1,
               "method": "m"}
    noisy = ("WARNING device missing branch information\n"
             "HUTCHLIB_JSON:" + json.dumps(payload) + "\n"
             "ERROR Unexpected channel ID\n")
    monkeypatch.setattr(lightpath.subprocess, "run", _fake_proc(noisy))
    assert lightpath.beam_path("mfx")["value"]["settled"] is True


def test_a_failed_path_raises_rather_than_falling_back(monkeypatch):
    """The obvious fallback (lc.devices) is every lightpath device in the
    facility, not this beamline's path. Reporting another beamline's devices as
    this one's is worse than failing."""
    monkeypatch.setattr(lightpath.subprocess, "run", _fake_proc("boom\n", rc=1))
    with pytest.raises(lightpath.LightpathError, match="could not compute the path"):
        lightpath.beam_path("mfx")


# --------------------------------------------------------------------------
# check_hutch
# --------------------------------------------------------------------------
HAPPI_FIXTURE = {
    "a": {"name": "m_motor", "prefix": "X:MMS:01", "beamline": "MFX",
          "device_class": "pcdsdevices.device_types.IMS", "z": -1.0},
    "b": {"name": "m_composite", "prefix": "X:LJH", "beamline": "MFX",
          "device_class": "pcdsdevices.liquid_jet.LiquidJet", "z": -1.0},
    "c": {"name": "m_onpath", "prefix": "X:VGC:01", "beamline": "MFX",
          "device_class": "pcdsdevices.device_types.GateValve", "z": 10.0,
          "lightpath": True},
    "d": {"name": "other_hutch", "prefix": "Y:1", "beamline": "XPP", "z": 1.0},
}


@pytest.fixture()
def happi_db(tmp_path, monkeypatch):
    path = tmp_path / "db.json"
    path.write_text(json.dumps(HAPPI_FIXTURE))
    monkeypatch.setenv("HUTCH_HAPPI_DB", str(path))
    return path


def test_each_device_carries_exactly_one_disposition(happi_db, monkeypatch):
    monkeypatch.setattr(check_hutch, "caget", lambda pvs, timeout=None: {pvs[0]: "1.25"})
    rows = {r["name"]: r for r in check_hutch.check_hutch("mfx")["value"]}

    assert set(rows) == {"m_motor", "m_composite", "m_onpath"}  # not the XPP device
    # A lightpath device defers: check_beam is authoritative for it, and a
    # second opinion here is how an operator ends up trusting the wrong one.
    assert rows["m_onpath"]["on_lightpath"] is True
    assert "value" not in rows["m_onpath"]
    # A motor record reads back, and says which PV it came from.
    assert rows["m_motor"]["value"] == "1.25"
    assert rows["m_motor"]["read_from"] == "X:MMS:01.RBV"
    # A composite device's components are named by its class, not its prefix.
    # Guessing a suffix would invent a PV, so it stops.
    assert rows["m_composite"]["needs_session"] is True


def test_an_unreadable_record_is_not_a_missing_one(happi_db, monkeypatch):
    """`unreadable` means the PV did not answer; `needs_session` means we never
    had a name to try. Collapsing them loses which one the operator must fix."""
    def boom(pvs, timeout=None):
        raise EpicsError("no value returned")
    monkeypatch.setattr(check_hutch, "caget", boom)
    rows = {r["name"]: r for r in check_hutch.check_hutch("mfx")["value"]}
    assert "no value returned" in rows["m_motor"]["unreadable"]
    assert "unreadable" not in rows["m_composite"]
    assert rows["m_composite"]["needs_session"] is True


def test_one_bad_pv_does_not_blank_the_others(happi_db, monkeypatch):
    """caget prints nothing on stdout when ANY name in a batch is missing, so
    reading the hutch in one call let a single absent record hide every value."""
    HAPPI_FIXTURE["e"] = {"name": "m_motor2", "prefix": "X:MMS:02", "beamline": "MFX",
                          "device_class": "pcdsdevices.device_types.IMS", "z": -1.0}
    happi_db.write_text(json.dumps(HAPPI_FIXTURE))
    try:
        def selective(pvs, timeout=None):
            if pvs[0] == "X:MMS:01.RBV":
                raise EpicsError("not found")
            return {pvs[0]: "7.0"}
        monkeypatch.setattr(check_hutch, "caget", selective)
        rows = {r["name"]: r for r in check_hutch.check_hutch("mfx")["value"]}
        assert rows["m_motor2"]["value"] == "7.0"
        assert "unreadable" in rows["m_motor"]
    finally:
        del HAPPI_FIXTURE["e"]


def test_inventory_only_does_not_touch_channel_access(happi_db, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("--inventory-only must not read PVs")
    monkeypatch.setattr(check_hutch, "caget", forbidden)
    assert check_hutch.check_hutch("mfx", inventory_only=True)["count"] == 3


def test_an_unknown_beamline_names_the_likely_mistake(happi_db):
    with pytest.raises(happi.HappiError, match="UPPERCASE"):
        check_hutch.check_hutch("nosuch", inventory_only=True)


# --------------------------------------------------------------------------
# preset
# --------------------------------------------------------------------------
def test_a_preset_is_found_under_beamline(tmp_path, monkeypatch):
    """The old path was presets/<device>.yml, which has never existed."""
    root = tmp_path / "mfx" / "presets" / "beamline"
    root.mkdir(parents=True)
    (root / "m1.yml").write_text("value: 1.0\n")
    monkeypatch.setenv("HUTCH_PYTHON_APPS", str(tmp_path))
    assert "value: 1.0" in happi.preset("mfx", "m1")["value"]


def test_an_experiment_preset_is_found_too(tmp_path, monkeypatch):
    (tmp_path / "mfx" / "presets" / "beamline").mkdir(parents=True)
    exp = tmp_path / "mfx" / "presets" / "101609126"
    exp.mkdir(parents=True)
    (exp / "m2.yml").write_text("value: 2.0\n")
    monkeypatch.setenv("HUTCH_PYTHON_APPS", str(tmp_path))
    assert "value: 2.0" in happi.preset("mfx", "m2", "101609126")["value"]


def test_a_missing_preset_names_where_it_looked(tmp_path, monkeypatch):
    """Most devices have no preset. The error has to steer the caller to the
    operator rather than toward a hardcoded setpoint."""
    monkeypatch.setenv("HUTCH_PYTHON_APPS", str(tmp_path))
    with pytest.raises(happi.HappiError, match="ask the operator"):
        happi.preset("mfx", "mr1l4_homs")
