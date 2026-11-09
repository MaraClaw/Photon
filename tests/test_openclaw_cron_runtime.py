from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final
from uuid import uuid4

import pytest

HARNESS: Final = Path(__file__).with_name("openclaw_cron_context_qa.mjs")


def test_cron_maintenance_keeps_lifecycle_context_after_preparation_expires(tmp_path: Path) -> None:
    """Run the installed package offline, with no user state or credentials mounted."""
    image = os.environ.get("OPENCLAW_CRON_QA_IMAGE")
    if image is None:
        return pytest.skip("set OPENCLAW_CRON_QA_IMAGE to run the actual-package Docker regression")
    assert image.strip(), "OPENCLAW_CRON_QA_IMAGE must name a local Docker image"
    name = f"openclaw-cron-qa-{uuid4().hex}"
    env = os.environ | {
        "TMPDIR": str(tmp_path),
        "NODE_COMPILE_CACHE": str(tmp_path / "node-compile-cache"),
        "BUN_RUNTIME_TRANSPILER_CACHE_PATH": str(tmp_path / "bun-transpiler"),
    }
    command = [
        "docker", "run", "--rm", "--pull=never", "--name", name,
        "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,mode=1777,size=512m",
        "--env", "HOME=/tmp/cron-home", "--env", "OPENCLAW_HOME=/tmp/cron-home",
        "--env", "OPENCLAW_STATE_DIR=/tmp/cron-state",
        "--env", "OPENCLAW_CONFIG_PATH=/tmp/cron-state/openclaw.json",
        "--env", "TMPDIR=/tmp", "--env", "NODE_COMPILE_CACHE=/tmp/node-compile-cache",
        "--env", "BUN_RUNTIME_TRANSPILER_CACHE_PATH=/tmp/bun-transpiler",
        "--mount", f"type=bind,source={HARNESS},target=/cron-context-qa.mjs,readonly",
        "--entrypoint", "node", image, "/cron-context-qa.mjs",
    ]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as error:
        cleanup = subprocess.run(
            ["docker", "rm", "--force", name], env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        return pytest.fail(
            f"Cron runtime timed out in {image}: {error.stdout!r}\n{error.stderr!r}\n"
            f"Owned container cleanup ({cleanup.returncode}): {cleanup.stdout}{cleanup.stderr}"
        )
    output = result.stdout + result.stderr
    print(output)
    assert result.returncode == 0, f"Cron runtime failed in {image} (exit {result.returncode}):\n{output}"
