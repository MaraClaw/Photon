import subprocess
from pathlib import Path

import pytest

from openclaw_officecli_smoke_cleanup import CLEANUP_PROBE, CleanupFailure, cleanup_error_message, run_cleanup
from openclaw_officecli_smoke_runtime import CommandResult, RunRequest, SmokeWorkspace, create_workspace, run_container, wait_for_gateway


def test_cleanup_probe_makes_state_contents_writable(tmp_path: Path) -> None:
    # Given
    release = tmp_path / "agents" / "main"
    release.mkdir(parents=True)
    binary = release / "officecli"
    binary.write_text("test binary", encoding="utf-8")
    binary.chmod(0o444)
    release.chmod(0o555)

    # When
    completed = subprocess.run(  # noqa: S603 - execute the owned teardown probe against temporary state.
        ("/bin/sh", "-c", CLEANUP_PROBE),
        env={"OPENCLAW_STATE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    assert binary.stat().st_mode & 0o222 == 0o222
    assert release.stat().st_mode & 0o222 == 0o222


class CleanupProbeError(Exception):
    """Raised by the simulated permission-normalization probe."""


def test_cleanup_attempts_later_phases_when_probe_raises() -> None:
    # Given
    calls: list[str] = []
    probe_error = CleanupProbeError()

    def probe() -> None:
        calls.append("probe")
        raise probe_error

    def remove_online_container() -> None:
        calls.append("online-container")

    def remove_offline_container() -> None:
        calls.append("offline-container")

    def remove_image() -> None:
        calls.append("image")

    def remove_state() -> None:
        calls.append("state")

    # When
    failures = run_cleanup(
        (
            ("permission normalization", probe),
            ("online container removal", remove_online_container),
            ("offline container removal", remove_offline_container),
            ("image removal", remove_image),
            ("temporary state deletion", remove_state),
        )
    )

    # Then
    assert calls == ["probe", "online-container", "offline-container", "image", "state"]
    assert failures == (CleanupFailure("permission normalization", probe_error),)


def test_cleanup_failures_when_test_is_already_failing_do_not_mask_original_error() -> None:
    # Given
    cleanup_failure = CleanupFailure("image removal", RuntimeError("image missing"))
    original_error = RuntimeError("linux/arm64 image build failed")

    # When
    message = cleanup_error_message((cleanup_failure,), original_error)

    # Then
    assert message is None
    assert original_error.__notes__ == ["Cleanup failures:\nimage removal: RuntimeError('image missing')"]


def test_gateway_run_uses_image_defaults_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[str, ...]] = []

    def record(command: tuple[str, ...]) -> CommandResult:
        commands.append(command)
        return CommandResult(command, 0, "container-id", "")

    monkeypatch.setattr("openclaw_officecli_smoke_runtime.run_command", record)
    workspace = SmokeWorkspace(tmp_path, "test", tmp_path / "keyring")
    run_container(workspace, RunRequest("arm64", "fresh", tmp_path, "/home/node/.openclaw"))
    command = commands[0]
    assert command[-1] == workspace.image("arm64")
    assert "--entrypoint" not in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--platform") + 1] == "linux/arm64"


def test_wait_for_gateway_reports_logs_when_container_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    def record(command: tuple[str, ...], *, timeout_seconds: int) -> CommandResult:
        if command[1] == "inspect":
            return CommandResult(command, 0, "false\n", "")
        return CommandResult(command, 0, "startup failure", "")

    monkeypatch.setattr("openclaw_officecli_smoke_runtime.run_command", record)
    with pytest.raises(AssertionError, match="startup failure"):
        wait_for_gateway("test-gateway")


def test_workspace_setup_failure_removes_partial_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "partial"
    root.mkdir()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.tempfile.mkdtemp", lambda **kwargs: str(root))

    def fail_chmod(path: Path, mode: int) -> None:
        raise PermissionError("setup failure")

    monkeypatch.setattr(Path, "chmod", fail_chmod)
    with pytest.raises(PermissionError, match="setup failure"):
        create_workspace()
    assert not root.exists()
