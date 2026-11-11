import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build-openclaw-local-dockerfile.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile.openclaw"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
OPENCLAW_VERSION = "2026.11.3"
OPENCLAW_SHA256 = "sha256:dae4c4f7549561db2fe5ac43af7ba0c9491f876380f070f7ffb87bf9721a10df"
GOGCLI_VERSION = "0.48.0"
GOGCLI_ARM64_SHA256 = "sha256:fc40524368d2cb6213bdfe0b736b5884e1b55c6368706d7a285542cb7e559b68"
GOGCLI_AMD64_SHA256 = "sha256:8809d2f4fe95a0299b1a2a5c6bc912c4aba4b90a7b5e2c1f5cf11b9945a50004"
OFFICECLI_PINS = {
    "OFFICECLI_VERSION": "v1.0.160",
    "OFFICECLI_ARM64_SHA256": "e8f06aff6c59f11a73a1a1218bda3ce170299201fd56bce6d62ecd4d0c024e53",
    "OFFICECLI_AMD64_SHA256": "baa084b64ec634bd9d41a0592db3ed85f11658b358bab898679537ea7f3e73a9",
    "OFFICECLI_SKILL_SHA256": "c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4",
}
PIN_NAMES = (
    "OPENCLAW_VERSION",
    "OPENCLAW_SHA256",
    "DEBIAN_SNAPSHOT",
    "GOGCLI_VERSION",
    "GOGCLI_AMD64_SHA256",
    "GOGCLI_ARM64_SHA256",
    *OFFICECLI_PINS,
    "GOGCLI_SKILLS_COMMIT",
    "GOGCLI_SKILLS_SHA256",
    "CLAWSEC_COMMIT",
    "CLAWSEC_SHA256",
    "HUMANIZER_COMMIT",
    "HUMANIZER_SHA256",
)
ARCHIVE_PIN_NAMES = ("GOGCLI_SKILLS_COMMIT", "GOGCLI_SKILLS_SHA256", "CLAWSEC_COMMIT", "CLAWSEC_SHA256", "HUMANIZER_COMMIT", "HUMANIZER_SHA256")


def read_docker_argv(log_path: Path) -> list[str]:
    return Path(f"{log_path}.argv").read_bytes().decode().split("\0")[:-1]


def forwarded_archive_pins(argv: list[str]) -> dict[str, str]:
    pins = {
        name: value
        for flag, assignment in zip(argv, argv[1:], strict=False)
        if flag == "--build-arg"
        for name, _, value in (assignment.partition("="),)
        if name in ARCHIVE_PIN_NAMES
    }
    assert sum(argument.startswith(tuple(f"{name}=" for name in ARCHIVE_PIN_NAMES)) for argument in argv) == len(pins)
    return pins


def forwarded_pins(argv: list[str]) -> dict[str, str]:
    pins = {
        name: value
        for flag, assignment in zip(argv, argv[1:], strict=False)
        if flag == "--build-arg"
        for name, _, value in (assignment.partition("="),)
        if name in PIN_NAMES
    }
    assert sum(argument.startswith(tuple(f"{name}=" for name in PIN_NAMES)) for argument in argv) == len(pins)
    return pins


def dockerfile_arg_defaults() -> dict[str, str]:
    return {
        name: value
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("ARG ")
        for name, separator, value in (line.removeprefix("ARG ").partition("="),)
        if separator
    }


def write_env_file(path: Path, assignments: dict[str, str]) -> None:
    path.write_text("".join(f"{name}={shlex.quote(value)}\n" for name, value in assignments.items()), encoding="utf-8")


