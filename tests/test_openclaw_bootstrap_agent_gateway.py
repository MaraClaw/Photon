import json
import os
import signal
import time

import openclaw_bootstrap_test_support as bootstrap_support
import pytest
from openclaw_bootstrap_test_support import (
    Guest,
    Readyz,
    alive,
    valid_event,
    wait_until,
)

guest = bootstrap_support.guest
readyz = bootstrap_support.readyz

GATEWAY = ("openclaw", "gateway")
TIMEOUT = "OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS"
PORT = "OPENCLAW_GATEWAY_PORT"


def wait_probed(readyz: Readyz, count: int = 3) -> None:
    target = len(readyz.hits) + count
    wait_until(lambda: len(readyz.hits) >= target)


def test_event_is_published_after_readyz_and_removed_with_the_gateway(guest: Guest, readyz: Readyz) -> None:
    guest.write_event()  # leftover that would still match this guest's identity
    with guest.running(*GATEWAY, "--flag", "value", "", FAKE_TERM_EXIT="9") as helper:
        guest.wait_started()
        wait_probed(readyz)
        start = guest.starts()[0]
        assert start["argv"] == ["gateway", "--flag", "value", ""]
        assert start["stale_event"] is False
        assert not guest.event.exists()
        assert helper.poll() is None
        readyz.status = 200
        wait_until(guest.event.exists)
        assert json.loads(guest.event.read_bytes()) == valid_event()
        assert guest.run("check-ready").returncode == 0
        assert [entry.name for entry in guest.event.parent.iterdir()] == [guest.event.name]
        os.kill(helper.pid, signal.SIGTERM)
        assert helper.wait(timeout=10) == 9
        assert not guest.event.exists()
        assert not alive(guest.gateway_pid())


def test_event_published_by_a_previous_run_cannot_authorize_the_next_run(guest: Guest, readyz: Readyz) -> None:
    with guest.running(*GATEWAY, FAKE_TERM_EXIT="0") as helper:
        guest.wait_started()
        readyz.status = 200
        wait_until(guest.event.exists)
        previous = guest.event.read_bytes()
        os.kill(helper.pid, signal.SIGTERM)
        helper.wait(timeout=10)
    guest.log.unlink()
    guest.set_identity(ticks="9007199254740994")
    guest.write_event(previous)
    readyz.status = 503
    assert guest.run("check-ready").returncode == 75
    with guest.running(*GATEWAY):
        guest.wait_started()
        wait_probed(readyz)
        assert guest.starts()[0]["stale_event"] is False
        assert guest.run("check-ready").returncode == 75
        readyz.status = 200
        wait_until(lambda: guest.event.exists() and json.loads(guest.event.read_bytes()) == valid_event(ticks="9007199254740994"))
        assert guest.run("check-ready").returncode == 0


def test_publication_replaces_the_event_file_instead_of_rewriting_it(guest: Guest, readyz: Readyz) -> None:
    with guest.running(*GATEWAY):
        guest.wait_started()
        wait_probed(readyz)
        guest.write_event(b"written by something else after startup")
        with guest.event.open("rb") as earlier:
            readyz.status = 200
            wait_until(lambda: guest.event.read_bytes().startswith(b"{\"version\""))
            assert earlier.read() == b"written by something else after startup"
        assert json.loads(guest.event.read_bytes()) == valid_event()


PRE_READY = {
    "exit-nonzero": ({"FAKE_MODE": "exit", "FAKE_EXIT": "3"}, 3),
    "exit-zero": ({"FAKE_MODE": "exit", "FAKE_EXIT": "0"}, 1),
    "timeout": ({TIMEOUT: "0.6"}, 124),
    "timeout-term-ignored": ({TIMEOUT: "0.6", "FAKE_MODE": "ignore-term"}, 124),
}


@pytest.mark.parametrize("environment,expected", PRE_READY.values(), ids=PRE_READY.keys())
def test_pre_ready_failure_publishes_nothing_and_leaves_no_gateway(guest: Guest, readyz: Readyz, environment: dict[str, str], expected: int) -> None:
    with guest.running(*GATEWAY, **environment) as helper:
        assert helper.wait(timeout=15) == expected
        assert not alive(guest.gateway_pid())
    assert not guest.event.parent.exists()


