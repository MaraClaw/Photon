import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pywhales import DockerException

from openclaw_officecli_smoke_cleanup import CLEANUP_PROBE, CleanupFailure, cleanup_error_message, run_cleanup
from openclaw_officecli_smoke_probe import CHILD_MARKER
from openclaw_officecli_smoke_runtime import (
    RunRequest,
    SmokeWorkspace,
    create_workspace,
    probe_gateway,
    run_container,
    run_gateway_scenarios,
    run_gateway_smoke,
    wait_for_gateway,
)


def test_cleanup_probe_makes_state_contents_writable(tmp_path: Path) -> None:
    # Given
    release = tmp_path / "agents" / "main"
    release.mkdir(parents=True)
    binary = release / "officecli"
    binary.write_text("test binary", encoding="utf-8")
    binary.chmod(0o444)
    release.chmod(0o555)

    # When
    pid = os.posix_spawn("/bin/sh", ("/bin/sh", "-c", CLEANUP_PROBE), {"OPENCLAW_STATE_DIR": str(tmp_path)})
    _, status = os.waitpid(pid, 0)

    # Then
    assert os.waitstatus_to_exitcode(status) == 0
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
    run = Mock()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.run", run)
    workspace = SmokeWorkspace(tmp_path, "test", tmp_path / "keyring")
    run_container(workspace, RunRequest("arm64", "fresh", tmp_path, "/home/node/.openclaw"))
    assert run.call_args.args == (workspace.image("arm64"),)
    options = run.call_args.kwargs
    assert "entrypoint" not in options
    assert options["networks"] == ["none"]
    assert options["platform"] == "linux/arm64"
    assert options["detach"] is True
    assert options["mounts"][1] == ["type=bind", f"source={workspace.keyring_file}", "target=/run/secrets/gogcli_keyring_password", "readonly"]


def test_wait_for_gateway_reports_logs_when_container_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.container.inspect", lambda name: SimpleNamespace(state=SimpleNamespace(running=False)))
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.logs", lambda name, timeout: "startup failure")
    with pytest.raises(AssertionError, match="startup failure"):
        wait_for_gateway("test-gateway")


def test_probe_failure_preserves_output_in_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error = DockerException(["docker", "exec"], 1, b"probe output", b"probe failure", raw_diagnostics=True)
    monkeypatch.setenv("OPENCLAW_SMOKE_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.execute", Mock(side_effect=error))
    with pytest.raises(DockerException, match="probe failure"):
        probe_gateway("test-gateway", "probe")
    evidence = list(tmp_path.glob("*.log"))
    assert len(evidence) == 1
    assert "probe output" in evidence[0].read_text(encoding="utf-8")
    assert "probe failure" in evidence[0].read_text(encoding="utf-8")


def test_partial_build_cleanup_continues_after_normalization_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = SmokeWorkspace(root, "partial", root / "keyring")
    workspace.owns_builder = True
    workspace.built_architectures.add("amd64")
    for scenario in ("fresh", "custom"):
        state = root / "amd64" / scenario
        state.mkdir(parents=True)
        workspace.state_mounts["amd64", scenario] = state
        workspace.containers.append(workspace.container("amd64", scenario))
    workspace.containers.append(workspace.container("amd64", "memory"))
    remove_container = Mock()
    remove_image = Mock()
    remove_builder = Mock()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.remove", remove_container)
    image_exists = Mock(side_effect=lambda image: image == workspace.image("amd64"))
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.exists", image_exists)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.remove", remove_image)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.run", Mock(side_effect=RuntimeError("normalization failed")))
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.buildx.list", lambda: [SimpleNamespace(name=workspace.builder)])
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.buildx.remove", remove_builder)
    failures = workspace.cleanup()
    assert [failure.phase for failure in failures] == ["permission normalization: amd64/fresh", "permission normalization: amd64/custom"]
    assert [call.args[0] for call in remove_container.call_args_list] == [
        workspace.container("amd64", scenario) for scenario in ("fresh", "custom", "memory", "cleanup-fresh", "cleanup-custom")
    ]
    assert all(call.args == (workspace.image("amd64"),) for call in image_exists.call_args_list)
    remove_image.assert_called_once_with(workspace.image("amd64"), force=True, prune=False)
    remove_builder.assert_called_once_with(workspace.builder)
    assert not root.exists()


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


