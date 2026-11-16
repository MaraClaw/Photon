from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

from openclaw_officecli_smoke_cleanup import cleanup_error_message
from openclaw_officecli_smoke_runtime import (
    SmokeWorkspace,
    build_image,
    create_builder,
    create_workspace,
    docker,
    run_gateway_scenarios,
)


@pytest.fixture(scope="module")
def smoke_workspace() -> Iterator[SmokeWorkspace]:
    if os.environ.get("RUN_OPENCLAW_OFFICECLI_SMOKE") != "1":
        pytest.skip("set RUN_OPENCLAW_OFFICECLI_SMOKE=1 to execute both real Docker guest targets")
    docker.version()
    workspace = create_workspace()
    try:
        create_builder(workspace)
        for architecture in ("amd64", "arm64"):
            build_image(workspace, architecture)
        yield workspace
    finally:
        failures = workspace.cleanup()
        if (message := cleanup_error_message(failures, sys.exception())) not in (None, ""):
            pytest.fail(message)


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_dual_platform_offline_gateway_smoke(smoke_workspace: SmokeWorkspace, architecture: str) -> None:
    run_gateway_scenarios(smoke_workspace, architecture)
