import json
import sys
import time
from pathlib import Path

import openclaw_bootstrap_test_support as bootstrap_support
import pytest
from openclaw_bootstrap_test_support import (
    BOOT_ID,
    TICKS,
    Guest,
    JsonValue,
    Readyz,
    valid_event,
)

guest = bootstrap_support.guest
readyz = bootstrap_support.readyz

PENDING = 75
ACTION = "import json, sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); sys.exit(37)"
ARGUMENTS = ["a b", "", "$(x);*", "--flag=value"]


def action(marker: Path) -> list[str]:
    return [sys.executable, "-c", ACTION, str(marker), *ARGUMENTS]


def variant(**changes: JsonValue) -> bytes:
    return json.dumps(valid_event() | changes).encode()


def numeric(replacement: str) -> bytes:
    return json.dumps(valid_event()).replace(f'"{TICKS}"', replacement).encode()


INVALID_EVENTS = {
    "empty": b"", "truncated": b"{", "array": b"[]", "null": b"null", "string": b'"text"', "binary": b"\xff\xfe\x00",
    "too-deep": b"[" * 3000, "oversized": b" " * 5000 + json.dumps(valid_event()).encode(),
    "version-2": variant(version=2), "version-text": variant(version="1"), "version-bool": variant(version=True),
    "version-float": variant(version=1.0), "other-event": variant(event="Other"),
    **{f"missing-{key}": json.dumps({k: v for k, v in valid_event().items() if k != key}).encode() for key in valid_event()},
    "other-boot": variant(boot_id="00000000-0000-4000-8000-000000000000"), "boot-case": variant(boot_id=BOOT_ID.upper()),
    "boot-number": variant(boot_id=1), "ticks-float-equal": variant(pid1_start_ticks="9007199254740992"),
    "ticks-number": numeric(TICKS), "ticks-float-number": numeric("9007199254740992.0"),
    "ticks-leading-zero": variant(pid1_start_ticks="0" + TICKS), "ticks-space": variant(pid1_start_ticks=" " + TICKS),
    "ticks-empty": variant(pid1_start_ticks=""), "extra-key": variant(extra="x"),
}


def assert_denied(guest: Guest, readyz: Readyz, marker: Path) -> None:
    for arguments in (("check-ready",), ("exec", "--", *action(marker))):
        result = guest.run(*arguments)
        assert result.returncode == PENDING
        assert result.stderr.startswith("bootstrap-agent-container: bootstrap pending:")
    assert readyz.hits == []
    assert not marker.exists()


@pytest.mark.parametrize("payload", INVALID_EVENTS.values(), ids=INVALID_EVENTS.keys())
def test_guard_denies_missing_or_invalid_event(guest: Guest, readyz: Readyz, tmp_path: Path, payload: bytes | None) -> None:
    readyz.status = 200
    guest.write_event(payload)
    assert_denied(guest, readyz, tmp_path / "ran")


def test_guard_denies_missing_event(guest: Guest, readyz: Readyz, tmp_path: Path) -> None:
    readyz.status = 200
    assert_denied(guest, readyz, tmp_path / "ran")


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_guard_denies_event_that_is_not_a_regular_file(guest: Guest, readyz: Readyz, tmp_path: Path, kind: str) -> None:
    readyz.status = 200
    guest.event.parent.mkdir()
    if kind == "symlink":
        target = tmp_path / "valid.json"
        target.write_text(json.dumps(valid_event()))
        guest.event.symlink_to(target)
    else:
        guest.event.mkdir()
    assert_denied(guest, readyz, tmp_path / "ran")


@pytest.mark.parametrize("unreadable", ["boot_id", "stat"])
def test_guard_denies_when_guest_identity_cannot_be_read(guest: Guest, readyz: Readyz, tmp_path: Path, unreadable: str) -> None:
    readyz.status = 200
    guest.write_event()
    getattr(guest, unreadable).unlink()
    assert_denied(guest, readyz, tmp_path / "ran")


@pytest.mark.parametrize("layout", ["compact", "reordered"])
def test_check_ready_accepts_current_event_and_probes_readyz(guest: Guest, readyz: Readyz, layout: str) -> None:
    readyz.status = 200
    document = valid_event()
    guest.write_event(json.dumps(dict(reversed(document.items())), indent=2).encode() if layout == "reordered" else json.dumps(document, separators=(",", ":")).encode())
    assert guest.run("check-ready").returncode == 0
    assert readyz.hits == ["/readyz"]


@pytest.mark.parametrize("state", ["unready", "refused", "hung"])
def test_check_ready_fails_within_bound_when_gateway_is_not_ready(guest: Guest, readyz: Readyz, state: str) -> None:
    guest.write_event()
    readyz.hang = state == "hung"
    if state == "refused":
        readyz.close()
    started = time.monotonic()
    assert guest.run("check-ready").returncode == 1
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("change", [{"boot_id": "00000000-0000-4000-8000-000000000000"}, {"ticks": "9007199254740992"}], ids=["boot", "pid1-start"])
def test_event_from_previous_run_cannot_authorize_next_run(guest: Guest, readyz: Readyz, tmp_path: Path, change: dict[str, str]) -> None:
    readyz.status = 200
    guest.write_event()
    assert guest.run("check-ready").returncode == 0
    readyz.hits.clear()
    guest.set_identity(**change)
    assert_denied(guest, readyz, tmp_path / "ran")
    assert guest.event.exists()
    guest.set_identity()
    assert guest.run("check-ready").returncode == 0


def test_exec_runs_action_unchanged_for_current_event(guest: Guest, tmp_path: Path) -> None:
    guest.write_event()
    before = guest.event.read_bytes()
    marker = tmp_path / "ran"
    assert guest.run("exec", "--", *action(marker)).returncode == 37
    assert json.loads(marker.read_text()) == ARGUMENTS
    assert guest.event.read_bytes() == before


def test_exec_reports_actions_that_cannot_run(guest: Guest, tmp_path: Path) -> None:
    guest.write_event()
    plain = tmp_path / "plain"
    plain.write_text("not executable")
    assert guest.run("exec", "--", str(tmp_path / "missing")).returncode == 127
    assert guest.run("exec", "--", str(plain)).returncode == 126


@pytest.mark.parametrize("arguments", [(), ("exec",), ("exec", "--"), ("exec", "true"), ("check-ready", "extra")])
def test_usage_errors_exit_two_without_running_anything(guest: Guest, readyz: Readyz, arguments: tuple[str, ...]) -> None:
    guest.write_event()
    readyz.status = 200
    assert guest.run(*arguments).returncode == 2
    assert readyz.hits == []


def test_default_invocation_execs_non_gateway_command_unchanged(guest: Guest, readyz: Readyz, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    result = guest.run(*action(marker), env={"OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS": "invalid", "OPENCLAW_GATEWAY_PORT": "invalid"})
    assert result.returncode == 37
    assert json.loads(marker.read_text()) == ARGUMENTS
    assert not guest.event.exists()
    assert readyz.hits == []


@pytest.mark.parametrize("arguments", [(), ("channels", "list"), ("gateway", "status"), ("gateway", "call", "health")])
def test_openclaw_commands_other_than_the_gateway_server_run_unchanged(guest: Guest, readyz: Readyz, arguments: tuple[str, ...]) -> None:
    result = guest.run("openclaw", *arguments, env={"FAKE_MODE": "exit", "FAKE_EXIT": "7"})
    assert result.returncode == 7
    assert guest.starts()[0]["argv"] == list(arguments)
    assert not guest.event.exists()
    assert readyz.hits == []
