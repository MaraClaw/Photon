"""Fake guest, fake gateway and loopback /readyz server for the bootstrap helper tests."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[1] / "docker/openclaw"
BOOT_ID = "6f1f0c1e-7a52-4c1b-9d36-0f3e5b8a2c41"
TICKS = "9007199254740993"  # 2**53 + 1: a float comparison would equate it with 2**53
EVENT_NAME = "AgentBootstrapCompleted"

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

FAKE_GATEWAY = """#!{python}
import json, os, signal, sys, time

def note(name, **extra):
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({{"event": name, **extra}}) + "\\n")

def leave(signum, frame):
    note("signal", signum=signum)
    sys.exit(int(os.environ.get("FAKE_TERM_EXIT", "0")))

def usr1(signum, frame):
    note("signal", signum=signum)
    if "FAKE_USR1_EXIT" in os.environ:
        sys.exit(int(os.environ["FAKE_USR1_EXIT"]))

def ignored(signum, frame):
    note("signal-ignored", signum=signum)

terminate = ignored if os.environ.get("FAKE_MODE") == "ignore-term" else leave
for number, handler in ((signal.SIGTERM, terminate), (signal.SIGHUP, leave), (signal.SIGINT, leave), (signal.SIGUSR1, usr1)):
    signal.signal(number, handler)
signal.alarm(120)  # a gateway orphaned by a failed test ends itself
note("start", pid=os.getpid(), argv=sys.argv[1:], stale_event=os.path.exists(os.environ["FAKE_EVENT"]))
if os.environ.get("FAKE_MODE") == "exit":
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
while True:
    signal.pause()
"""


def stat_line(ticks: str) -> bytes:
    """A /proc/1/stat line whose comm field imitates fields 3 and 4 to defeat naive splitting."""
    return b"1 (tini ) S 99) S " + b" ".join([*[b"0"] * 18, ticks.encode(), *[b"0"] * 30]) + b"\n"


def valid_event(boot_id: str = BOOT_ID, ticks: str = TICKS) -> dict[str, str | int]:
    return {"version": 1, "event": EVENT_NAME, "boot_id": boot_id, "pid1_start_ticks": ticks}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition was not reached"
        time.sleep(0.02)


class Readyz:
    """Loopback /readyz server. Tests mutate status, hang and the request hook on purpose."""

    def __init__(self) -> None:
        self.status = 503
        self.hang = False
        self.hits: list[str] = []
        self.on_request: Callable[[], None] | None = None
        self._release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.hits.append(self.path)
                if owner.hang:
                    owner._release.wait(30)
                if owner.on_request is not None:
                    owner.on_request()
                self.send_response(owner.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: str | int) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._release.set()
            self._server.shutdown()
            self._server.server_close()


@dataclass(frozen=True, slots=True)
class Guest:
    """A temporary guest: helper copy with redirected identity and event paths, fake gateway, env."""

    root: Path
    helper: Path
    event: Path
    boot_id: Path
    stat: Path
    log: Path
    env: dict[str, str]

    def set_identity(self, boot_id: str = BOOT_ID, ticks: str = TICKS) -> None:
        self.boot_id.write_text(boot_id + "\n")
        self.stat.write_bytes(stat_line(ticks))

    def write_event(self, payload: bytes | None = None) -> None:
        self.event.parent.mkdir(parents=True, exist_ok=True)
        self.event.write_bytes(json.dumps(valid_event()).encode() if payload is None else payload)

    def command(self, *arguments: str) -> list[str]:
        return [sys.executable, str(self.helper), *arguments]

    def run(self, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.command(*arguments), env=self.env | (env or {}), capture_output=True, text=True, timeout=20, check=False)

    def records(self) -> list[dict[str, JsonValue]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def starts(self) -> list[dict[str, JsonValue]]:
        return [entry for entry in self.records() if entry["event"] == "start"]

    def wait_started(self) -> None:
        wait_until(lambda: bool(self.starts()))

    def gateway_pid(self) -> int:
        pid = self.starts()[0]["pid"]
        assert isinstance(pid, int)
        return pid

    def output(self) -> str:
        return (self.root / "helper.out").read_text() if (self.root / "helper.out").exists() else ""

    @contextlib.contextmanager
    def running(self, *arguments: str, **env: str) -> Iterator[subprocess.Popen[bytes]]:
        with (self.root / "helper.out").open("wb") as output:
            process = subprocess.Popen(self.command(*arguments), env=self.env | env, stdout=output, stderr=output, start_new_session=True)
            try:
                yield process
            finally:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                for entry in self.starts():
                    pid = entry["pid"]
                    if isinstance(pid, int):
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(pid, signal.SIGKILL)
                process.wait(timeout=10)


def install(name: str, replacements: dict[str, str], destination: Path) -> None:
    """Copy an image file, redirecting literals; every literal must occur exactly once."""
    source = (SOURCES / name).read_text()
    for old, new in replacements.items():
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    destination.write_text(source)
    destination.chmod(0o755)


def make_guest(root: Path, port: int) -> Guest:
    helper = root / "bootstrap-agent-container.py"
    event = root / "event dir" / "AgentBootstrapCompleted.json"
    guest = Guest(root, helper, event, root / "boot_id", root / "pid1-stat", root / "gateway.log", {})
    install("bootstrap_event.py", {
        '"/tmp/maraclaw-bootstrap"': json.dumps(str(event.parent)),
        '"/proc/sys/kernel/random/boot_id"': json.dumps(str(guest.boot_id)),
        '"/proc/1/stat"': json.dumps(str(guest.stat)),
    }, root / "bootstrap_event.py")
    install("bootstrap-agent-container.py", {
        "TERMINATION_GRACE_SECONDS: Final = 2.0": "TERMINATION_GRACE_SECONDS: Final = 0.3",
        "READYZ_TIMEOUT_SECONDS: Final = 5.0": "READYZ_TIMEOUT_SECONDS: Final = 0.5",
    }, helper)
    gateway = root / "bin" / "openclaw"
    gateway.parent.mkdir()
    gateway.write_text(FAKE_GATEWAY.format(python=sys.executable))
    gateway.chmod(0o755)
    guest.set_identity()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("OPENCLAW_", "FAKE_"))}
    env.update(PATH=f"{gateway.parent}{os.pathsep}{env['PATH']}", FAKE_LOG=str(guest.log), FAKE_EVENT=str(event), OPENCLAW_GATEWAY_PORT=str(port), PYTHONDONTWRITEBYTECODE="1")
    guest.env.update(env)
    return guest


@pytest.fixture
def readyz() -> Iterator[Readyz]:
    server = Readyz()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def guest(tmp_path: Path, readyz: Readyz) -> Guest:
    return make_guest(tmp_path, readyz.port)