def test_borrowed_image_survives_failed_gateway_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "borrowed"
    root.mkdir()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.tempfile.mkdtemp", lambda **kwargs: str(root))
    run = Mock(side_effect=[RuntimeError("gateway launch failed"), ""])
    remove_container = Mock()
    image_remove = Mock()
    builder_list = Mock()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.run", run)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.remove", remove_container)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.exists", lambda image: image == "borrowed:arm64")
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.remove", image_remove)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.buildx.list", builder_list)

    with pytest.raises(RuntimeError, match="gateway launch failed"):
        run_gateway_smoke("borrowed:arm64", "arm64")

    assert not root.exists()
    assert [call.args for call in run.call_args_list] == [
        ("borrowed:arm64",), ("borrowed:arm64", ["-c", CLEANUP_PROBE]),
    ]
    assert all(call.kwargs["platform"] == "linux/arm64" for call in run.call_args_list)
    assert len(remove_container.call_args_list) == 2
    assert all("-arm64-" in call.args[0] for call in remove_container.call_args_list)
    image_remove.assert_not_called()
    builder_list.assert_not_called()


def test_selected_workspace_setup_creates_only_requested_architecture() -> None:
    workspace = create_workspace(("arm64",), borrowed_image="borrowed:arm64")
    try:
        assert sorted(path.name for path in workspace.root.iterdir()) == ["arm64", "gogcli-keyring-password"]
        assert sorted(path.name for path in (workspace.root / "arm64").iterdir()) == ["custom", "fresh"]
        assert workspace.image("arm64") == "borrowed:arm64"
        with pytest.raises(ValueError, match="architecture not selected"):
            workspace.image("amd64")
    finally:
        assert workspace.cleanup() == ()


@pytest.mark.parametrize("state_exists,image_exists", [(False, True), (True, False)])
def test_missing_state_or_image_does_not_launch_cleanup_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_exists: bool, image_exists: bool,
) -> None:
    root = tmp_path / "partial"
    root.mkdir()
    workspace = SmokeWorkspace(root, "partial", root / "keyring", ("arm64",), "borrowed:arm64")
    state = root / "arm64" / "fresh"
    if state_exists:
        state.mkdir(parents=True)
    workspace.state_mounts["arm64", "fresh"] = state
    run = Mock()
    remove_container = Mock()
    image_remove = Mock()
    builder_list = Mock()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.run", run)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.remove", remove_container)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.exists", lambda image: image_exists)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.remove", image_remove)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.buildx.list", builder_list)

    assert workspace.cleanup() == ()

    assert not root.exists()
    run.assert_not_called()
    remove_container.assert_not_called()
    image_remove.assert_not_called()
    builder_list.assert_not_called()


def test_memory_probe_creation_failure_still_removes_owned_container(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = create_workspace(("arm64",), borrowed_image="borrowed:arm64")
    remove_container = Mock()
    image_remove = Mock()
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.run", Mock())
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.stop", Mock())
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.wait_for_gateway", Mock())
    monkeypatch.setattr(
        "openclaw_officecli_smoke_runtime.probe_gateway",
        lambda *args: (workspace.root / "arm64" / "fresh" / CHILD_MARKER).touch() or "",
    )
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.create", Mock(side_effect=RuntimeError("memory create timed out")))
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.remove", remove_container)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.exists", lambda image: True)
    monkeypatch.setattr("openclaw_officecli_smoke_runtime.docker.image.remove", image_remove)
    try:
        with pytest.raises(RuntimeError, match="memory create timed out"):
            run_gateway_scenarios(workspace, "arm64")
    finally:
        assert workspace.cleanup() == ()

    assert workspace.container("arm64", "memory") in [call.args[0] for call in remove_container.call_args_list]
    image_remove.assert_not_called()
    assert not workspace.root.exists()
