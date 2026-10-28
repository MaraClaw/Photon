import hashlib
import os
import shlex
import subprocess
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-officecli.sh"


@pytest.fixture
def install_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    destination = tmp_path / "system bin"
    destination.mkdir()
    npm_root = tmp_path / "npm root"
    npm_root.mkdir()
    binary = b'#!/bin/sh\nprintf "executed\\n" >> "$EXEC_LOG"\nexit "${FAIL_VERSION:-0}"\n'
    arm_binary = binary + b'# arm64\n'
    skill = b"---\nname: officecli\n---\n"
    (tmp_path / "binary").write_bytes(binary)
    (tmp_path / "binary-arm64").write_bytes(arm_binary)
    (tmp_path / "skill").write_bytes(skill)
    source = INSTALLER.read_text()
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
        "OFFICECLI_VERSION": "",
        "OFFICECLI_AMD64_SHA256": hashlib.sha256(binary).hexdigest(),
        "OFFICECLI_ARM64_SHA256": hashlib.sha256(arm_binary).hexdigest(),
        "OFFICECLI_SKILL_SHA256": hashlib.sha256(skill).hexdigest(),
        "FIXTURES": str(tmp_path),
        "NPM_ROOT": str(npm_root),
        "EXEC_LOG": str(tmp_path / "executed"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "TMPDIR": str(tmp_path),
    }
    return script, env


def run_installer(fixture: tuple[Path, dict[str, str]], **changes: str) -> subprocess.CompletedProcess[str]:
    script, environment = fixture
    return subprocess.run(["/bin/sh", str(script)], env=environment | changes, capture_output=True, text=True, check=False, timeout=15)


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
    assert lines[0].endswith(f"/v1.0.159/{asset}")
    assert lines[1].endswith("/v1.0.159/SKILL.md")
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


