import os
import subprocess
from pathlib import Path

import pytest

from test_openclaw_build_script import (
    ARCHIVE_PIN_NAMES,
    GOGCLI_AMD64_SHA256,
    GOGCLI_ARM64_SHA256,
    OFFICECLI_PINS,
    forwarded_archive_pins,
    install_fake_docker,
    read_docker_argv,
    write_env_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_SCRIPT = REPO_ROOT / "publish-openclaw-local-dockerfile.sh"
OPENCLAW_VERSION = "2026.10.8"


def build_env(bin_dir: Path, log_path: Path, missing_env_file: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for key in ("DOCKERHUB_NAMESPACE", "OPENCLAW_PUBLISH_IMAGE", "IMAGE_TAG", "PUSH_LATEST", "DOCKER_PLATFORM"):
        environment.pop(key, None)
    for key in (*OFFICECLI_PINS, *ARCHIVE_PIN_NAMES):
        environment.pop(key, None)
    environment.update(
        ENV_FILE=str(missing_env_file),
        DOCKER_FAKE_LOG=str(log_path),
        PATH=f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}",
    )
    return environment


def run_publish_script(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script with Docker replaced by command capture.
        [str(PUBLISH_SCRIPT)], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("script", [PUBLISH_SCRIPT, REPO_ROOT / "build-openclaw-local-dockerfile.sh"])
@pytest.mark.parametrize("source", ["default", "process", "file"])
def test_officecli_pins_are_forwarded_from_selected_source(tmp_path: Path, script: Path, source: str) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    environment = build_env(bin_dir, log_path, env_file) | {"DOCKERHUB_NAMESPACE": "acme"}
    process_pins = {name: f"process-{name}" for name in OFFICECLI_PINS}
    file_pins = {name: f"file-{name}" for name in OFFICECLI_PINS}
    if source != "default":
        environment.update(process_pins)
    if source == "file":
        env_file.write_text("".join(f"{name}={value}\n" for name, value in file_pins.items()))
    expected = {"default": OFFICECLI_PINS, "process": process_pins, "file": file_pins}[source]

    result = subprocess.run([str(script)], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    args = Path(f"{log_path}.argv").read_bytes().decode().split("\0")[:-1]
    for name, value in expected.items():
        assert args[args.index(f"{name}={value}") - 1] == "--build-arg"


@pytest.mark.parametrize("script", [PUBLISH_SCRIPT, REPO_ROOT / "build-openclaw-local-dockerfile.sh"])
@pytest.mark.parametrize("name", ARCHIVE_PIN_NAMES)
@pytest.mark.parametrize(
    ("inherited", "from_file", "expected"),
    [
        (None, None, None),
        ("", None, None),
        ("process value; $HOME 'quoted'", None, "process value; $HOME 'quoted'"),
        (None, "file value; $HOME 'quoted'", "file value; $HOME 'quoted'"),
        ("process value", "file value", "file value"),
        ("process value", "", None),
        (None, "", None),
    ],
)
def test_each_archive_pin_is_forwarded_alone_only_when_nonempty_after_file_precedence(
    tmp_path: Path, script: Path, name: str, inherited: str | None, from_file: str | None, expected: str | None
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "pins.env"
    environment = build_env(bin_dir, log_path, env_file) | {"DOCKERHUB_NAMESPACE": "acme"}
    if inherited is not None:
        environment[name] = inherited
    if from_file is not None:
        write_env_file(env_file, {name: from_file})

    # When
    result = subprocess.run([str(script)], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False)  # noqa: S603

    # Then
    assert result.returncode == 0, result.stderr
    assert forwarded_archive_pins(read_docker_argv(log_path)) == ({} if expected is None else {name: expected})


@pytest.mark.parametrize("script", [PUBLISH_SCRIPT, REPO_ROOT / "build-openclaw-local-dockerfile.sh"])
def test_archive_pins_combine_process_and_file_sources_per_variable(tmp_path: Path, script: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "pins.env"
    write_env_file(env_file, {"GOGCLI_SKILLS_SHA256": "file-sha", "CLAWSEC_COMMIT": ""})
    environment = build_env(bin_dir, log_path, env_file) | {
        "DOCKERHUB_NAMESPACE": "acme",
        "GOGCLI_SKILLS_COMMIT": "process-commit",
        "GOGCLI_SKILLS_SHA256": "process-sha",
        "CLAWSEC_COMMIT": "process-clawsec-commit",
        "CLAWSEC_SHA256": "process-clawsec-sha",
    }

    # When
    result = subprocess.run([str(script)], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False)  # noqa: S603

    # Then
    assert result.returncode == 0, result.stderr
    assert forwarded_archive_pins(read_docker_argv(log_path)) == {
        "GOGCLI_SKILLS_COMMIT": "process-commit",
        "GOGCLI_SKILLS_SHA256": "file-sha",
        "CLAWSEC_SHA256": "process-clawsec-sha",
    }


@pytest.mark.parametrize(
    ("configuration", "error"),
    [
        ({}, "DOCKERHUB_NAMESPACE"),
        ({"OPENCLAW_PUBLISH_IMAGE": "MyUser/OpenClaw"}, "must be lowercase"),
        ({"OPENCLAW_PUBLISH_IMAGE": "nonamespace"}, "namespace/name"),
        ({"DOCKERHUB_NAMESPACE": "acme", "IMAGE_TAG": "bad/tag"}, "not a valid Docker tag"),
    ],
)
def test_invalid_publication_is_rejected_before_docker(
    tmp_path: Path, configuration: dict[str, str], error: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    environment = build_env(bin_dir, log_path, tmp_path / "missing.env") | configuration

    # When
    result = run_publish_script(environment)

    # Then
    assert result.returncode == 2
    assert error in result.stderr
    assert not log_path.exists()


@pytest.mark.parametrize("push_latest", [None, "0", "true", "1"])
@pytest.mark.parametrize("local_platform", ["linux/amd64", "linux/arm64"])
def test_publisher_pushes_both_targets_in_one_build_without_local_load(
    tmp_path: Path, push_latest: str | None, local_platform: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    environment = build_env(bin_dir, log_path, tmp_path / "missing.env")
    environment.update(DOCKERHUB_NAMESPACE="acme", DOCKER_PLATFORM=local_platform)
    if push_latest is not None:
        environment["PUSH_LATEST"] = push_latest

    # When
    result = run_publish_script(environment)

    # Then
    assert result.returncode == 0, result.stderr
    commands = log_path.read_text().splitlines()
    assert len(commands) == 1
    command = commands[0]
    assert command.startswith("buildx build --platform linux/amd64,linux/arm64 ")
    assert f"-t acme/openclaw:{OPENCLAW_VERSION}" in command
    assert ("-t acme/openclaw:latest" in command) == (push_latest == "1")
    assert "--push" in command
    assert "--load" not in command
    assert "login" not in command
    assert f"--build-arg GOGCLI_AMD64_SHA256={GOGCLI_AMD64_SHA256}" in command
    assert f"--build-arg GOGCLI_ARM64_SHA256={GOGCLI_ARM64_SHA256}" in command
    assert "GOG_KEYRING_PASSWORD" not in command


def test_publisher_prefers_env_file_repository_tag_and_pins(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "publish.env"
    env_file.write_text(
        'OPENCLAW_PUBLISH_IMAGE="file/guest"\nIMAGE_TAG="custom-tag"\n'
        'OPENCLAW_VERSION="2026.10.8"\nGOGCLI_AMD64_SHA256="custom-amd64"\n'
    )
    environment = build_env(bin_dir, log_path, env_file)
    environment.update(OPENCLAW_PUBLISH_IMAGE="process/guest", IMAGE_TAG="inherited")

    # When
    result = run_publish_script(environment)

    # Then
    assert result.returncode == 0, result.stderr
    command = log_path.read_text()
    assert "-t file/guest:custom-tag" in command
    assert "--build-arg GOGCLI_AMD64_SHA256=custom-amd64" in command
    assert "process/guest" not in command


@pytest.mark.parametrize("script", [PUBLISH_SCRIPT, REPO_ROOT / "build-openclaw-local-dockerfile.sh"])
def test_docker_failure_propagates_without_false_success(tmp_path: Path, script: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    environment = build_env(bin_dir, log_path, tmp_path / "missing.env")
    environment.update(DOCKERHUB_NAMESPACE="acme", DOCKER_FAKE_EXIT="41")
    environment.update({name: f"override-{name}" for name in ARCHIVE_PIN_NAMES})

    # When
    result = subprocess.run(  # noqa: S603 - fixed scripts with captured Docker invocation, no registry access.
        [str(script)], cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False
    )

    # Then
    assert result.returncode == 41
    assert len(log_path.read_text().splitlines()) == 1
    assert "Successfully" not in result.stdout
    assert "Published" not in result.stdout


@pytest.mark.parametrize("missing", [None, "linux-amd64/officecli", "linux-arm64/gogcli.tar.gz", "common/soul-guardian.tar.gz"])
def test_staged_publish_requires_both_targets_before_docker(tmp_path: Path, missing: str | None) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    assets = tmp_path / "assets with spaces"
    for name in ("linux-amd64/gogcli.tar.gz", "linux-amd64/officecli", "linux-arm64/gogcli.tar.gz",
                 "linux-arm64/officecli", "common/officecli-SKILL.md", "common/gogcli-skills.tar.gz", "common/soul-guardian.tar.gz"):
        if name != missing:
            path = assets / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    env = build_env(bin_dir, log_path, tmp_path / "missing.env") | {
        "DOCKERHUB_NAMESPACE": "acme", "OPENCLAW_ASSET_MODE": "staged", "OPENCLAW_ASSET_DIR": str(assets),
    }
    result = run_publish_script(env)
    if missing is not None:
        assert result.returncode != 0
        assert not log_path.exists()
    else:
        assert result.returncode == 0, result.stderr
        args = Path(f"{log_path}.argv").read_bytes().decode().split("\0")[:-1]
        assert args[args.index("--build-context") + 1] == f"openclaw_assets={assets.resolve()}"
        assert "OPENCLAW_ASSET_MODE=staged" in args
        assert "--push" in args and "--load" not in args


def test_staged_publish_keeps_staged_arguments_and_tags_with_archive_pins(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    assets = tmp_path / "assets with spaces"
    for name in ("linux-amd64/gogcli.tar.gz", "linux-amd64/officecli", "linux-arm64/gogcli.tar.gz",
                 "linux-arm64/officecli", "common/officecli-SKILL.md", "common/gogcli-skills.tar.gz", "common/soul-guardian.tar.gz"):
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    pins = {name: f"staged {name}" for name in ARCHIVE_PIN_NAMES}
    env = build_env(bin_dir, log_path, tmp_path / "missing.env") | pins | {
        "DOCKERHUB_NAMESPACE": "acme", "PUSH_LATEST": "1", "OPENCLAW_ASSET_MODE": "staged", "OPENCLAW_ASSET_DIR": str(assets),
    }

    # When
    result = run_publish_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    args = read_docker_argv(log_path)
    assert forwarded_archive_pins(args) == pins
    assert args[args.index("--build-context") + 1] == f"openclaw_assets={assets.resolve()}"
    assert "OPENCLAW_ASSET_MODE=staged" in args
    assert args[args.index("--platform") + 1] == "linux/amd64,linux/arm64"
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "-t"] == [
        f"acme/openclaw:{OPENCLAW_VERSION}", "acme/openclaw:latest",
    ]
    assert "--push" in args and "--load" not in args
