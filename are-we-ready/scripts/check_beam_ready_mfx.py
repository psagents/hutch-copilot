"""
check_beam_ready_mfx.py — MFX beam readiness checker
=====================================================
Canonical source: are-we-ready/scripts/check_beam_ready_mfx.py
                  (within the hutch-copilot skill tree)

This script runs inside a hutch-python session on mfx-daq where all device
objects (mr1l4_homs, yag0, beam_status, …) are already in the namespace.
It is NOT meant to be imported from a plain Python environment.

Recommended usage — send inline from S3DF via the hutch-python IPython bridge:

    SCRIPT=$(find /sdf/home -name 'check_beam_ready_mfx.py' \\
        -path '*/hutch-copilot/*' 2>/dev/null | head -1)
    python3 -c "
    import json, pathlib
    code = pathlib.Path('$SCRIPT').read_text() + '\\ncheck_beam_ready()'
    print(json.dumps({'code': code}))
    " | nc -w 30 localhost 9999

Optional persistent install on mfx-daq (write once, reuse from hutch console):

    SCRIPT=$(find /sdf/home -name 'check_beam_ready_mfx.py' \\
        -path '*/hutch-copilot/*' 2>/dev/null | head -1)
    python3 -c "
    import json, pathlib
    content = pathlib.Path('$SCRIPT').read_text()
    code = 'with open(\"/tmp/check_beam_ready_mfx.py\",\"w\") as _f: _f.write(' + repr(content) + ')'
    print(json.dumps({'code': code}))
    " | nc -w 10 localhost 9999
    # Then from the hutch console:
    # exec(open('/tmp/check_beam_ready_mfx.py').read()); check_beam_ready()
"""

import epics

# ── Constants ─────────────────────────────────────────────────────────────────
MFX_PITCH = -562.035  # mr1l4_homs pitch (µrad) when beam → MFX
MEC_PITCH = 819.2  # mr1l4_homs pitch (µrad) when beam → MEC
PITCH_TOL = 10.0  # µrad tolerance for destination check
MIN_BEAM_MJ = 0.05  # mJ threshold — below this = "no beam"

# ANSI colour codes
_PASS = "\033[32m✓ PASS\033[0m"
_FAIL = "\033[31m✗ FAIL\033[0m"
_WARN = "\033[33m⚠ WARN\033[0m"
_INFO = "\033[36mℹ INFO\033[0m"


def _row(label, status, detail):
    print(f"  {status:<20s}  {label:<30s}  {detail}")


def check_beam_ready():
    """
    Print a beam readiness report for MFX and return True if all critical
    checks pass.

    Checks:
      1. Beam destination   — mr1l4_homs pitch (MFX vs MEC)
      2. Imagers / YAGs     — removed from beam path
      3. Valves             — state of all in-vacuum gate valves (informational)
      4. Energy             — DCCM readback + beam_status pulse energy
      5. Undulator pointing — X/Y from BPMS:UNDH:4690
      6. Slits              — sl1l0, dg1, dg2 upstream
      7. DAQ                — current run number
    """
    print()
    print("=" * 72)
    print("  MFX Beam Readiness Check")
    print("=" * 72)
    all_pass = True

    # ── 1. Beam destination ───────────────────────────────────────────────────
    print("\n[1] Beam Destination")
    try:
        pitch = mr1l4_homs.pitch.wm()
        dist_mfx = abs(pitch - MFX_PITCH)
        dist_mec = abs(pitch - MEC_PITCH)
        if dist_mfx < PITCH_TOL:
            _row("mr1l4 pitch", _PASS, f"{pitch:.3f} µrad  → MFX")
        elif dist_mec < PITCH_TOL:
            _row(
                "mr1l4 pitch",
                _FAIL,
                f"{pitch:.3f} µrad  → beam is at MEC (need {MFX_PITCH} for MFX)",
            )
            all_pass = False
        else:
            _row("mr1l4 pitch", _WARN, f"{pitch:.3f} µrad  (unrecognised destination)")
    except Exception as exc:
        _row("mr1l4 pitch", _FAIL, f"ERROR: {exc}")
        all_pass = False

    # ── 2. Imagers / YAGs ─────────────────────────────────────────────────────
    print("\n[2] Imagers / YAGs")
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

    # ── 3. Valves ──────────────────────────────────────────────────────────────
    print("\n[3] Valves  (OUT = open/clear, IN = blocking)")
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

    # ── 4. Energy ──────────────────────────────────────────────────────────────
    print("\n[4] Energy")
    try:
        dccm_ev = round(dccm.energy.wm() * 1000, 1)
        _row("DCCM energy", _INFO, f"{dccm_ev} eV")
    except Exception as exc:
        _row("DCCM energy", _WARN, f"ERROR: {exc}")
    try:
        bs = beam_status.get()
        _row("beam photon energy", _INFO, f"{bs.ev:.1f} eV  (beam_status)")
        avg_mj = (bs.mj1 + bs.mj2 + bs.mj3 + bs.mj4) / 4
        if avg_mj < MIN_BEAM_MJ:
            _row("beam pulse energy", _FAIL, f"{avg_mj * 1000:.1f} µJ avg — no beam?")
            all_pass = False
        else:
            _row(
                "beam pulse energy",
                _PASS,
                f"{avg_mj * 1000:.1f} µJ avg  "
                f"(mj1={bs.mj1 * 1000:.0f} mj2={bs.mj2 * 1000:.0f} "
                f"mj3={bs.mj3 * 1000:.0f} mj4={bs.mj4 * 1000:.0f} µJ)",
            )
    except Exception as exc:
        _row("beam_status", _WARN, f"ERROR: {exc}")

    # ── 5. Undulator pointing ──────────────────────────────────────────────────
    print("\n[5] Undulator Pointing  (BPMS:UNDH:4690)")
    try:
        x = epics.caget("BPMS:UNDH:4690:XOFF.D")
        y = epics.caget("BPMS:UNDH:4690:YOFF.D")
        if x is None or y is None:
            _row("undulator X/Y", _WARN, "PV not readable")
        else:
            _row("undulator X", _INFO, f"{x:.4f} mm")
            _row("undulator Y", _INFO, f"{y:.4f} mm")
    except Exception as exc:
        _row("undulator pointing", _WARN, f"ERROR: {exc}")

    # ── 6. Slits ───────────────────────────────────────────────────────────────
    print("\n[6] Slits")
    slit_devs = [
        ("sl1l0", sl1l0),
        ("dg1_slits", mfx_dg1_slits),
        ("dg2_upstream_slits", mfx_dg2_upstream_slits),
    ]
    for label, dev in slit_devs:
        try:
            xw = dev.xwidth.wm()
            yw = dev.ywidth.wm()
            _row(label, _INFO, f"x={xw:.3f} mm   y={yw:.3f} mm")
        except Exception as exc:
            _row(label, _WARN, f"could not check: {exc}")

    # ── 7. DAQ ─────────────────────────────────────────────────────────────────
    print("\n[7] DAQ")
    try:
        run_num = get_run()
        _row("current run", _INFO, f"run {run_num}")
    except Exception as exc:
        _row("DAQ run", _WARN, f"ERROR: {exc}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    if all_pass:
        print("  \033[32m✓  MFX IS READY FOR BEAM\033[0m")
    else:
        print("  \033[31m✗  MFX IS NOT READY — address FAIL items above\033[0m")
    print("=" * 72)
    print()
    return all_pass
