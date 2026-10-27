from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from openclaw_officecli_smoke_cleanup import CLEANUP_PROBE, CleanupFailure, run_cleanup

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
STATE_DIR: Final = "/home/node/.openclaw"
CUSTOM_STATE_DIR: Final = "/home/node/custom-state"
KEYRING_PATH: Final = "/run/secrets/gogcli_keyring_password"

DAEMON_ENV_EXEC: Final = r'''
const fs = require("node:fs");
const {spawnSync} = require("node:child_process");
const candidates = fs.readdirSync("/proc").filter(name => /^\d+$/.test(name)).flatMap(pid => {
  try {
    const command = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").replaceAll("\0", " ");
    if (!/openclaw.*gateway/.test(command)) return [];
    const entries = fs.readFileSync(`/proc/${pid}/environ`, "utf8").split("\0");
    const env = Object.fromEntries(entries.filter(entry => entry.includes("=")).map(entry => {
      const index = entry.indexOf("=");
      return [entry.slice(0, index), entry.slice(index + 1)];
    }));
    return env.GOG_KEYRING_PASSWORD ? [{pid, env}] : [];
  } catch (error) {
    if (error.code === "ENOENT" || error.code === "EACCES" || error.code === "ESRCH") return [];
    throw error;
  }
});
if (candidates.length !== 1) throw new Error(`expected one gateway process with keyring environment, found ${candidates.length}`);
console.log(`probe uses gateway PID ${candidates[0].pid} environment`);
const result = spawnSync("/bin/sh", ["-c", process.argv[1]], {env: candidates[0].env, stdio: "inherit"});
if (result.error) throw result.error;
process.exit(result.status === null ? 1 : result.status);
'''


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def describe(self) -> str:
        return (
            f"command: {shlex.join(self.command)}\nreturn code: {self.returncode}\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )


@dataclass(frozen=True, slots=True)
class RunRequest:
    architecture: str
    scenario: str
    state_dir: Path
    state_target: str


@dataclass(frozen=True, slots=True)
class SmokeWorkspace:
    root: Path
    identifier: str
    keyring_file: Path

    @property
    def builder(self) -> str:
        return f"officecli-smoke-{self.identifier}"

    def image(self, architecture: str) -> str:
        return f"maraclaw-officecli-smoke:{self.identifier}-{architecture}"

    def container(self, architecture: str, scenario: str) -> str:
        return f"maraclaw-officecli-smoke-{self.identifier}-{architecture}-{scenario}"

    def cleanup(self) -> tuple[CleanupFailure, ...]:
        operations = []
        for architecture in ("amd64", "arm64"):
            for scenario in ("fresh", "custom", "restart"):
                name = self.container(architecture, scenario)
                operations.append((f"container removal: {name}", lambda name=name: _remove_container(name)))
            for scenario in ("fresh", "custom"):
                operations.append((
                    f"permission normalization: {architecture}/{scenario}",
                    lambda architecture=architecture, scenario=scenario: _normalize_state(self, architecture, scenario),
                ))
                name = self.container(architecture, f"cleanup-{scenario}")
                operations.append((f"cleanup container removal: {name}", lambda name=name: _remove_container(name)))
            operations.append((f"image removal: {architecture}", lambda architecture=architecture: _remove_image(self.image(architecture))))
        operations.append(("builder removal", lambda: _remove_builder(self.builder)))
        operations.append(("temporary state deletion", lambda: shutil.rmtree(self.root)))
        return run_cleanup(tuple(operations))


def run_command(command: tuple[str, ...], *, timeout_seconds: int = 1_200) -> CommandResult:
    try:
        completed = subprocess.run(
            command, cwd=REPO_ROOT, capture_output=True, check=False, text=True, timeout=timeout_seconds
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise AssertionError(f"Docker command could not complete: {shlex.join(command)}: {error}") from error
    result = CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
    if directory := os.environ.get("OPENCLAW_SMOKE_EVIDENCE_DIR", ""):
        Path(directory, f"{uuid4().hex}.log").write_text(result.describe(), encoding="utf-8")
    return result


def assert_success(label: str, result: CommandResult) -> None:
    assert result.returncode == 0, f"{label} failed\n{result.describe()}"


def _remove_container(name: str) -> None:
    existing = run_command(("docker", "container", "ls", "-aq", "--filter", f"name=^/{name}$"))
    assert_success("owned container lookup", existing)
    if existing.stdout.strip():
        assert_success("owned container removal", run_command(("docker", "rm", "-f", name)))


def _image_exists(image: str) -> bool:
    result = run_command(("docker", "image", "ls", "-q", image))
    assert_success("owned image lookup", result)
    return bool(result.stdout.strip())


def _remove_image(image: str) -> None:
    if _image_exists(image):
        assert_success("image removal", run_command(("docker", "image", "rm", "-f", image)))


def _remove_builder(builder: str) -> None:
    result = run_command(("docker", "buildx", "ls", "--format", "{{.Name}}"))
    assert_success("builder lookup", result)
    if builder in result.stdout.replace("*", "").splitlines():
        assert_success("builder removal", run_command(("docker", "buildx", "rm", builder)))


def _normalize_state(workspace: SmokeWorkspace, architecture: str, scenario: str) -> None:
    if not _image_exists(workspace.image(architecture)):
        return
    assert_success("permission normalization", run_command((
        "docker", "run", "--rm", "--name", workspace.container(architecture, f"cleanup-{scenario}"),
        "--network", "none", "--platform", f"linux/{architecture}", "--user", "0",
        "--mount", f"type=bind,source={workspace.root / architecture / scenario},target={STATE_DIR}",
        "-e", f"OPENCLAW_STATE_DIR={STATE_DIR}", "--entrypoint", "sh", workspace.image(architecture),
        "-c", CLEANUP_PROBE,
    )))


def create_workspace() -> SmokeWorkspace:
    root = Path(tempfile.mkdtemp(prefix="maraclaw-officecli-smoke-"))
    try:
        root.chmod(0o755)
        for architecture in ("amd64", "arm64"):
            parent = root / architecture
            parent.mkdir(mode=0o755)
            for scenario, target in (("fresh", STATE_DIR), ("custom", CUSTOM_STATE_DIR)):
                path = parent / scenario
                path.mkdir(mode=0o777)
                path.chmod(0o777)
                config = {
                    "gateway": {"mode": "local", "auth": {"mode": "token", "token": "smoke-gateway-token"}},
                    "agents": {"defaults": {"workspace": f"{target}/workspace"}},
                    "memory": {"search": {"enabled": True, "provider": "none"}},
                    "plugins": {"slots": {"memory": "memory-core"}},
                }
                path.joinpath("openclaw.json").write_text(json.dumps(config), encoding="utf-8")
        keyring_file = root / "gogcli-keyring-password"
        keyring_file.write_text("smoke-test-keyring-password", encoding="utf-8")
        keyring_file.chmod(0o644)
        return SmokeWorkspace(root, uuid4().hex, keyring_file)
    except BaseException as error:
        try:
            shutil.rmtree(root)
        except OSError as cleanup_error:
            error.add_note(f"Temporary state deletion failed: {cleanup_error!r}")
        raise


def build_image(workspace: SmokeWorkspace, architecture: str) -> CommandResult:
    return run_command((
        "docker", "buildx", "build", "--builder", workspace.builder, "--platform", f"linux/{architecture}",
        "--load", "-f", "Dockerfile.openclaw", "-t", workspace.image(architecture), ".",
    ))


def run_container(workspace: SmokeWorkspace, request: RunRequest) -> CommandResult:
    node_architecture = "x64" if request.architecture == "amd64" else "arm64"
    return run_command((
        "docker", "run", "-d", "--name", workspace.container(request.architecture, request.scenario),
        "--platform", f"linux/{request.architecture}", "--network", "none",
        "--mount", f"type=bind,source={request.state_dir},target={request.state_target}",
        "--mount", f"type=bind,source={workspace.keyring_file},target={KEYRING_PATH},readonly",
        "-e", f"OPENCLAW_STATE_DIR={request.state_target}", "-e", "OPENCLAW_HOME=/home/node",
        "-e", f"GOG_HOME={request.state_target}/gogcli", "-e", f"GOG_KEYRING_PASSWORD_FILE={KEYRING_PATH}",
        "-e", "GOG_KEYRING_BACKEND=file", "-e", f"EXPECTED_NODE_ARCH={node_architecture}",
        workspace.image(request.architecture),
    ))


def wait_for_gateway(name: str) -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = run_command(("docker", "inspect", "--format", "{{.State.Running}}", name), timeout_seconds=10)
        assert_success("gateway container inspection", state)
        if state.stdout.strip() != "true":
            break
        health = run_command(("docker", "exec", name, "curl", "-fsS", "--max-time", "2", "http://127.0.0.1:18789/readyz"), timeout_seconds=10)
        if health.returncode == 0:
            return
        time.sleep(2)
    logs = run_command(("docker", "logs", name), timeout_seconds=20)
    raise AssertionError(f"gateway did not become ready: {name}\n{logs.describe()}")


def probe_gateway(name: str, probe: str) -> CommandResult:
    return run_command(("docker", "exec", name, "node", "-e", DAEMON_ENV_EXEC, probe), timeout_seconds=300)
