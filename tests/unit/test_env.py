"""bin/hutch-env and bin/hutch-session-start: the shell side of loading at a hutch.

Shell, so pytest shells out. What is pinned here is what breaks silently: a PATH
that gets replaced rather than prepended, a hutch derived from a hostname that is
not a hutch machine at all, and a SessionStart hook that fires off-hutch.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

from hutch_tools.common import script

ENV = script("bin", "hutch-env")


def _sh(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source {ENV} >/dev/null 2>&1; {snippet}"],
        capture_output=True, text=True, env=env,
    )


def test_tools_land_on_path():
    assert _sh("command -v hutch-state").stdout.strip().endswith("/bin/hutch-state")


def test_path_is_prepended_not_replaced():
    """An `export PATH=...` that drops the inherited PATH takes ssh, git and
    python with it -- and the failure shows up far from here."""
    out = _sh("echo $PATH").stdout.strip()
    assert "/usr/bin" in out, f"inherited PATH was lost: {out}"


def test_sourcing_twice_does_not_duplicate_the_entry():
    """The hook may fire more than once per shell; PATH should not grow."""
    repeated = subprocess.run(
        ["bash", "-c", f"source {ENV} >/dev/null 2>&1; source {ENV} >/dev/null 2>&1; echo $PATH"],
        capture_output=True, text=True,
    ).stdout
    assert repeated.count(str(ENV.parent)) == 1


def test_hutch_is_derived_from_the_hostname():
    for host, expected in [
        ("mfx-daq", "mfx"),
        ("mfx-control", "mfx"),
        ("rix-control", "rix"),
        ("tmo-console", "tmo"),
    ]:
        got = _sh(f"hutch_from_hostname {host}").stdout.strip()
        assert got == expected, f"{host} -> {got!r}, expected {expected!r}"


def test_a_non_hutch_host_is_refused_not_guessed():
    """On S3DF the tools cannot work. Returning a plausible-looking hutch would
    be worse than failing: it would send resolvers at paths that are not there."""
    for host in ["sdflogin001", "sdfiana027", "psana", "laptop"]:
        res = _sh(f"hutch_from_hostname {host}")
        assert res.returncode != 0, f"{host} was accepted as a hutch: {res.stdout!r}"
        assert not res.stdout.strip()


# --------------------------------------------------------------------------
# bin/hutch-session-start -- the SessionStart hook
# --------------------------------------------------------------------------
HOOK = script("bin", "hutch-session-start")


def _hook_on(hostname: str) -> subprocess.CompletedProcess:
    """Run the hook with `hostname` stubbed on PATH to report a given host."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        stub = pathlib.Path(tmp) / "hostname"
        stub.write_text(f"#!/bin/bash\necho {hostname}\n")
        stub.chmod(0o755)
        return subprocess.run(
            [str(HOOK)], capture_output=True, text=True,
            env={"PATH": f"{tmp}:/usr/bin:/bin"},
        )


def test_the_hook_is_silent_off_hutch():
    """The reason this is a hook and not a CLAUDE.md line. On S3DF the tools
    cannot reach /cds, CA or the DAQ logs, so hutch context there is misleading.
    A hook that always fires is worse than no hook."""
    for host in ["sdflogin001", "sdfiana027", "psdev", "laptop"]:
        res = _hook_on(host)
        assert res.stdout == "", f"{host} got output: {res.stdout!r}"
        assert res.returncode == 0, f"{host} exited {res.returncode}"


def test_the_hook_names_the_hutch_on_a_hutch_host():
    for host, hutch in [("mfx-daq", "mfx"), ("rix-daq", "rix"), ("tmo-control", "tmo")]:
        out = _hook_on(host).stdout
        assert f"hutch={hutch}" in out, f"{host}: {out!r}"
        assert "hutch-copilot" in out
        # The host names the hutch but never the experiment, so the hook has to
        # ask -- otherwise a session starts with no $E and the sub-skills that
        # require it ask at some arbitrary later moment. It must land in
        # systemMessage: additionalContext is model-facing, and an ask the
        # operator cannot see is one nobody answers.
        msg = json.loads(out)["systemMessage"]
        assert "Which experiment" in msg, f"{host}: {msg!r}"


