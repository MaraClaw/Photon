#!/usr/bin/env python3
"""Publish and enforce AgentBootstrapCompleted for the current guest lifetime.

check-ready: validate the event, then probe /readyz. exec -- ACTION...: validate, then exec.
COMMAND...: exec unchanged, or register the built-in MCP servers, supervise `openclaw gateway` and publish once /readyz answers.
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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Final

import builtin_mcp
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
ROOT_BOOLEAN_FLAGS: Final = frozenset({"--dev", "--no-color"})
ROOT_VALUE_FLAGS: Final = frozenset({"--profile", "--log-level", "--container"})
GATEWAY_BOOLEAN_FLAGS: Final = frozenset({
    "--tailscale-reset-on-exit", "--allow-unconfigured", "--dev", "--ambient-channels",
    "--dev-ambient-channels", "--reset", "--update-canary", "--force", "--verbose",
    "--cli-backend-logs", "--claude-cli-logs", "--compact", "--raw-stream", "--task-supervisor",
})
GATEWAY_VALUE_FLAGS: Final = frozenset({
    "--port", "--bind", "--token", "--token-file", "--auth", "--password",
    "--password-file", "--tailscale", "--ws-log", "--raw-stream-path", "--task-supervisor-child",
})
PROFILE_NAME: Final = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")


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


@dataclass(frozen=True, slots=True)
class GatewayInvocation:
    port: str | None
    profile: str | None


def gateway_invocation(command: list[str]) -> GatewayInvocation | None:
    """Pinned CLI option roles, without rewriting argv or treating option values as commands."""
    if not command or Path(command[0]).name != "openclaw":
        return None
    found_gateway = literal = root_dev = container = False
    profile = port = None
    positionals: list[str] = []
    index = 1
    while index < len(command):
        token = command[index]
        if not literal and token == "--":
            literal = True
            index += 1
            continue
        flag, equals, inline = token.partition("=")
        root = not literal and (flag in ROOT_BOOLEAN_FLAGS or flag in ROOT_VALUE_FLAGS)
        boolean = root and flag in ROOT_BOOLEAN_FLAGS or not literal and flag in GATEWAY_BOOLEAN_FLAGS
        valued = root and flag in ROOT_VALUE_FLAGS or not literal and flag in GATEWAY_VALUE_FLAGS
        if not literal and token.startswith("-"):
            if boolean:
                if equals or not root and not found_gateway:
                    return None
                if flag == "--dev" and not found_gateway:
                    if profile is not None and profile != "dev":
                        return None
                    root_dev, profile = True, "dev"
            elif valued:
                if not root and not found_gateway:
                    return None
                if equals:
                    value = inline
                else:
                    index += 1
                    if index >= len(command):
                        return None
                    value = command[index]
                if root and not value.strip():
                    return None
                if flag == "--profile":
                    selected = value.strip()
                    if root_dev or not PROFILE_NAME.fullmatch(selected):
                        return None
                    profile = selected
                elif flag == "--container":
                    container = True
                elif flag == "--port":
                    port = value
            else:
                return None
        elif not found_gateway:
            if token != "gateway":
                return None
            found_gateway = True
        else:
            positionals.append(token)
        index += 1
    if not found_gateway or positionals not in ([], ["run"]) or container and profile is not None:
        return None
    return GatewayInvocation(port, profile)


def profile_environment(profile: str | None) -> Mapping[str, str]:
    """Apply the pinned CLI's root profile selectors to registration and readiness, not child argv."""
    if profile is None:
        return os.environ
    environ = dict(os.environ)
    canonical = {key: value for key, value in environ.items() if key not in {"OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH"}}
    previous = builtin_mcp.config_path(canonical).parent
    selected = builtin_mcp.config_path(canonical | {"OPENCLAW_PROFILE": profile}).parent
    state = environ.get("OPENCLAW_STATE_DIR", "").strip()
    config = environ.get("OPENCLAW_CONFIG_PATH", "").strip()
    changed = previous != selected
    inherited_state = bool(state and changed and builtin_mcp.config_path(environ | {"OPENCLAW_CONFIG_PATH": ""}).parent == previous)
    inherited_config = bool(changed and (not state or inherited_state) and config and builtin_mcp.config_path(environ) == previous / "openclaw.json")
    service = environ.get("OPENCLAW_SERVICE_MARKER", "").strip() == "openclaw" and environ.get("OPENCLAW_SERVICE_KIND", "").strip() in {"", "gateway"}
    inherited_service = service and inherited_state and inherited_config
    if inherited_service:
        for key in ("OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH", "OPENCLAW_PROFILE", PORT_SETTING,
                    "OPENCLAW_LAUNCHD_LABEL", "OPENCLAW_SYSTEMD_UNIT", "OPENCLAW_WINDOWS_TASK_NAME"):
            environ.pop(key, None)
    environ["OPENCLAW_PROFILE"] = profile
    target = state if state and not inherited_state else str(selected)
    if not state or inherited_state:
        environ["OPENCLAW_STATE_DIR"] = target
    if not inherited_service and (not config or inherited_config):
        environ["OPENCLAW_CONFIG_PATH"] = str(Path(target) / "openclaw.json")
    if profile == "dev" and not environ.get(PORT_SETTING, "").strip():
        environ[PORT_SETTING] = "19001"
    return environ


def gateway_port(invocation: GatewayInvocation, environ: Mapping[str, str]) -> int:
    """Flag and environment precedence, with the pinned CLI's selected-profile default."""
    profile = environ.get("OPENCLAW_PROFILE", "").strip()
    default = 18789
    if profile.lower() != "default" and PROFILE_NAME.fullmatch(profile):
        hashed = 2166136261
        for byte in profile.encode("utf-8"):
            hashed = ((hashed ^ byte) * 16777619) & 0xFFFFFFFF
        default = 20000 + hashed % 40000
    setting = PORT_SETTING if invocation.port is None else "--port"
    raw = environ.get(PORT_SETTING, str(default)) if invocation.port is None else invocation.port
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


def serve_gateway(command: list[str], invocation: GatewayInvocation) -> int:
    environ = profile_environment(invocation.profile)
    port = gateway_port(invocation, environ)
    budget = startup_budget()
    identity = GuestIdentity.read()
    signals = SignalQueue()
    invalidate_event()
    builtin_mcp.register(report, environ=environ)
    try:
        child = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        return launch_failure(command[0], error)
    if (outcome := await_ready(child, signals, port, budget)) is not None:
        return outcome
    try:
        publish_event(identity, port)
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


def pending(reason: Reason) -> int:
    report(f"bootstrap pending: {reason}")
    return PENDING_EXIT


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["check-ready"]:
                verified = verify_event()
                if isinstance(verified, Reason):
                    return pending(verified)
                return 0 if readyz_answers(verified, READYZ_TIMEOUT_SECONDS) else 1
            case ["exec", "--", *action] if action:
                verified = verify_event()
                if isinstance(verified, Reason):
                    return pending(verified)
                return exec_command(action)
            case [] | ["exec", *_] | ["check-ready", *_]:
                report("usage: check-ready | exec -- ACTION... | COMMAND...")
                return 2
            case [*command]:
                invocation = gateway_invocation(command)
                return serve_gateway(command, invocation) if invocation is not None else exec_command(command)
    except PolicyError as error:
        report(str(error))
        return 2
    except IdentityError as error:
        report(str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
