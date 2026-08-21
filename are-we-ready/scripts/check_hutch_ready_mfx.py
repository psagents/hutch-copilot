"""
check_hutch_ready_mfx.py — MFX hutch readiness checker
=======================================================
Canonical source (S3DF):
  hutch-copilot/are-we-ready/scripts/check_hutch_ready_mfx.py

Complements check_beam_ready_mfx.py (which covers accelerator/machine checks).
This script checks the instrument side: imagers out of beam, valves open,
stoppers cleared, DAQ configured, and the XTC2 data path accessible.

Runs inside a hutch-python session on mfx-daq where all device objects
(yag0, mfx_dg1_valve_1, daq, …) are already in the namespace.
It is NOT meant to be imported from a plain Python environment.

--- Sending inline from S3DF via the IPython bridge (recommended) ---

    SCRIPT=~/.claude/skills/hutch-copilot/are-we-ready/scripts/check_hutch_ready_mfx.py
    python3 -c "
    import json, pathlib
    code = pathlib.Path('$SCRIPT').read_text() + '\ncheck_hutch_ready()'
    print(json.dumps({'code': code}))
    " | ssh -o ConnectTimeout=60 -J psdev mfx-daq "python3 -c \"
    import socket, json, sys
    s = socket.socket()
    s.connect(('localhost', 9999))
    s.sendall(sys.stdin.buffer.read())
    s.shutdown(socket.SHUT_WR)
    data = b''
    while True:
        chunk = s.recv(65536)
        if not chunk: break
        data += chunk
    resp = json.loads(data.decode())
    print(resp.get('output', resp.get('error', '')))
    \""

--- Install on mfx-daq for direct console use (run once from S3DF) ---

    ssh -o ConnectTimeout=10 -J psdev mfx-daq "cat > /tmp/check_hutch_ready_mfx.py" \\
        < ~/.claude/skills/hutch-copilot/are-we-ready/scripts/check_hutch_ready_mfx.py

    # Then from the hutch-python console on mfx-daq:
    exec(open('/tmp/check_hutch_ready_mfx.py').read())
    check_hutch_ready()
"""

import pathlib
import epics

# ── Constants ─────────────────────────────────────────────────────────────────
# Expected stopper state when beam path is clear
STOPPER_OUT_STATE = "OUT"

# XTC2 data root — experiment path is filled in at runtime
XTC2_ROOT = pathlib.Path("/sdf/data/lcls/ds")

# ANSI colour codes
_PASS = "\033[32m✓ PASS\033[0m"
_FAIL = "\033[31m✗ FAIL\033[0m"
_WARN = "\033[33m⚠ WARN\033[0m"
_INFO = "\033[36mℹ INFO\033[0m"


def _row(label, status, detail):
    print(f"  {status:<20s}  {label:<30s}  {detail}")


