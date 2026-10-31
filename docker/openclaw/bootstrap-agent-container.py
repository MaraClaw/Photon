#!/usr/bin/env python3
"""Publish and enforce AgentBootstrapCompleted for the current guest lifetime.

check-ready: validate the event, then probe /readyz. exec -- ACTION...: validate, then exec.
COMMAND...: exec unchanged, or supervise `openclaw gateway` and publish once /readyz answers.
"""

import contextlib
import errno
import http.client
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Final

from bootstrap_event import (
    EVENT_NAME,
    GuestIdentity,
    IdentityError,
    Reason,
    invalidate_event,
    publish_event,
    verify_event,
)

PENDING_EXIT: Final = 75
BUDGET_SETTING: Final = "OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS"
PORT_SETTING: Final = "OPENCLAW_GATEWAY_PORT"
DECIMAL: Final = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
PORT: Final = re.compile(r"[0-9]{1,5}")
READYZ_TIMEOUT_SECONDS: Final = 5.0
ATTEMPT_TIMEOUT_SECONDS: Final = 1.0
TERMINATION_GRACE_SECONDS: Final = 2.0
POLL_SECONDS: Final = 0.1
TERMINATING: Final = frozenset({signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM})
RELAYED: Final = TERMINATING | {signal.SIGUSR1, signal.SIGUSR2}


@dataclass(frozen=True, slots=True)
class PolicyError(ValueError):
    setting: str
    expected: str

    def __str__(self) -> str:
        return f"Invalid bootstrap policy: {self.setting} must be {self.expected}"


def startup_budget() -> float:
    raw = os.environ.get(BUDGET_SETTING, "120")
    value = float(raw) if DECIMAL.fullmatch(raw) else math.nan
    if not math.isfinite(value) or value <= 0:
        raise PolicyError(BUDGET_SETTING, "a positive decimal")
    return value


def port_flag(command: list[str]) -> str | None:
    """The value of `--port N` or `--port=N` on the gateway command line, if any."""
    for index, token in enumerate(command[2:], start=2):
        if token.startswith("--port="):
            return token.partition("=")[2]
        if token == "--port":
            return command[index + 1] if index + 1 < len(command) else ""
    return None


def gateway_port(command: list[str]) -> int:
    """OpenClaw's own precedence: --port, then OPENCLAW_GATEWAY_PORT, then 18789."""
    flagged = port_flag(command)
    setting = PORT_SETTING if flagged is None else "--port"
    raw = os.environ.get(PORT_SETTING, "18789") if flagged is None else flagged
    port = int(raw) if PORT.fullmatch(raw) else 0
    if not 0 < port <= 65535:
        raise PolicyError(setting, "a TCP port number")
    return port


def readyz_answers(port: int, timeout: float) -> bool:
    """True only when the loopback gateway answers /readyz with 200."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/readyz", headers={"Connection": "close"})
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def report(message: str) -> None:
    print(f"bootstrap-agent-container: {message}", file=sys.stderr)


def launch_failure(command: str, error: OSError) -> int:
    report(f"cannot execute {command}: {error.strerror}")
    return 127 if error.errno == errno.ENOENT else 126


def exec_command(command: list[str]) -> int:
    """Replace this process with command; only a failure to do so returns."""
    try:
        os.execvp(command[0], command)
    except OSError as error:
        return launch_failure(command[0], error)


def signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        if sys.platform != "darwin":
            raise


def stop(child: subprocess.Popen[bytes]) -> int:
    """Terminate the child's process group, escalate to KILL after the grace, reap it and return its status."""
    signal_group(child.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=TERMINATION_GRACE_SECONDS)
    signal_group(child.pid, signal.SIGKILL)
    return child.wait()


def exit_code(status: int) -> int:
    return 128 - status if status < 0 else status


class SignalQueue:
    """Handlers only record; the supervisor loop decides whether to cancel startup or relay."""

    def __init__(self) -> None:
        self.received: list[int] = []
        for signum in RELAYED:
            signal.signal(signum, self.record)

    def record(self, signum: int, _frame: FrameType | None) -> None:
        self.received.append(signum)

    def forward(self, child: subprocess.Popen[bytes], *, startup: bool) -> int:
        """Relay queued signals to the child's group; return the first terminating one held back during startup."""
        held = 0
        while self.received:
            signum = self.received.pop(0)
            if startup and signum in TERMINATING:
                held = held or signum
            else:
                signal_group(child.pid, signum)
        return held


def await_ready(child: subprocess.Popen[bytes], signals: SignalQueue, port: int, budget: float) -> int | None:
    """Wait for /readyz with the child confirmed alive; None means ready, otherwise the exit status."""
    deadline = time.monotonic() + budget
    answered = False
    while True:
        if held := signals.forward(child, startup=True):
            stop(child)
            return 128 + held
        if (status := child.poll()) is not None:
            stop(child)
            report("gateway exited before /readyz answered")
            return exit_code(status) or 1
        if answered:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop(child)
            report("gateway did not answer /readyz within the startup budget")
            return 124
        answered = readyz_answers(port, min(ATTEMPT_TIMEOUT_SECONDS, remaining))
        if not answered:
            time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def serve_gateway(command: list[str]) -> int:
    port = gateway_port(command)
    budget = startup_budget()
    identity = GuestIdentity.read()
    signals = SignalQueue()
    invalidate_event()
    try:
        child = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        return launch_failure(command[0], error)
    if (outcome := await_ready(child, signals, port, budget)) is not None:
        return outcome
    try:
        publish_event(identity)
    except OSError as error:
        stop(child)
        report(f"cannot publish {EVENT_NAME}: {error.strerror}")
        return 1
    try:
        while child.poll() is None:
            signals.forward(child, startup=False)
            time.sleep(POLL_SECONDS)
        return exit_code(stop(child))
    finally:
        invalidate_event()


def runs_gateway(command: list[str]) -> bool:
    """True for the long-running `openclaw gateway [run] [--option ...]`, not its management subcommands."""
    if len(command) < 2 or Path(command[0]).name != "openclaw" or command[1] != "gateway":
        return False
    rest = command[2:]
    return not rest or rest[0].startswith("-") or rest[0] == "run"


def pending(reason: Reason) -> int:
    report(f"bootstrap pending: {reason}")
    return PENDING_EXIT


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["check-ready"]:
                if (reason := verify_event()) is not None:
                    return pending(reason)
                return 0 if readyz_answers(gateway_port([]), READYZ_TIMEOUT_SECONDS) else 1
            case ["exec", "--", *action] if action:
                if (reason := verify_event()) is not None:
                    return pending(reason)
                return exec_command(action)
            case [] | ["exec", *_] | ["check-ready", *_]:
                report("usage: check-ready | exec -- ACTION... | COMMAND...")
                return 2
            case [*command]:
                return serve_gateway(command) if runs_gateway(command) else exec_command(command)
    except PolicyError as error:
        report(str(error))
        return 2
    except IdentityError as error:
        report(str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
