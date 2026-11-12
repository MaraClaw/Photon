import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Final, Iterator

import pytest

HELPER: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/startup-probe.py"
LINUX: Final = sys.platform.startswith("linux")
ESCAPE: Final = """\
import os, signal, sys
read_fd, write_fd = os.pipe()
if os.fork() == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(sys.argv[1], "w") as pid_file:
        pid_file.write(str(os.getpid()))
    os.write(write_fd, b"x")
    while True:
        signal.pause()
os.read(read_fd, 1)
if sys.argv[2] == "exit":
    sys.exit(0)
while True:
    signal.pause()
"""


def run_helper(tmp_path: Path, timeout: str, grace: str, *arguments: str) -> tuple[int, float]:
    control = tmp_path / "control"
    control.touch()
    env = os.environ | {"OPENCLAW_PROBE_TIMEOUT_SECONDS": timeout, "OPENCLAW_PROBE_GRACE_SECONDS": grace}
    started = time.monotonic()
    result = subprocess.run(
        ["/bin/sh", "-c", 'exec "$@" 3<"$CONTROL"', "sh", sys.executable, "-B", str(HELPER), *arguments],
        env=env | {"CONTROL": str(control)}, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
    )
    return result.returncode, time.monotonic() - started


def run_probe(tmp_path: Path, timeout: str, grace: str, *command: str) -> tuple[int, float]:
    return run_helper(tmp_path, timeout, grace, "--run", *command)


def run_verify(tmp_path: Path, timeout: str, grace: str, inventory_step: str, *command: str) -> tuple[int, float]:
    """Supervise a stand-in inventory helper whose verify() body is INVENTORY_STEP, then COMMAND."""
    helper = tmp_path / "installed-assets.py"
    helper.write_text(
        "import json, os, signal, sys, time\nfrom pathlib import Path\n\n\n"
        "def verify(component, inventory_dir, skills_root):\n" + textwrap.indent(inventory_step, "    ")
    )
    return run_helper(tmp_path, timeout, grace, "--verify", str(helper), "tavily", str(tmp_path / "inventory"), str(tmp_path / "skills"), *command)


def touch(marker: Path) -> tuple[str, ...]:
    return ("/bin/sh", "-c", 'touch "$1"', "sh", str(marker))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@contextlib.contextmanager
def escaped_descendant(tmp_path: Path) -> Iterator[Path]:
    pid_file = tmp_path / "escaped.pid"
    try:
        yield pid_file
    finally:
        if pid_file.is_file() and alive(pid := int(pid_file.read_text())):
            os.kill(pid, signal.SIGKILL)


def test_timed_out_probe_stops_waiting_once_its_group_exits(tmp_path: Path) -> None:
    # Given a probe that hangs past the deadline but exits promptly on TERM, and a long grace.
    command = (sys.executable, "-c", "import signal; signal.pause()")

    # When the deadline expires.
    status, elapsed = run_probe(tmp_path, "0.3", "30", *command)

    # Then the timeout is reported without sitting out the 30 second grace.
    assert status == 124
    assert elapsed < 15


@pytest.mark.parametrize("probe", ["hang", "exit"])
def test_descendant_that_escaped_the_group_cannot_hold_the_probe(tmp_path: Path, probe: str) -> None:
    # Given a probe whose descendant moved to its own session and ignores TERM.
    with escaped_descendant(tmp_path) as pid_file:
        # When the probe times out, or finishes while the descendant lives on.
        status, elapsed = run_probe(tmp_path, "0.5", "0.2", sys.executable, "-c", ESCAPE, str(pid_file), probe)

        # Then the probe's own result is reported within its bounds.
        assert status == (124 if probe == "hang" else 0)
        assert elapsed < 15
        # And on Linux, where the helper is the subreaper, the descendant is terminated and reaped.
        if LINUX:
            assert not alive(int(pid_file.read_text()))


def test_inventory_check_runs_in_a_detached_child_of_the_one_interpreter(tmp_path: Path) -> None:
    # Given an inventory step that reports the process it runs in.
    report = tmp_path / "report.json"
    step = f"""\
try:
    os.fstat(3)
    control_open = True
except OSError:
    control_open = False
Path({str(report)!r}).write_text(json.dumps({{
    "arguments": [component, str(inventory_dir), str(skills_root)],
    "pid": os.getpid(), "session": os.getsid(0), "executable": sys.executable, "control_open": control_open,
    "handlers": [signal.getsignal(signum) == signal.SIG_DFL for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)],
}}))
"""
    marker = tmp_path / "probe-ran"

    # When the supervisor verifies and then probes.
    status, _ = run_verify(tmp_path, "10", "1", step, *touch(marker))

    # Then the step received the validator's arguments in its own session, without the
    # supervisor's handlers or control descriptor, and the probe followed it.
    assert status == 0
    seen = json.loads(report.read_text())
    assert seen["arguments"] == ["tavily", str(tmp_path / "inventory"), str(tmp_path / "skills")]
    assert seen["session"] == seen["pid"]
    assert seen["executable"] == sys.executable
    assert seen["handlers"] == [True, True, True]
    assert seen["control_open"] is False
    assert marker.exists()
    # And it was reaped, and nothing was cached beside the helper.
    assert not alive(seen["pid"])
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))