def test_hung_readyz_cannot_extend_the_startup_budget(guest: Guest, readyz: Readyz) -> None:
    readyz.hang = True
    started = time.monotonic()
    with guest.running(*GATEWAY, **{TIMEOUT: "1"}) as helper:
        assert helper.wait(timeout=15) == 124
        assert not alive(guest.gateway_pid())
    assert time.monotonic() - started < 8
    assert not guest.event.parent.exists()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_signal_before_ready_cancels_startup(guest: Guest, readyz: Readyz, signum: signal.Signals) -> None:
    with guest.running(*GATEWAY, FAKE_MODE="ignore-term") as helper:
        guest.wait_started()
        wait_probed(readyz, 2)
        os.kill(helper.pid, signum)
        assert helper.wait(timeout=10) == 128 + signum
        assert not alive(guest.gateway_pid())
    assert not guest.event.parent.exists()


def test_gateway_that_dies_as_readyz_answers_is_never_published(guest: Guest, readyz: Readyz) -> None:
    with guest.running(*GATEWAY) as helper:
        guest.wait_started()
        pid = guest.gateway_pid()

        def kill_then_answer() -> None:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.3)

        readyz.on_request = kill_then_answer
        readyz.status = 200
        assert helper.wait(timeout=10) == 128 + signal.SIGKILL
    assert not guest.event.parent.exists()


def test_gateway_exit_after_ready_propagates_status_and_removes_event(guest: Guest, readyz: Readyz) -> None:
    with guest.running(*GATEWAY, FAKE_USR1_EXIT="5") as helper:
        guest.wait_started()
        readyz.status = 200
        wait_until(guest.event.exists)
        os.kill(helper.pid, signal.SIGUSR1)
        assert helper.wait(timeout=10) == 5
    assert any(entry.get("signum") == signal.SIGUSR1 for entry in guest.records())
    assert not guest.event.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_event_that_cannot_be_published_stops_the_gateway(guest: Guest, readyz: Readyz) -> None:
    guest.event.parent.mkdir(mode=0o700)
    guest.event.parent.chmod(0o500)
    try:
        with guest.running(*GATEWAY) as helper:
            guest.wait_started()
            readyz.status = 200
            assert helper.wait(timeout=10) == 1
            assert not alive(guest.gateway_pid())
    finally:
        guest.event.parent.chmod(0o700)


POLICIES = [(TIMEOUT, value) for value in ("", "0", "-1", "nan", "inf", "1e2", " 1")] + [(PORT, value) for value in ("", "0", "65536", "abc", "-1", "+1", " 80")]


@pytest.mark.parametrize("setting,value", POLICIES)
def test_invalid_policy_blocks_gateway_launch(guest: Guest, readyz: Readyz, setting: str, value: str) -> None:
    assert guest.run(*GATEWAY, env={setting: value}).returncode == 2
    assert not guest.log.exists()
    assert not guest.event.parent.exists()
    assert readyz.hits == []


@pytest.mark.parametrize("style", ["separate", "equals"])
def test_port_flag_takes_precedence_over_the_environment(guest: Guest, readyz: Readyz, style: str) -> None:
    flag = ("--port", str(readyz.port)) if style == "separate" else (f"--port={readyz.port}",)
    with guest.running(*GATEWAY, *flag, **{PORT: "1"}) as helper:
        guest.wait_started()
        readyz.status = 200
        wait_until(guest.event.exists)
        assert helper.poll() is None


@pytest.mark.parametrize("flag", [("--port",), ("--port=",), ("--port=abc",), ("--port", "70000")])
def test_invalid_port_flag_blocks_gateway_launch(guest: Guest, flag: tuple[str, ...]) -> None:
    assert guest.run(*GATEWAY, *flag).returncode == 2
    assert not guest.log.exists()


def test_gateway_that_cannot_be_launched_publishes_nothing(guest: Guest, readyz: Readyz) -> None:
    assert guest.run("/nonexistent/openclaw", "gateway").returncode == 127
    assert not guest.event.parent.exists()


def test_unreadable_guest_identity_blocks_gateway_launch(guest: Guest, readyz: Readyz) -> None:
    guest.boot_id.unlink()
    assert guest.run(*GATEWAY).returncode == 1
    assert not guest.log.exists()
    assert readyz.hits == []
