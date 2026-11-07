import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DOCKERFILE: Final = REPO_ROOT / "Dockerfile.openclaw"
REQUIRED_ARGUMENTS: Final = (
    ("BASH_VERSION", "5.2.37-2+b10"),
    ("CURL_VERSION", "8.14.1-2+deb13u7"),
    ("LIBCURL4_VERSION", "8.14.1-2+deb13u7"),
    ("LIBICU76_VERSION", "76.1-4"),
)
REQUIRED_INSTALLS: Final = (
    "bash=${BASH_VERSION}",
    "curl=${CURL_VERSION}",
    "libcurl4t64=${LIBCURL4_VERSION}",
    "libicu76=${LIBICU76_VERSION}",
)
VALID_TRIXIE_PIN_BLOCK: Final = """\
ARG BASH_VERSION=5.2.37-2+b10
ARG CURL_VERSION=8.14.1-2+deb13u7
ARG LIBCURL4_VERSION=8.14.1-2+deb13u7
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
    "bash=${BASH_VERSION}" \\
    "curl=${CURL_VERSION}" \\
    "libcurl4t64=${LIBCURL4_VERSION}" \\
    && rm -rf /var/lib/apt/lists/*
"""
VALID_SNAPSHOT_BLOCK: Final = """\
ARG DEBIAN_SNAPSHOT=20261027T000000Z
RUN sed -i \\
        -e "s#http://deb.debian.org/debian-security#https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}#" \\
        -e "s#http://deb.debian.org/debian#https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}#" \\
        -e '/^URIs:/a Check-Valid-Until: no' \\
        /etc/apt/sources.list.d/debian.sources \\
    && apt-get -o Acquire::Retries=3 update \\
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends curl
"""


def _argument_values(source: str) -> dict[str, str]:
    arguments: dict[str, str] = {}
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line.startswith("ARG "):
            continue
        name, separator, value = line.removeprefix("ARG ").partition("=")
        if separator:
            arguments[name] = value
    return arguments


def _apt_install_packages(source: str) -> tuple[str, ...]:
    install = re.search(r"apt-get\b[^\n]*\binstall -y --no-install-recommends", source)
    install_start = install.start() if install else -1
    cleanup_start = source.find("&& rm -rf /var/lib/apt/lists/*", install_start)
    if install_start < 0 or cleanup_start < 0:
        return ()
    return tuple(
        line.strip().removesuffix("\\").strip().strip('"')
        for line in source[install_start:cleanup_start].splitlines()
        if line.lstrip().startswith('"')
    )


def _trixie_pin_errors(source: str) -> tuple[str, ...]:
    arguments = _argument_values(source)
    packages = _apt_install_packages(source)
    errors = [f"{name} must equal {value}" for name, value in REQUIRED_ARGUMENTS if arguments.get(name) != value]
    errors.extend(f"missing exact {package} install" for package in REQUIRED_INSTALLS if package not in packages)
    if arguments.get("CURL_VERSION") != arguments.get("LIBCURL4_VERSION"):
        errors.append("curl and libcurl4 versions must match")
    return tuple(errors)


def _snapshot_errors(source: str) -> tuple[str, ...]:
    errors: list[str] = []
    snapshot = _argument_values(source).get("DEBIAN_SNAPSHOT", "")
    if not re.fullmatch(r"\d{8}T\d{6}Z", snapshot):
        errors.append("DEBIAN_SNAPSHOT must be a YYYYMMDDTHHMMSSZ timestamp")
    update_start = source.find("apt-get ")
    rewrite_start = source.find("/etc/apt/sources.list.d/debian.sources")
    if not 0 <= rewrite_start < update_start:
        errors.append("apt sources must be rewritten before apt-get runs")
    for suite in ("debian", "debian-security"):
        if f"https://snapshot.debian.org/archive/{suite}/${{DEBIAN_SNAPSHOT}}" not in source:
            errors.append(f"missing {suite} snapshot URI")
    if "Check-Valid-Until: no" not in source:
        errors.append("snapshot sources must disable Check-Valid-Until")
    if len(re.findall(r"apt-get -o Acquire::Retries=3 (?:update|install)\b", source)) != 2:
        errors.append("apt-get update and install must both retry")
    return tuple(errors)