def install_fake_docker(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    log_path = root / "docker.log"
    docker_path = bin_dir / "docker"
    docker_path.write_text(
        '#!/usr/bin/env sh\nprintf \'%s\\n\' "$*" >> "$DOCKER_FAKE_LOG"\n'
        'printf \'%s\\0\' "$@" > "$DOCKER_FAKE_LOG.argv"\nexit "${DOCKER_FAKE_EXIT:-0}"\n',
        encoding="utf-8",
    )
    docker_path.chmod(docker_path.stat().st_mode | stat.S_IXUSR)
    return bin_dir, log_path


def build_env(bin_dir: Path, log_path: Path, missing_env_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ENV_FILE"] = str(missing_env_file)
    env["DOCKER_FAKE_LOG"] = str(log_path)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("MARACLAW_OPENCLAW_IMAGE", None)
    env.pop("DOCKER_PLATFORM", None)
    for name in PIN_NAMES:
        env.pop(name, None)
    return env


def run_build_script(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script path under test
        [str(BUILD_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_openclaw_build_script_uses_arm64_platform_by_default(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    command = log_path.read_text(encoding="utf-8")
    assert "--platform linux/arm64" in command
    assert "-t arm64-openclaw:local --load" in command


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize("local_image", [None, "", "workspace/openclaw:custom"])
def test_openclaw_build_script_selects_local_image_when_configured(
    tmp_path: Path, local_image: str | None, architecture: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["DOCKER_PLATFORM"] = f"linux/{architecture}"
    if local_image is not None:
        env["MARACLAW_OPENCLAW_IMAGE"] = local_image
    expected_image = f"{architecture}-{local_image or 'openclaw:local'}"

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    assert f"-t {expected_image} --load" in log_path.read_text(encoding="utf-8")
    assert result.stdout.count(expected_image) == 2


@pytest.mark.parametrize("inherited_image", [None, "process/openclaw:inherited"])
@pytest.mark.parametrize("file_image", ["workspace/openclaw:from-file", ""])
@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_openclaw_build_script_prefers_image_when_env_file_assigns_it(
    tmp_path: Path, inherited_image: str | None, file_image: str, architecture: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    env_file.write_text(
        f'MARACLAW_OPENCLAW_IMAGE="{file_image}"\nDOCKER_PLATFORM="linux/{architecture}"\n', encoding="utf-8"
    )
    env = build_env(bin_dir, log_path, env_file)
    env["DOCKER_PLATFORM"] = "linux/arm64" if architecture == "amd64" else "linux/amd64"
    if inherited_image is not None:
        env["MARACLAW_OPENCLAW_IMAGE"] = inherited_image
    expected_image = f"{architecture}-{file_image or 'openclaw:local'}"

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    assert f"-t {expected_image} --load" in log_path.read_text(encoding="utf-8")
    assert result.stdout.count(expected_image) == 2


@pytest.mark.parametrize("argument", ["--help", "-h"])
def test_openclaw_build_script_help_bypasses_env_and_docker(tmp_path: Path, argument: str) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    env_file.write_text("exit 99\n", encoding="utf-8")
    env = build_env(bin_dir, log_path, env_file)

    result = run_build_script(env, argument)

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert not log_path.exists()


@pytest.mark.parametrize("arguments", [("--unknown",), ("--help", "--unknown")])
def test_openclaw_build_script_rejects_unknown_arguments_before_env_and_docker(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    env_file.write_text("exit 99\n", encoding="utf-8")
    env = build_env(bin_dir, log_path, env_file)

    result = run_build_script(env, *arguments)

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not log_path.exists()


def test_openclaw_build_script_forwards_no_pins_by_default_so_dockerfile_defaults_apply(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    assert forwarded_pins(read_docker_argv(log_path)) == {}
    defaults = dockerfile_arg_defaults()
    assert defaults["OPENCLAW_VERSION"] == OPENCLAW_VERSION
    assert defaults["OPENCLAW_SHA256"] == OPENCLAW_SHA256


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
def test_openclaw_build_script_loads_one_target_without_attestations(tmp_path: Path, platform: str) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["DOCKER_PLATFORM"] = platform

    # When
    result = run_build_script(env)

    # Then
    command = log_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert command.count("--platform") == 1
    assert f"--platform {platform}" in command
    assert "--load" in command
    assert "--push" not in command
    assert "--provenance" not in command
    assert "--sbom" not in command


def test_environment_example_lists_dockerfile_pin_defaults_as_commented_overrides() -> None:
    # Given
    pin_names = ("OPENCLAW_VERSION", "OPENCLAW_SHA256", "DEBIAN_SNAPSHOT", "GOGCLI_VERSION", "GOGCLI_AMD64_SHA256", "GOGCLI_ARM64_SHA256", *OFFICECLI_PINS)
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()

    # When
    active = [line for line in lines if line.partition("=")[0] in PIN_NAMES]
    commented = {
        key: value
        for line in lines
        if line.startswith("# ")
        for key, separator, value in (line.removeprefix("# ").partition("="),)
        if separator and key in pin_names
    }

    # Then
    assert active == []
    assert commented == {name: dockerfile_arg_defaults()[name] for name in pin_names}
    assert "Dockerfile.openclaw" in "\n".join(lines)


def test_openclaw_build_script_passes_configured_openclaw_version_and_sha(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["OPENCLAW_VERSION"] = "2026.6.12"
    env["OPENCLAW_SHA256"] = "sha256:configured"

    # When
    result = run_build_script(env)

    # Then
    command = log_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "--build-arg OPENCLAW_VERSION=2026.6.12" in command
    assert "--build-arg OPENCLAW_SHA256=sha256:configured" in command


def test_openclaw_build_script_passes_configured_gogcli_version_and_per_arch_sha(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["GOGCLI_VERSION"] = "0.99.0"
    env["GOGCLI_AMD64_SHA256"] = "configured-amd64-sha"
    env["GOGCLI_ARM64_SHA256"] = "configured-arm64-sha"

    # When
    result = run_build_script(env)

    # Then
    command = log_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "--build-arg GOGCLI_VERSION=0.99.0" in command
    assert "--build-arg GOGCLI_AMD64_SHA256=configured-amd64-sha" in command
    assert "--build-arg GOGCLI_ARM64_SHA256=configured-arm64-sha" in command


@pytest.mark.parametrize("platform", ["linux/386", "darwin/arm64", "linux/amd64,linux/arm64"])
def test_openclaw_build_script_rejects_unsupported_or_multiple_platforms(tmp_path: Path, platform: str) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["DOCKER_PLATFORM"] = platform

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 2
    assert "exactly one supported target" in result.stderr
    assert not log_path.exists()


@pytest.mark.parametrize(
    ("architecture", "digest"),
    [
        ("amd64", "9b336a6647435ad6cb01107c1564e50eb867aebb14d0ca1dc04776f899b27e44"),
        ("arm64", "552fd665f95f93fbbba5d145807667ddbf3b62fd45d555541b62eed7310cfe19"),
    ],
)
def test_dockerfile_selects_pinned_base_for_target_architecture(architecture: str, digest: str) -> None:
    # Given
    content = DOCKERFILE.read_text(encoding="utf-8")

    # When
    lines = content.splitlines()
    argument = f"OPENCLAW_BASE_IMAGE_{architecture.upper()}"

    # Then
    assert f"ARG {argument}=nikolaik/python-nodejs:python3.15-nodejs26-slim@sha256:{digest}" in lines
    assert f"FROM ${{{argument}}} AS base_{architecture}" in lines
    assert "FROM base_${TARGETARCH}" in lines


def test_openclaw_dockerfile_pins_trixie_packages_without_hadolint_ignore() -> None:
    # Given
    content = DOCKERFILE.read_text(encoding="utf-8")

    # When
    package_install_block = "\n".join(
        line.strip()
        for line in content.splitlines()
        if "ca-certificates" in line or "curl" in line or "hadolint ignore=DL3008" in line
    )

    # Then
    assert "CA_CERTIFICATES_VERSION=20250419+deb13u1" in content
    assert "CURL_VERSION=8.14.1-2+deb13u7" in content
    assert '"ca-certificates=${CA_CERTIFICATES_VERSION}"' in package_install_block
    assert '"curl=${CURL_VERSION}"' in package_install_block
    assert "hadolint ignore=DL3008" not in package_install_block


def test_openclaw_dockerfile_uses_shell_that_preserves_bash_package_version_argument() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert content.index('SHELL ["/bin/sh", "-c"]') < content.index('"bash=${BASH_VERSION}"')


def test_openclaw_dockerfile_verifies_openclaw_npm_tarball_before_install() -> None:
    # Given
    content = DOCKERFILE.read_text(encoding="utf-8")
    install_command = "npm ci --prefix /opt/openclaw --install-strategy=shallow --omit=dev --no-audit --no-fund"
    lock_check = "sha512sum -c /tmp/openclaw.lock.sha512"

    # When
    checksum_block = "\n".join(
        line.strip()
        for line in content.splitlines()
        if "openclaw" in line.lower() or "OPENCLAW_SHA256" in line or "OPENCLAW_TARBALL" in line
    )

    # Then
    assert f"OPENCLAW_VERSION={OPENCLAW_VERSION}" in content
    assert f"OPENCLAW_SHA256={OPENCLAW_SHA256}" in content
    assert 'npm pack "openclaw@${OPENCLAW_VERSION}"' in checksum_block
    assert 'printf \'%s  %s\\n\' "${OPENCLAW_SHA256#sha256:}" "$OPENCLAW_TARBALL"' in checksum_block
    assert "sha256sum -c /tmp/openclaw.tgz.sha256" in checksum_block
    assert install_command in checksum_block
    assert lock_check in checksum_block
    assert content.index("sha256sum -c /tmp/openclaw.tgz.sha256") < content.index(lock_check) < content.index(install_command)
    assert content.index(install_command) < content.index("\nUSER node\n")
    assert "--dangerously-allow-all-scripts" not in content
    assert "npm install -g --allow-scripts" not in content
    assert 'npm install -g "openclaw@${OPENCLAW_VERSION}"' not in content


def test_openclaw_dockerfile_pins_and_verifies_gogcli_release_archive() -> None:
    # Given
    content = DOCKERFILE.read_text(encoding="utf-8")

    # When
    gogcli_block = "\n".join(
        line.strip()
        for line in content.splitlines()
        if "GOGCLI" in line or "gogcli" in line or "/usr/local/bin/gog" in line
    )

    # Then
    assert f"GOGCLI_VERSION={GOGCLI_VERSION}" in content
    assert f"GOGCLI_ARM64_SHA256={GOGCLI_ARM64_SHA256}" in content
    assert f"GOGCLI_AMD64_SHA256={GOGCLI_AMD64_SHA256}" in content
    assert (
        '"https://github.com/openclaw/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_${TARGETOS}_${TARGETARCH}.tar.gz"'
        in gogcli_block
    )
    assert "printf '%s  %s\\n' \"${GOGCLI_SHA256#sha256:}\" \"$GOGCLI_ARCHIVE\"" in gogcli_block
    assert "sha256sum -c /tmp/gogcli.tar.gz.sha256" in gogcli_block
    assert 'tar -xzf "$GOGCLI_ARCHIVE" -C /usr/local/bin ./gog' in gogcli_block
    assert "online) curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https'" in content


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_staged_local_build_passes_one_named_context_with_spaces(tmp_path: Path, architecture: str) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    assets = tmp_path / "assets with spaces"
    for name in (f"linux-{architecture}/gogcli.tar.gz", f"linux-{architecture}/officecli",
                 "common/officecli-SKILL.md", "common/gogcli-skills.tar.gz", "common/soul-guardian.tar.gz", "common/humanizer.tar.gz"):
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    env_file = tmp_path / "build.env"
    env_file.write_text(f'OPENCLAW_ASSET_MODE=staged\nOPENCLAW_ASSET_DIR="{assets}"\n')
    env = build_env(bin_dir, log_path, env_file) | {"DOCKER_PLATFORM": f"linux/{architecture}", "OPENCLAW_ASSET_MODE": "online"}
    result = run_build_script(env)
    assert result.returncode == 0, result.stderr
    args = Path(f"{log_path}.argv").read_bytes().decode().split("\0")[:-1]
    assert args[args.index("--build-context") + 1] == f"openclaw_assets={assets.resolve()}"
    assert "OPENCLAW_ASSET_MODE=staged" in args
    assert "--load" in args and "--push" not in args


@pytest.mark.parametrize("mode", ["staged", "invalid"])
def test_invalid_asset_configuration_rejected_before_build(tmp_path: Path, mode: str) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env") | {"OPENCLAW_ASSET_MODE": mode, "OPENCLAW_ASSET_DIR": str(tmp_path)}
    result = run_build_script(env)
    assert result.returncode != 0
    assert not log_path.exists()


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_staged_local_build_keeps_staged_arguments_with_archive_pins(tmp_path: Path, architecture: str) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    assets = tmp_path / "assets with spaces"
    for name in (f"linux-{architecture}/gogcli.tar.gz", f"linux-{architecture}/officecli",
                 "common/officecli-SKILL.md", "common/gogcli-skills.tar.gz", "common/soul-guardian.tar.gz", "common/humanizer.tar.gz"):
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    pins = {name: f"staged {name}" for name in ARCHIVE_PIN_NAMES}
    env = build_env(bin_dir, log_path, tmp_path / "missing.env") | pins | {
        "DOCKER_PLATFORM": f"linux/{architecture}", "OPENCLAW_ASSET_MODE": "staged", "OPENCLAW_ASSET_DIR": str(assets),
    }

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    args = read_docker_argv(log_path)
    assert forwarded_archive_pins(args) == pins
    assert args[args.index("--build-context") + 1] == f"openclaw_assets={assets.resolve()}"
    assert "OPENCLAW_ASSET_MODE=staged" in args
    assert args[args.index("--platform") + 1] == f"linux/{architecture}"
    assert args[args.index("-t") + 1] == f"{architecture}-openclaw:local"
    assert "--load" in args and "--push" not in args