@pytest.mark.parametrize(
    ("step", "probe", "status", "probe_ran"),
    [
        pytest.param("raise ValueError('installed assets changed')\n", None, 1, False, id="inventory-fails"),
        pytest.param("os._exit(7)\n", None, 1, False, id="inventory-exits-nonzero"),
        pytest.param("pass\n", ("/bin/sh", "-c", "exit 9"), 3, False, id="probe-fails"),
        pytest.param("pass\n", ("/nonexistent/openclaw-probe",), 3, False, id="probe-missing"),
        pytest.param("pass\n", None, 0, True, id="both-pass"),
    ],
)
def test_verify_status_names_the_step_that_failed(
    tmp_path: Path, step: str, probe: tuple[str, ...] | None, status: int, probe_ran: bool,
) -> None:
    # Given an inventory step and the probe that would follow it.
    marker = tmp_path / "probe-ran"

    # When both are supervised by one helper.
    result, _ = run_verify(tmp_path, "10", "1", step, *(probe or touch(marker)))

    # Then 1 means the inventory check failed and nothing else started, 3 that the probe failed.
    assert result == status
    assert marker.exists() == probe_ran


def test_inventory_check_without_a_probe_reports_its_own_result(tmp_path: Path) -> None:
    assert run_verify(tmp_path, "10", "1", "pass\n")[0] == 0
    assert run_verify(tmp_path, "10", "1", "raise KeyError('roots')\n")[0] == 1


def test_each_step_gets_its_own_deadline(tmp_path: Path) -> None:
    # Given two steps that each take more than half of the timeout.
    marker = tmp_path / "probe-finished"

    # When they run back to back.
    status, elapsed = run_verify(tmp_path, "3", "1", "time.sleep(1.8)\n", "/bin/sh", "-c", 'sleep 1.8 && touch "$1"', "sh", str(marker))

    # Then neither is cut off by time the other one spent.
    assert status == 0
    assert marker.exists()
    assert elapsed > 3


@pytest.mark.parametrize("ignore_term", [False, True])
def test_hung_inventory_check_is_killed_and_reaped(tmp_path: Path, ignore_term: bool) -> None:
    # Given an inventory step that never returns, with or without honouring TERM.
    pid_file = tmp_path / "inventory.pid"
    marker = tmp_path / "probe-ran"
    step = f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
    if ignore_term:
        step += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    step += "while True:\n    signal.pause()\n"

    # When its deadline expires; a child that honours TERM must not sit out a long grace.
    status, elapsed = run_verify(tmp_path, "1", "0.2" if ignore_term else "30", step, *touch(marker))

    # Then the timeout is reported, the probe never starts and the child is gone.
    assert status == 124
    assert elapsed < 15
    assert not marker.exists()
    assert not alive(int(pid_file.read_text()))


def test_probe_after_the_inventory_check_times_out_with_the_usual_status(tmp_path: Path) -> None:
    status, elapsed = run_verify(tmp_path, "0.5", "0.2", "pass\n", sys.executable, "-c", "import signal; signal.pause()")
    assert status == 124
    assert elapsed < 15


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_cancellation_recorded_during_the_inventory_check_skips_the_probe(tmp_path: Path, signum: int) -> None:
    # Given an inventory step that passes, during which the validator records a signal.
    marker = tmp_path / "probe-ran"
    step = f"Path(os.environ['CONTROL']).write_text('{int(signum)}\\n')\n"

    # When the supervisor moves on from that step.
    status, _ = run_verify(tmp_path, "10", "1", step, *touch(marker))

    # Then it reports the signal instead of starting the probe.
    assert status == 128 + signum
    assert not marker.exists()


@pytest.mark.parametrize("setting", ["OPENCLAW_PROBE_TIMEOUT_SECONDS", "OPENCLAW_PROBE_GRACE_SECONDS"])
def test_invalid_policy_stops_before_the_inventory_check(tmp_path: Path, setting: str) -> None:
    marker = tmp_path / "inventory-ran"
    arguments = ("0", "1") if setting.endswith("TIMEOUT_SECONDS") else ("1", "0")
    status, _ = run_verify(tmp_path, *arguments, f"Path({str(marker)!r}).touch()\n")
    assert status == 2
    assert not marker.exists()
