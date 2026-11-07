import contextlib
import os
import signal
import subprocess
import sys
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


def run_probe(tmp_path: Path, timeout: str, grace: str, *command: str) -> tuple[int, float]:
    control = tmp_path / "control"
    control.touch()
    env = os.environ | {"OPENCLAW_PROBE_TIMEOUT_SECONDS": timeout, "OPENCLAW_PROBE_GRACE_SECONDS": grace}
    started = time.monotonic()
    result = subprocess.run(
        ["/bin/sh", "-c", 'exec "$@" 3<"$CONTROL"', "sh", sys.executable, "-B", str(HELPER), "--run", *command],
        env=env | {"CONTROL": str(control)}, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
    )
    return result.returncode, time.monotonic() - started


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
