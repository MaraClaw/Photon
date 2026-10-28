import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "build-openclaw-local-dockerfile.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile.openclaw"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
OPENCLAW_VERSION = "2026.10.8"
OPENCLAW_SHA256 = "sha256:7e36685a0b5fd9c8c3afcfa93ee33fad871f7c6c8b6e695cae6720d14a765f18"
GOGCLI_VERSION = "0.47.1"
GOGCLI_ARM64_SHA256 = "sha256:c677ac6abf5e187e987a87fe1b588f454b002a7d7019de81b9e8db02e9e902b6"
GOGCLI_AMD64_SHA256 = "sha256:279bc3a03c1919d774e268ef229949f45b753a0e21257f9fb315b05be7a40ea9"


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
    return env


def run_build_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script path under test
        [str(BUILD_SCRIPT)],
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
    assert "--platform linux/arm64" in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("local_image", [None, "", "workspace/openclaw:custom"])
def test_openclaw_build_script_selects_local_image_when_configured(tmp_path: Path, local_image: str | None) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    if local_image is not None:
        env["MARACLAW_OPENCLAW_IMAGE"] = local_image
    expected_image = local_image or "openclaw:local"

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    assert f"-t {expected_image} --load" in log_path.read_text(encoding="utf-8")
    assert result.stdout.count(expected_image) == 2


@pytest.mark.parametrize("inherited_image", [None, "process/openclaw:inherited"])
@pytest.mark.parametrize("file_image", ["workspace/openclaw:from-file", ""])
def test_openclaw_build_script_prefers_image_when_env_file_assigns_it(
    tmp_path: Path, inherited_image: str | None, file_image: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    env_file.write_text(f'MARACLAW_OPENCLAW_IMAGE="{file_image}"\n', encoding="utf-8")
    env = build_env(bin_dir, log_path, env_file)
    if inherited_image is not None:
        env["MARACLAW_OPENCLAW_IMAGE"] = inherited_image
    expected_image = file_image or "openclaw:local"

    # When
    result = run_build_script(env)

    # Then
    assert result.returncode == 0, result.stderr
    assert f"-t {expected_image} --load" in log_path.read_text(encoding="utf-8")
    assert result.stdout.count(expected_image) == 2


def test_openclaw_build_script_passes_default_openclaw_version_and_sha(tmp_path: Path) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")

    # When
    result = run_build_script(env)

    # Then
    command = log_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert f"--build-arg OPENCLAW_VERSION={OPENCLAW_VERSION}" in command
    assert f"--build-arg OPENCLAW_SHA256={OPENCLAW_SHA256}" in command


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
def test_openclaw_build_script_loads_one_target_with_per_arch_pins(tmp_path: Path, platform: str) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["DOCKER_PLATFORM"] = platform

    # When
    result = run_build_script(env)

    # Then
    command = log_path.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert f"--build-arg GOGCLI_VERSION={GOGCLI_VERSION}" in command
    assert f"--build-arg GOGCLI_AMD64_SHA256={GOGCLI_AMD64_SHA256}" in command
    assert f"--build-arg GOGCLI_ARM64_SHA256={GOGCLI_ARM64_SHA256}" in command
    assert command.count("--platform") == 1
    assert f"--platform {platform}" in command
    assert "--load" in command
    assert "--push" not in command


def test_environment_example_pins_openclaw_and_gogcli_releases() -> None:
    # Given
    expected_pins = {
        "OPENCLAW_VERSION": OPENCLAW_VERSION,
        "OPENCLAW_SHA256": OPENCLAW_SHA256,
        "GOGCLI_VERSION": GOGCLI_VERSION,
    }
    content = ENV_EXAMPLE.read_text(encoding="utf-8")

    # When
    example_pins = {
        key: value
        for line in content.splitlines()
        for key, separator, value in (line.partition("="),)
        if separator and key in expected_pins
    }

    # Then
    assert example_pins == expected_pins


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
        ("amd64", "0081cd01c832e6db2ddb040a3dd4c7d282c4068c57a39280c6a40b2853ec000e"),
        ("arm64", "0d79217e1afd8ec1657305749dc3eb187963ab7934e35f89ac40c5bb4a165f07"),
    ],
)
def test_dockerfile_selects_pinned_base_for_target_architecture(architecture: str, digest: str) -> None:
    # Given
    content = DOCKERFILE.read_text(encoding="utf-8")

    # When
    lines = content.splitlines()
    argument = f"OPENCLAW_BASE_IMAGE_{architecture.upper()}"

    # Then
    assert f"ARG {argument}=nikolaik/python-nodejs@sha256:{digest}" in lines
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
    install_command = 'npm install -g --allow-scripts="$OPENCLAW_TARBALL" "$OPENCLAW_TARBALL"'

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
    assert content.index("sha256sum -c /tmp/openclaw.tgz.sha256") < content.index(install_command)
    assert content.index(install_command) < content.index("\nUSER node\n")
    assert "--dangerously-allow-all-scripts" not in content
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


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_staged_local_build_passes_one_named_context_with_spaces(tmp_path: Path, architecture: str) -> None:
    bin_dir, log_path = install_fake_docker(tmp_path)
    assets = tmp_path / "assets with spaces"
    for name in (f"linux-{architecture}/gogcli.tar.gz", f"linux-{architecture}/officecli",
                 "common/officecli-SKILL.md", "common/gogcli-skills.tar.gz", "common/soul-guardian.tar.gz"):
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
