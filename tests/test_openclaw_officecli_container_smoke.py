from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator

import pytest

from openclaw_officecli_smoke_cleanup import cleanup_error_message
from openclaw_officecli_smoke_probe import CHILD_MARKER, MEMORY_MARKER, NATIVE_MEMORY_PROBE, PROBE
from openclaw_officecli_smoke_runtime import (
    CUSTOM_STATE_DIR,
    STATE_DIR,
    RunRequest,
    SmokeWorkspace,
    build_image,
    create_workspace,
    docker,
    probe_gateway,
    run_container,
    wait_for_gateway,
)


@pytest.fixture(scope="module")
def smoke_workspace() -> Iterator[SmokeWorkspace]:
    if os.environ.get("RUN_OPENCLAW_OFFICECLI_SMOKE") != "1":
        pytest.skip("set RUN_OPENCLAW_OFFICECLI_SMOKE=1 to execute both real Docker guest targets")
    host_node = subprocess.run(("node", "--version"), capture_output=True, text=True, check=True, timeout=1200)
    assert host_node.stdout.strip() == "v26.12.1"
    docker.version()
    workspace = create_workspace()
    try:
        docker.buildx.create(name=workspace.builder, driver="docker-container")
        for architecture in ("amd64", "arm64"):
            build_image(workspace, architecture)
        yield workspace
    finally:
        failures = workspace.cleanup()
        if (message := cleanup_error_message(failures, sys.exception())) not in (None, ""):
            pytest.fail(message)


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_dual_platform_offline_gateway_smoke(smoke_workspace: SmokeWorkspace, architecture: str) -> None:
    workspace = smoke_workspace
    fresh = workspace.root / architecture / "fresh"
    custom = workspace.root / architecture / "custom"
    for scenario, state, target in (
        ("fresh", fresh, STATE_DIR),
        ("custom", custom, CUSTOM_STATE_DIR),
        ("restart", custom, CUSTOM_STATE_DIR),
    ):
        name = workspace.container(architecture, scenario)
        if scenario == "restart":
            assert custom.joinpath(CHILD_MARKER).is_file()
            custom.joinpath(CHILD_MARKER).unlink()
            docker.stop(workspace.container(architecture, "custom"))
        run_container(workspace, RunRequest(architecture, scenario, state, target))
        wait_for_gateway(name)
        probe = probe_gateway(name, PROBE + (NATIVE_MEMORY_PROBE if scenario == "fresh" else ""))
        assert state.joinpath(CHILD_MARKER).is_file()
        if scenario == "fresh":
            assert state.joinpath(MEMORY_MARKER).is_file()
        docker.execute(name, ["curl", "-fsS", "--max-time", "2", "http://127.0.0.1:18789/readyz"], timeout=1200)
        sys.stdout.write(f"linux/{architecture} {scenario} ready; no model inference performed\n{probe}\n")
    server_arch = docker.info().architecture
    native = {"amd64": ("x86_64", "amd64"), "arm64": ("aarch64", "arm64")}
    execution = "native" if server_arch in native[architecture] else "emulated"
    sys.stdout.write(f"linux/{architecture}: {execution} execution on {server_arch}\n")
