"""AgentBootstrapCompleted: the completion event of one guest lifetime, its publisher and its guard.

The event binds the kernel boot id and PID 1 start time, so a file left by an earlier boot or
container start never authorizes this one, whether or not anything removed it.
"""

import contextlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

EVENT_DIR: Final = Path("/tmp/maraclaw-bootstrap")
EVENT_NAME: Final = "AgentBootstrapCompleted"
EVENT_PATH: Final = EVENT_DIR / f"{EVENT_NAME}.json"
EVENT_VERSION: Final = 1
EVENT_KEYS: Final = frozenset({"version", "event", "boot_id", "pid1_start_ticks"})
EVENT_MAX_BYTES: Final = 4096
BOOT_ID_PATH: Final = Path("/proc/sys/kernel/random/boot_id")
PID1_STAT_PATH: Final = Path("/proc/1/stat")


@dataclass(frozen=True, slots=True)
class IdentityError(Exception):
    source: Path

    def __str__(self) -> str:
        return f"Cannot read guest identity from {self.source}"


@dataclass(frozen=True, slots=True)
class GuestIdentity:
    """What tells this guest lifetime apart from every earlier one on the same host."""

    boot_id: str
    pid1_start_ticks: str

    @classmethod
    def read(cls) -> GuestIdentity:
        try:
            boot_id = BOOT_ID_PATH.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            raise IdentityError(BOOT_ID_PATH) from None
        try:
            raw = PID1_STAT_PATH.read_bytes()
        except OSError:
            raise IdentityError(PID1_STAT_PATH) from None
        # comm (field 2) may hold spaces and parentheses: fields 3.. start after the last ")".
        _, found, tail = raw.rpartition(b")")
        fields = tail.split()
        if not boot_id or not found or len(fields) < 20 or not fields[19].isdigit():
            raise IdentityError(PID1_STAT_PATH)
        return cls(boot_id, fields[19].decode("ascii"))


class Reason(StrEnum):
    MISSING = "event missing"
    MALFORMED = "event malformed"
    UNSUPPORTED = "event unsupported"
    MISMATCHED = "event belongs to another guest lifetime"
    UNVERIFIABLE = "guest identity unreadable"


def classify(document: JsonValue, current: GuestIdentity) -> Reason | None:
    """None authorizes; otherwise why the event is denied."""
    match document:  # JSON is open-ended; the default case denies.
        case {"version": int() as version, "event": str() as name, "boot_id": str() as boot_id, "pid1_start_ticks": str() as ticks} if document.keys() == EVENT_KEYS:
            if type(version) is not int or version != EVENT_VERSION or name != EVENT_NAME:
                return Reason.UNSUPPORTED
            return None if (boot_id, ticks) == (current.boot_id, current.pid1_start_ticks) else Reason.MISMATCHED
        case _:
            return Reason.MALFORMED


def verify_event() -> Reason | None:
    """None only for an event written for this guest lifetime; nothing is cached or relied on to be removed."""
    try:
        current = GuestIdentity.read()
    except IdentityError:
        return Reason.UNVERIFIABLE
    try:
        descriptor = os.open(EVENT_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return Reason.MISSING
    except OSError:
        return Reason.MALFORMED
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return Reason.MALFORMED
        raw = os.read(descriptor, EVENT_MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        document: JsonValue = json.loads(raw) if len(raw) <= EVENT_MAX_BYTES else None
    except (ValueError, RecursionError):
        return Reason.MALFORMED
    return classify(document, current)


def publish_event(identity: GuestIdentity) -> None:
    """Replace the event atomically: write a sibling file, then rename it over the event."""
    document = {"version": EVENT_VERSION, "event": EVENT_NAME, "boot_id": identity.boot_id, "pid1_start_ticks": identity.pid1_start_ticks}
    EVENT_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".publish.", dir=EVENT_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            json.dump(document, stream, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, EVENT_PATH)
    finally:
        Path(temporary).unlink(missing_ok=True)


def invalidate_event() -> None:
    """Hygiene only: freshness comes from the identity check, so a failed removal is harmless."""
    with contextlib.suppress(OSError):
        EVENT_PATH.unlink(missing_ok=True)