def test_openclaw_dockerfile_uses_approved_trixie_package_pins() -> None:
    # Given
    source = DOCKERFILE.read_text(encoding="utf-8")

    # When
    errors = _trixie_pin_errors(source)

    # Then
    assert errors == ()


def test_trixie_pin_contract_rejects_stale_or_unpinned_package_changes() -> None:
    # Given
    regression_cases = (
        ("BASH_VERSION=5.2.37-2+b10", "BASH_VERSION=5.2.37-2+b2", "BASH_VERSION"),
        ("CURL_VERSION=8.14.1-2+deb13u7", "CURL_VERSION=8.14.1-2+deb13u5", "CURL_VERSION"),
        ('    "libcurl4t64=${LIBCURL4_VERSION}" \\\n', "", "libcurl4t64=${LIBCURL4_VERSION}"),
        ("LIBCURL4_VERSION=8.14.1-2+deb13u7", "LIBCURL4_VERSION=8.14.1-2+deb13u5", "versions must match"),
        ('"curl=${CURL_VERSION}"', '"curl"', "curl=${CURL_VERSION}"),
    )

    for needle, replacement, expected_error in regression_cases:
        candidate = VALID_TRIXIE_PIN_BLOCK.replace(needle, replacement, 1)
        assert candidate != VALID_TRIXIE_PIN_BLOCK

        # When
        errors = _trixie_pin_errors(candidate)

        # Then
        assert any(expected_error in error for error in errors)


def test_openclaw_dockerfile_resolves_apt_from_the_pinned_debian_snapshot() -> None:
    # Given
    source = DOCKERFILE.read_text(encoding="utf-8")

    # When
    errors = _snapshot_errors(source)

    # Then
    assert errors == ()


def test_snapshot_contract_rejects_floating_or_unrewritten_apt_sources() -> None:
    # Given
    regression_cases = (
        ("ARG DEBIAN_SNAPSHOT=20261027T000000Z", "ARG DEBIAN_SNAPSHOT=latest", "YYYYMMDDTHHMMSSZ"),
        ("debian-security/${DEBIAN_SNAPSHOT}", "debian-security", "debian-security snapshot URI"),
        ("Check-Valid-Until: no", "Check-Valid-Until: yes", "Check-Valid-Until"),
        ("apt-get -o Acquire::Retries=3 install", "apt-get install", "both retry"),
    )
    assert _snapshot_errors(VALID_SNAPSHOT_BLOCK) == ()

    for needle, replacement, expected_error in regression_cases:
        candidate = VALID_SNAPSHOT_BLOCK.replace(needle, replacement, 1)
        assert candidate != VALID_SNAPSHOT_BLOCK

        # When
        errors = _snapshot_errors(candidate)

        # Then
        assert any(expected_error in error for error in errors)

    # And an apt run that precedes the sources rewrite is rejected.
    unrewritten = "RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y\n" + VALID_SNAPSHOT_BLOCK
    assert any("before apt-get" in error for error in _snapshot_errors(unrewritten))


@pytest.mark.parametrize("target", ["linux/amd64", "linux/arm64", "linux/386", "darwin/arm64"])
def test_dockerfile_target_selection_uses_buildkit_arguments_not_host_architecture(target: str) -> None:
    # Given
    source = DOCKERFILE.read_text()
    stage = source.split("FROM base_${TARGETARCH}", 1)[1]
    assert "ARG TARGETOS\n" in stage
    assert "ARG TARGETARCH\n" in stage
    selection = source.split("case ", 1)[1].split("esac", 1)[0]
    operating_system, architecture = target.split("/")
    arguments = _argument_values(source)

    # When
    result = subprocess.run(  # noqa: S603 - execute only the target selection, never the download/build.
        ["/bin/sh", "-c", f"case {selection}esac\nprintf '%s' \"$GOGCLI_SHA256\""],
        env={"TARGETOS": operating_system, "TARGETARCH": architecture, **arguments},
        text=True,
        capture_output=True,
        check=False,
    )

    # Then
    if target in {"linux/amd64", "linux/arm64"}:
        expected = {
            "amd64": "sha256:279bc3a03c1919d774e268ef229949f45b753a0e21257f9fb315b05be7a40ea9",
            "arm64": "sha256:c677ac6abf5e187e987a87fe1b588f454b002a7d7019de81b9e8db02e9e902b6",
        }
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected[architecture]
    else:
        assert result.returncode != 0
        assert "unsupported guest target" in result.stderr
