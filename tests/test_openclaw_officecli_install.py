import hashlib
import os
import shlex
import subprocess
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-officecli.sh"
PINS: Final = {
    "amd64": "9ee41d5558caea7bcd5aab1b3e76b2bd70f0535f09cf65dda3668130510cc719",
    "arm64": "b1d5e74d93a8471a47b037cc7774ac29c559940e226f99b9fdf788966cc395b8",
}
SKILL_PIN: Final = "c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4"


@pytest.fixture
def install_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    destination = tmp_path / "system bin"
    destination.mkdir()
    npm_root = tmp_path / "npm root"
    npm_root.mkdir()
    binary = b'#!/bin/sh\nprintf "executed\\n" >> "$EXEC_LOG"\n'
    arm_binary = binary + b'# arm64\n'
    skill = b"---\nname: officecli\n---\n"
    (tmp_path / "binary").write_bytes(binary)
    (tmp_path / "binary-arm64").write_bytes(arm_binary)
    (tmp_path / "skill").write_bytes(skill)
    source = INSTALLER.read_text()
    source = source.replace(PINS["amd64"], hashlib.sha256(binary).hexdigest())
    source = source.replace(PINS["arm64"], hashlib.sha256(arm_binary).hexdigest())
    source = source.replace(SKILL_PIN, hashlib.sha256(skill).hexdigest())
    source = source.replace("/usr/local/bin/officecli", shlex.quote(str(destination / "officecli")))
    script = tmp_path / "installer.sh"
    script.write_text(source)
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CURL_LOG"\n'
        'while [ "$1" != "-o" ]; do shift; done\nout="$2"; url="$3"\n'
        'case "$url" in */SKILL.md) file=skill;; */officecli-linux-arm64) file=binary-arm64;; *) file=binary;; esac\n'
        '[ "${FAIL_DOWNLOAD:-}" != "$file" ] || exit 22\n'
        'cp "$FIXTURES/$file" "$out"\n'
    )
    curl.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\nprintf "%s\\n" "$NPM_ROOT"\n')
    npm.chmod(0o755)
    env = os.environ | {
        "PATH": f'{bin_dir}:{os.environ["PATH"]}',
        "TARGETOS": "linux",
        "TARGETARCH": "amd64",
        "FIXTURES": str(tmp_path),
        "NPM_ROOT": str(npm_root),
        "EXEC_LOG": str(tmp_path / "executed"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "TMPDIR": str(tmp_path),
    }
    return script, env


def run_installer(fixture: tuple[Path, dict[str, str]], **changes: str) -> subprocess.CompletedProcess[str]:
    script, environment = fixture
    return subprocess.run(["/bin/sh", str(script)], env=environment | changes, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(("architecture", "asset"), [("amd64", "officecli-linux-x64"), ("arm64", "officecli-linux-arm64")])
def test_pinned_artifacts_install_for_each_target(install_fixture: tuple[Path, dict[str, str]], architecture: str, asset: str) -> None:
    script, environment = install_fixture
    result = run_installer(install_fixture, TARGETARCH=architecture)
    assert result.returncode == 0, result.stderr
    root = script.parent
    fixture_name = "binary-arm64" if architecture == "arm64" else "binary"
    assert (root / "system bin/officecli").read_bytes() == (root / fixture_name).read_bytes()
    assert (root / "npm root/openclaw/skills/officecli/SKILL.md").read_bytes() == (root / "skill").read_bytes()
    assert (root / "system bin/officecli").stat().st_mode & 0o777 == 0o755
    assert (root / "npm root/openclaw/skills/officecli/SKILL.md").stat().st_mode & 0o777 == 0o644
    lines = Path(environment["CURL_LOG"]).read_text().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(f"/v1.0.158/{asset}")
    assert lines[1].endswith("/v1.0.158/SKILL.md")
    for line in lines:
        assert line.startswith("--disable --config /dev/null ")
        assert "--proto =https --proto-redir =https" in line
        assert "--noproxy *" in line


@pytest.mark.parametrize("artifact", ["binary", "skill"])
@pytest.mark.parametrize("failure", ["download", "digest"])
def test_untrusted_artifact_fails_before_binary_execution(install_fixture: tuple[Path, dict[str, str]], artifact: str, failure: str) -> None:
    script, environment = install_fixture
    if failure == "digest":
        (script.parent / artifact).write_text('malicious content\n')
    result = run_installer(install_fixture, FAIL_DOWNLOAD=artifact if failure == "download" else "")
    assert result.returncode != 0
    assert not Path(environment["EXEC_LOG"]).exists()
    assert not (script.parent / "system bin/officecli").exists()


def test_other_architecture_binary_is_rejected(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, environment = install_fixture
    (script.parent / "binary-arm64").write_bytes((script.parent / "binary").read_bytes())
    result = run_installer(install_fixture, TARGETARCH="arm64")
    assert result.returncode != 0
    assert not Path(environment["EXEC_LOG"]).exists()


@pytest.mark.parametrize(("system", "arch"), [("linux", "riscv64"), ("darwin", "arm64"), ("", "amd64")])
def test_unsupported_target_fails_without_download(install_fixture: tuple[Path, dict[str, str]], system: str, arch: str) -> None:
    _, environment = install_fixture
    result = run_installer(install_fixture, TARGETOS=system, TARGETARCH=arch)
    assert result.returncode != 0
    assert not Path(environment["CURL_LOG"]).exists()


def test_committed_pins_are_not_overridable() -> None:
    source = INSTALLER.read_text()
    assert 'OFFICECLI_TAG="v1.0.158"' in source
    for arch, pin in PINS.items():
        assert f'OFFICECLI_{arch.upper()}_SHA256="{pin}"' in source
    assert f'OFFICECLI_SKILL_SHA256="{SKILL_PIN}"' in source
    assert "readonly OFFICECLI_TAG OFFICECLI_ARM64_SHA256 OFFICECLI_AMD64_SHA256 OFFICECLI_SKILL_SHA256" in source
