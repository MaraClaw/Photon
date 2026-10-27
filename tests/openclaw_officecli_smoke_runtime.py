from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from pywhales import DockerClient, DockerException
from pywhales.exceptions import NoSuchContainer

from openclaw_officecli_smoke_cleanup import CLEANUP_PROBE, CleanupFailure, run_cleanup

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
STATE_DIR: Final = "/home/node/.openclaw"
CUSTOM_STATE_DIR: Final = "/home/node/custom-state"
KEYRING_PATH: Final = "/run/secrets/gogcli_keyring_password"
docker = DockerClient(client_type="docker", raw_diagnostics=True)

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


def record_evidence(label: str, output: str) -> None:
    if directory := os.environ.get("OPENCLAW_SMOKE_EVIDENCE_DIR", ""):
        Path(directory, f"{uuid4().hex}.log").write_text(f"{label}\n{output}", encoding="utf-8")


def _remove_container(name: str) -> None:
    try:
        docker.remove(name, force=True)
    except NoSuchContainer:
        return


def _image_exists(image: str) -> bool:
    return docker.image.exists(image)


def _remove_image(image: str) -> None:
    if _image_exists(image):
        docker.image.remove(image, force=True)


def _remove_builder(builder: str) -> None:
    if any(candidate.name == builder for candidate in docker.buildx.list()):
        docker.buildx.remove(builder)


def _normalize_state(workspace: SmokeWorkspace, architecture: str, scenario: str) -> None:
    if not _image_exists(workspace.image(architecture)):
        return
    docker.run(
        workspace.image(architecture), ["-c", CLEANUP_PROBE], remove=True,
        name=workspace.container(architecture, f"cleanup-{scenario}"), networks=["none"],
        platform=f"linux/{architecture}", user="0", entrypoint="sh",
        mounts=[["type=bind", f"source={workspace.root / architecture / scenario}", f"target={STATE_DIR}"]],
        envs={"OPENCLAW_STATE_DIR": STATE_DIR}, timeout=1200,
    )


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


def build_image(workspace: SmokeWorkspace, architecture: str) -> None:
    output: list[str] = []
    try:
        with docker.buildx.build(
            REPO_ROOT, builder=workspace.builder, platforms=[f"linux/{architecture}"], load=True,
            file=REPO_ROOT / "Dockerfile.openclaw", tags=workspace.image(architecture),
            progress="plain", stream_logs=True, timeout=1200,
        ) as logs:
            for line in logs:
                output.append(line)
    except DockerException as error:
        output.append(f"\n{error}\n")
        raise
    finally:
        record_evidence(f"linux/{architecture} image build", "".join(output))


def run_container(workspace: SmokeWorkspace, request: RunRequest) -> None:
    node_architecture = "x64" if request.architecture == "amd64" else "arm64"
    docker.run(
        workspace.image(request.architecture), detach=True,
        name=workspace.container(request.architecture, request.scenario),
        platform=f"linux/{request.architecture}", networks=["none"],
        mounts=[
            ["type=bind", f"source={request.state_dir}", f"target={request.state_target}"],
            ["type=bind", f"source={workspace.keyring_file}", f"target={KEYRING_PATH}", "readonly"],
        ],
        envs={"OPENCLAW_STATE_DIR": request.state_target, "OPENCLAW_HOME": "/home/node",
              "GOG_HOME": f"{request.state_target}/gogcli", "GOG_KEYRING_PASSWORD_FILE": KEYRING_PATH,
              "GOG_KEYRING_BACKEND": "file", "EXPECTED_NODE_ARCH": node_architecture}, timeout=1200,
    )


def wait_for_gateway(name: str) -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = docker.container.inspect(name).state
        if state is None or not state.running:
            break
        try:
            docker.execute(name, ["curl", "-fsS", "--max-time", "2", "http://127.0.0.1:18789/readyz"], timeout=10)
        except DockerException:
            time.sleep(2)
        else:
            return
    logs = docker.logs(name, timeout=20)
    record_evidence(f"gateway logs: {name}", logs)
    raise AssertionError(f"gateway did not become ready: {name}\n{logs}")


def probe_gateway(name: str, probe: str) -> str:
    try:
        output = docker.execute(name, ["node", "-e", DAEMON_ENV_EXEC, probe], timeout=300)
    except DockerException as error:
        record_evidence(f"gateway probe: {name}", str(error))
        raise
    record_evidence(f"gateway probe: {name}", output)
    return output
