from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import uuid4

from pywhales import DockerClient, DockerException
from pywhales.exceptions import NoSuchContainer

from openclaw_officecli_smoke_cleanup import CLEANUP_PROBE, CleanupFailure, cleanup_error_message, run_cleanup
from openclaw_officecli_smoke_probe import CHILD_MARKER, MEMORY_MARKER, NATIVE_MEMORY_PROBE, PROBE

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
STATE_DIR: Final = "/home/node/custom-state"
CUSTOM_STATE_DIR: Final = "/home/node/alternate-state"
KEYRING_PATH: Final = "/run/secrets/gogcli_keyring_password"
docker = DockerClient(client_type="docker", raw_diagnostics=True)

DAEMON_ENV_EXEC: Final = r'''
const fs = require("node:fs");
const {spawnSync} = require("node:child_process");
const candidates = fs.readdirSync("/proc").filter(name => /^\d+$/.test(name)).flatMap(pid => {
  try {
    const title = fs.readFileSync(`/proc/${pid}/comm`, "utf8").trim();
    // Linux comm truncates the gateway title to 15 bytes, including under Rosetta.
    if (title !== "openclaw-gatewa") return [];
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


@dataclass(slots=True)
class SmokeWorkspace:
    root: Path
    identifier: str
    keyring_file: Path
    architectures: tuple[str, ...] = ("amd64", "arm64")
    borrowed_image: str | None = None
    owns_builder: bool = False
    built_architectures: set[str] = field(default_factory=set)
    containers: list[str] = field(default_factory=list)
    state_mounts: dict[tuple[str, str], Path] = field(default_factory=dict)

    @property
    def builder(self) -> str:
        return f"officecli-smoke-{self.identifier}"

    def image(self, architecture: str) -> str:
        if architecture not in self.architectures:
            raise ValueError(f"architecture not selected: {architecture}")
        if self.borrowed_image is not None:
            return self.borrowed_image
        return f"maraclaw-officecli-smoke:{self.identifier}-{architecture}"

    def container(self, architecture: str, scenario: str) -> str:
        return f"maraclaw-officecli-smoke-{self.identifier}-{architecture}-{scenario}"

    def cleanup(self) -> tuple[CleanupFailure, ...]:
        operations = []
        for name in self.containers:
            operations.append((f"container removal: {name}", lambda name=name: _remove_container(name)))
        for architecture, scenario in self.state_mounts:
            operations.append((
                f"permission normalization: {architecture}/{scenario}",
                lambda architecture=architecture, scenario=scenario: _normalize_state(self, architecture, scenario),
            ))
            name = self.container(architecture, f"cleanup-{scenario}")
            operations.append((
                f"cleanup container removal: {name}",
                lambda name=name: _remove_container(name) if name in self.containers else None,
            ))
        for architecture in self.architectures:
            if self.borrowed_image is None and architecture in self.built_architectures:
                operations.append((f"image removal: {architecture}", lambda architecture=architecture: _remove_image(self.image(architecture))))
        if self.owns_builder:
            operations.append(("builder removal", lambda: _remove_builder(self.builder)))
        operations.append(("temporary state deletion", lambda: shutil.rmtree(self.root)))
        return run_cleanup(tuple(operations))


def record_evidence(label: str, output: str) -> None:
    if directory := os.environ.get("OPENCLAW_SMOKE_EVIDENCE_DIR", ""):
        Path(directory, f"{uuid4().hex}.log").write_text(f"{label}\n{output}", encoding="utf-8")


def _remove_container(name: str) -> None:
    try:
        docker.remove(name, force=True, volumes=True)
    except NoSuchContainer:
        return


def _image_exists(image: str) -> bool:
    return docker.image.exists(image)


def _remove_image(image: str) -> None:
    if _image_exists(image):
        docker.image.remove(image, force=True, prune=False)


def _remove_builder(builder: str) -> None:
    if any(candidate.name == builder for candidate in docker.buildx.list()):
        docker.buildx.remove(builder)


def _normalize_state(workspace: SmokeWorkspace, architecture: str, scenario: str) -> None:
    state = workspace.state_mounts[architecture, scenario]
    if not state.is_dir() or not _image_exists(workspace.image(architecture)):
        return
    workspace.containers.append(workspace.container(architecture, f"cleanup-{scenario}"))
    docker.run(
        workspace.image(architecture), ["-c", CLEANUP_PROBE], remove=True,
        name=workspace.container(architecture, f"cleanup-{scenario}"), networks=["none"],
        platform=f"linux/{architecture}", user="0", entrypoint="sh",
        mounts=[["type=bind", f"source={state}", f"target={STATE_DIR}"]],
        envs={"OPENCLAW_STATE_DIR": STATE_DIR}, timeout=1200,
    )


def create_workspace(
    architectures: tuple[str, ...] = ("amd64", "arm64"), *, borrowed_image: str | None = None,
) -> SmokeWorkspace:
    if not architectures or len(set(architectures)) != len(architectures) or any(
        architecture not in ("amd64", "arm64") for architecture in architectures
    ):
        raise ValueError(f"unsupported smoke architectures: {architectures!r}")
    if borrowed_image is not None and (len(architectures) != 1 or not borrowed_image.strip()):
        raise ValueError("a borrowed image requires exactly one architecture and a nonempty image name")
    root = Path(tempfile.mkdtemp(prefix="maraclaw-officecli-smoke-"))
    try:
        root.chmod(0o755)
        for architecture in architectures:
            parent = root / architecture
            parent.mkdir(mode=0o755)
            for scenario, target in (("fresh", STATE_DIR), ("custom", CUSTOM_STATE_DIR)):
                path = parent / scenario
                path.mkdir(mode=0o777)
                # Shared with the container's node user; OpenClaw requires sticky protection on
                # a group/world-writable temp-root ancestor such as the state log directory's parent.
                path.chmod(0o1777)
                config = {
                    "gateway": {"mode": "local", "auth": {"mode": "token", "token": "smoke-gateway-token"}},
                    "agents": {"defaults": {"workspace": f"{target}/workspace"}},
                    "skills": {"load": {"extraDirs": ["/home/node/.openclaw/skills"]}},
                    "memory": {"search": {"enabled": True, "provider": "none"}},
                    "plugins": {"slots": {"memory": "memory-core"}},
                }
                path.joinpath("openclaw.json").write_text(json.dumps(config), encoding="utf-8")
        keyring_file = root / "gogcli-keyring-password"
        keyring_file.write_text("smoke-test-keyring-password", encoding="utf-8")
        keyring_file.chmod(0o644)
        return SmokeWorkspace(root, uuid4().hex, keyring_file, architectures, borrowed_image)
    except BaseException as error:
        try:
            shutil.rmtree(root)
        except OSError as cleanup_error:
            error.add_note(f"Temporary state deletion failed: {cleanup_error!r}")
        raise


def create_builder(workspace: SmokeWorkspace) -> None:
    if workspace.borrowed_image is not None:
        raise ValueError("cannot build a borrowed image")
    workspace.owns_builder = True
    docker.buildx.create(name=workspace.builder, driver="docker-container")


def build_image(workspace: SmokeWorkspace, architecture: str) -> None:
    if workspace.borrowed_image is not None:
        raise ValueError("cannot build a borrowed image")
    workspace.image(architecture)
    workspace.built_architectures.add(architecture)
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
    image = workspace.image(request.architecture)
    name = workspace.container(request.architecture, request.scenario)
    workspace.containers.append(name)
    state_scenario = "custom" if request.scenario == "restart" else request.scenario
    workspace.state_mounts[request.architecture, state_scenario] = request.state_dir
    docker.run(
        image, detach=True,
        name=name,
        platform=f"linux/{request.architecture}", networks=["none"],
        mounts=[
            ["type=bind", f"source={request.state_dir}", f"target={request.state_target}"],
            ["type=bind", f"source={workspace.keyring_file}", f"target={KEYRING_PATH}", "readonly"],
        ],
        envs={"HOME": "/home/node", "OPENCLAW_STATE_DIR": request.state_target, "OPENCLAW_HOME": "/home/node",
              "OPENCLAW_CONFIG_PATH": f"{request.state_target}/openclaw.json",
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


def run_gateway_scenarios(workspace: SmokeWorkspace, architecture: str) -> None:
    """Exercise the same offline gateway, skills, OfficeCLI and memory checks for every caller."""
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
        run_container(workspace, RunRequest(architecture, scenario, state, target))
        wait_for_gateway(name)
        probe = probe_gateway(name, PROBE)
        assert state.joinpath(CHILD_MARKER).is_file()
        if scenario == "fresh":
            docker.stop(name)
            memory_name = workspace.container(architecture, "memory")
            workspace.containers.append(memory_name)
            with docker.create(
                workspace.image(architecture), ["sh", "-euc", NATIVE_MEMORY_PROBE],
                name=memory_name, platform=f"linux/{architecture}",
                networks=["none"], volumes_from=[name],
                tmpfs=["/home/node/.cache:uid=1000,gid=1000,mode=0700"],
                envs={"HOME": "/home/node", "OPENCLAW_HOME": "/home/node", "OPENCLAW_STATE_DIR": target,
                      "OPENCLAW_CONFIG_PATH": f"{target}/openclaw.json"},
            ) as memory:
                probe += docker.start(memory, attach=True, timeout=300)
            assert state.joinpath(MEMORY_MARKER).is_file()
            docker.start(name)
            wait_for_gateway(name)
        docker.execute(name, ["curl", "-fsS", "--max-time", "2", "http://127.0.0.1:18789/readyz"], timeout=1200)
        sys.stdout.write(f"linux/{architecture} {scenario} ready; no model inference performed\n{probe}\n")
        docker.stop(name)
    server_arch = docker.info().architecture
    native = {"amd64": ("x86_64", "amd64"), "arm64": ("aarch64", "arm64")}
    execution = "native" if server_arch in native[architecture] else "emulated"
    sys.stdout.write(f"linux/{architecture}: {execution} execution on {server_arch}\n")


def run_gateway_smoke(image: str, architecture: str) -> None:
    """Run all smoke scenarios against a caller-owned image without building or deleting it."""
    workspace = create_workspace((architecture,), borrowed_image=image)
    try:
        run_gateway_scenarios(workspace, architecture)
    finally:
        message = cleanup_error_message(workspace.cleanup(), sys.exception())
        if message:
            raise RuntimeError(f"Gateway smoke cleanup failed:\n{message}")
