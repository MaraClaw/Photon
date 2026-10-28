#!/usr/bin/env python3
"""Supervise one startup probe, never the validator's final command."""

import ctypes
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
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
            grace_deadline = time.monotonic() + policy.grace
            while time.monotonic() < grace_deadline:
                time.sleep(min(0.02, max(0, grace_deadline - time.monotonic())))
        signal_group(process.pid, signal.SIGKILL)
        status = process.wait()
        if sys.platform.startswith("linux"):
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    break
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
        if sys.argv[1:] == ["--check-policy"]:
            return 128 + cancellation.signum if cancellation.check() else 0
        return supervise(sys.argv[2:], policy, cancellation)
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError:
        print("Startup probe supervisor failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