@pytest.mark.parametrize(("architecture", "asset"), [("amd64", "officecli-linux-x64"), ("arm64", "officecli-linux-arm64")])
@pytest.mark.parametrize("prefix", ["", "sha256:"])
def test_custom_pins_install_and_select_versioned_urls(
    install_fixture: tuple[Path, dict[str, str]], architecture: str, asset: str, prefix: str,
) -> None:
    _, environment = install_fixture
    binary_pin = f"OFFICECLI_{architecture.upper()}_SHA256"
    result = run_installer(
        install_fixture, TARGETARCH=architecture, OFFICECLI_VERSION="v9.8.7",
        **{binary_pin: prefix + environment[binary_pin], "OFFICECLI_SKILL_SHA256": prefix + environment["OFFICECLI_SKILL_SHA256"]},
    )
    assert result.returncode == 0, result.stderr
    lines = Path(environment["CURL_LOG"]).read_text().splitlines()
    assert lines[0].endswith(f"/v9.8.7/{asset}")
    assert lines[1].endswith("/v9.8.7/SKILL.md")


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize("artifact", ["binary", "skill"])
@pytest.mark.parametrize("prefix", ["", "sha256:"])
def test_wrong_custom_pin_is_rejected_before_execution(
    install_fixture: tuple[Path, dict[str, str]], architecture: str, artifact: str, prefix: str,
) -> None:
    _, environment = install_fixture
    pin = f"OFFICECLI_{architecture.upper()}_SHA256" if artifact == "binary" else "OFFICECLI_SKILL_SHA256"
    result = run_installer(install_fixture, TARGETARCH=architecture, **{pin: prefix + "0" * 64})
    assert result.returncode != 0
    assert not Path(environment["EXEC_LOG"]).exists()


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_local_inputs_install_without_network(install_fixture: tuple[Path, dict[str, str]], architecture: str) -> None:
    script, env = install_fixture
    binary = script.parent / ("binary-arm64" if architecture == "arm64" else "binary")
    result = subprocess.run(
        ["sh", str(script), "--binary-file", str(binary), "--skill-file", str(script.parent / "skill")],
        env=env | {"TARGETARCH": architecture}, capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (script.parent / "system bin/officecli").read_bytes() == binary.read_bytes()
    assert not Path(env["CURL_LOG"]).exists()


@pytest.mark.parametrize("failure", ["missing", "partial", "binary", "skill", "wrong-arch"])
def test_bad_local_inputs_fail_without_network(install_fixture: tuple[Path, dict[str, str]], failure: str) -> None:
    script, env = install_fixture
    binary = script.parent / ("missing" if failure == "missing" else "binary-arm64" if failure == "wrong-arch" else "binary")
    skill = script.parent / "skill"
    if failure in ("binary", "skill"):
        (binary if failure == "binary" else skill).write_bytes(b"bad")
    args = ["--binary-file", str(binary)]
    if failure != "partial":
        args.extend(["--skill-file", str(skill)])
    result = subprocess.run(["sh", str(script), *args], env=env, capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode != 0
    assert not Path(env["CURL_LOG"]).exists()
    assert not Path(env["EXEC_LOG"]).exists()


def seed_previous(root: Path, binary_exists: bool, skill_exists: bool) -> None:
    if binary_exists:
        binary = root / "system bin/officecli"
        binary.write_text("old binary")
        binary.chmod(0o751)
    if skill_exists:
        skill = root / "npm root/openclaw/skills/officecli"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("old skill")
        (skill / "extra").write_text("old extra")
        (skill / "extra").chmod(0o640)
        (skill / "link").symlink_to("extra")


def assert_previous(root: Path, binary_exists: bool, skill_exists: bool) -> None:
    binary = root / "system bin/officecli"
    skill = root / "npm root/openclaw/skills/officecli"
    assert binary.exists() == binary_exists
    assert skill.exists() == skill_exists
    if binary_exists:
        assert binary.read_text() == "old binary"
        assert binary.stat().st_mode & 0o777 == 0o751
    if skill_exists:
        assert (skill / "SKILL.md").read_text() == "old skill"
        assert (skill / "extra").read_text() == "old extra"
        assert (skill / "extra").stat().st_mode & 0o777 == 0o640
        assert (skill / "link").is_symlink()
        assert os.readlink(skill / "link") == "extra"


def inject_copy_failure(root: Path) -> None:
    wrapper = root / "bin/cp"
    wrapper.write_text(
        '#!/bin/sh\n'
        'case "${3:-}" in *"/backup/"*) [ "${FAIL_RESTORE:-}" != 1 ] || exit 1;; esac\n'
        '/bin/cp "$@" || exit $?\n'
        'case "$*" in *"/payload/$FAIL_WRITE "*)\n'
        '  if [ -n "${INJECT_SIGNAL:-}" ]; then kill -s "$INJECT_SIGNAL" "$PPID"; else exit 1; fi;;\n'
        'esac\n'
    )
    wrapper.chmod(0o755)


@pytest.mark.parametrize(("binary_exists", "skill_exists"), [(True, True), (False, False), (True, False), (False, True)])
@pytest.mark.parametrize("failure", ["officecli", "skill", "version"])
def test_failed_replacement_restores_previous_pair(
    install_fixture: tuple[Path, dict[str, str]], binary_exists: bool, skill_exists: bool, failure: str,
) -> None:
    root = install_fixture[0].parent
    seed_previous(root, binary_exists, skill_exists)
    inject_copy_failure(root)
    result = run_installer(install_fixture, FAIL_WRITE=failure, FAIL_VERSION="1" if failure == "version" else "0")
    assert result.returncode != 0
    assert_previous(root, binary_exists, skill_exists)
    assert not list(root.glob("officecli.*"))


@pytest.mark.parametrize(("signal", "status"), [("HUP", 129), ("INT", 130), ("TERM", 143)])
def test_signal_after_write_restores_previous_pair(install_fixture: tuple[Path, dict[str, str]], signal: str, status: int) -> None:
    root = install_fixture[0].parent
    seed_previous(root, True, True)
    inject_copy_failure(root)
    result = run_installer(install_fixture, FAIL_WRITE="skill", INJECT_SIGNAL=signal)
    assert result.returncode == status
    assert_previous(root, True, True)
    assert not list(root.glob("officecli.*"))


def test_restore_failure_retains_both_backups(install_fixture: tuple[Path, dict[str, str]]) -> None:
    root = install_fixture[0].parent
    seed_previous(root, True, True)
    inject_copy_failure(root)
    result = run_installer(install_fixture, FAIL_WRITE="skill", FAIL_RESTORE="1")
    assert result.returncode != 0
    backups = list(root.glob("officecli.*/backup"))
    assert len(backups) == 1
    assert (backups[0] / "officecli").read_text() == "old binary"
    assert (backups[0] / "skill/extra").read_text() == "old extra"
    assert str(backups[0].parent) in result.stderr


def test_successful_rerun_replaces_entire_skill(install_fixture: tuple[Path, dict[str, str]]) -> None:
    root = install_fixture[0].parent
    seed_previous(root, True, True)
    result = run_installer(install_fixture)
    assert result.returncode == 0, result.stderr
    result = run_installer(install_fixture)
    assert result.returncode == 0, result.stderr
    skill = root / "npm root/openclaw/skills/officecli"
    assert sorted(path.name for path in skill.iterdir()) == ["SKILL.md"]
    assert (root / "system bin/officecli").read_bytes() == (root / "binary").read_bytes()
    assert not list(root.glob("officecli.*"))


def test_rollback_preserves_top_level_symlinks(install_fixture: tuple[Path, dict[str, str]]) -> None:
    root = install_fixture[0].parent
    seed_previous(root, True, True)
    binary = root / "system bin/officecli"
    skill = root / "npm root/openclaw/skills/officecli"
    binary.rename(binary.with_name("previous"))
    binary.symlink_to("previous")
    skill.rename(skill.with_name("previous"))
    skill.symlink_to("previous")
    result = run_installer(install_fixture, FAIL_VERSION="1")
    assert result.returncode != 0
    assert binary.is_symlink() and os.readlink(binary) == "previous"
    assert skill.is_symlink() and os.readlink(skill) == "previous"
    assert_previous(root, True, True)