def check_hutch_ready(hutch="mfx", experiment=None):
    """
    Print a hutch readiness report for MFX and return True if all critical
    checks pass.

    Checks:
      1. Imagers / YAGs     — removed from beam path
      2. Valves             — state of all in-vacuum gate valves (informational)
      3. Stoppers           — MFX:PPS:MMS:ST1 and ST2 must be OUT
      4. DAQ                — connected, configured, detector name reported
      5. XTC2 path          — data directory accessible

    Parameters
    ----------
    hutch : str
        Hutch name (lowercase), e.g. "mfx".
    experiment : str or None
        Experiment ID, e.g. "mfxltest01". If None, XTC2 path check is skipped.
    """
    print()
    print("=" * 72)
    print("  MFX Hutch Readiness Check")
    print("=" * 72)
    all_pass = True

    # ── 1. Imagers / YAGs ─────────────────────────────────────────────────────
    print("\n[1] Imagers / YAGs")
    imagers = [
        ("yag0", yag0),
        ("yag1", yag1),
        ("yag2", yag2),
        ("dg1_pim", mfx_dg1_pim),
        ("dg2_pim", mfx_dg2_pim),
        ("dia_pim", mfx_dia_pim),
    ]
    for label, dev in imagers:
        try:
            if dev.removed:
                _row(label, _PASS, "removed from beam")
            else:
                _row(label, _WARN, "IN beam path — intended?")
        except Exception as exc:
            _row(label, _WARN, f"could not check: {exc}")

    # ── 2. Valves ──────────────────────────────────────────────────────────────
    print("\n[2] Valves  (OUT = open/clear, IN = blocking)")
    valves = [
        ("dg1_valve_1", mfx_dg1_valve_1),
        ("dg1_valve_2", mfx_dg1_valve_2),
        ("dia_valve_01", mfx_dia_valve_01),
        ("dia_valve_02", mfx_dia_valve_02),
        ("dvd_valve", mfx_dvd_valve),
        ("mxt_valve", mfx_mxt_valve),
    ]
    for label, dev in valves:
        try:
            state = dev.state.get()
            _row(label, _INFO, f"state = {state}")
        except Exception as exc:
            _row(label, _WARN, f"could not check: {exc}")

    # ── 3. Stoppers ────────────────────────────────────────────────────────────
    print("\n[3] Stoppers  (must be OUT for beam delivery)")
    for n in (1, 2):
        pv = f"MFX:PPS:MMS:ST{n}:STATE"
        try:
            state = epics.caget(pv)
            if state is None:
                _row(f"stopper ST{n}", _WARN, f"PV not readable ({pv})")
            elif str(state).upper() == STOPPER_OUT_STATE:
                _row(f"stopper ST{n}", _PASS, f"OUT  ({pv})")
            else:
                _row(f"stopper ST{n}", _FAIL, f"state={state}  ({pv})  — BLOCKING")
                all_pass = False
        except Exception as exc:
            _row(f"stopper ST{n}", _FAIL, f"ERROR: {exc}")
            all_pass = False

    # ── 4. DAQ ─────────────────────────────────────────────────────────────────
    print("\n[4] DAQ")
    detector_name = None
    try:
        status = daq.status()
        _row("daq.status()", _INFO, str(status))
        if "Disconnected" in str(status):
            _row(
                "DAQ connected",
                _FAIL,
                "DAQ is disconnected — connect before collecting data",
            )
            all_pass = False
    except Exception as exc:
        _row("daq.status()", _WARN, f"ERROR: {exc}")

    try:
        config_info = daq.config_info()
        _row("daq.config_info()", _INFO, str(config_info))
        # Try to extract a detector name from the config info string
        config_str = str(config_info)
        if config_str and config_str.strip():
            detector_name = config_str.split(".")[0].strip()
            _row("detector", _PASS, f"{detector_name}")
        else:
            _row("detector", _WARN, "no detector configured in DAQ session")
    except Exception as exc:
        _row("daq.config_info()", _WARN, f"ERROR: {exc}")

    # ── 5. XTC2 path ───────────────────────────────────────────────────────────
    print("\n[5] XTC2 Data Path")
    if experiment:
        xtc2_path = XTC2_ROOT / hutch / experiment / "xtc2"
        try:
            if xtc2_path.exists():
                files = list(xtc2_path.glob("*.xtc2"))
                _row(
                    "XTC2 path",
                    _PASS,
                    f"{xtc2_path}  ({len(files)} file(s) already present)",
                )
            else:
                # Directory may not exist yet — that is normal before the first run
                _row(
                    "XTC2 path",
                    _INFO,
                    f"{xtc2_path}  (not yet created — normal before first run)",
                )
        except Exception as exc:
            _row("XTC2 path", _WARN, f"could not check {xtc2_path}: {exc}")
    else:
        _row("XTC2 path", _INFO, "experiment ID not provided — skipped")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    if all_pass:
        print("  \033[32m✓  MFX HUTCH IS READY FOR DATA COLLECTION\033[0m")
        if detector_name:
            print(f"     Detector: {detector_name}")
    else:
        print("  \033[31m✗  MFX HUTCH IS NOT READY — address FAIL items above\033[0m")
    print("=" * 72)
    print()
    return all_pass