def test_the_hook_flags_control_machines_as_the_wrong_host():
    """-control has no Slurm controller, so DAQ state is invisible there. Saying
    so beats letting a session discover it mid-shift."""
    assert "mfx-daq" in _hook_on("mfx-control").stdout


def test_the_hook_never_fails_the_session():
    """Any non-zero exit or hang degrades every session start, including on the
    hosts this has no business firing on."""
    for host in ["mfx-daq", "sdflogin001", ""]:
        assert _hook_on(host).returncode == 0


# --------------------------------------------------------------------------
# External paths -- bash declares, Python reads the same declaration
# --------------------------------------------------------------------------
def test_no_external_path_literal_survives_in_the_package():
    """The drift test, and the reason this contract exists.

    The EPICS bin dir was hand-written in both daq.py and hutch-env, with
    nothing tying them together: an upgrade updates one and leaves the other,
    and which copy wins depends on the code path that host takes. With one
    declaration and Python reading it, drift is not possible.
    """
    from hutch_tools import common as paths

    offenders = {}
    pattern = re.compile(r'"(/cds/[^"]+|/sdf/(?:group|data)/[^"]+)"')
    for py in sorted((paths.skill_root() / "hutch_tools").glob("*.py")):
        hits = set(pattern.findall(py.read_text(errors="replace")))
        if hits:
            offenders[py.name] = sorted(hits)
    assert not offenders, (
        f"external path literals in the package: {offenders}. Declare them in "
        f"bin/hutch-env and read them with common.external_path()."
    )


@pytest.mark.parametrize("name", [
    "EPICS_BIN", "HAPPI_DB", "PYTHON_APPS", "PCDS_PYTHON", "PSCONDA",
    "LUTE_PY39", "LUTE_PY311", "DAQ_CONFIG_DIR", "OPR_LOG_ROOT",
    "DS_ROOT", "SKILL_ROOTS", "ARCHIVER_URL",
])
def test_bash_and_python_agree_on_every_external_path(name):
    """Sourcing hutch-env and calling external_path() must give one answer."""
    from hutch_tools import common as paths

    from_bash = _sh(f'echo "$HUTCH_{name}"').stdout.strip()
    assert from_bash, f"hutch-env did not export HUTCH_{name}"
    assert from_bash == paths.external_path(name)


def test_a_preset_external_path_is_respected():
    """`${VAR:-default}` and the `if [ -z ]` guard, not a bare assignment: an
    operator or a test must be able to point a path somewhere else."""
    for name in ("EPICS_BIN", "DAQ_CONFIG_DIR"):
        out = subprocess.run(
            ["bash", "-c", f"source {ENV} >/dev/null 2>&1; echo \"$HUTCH_{name}\""],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", f"HUTCH_{name}": "/tmp/preset"},
        ).stdout.strip()
        assert out == "/tmp/preset", f"HUTCH_{name} was clobbered: {out!r}"


def test_the_external_paths_survive_the_unset():
    """They are exported for Python to read; unsetting them defeats the point."""
    unset = [ln for ln in ENV.read_text().splitlines() if ln.startswith("unset ")]
    assert unset, "the unset line vanished"
    assert not any("HUTCH_" in ln for ln in unset), unset


def test_pythonpath_is_not_exported():
    """bin/hutch-* set sys.path themselves. A second mechanism is a second bug."""
    assert not _sh("echo ${PYTHONPATH-unset}").stdout.strip().replace("unset", "")


def test_epics_ca_addr_list_is_left_alone():
    """Some hosts set it (mfx-control does), the DAQ machines do not and CA
    works there by broadcast. Either way hutch-env must not touch it: pinning a
    value would break a host that was already working."""
    before = "10.0.0.1 10.0.0.2"
    after = subprocess.run(
        ["bash", "-c", f"source {ENV} >/dev/null 2>&1; echo $EPICS_CA_ADDR_LIST"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "EPICS_CA_ADDR_LIST": before},
    ).stdout.strip()
    assert after == before

    unset = subprocess.run(
        ["bash", "-c", f"source {ENV} >/dev/null 2>&1; echo ${{EPICS_CA_ADDR_LIST-unset}}"],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
    ).stdout.strip()
    assert unset == "unset"
