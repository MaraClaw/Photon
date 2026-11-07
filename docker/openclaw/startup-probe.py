#!/usr/bin/env python3
"""Supervise one startup probe, never the validator's final command."""

import contextlib
import ctypes
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


@dataclass(frozen=True, slots=True)
class PolicyError(ValueError):
    setting: str

    def __str__(self) -> str:
        return f"Invalid startup probe policy: {self.setting} must be a positive decimal"


@dataclass(frozen=True, slots=True)
class Policy:
    timeout: float
    grace: float

    @classmethod
    def from_environment(cls) -> "Policy":
        values: list[float] = []
        for setting, default in (
            ("OPENCLAW_PROBE_TIMEOUT_SECONDS", "30"),
            ("OPENCLAW_PROBE_GRACE_SECONDS", "2"),
        ):
            raw = os.environ.get(setting, default)
            if re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", raw) is None:
                raise PolicyError(setting)
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise PolicyError(setting)
            values.append(value)
        return cls(*values)


class Cancellation:
    """Remember the first handled signal without interrupting cleanup."""

    def __init__(self) -> None:
        self.signum: int = 0

    def record(self, signum: int, _frame: FrameType | None) -> None:
        if not self.signum:
            self.signum = signum

    def check(self) -> int:
        os.lseek(3, 0, os.SEEK_SET)
        message = os.read(3, 32)
        if not self.signum and message.endswith(b"\n"):
            self.signum = int(message)
        return self.signum


def enable_subreaper() -> None:
    """Make this helper, rather than tini, own orphaned probe descendants."""
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    enabled = ctypes.c_int()
    if prctl(36, ctypes.c_void_p(1), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot enable startup probe subreaper")
    if prctl(37, ctypes.byref(enabled), 0, 0, 0) != 0 or enabled.value != 1:
        raise OSError(ctypes.get_errno(), "Cannot verify startup probe subreaper")


def signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        if sys.platform != "darwin":
            raise


def live_processes() -> list[tuple[int, int, int]]:
    """(pid, ppid, pgrp) of every non-zombie process visible in /proc."""
    processes = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            raw = Path(entry.path, "stat").read_bytes()
        except OSError:
            continue
        # comm may hold spaces and parentheses: state, ppid and pgrp follow the last ")".
        state, ppid, pgrp = raw.rpartition(b")")[2].split()[:3]
        if state != b"Z":
            processes.append((int(entry.name), int(ppid), int(pgrp)))
    return processes


def group_alive(pgid: int) -> bool:
    """Whether a member of the probe's group can still run; zombies awaiting reaping cannot."""
    if sys.platform.startswith("linux"):
        return any(pgrp == pgid for _, _, pgrp in live_processes())
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        # Darwin refuses to signal a group holding only zombies.
        return False
    return True


def wait_group(pgid: int, deadline: float) -> None:
    while time.monotonic() < deadline and group_alive(pgid):
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def reap_escaped(grace: float) -> None:
    """Reap descendants that escaped the probe's group: TERM, KILL after the grace, give up after another."""
    deadline = time.monotonic() + grace
    give_up = deadline + grace
    signum = signal.SIGTERM
    signalled: set[int] = set()
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0]:
                continue
        except ChildProcessError:
            return
        now = time.monotonic()
        if now >= give_up:
            return
        if now >= deadline:
            signum = signal.SIGKILL
            signalled.clear()
        for pid, ppid, _ in live_processes():
            if ppid == os.getpid() and pid not in signalled:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signum)
                signalled.add(pid)
        time.sleep(0.02)


def supervise(command: list[str], policy: Policy, cancellation: Cancellation) -> int:
    enable_subreaper()
    if cancellation.check():
        return 128 + cancellation.signum
    deadline = time.monotonic() + policy.timeout
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout if sys.argv[1] == "--capture" else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return 1
    timed_out = False
    try:
        while not cancellation.check():
            if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    finally:
        signal_group(process.pid, signal.SIGTERM)
        if timed_out or cancellation.check():
            wait_group(process.pid, time.monotonic() + policy.grace)
        signal_group(process.pid, signal.SIGKILL)
        status = process.wait()
        if sys.platform.startswith("linux"):
            reap_escaped(policy.grace)
    if cancellation.check():
        return 128 + cancellation.signum
    if timed_out:
        print("Startup probe timed out", file=sys.stderr)
        return 124
    return int(status != 0)


def main() -> int:
    cancellation = Cancellation()
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, cancellation.record)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    try:
        policy = Policy.from_environment()
        return supervise(sys.argv[2:], policy, cancellation)
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError:
        print("Startup probe supervisor failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
