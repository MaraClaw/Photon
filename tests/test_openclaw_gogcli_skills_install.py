import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-gogcli-skills.sh"
COMMIT: Final = "b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4"
PIN: Final = "e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25"


@pytest.fixture
def install_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    source = tmp_path / f"gogcli-{COMMIT}/.agents/skills"
    for name in ("gog", "gog-calendar", "crabbox"):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name} upstream\n")
    agents = source / "gog/agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text("interface: {}\n")
    (source / "gog-calendar/sibling").symlink_to("../gog/SKILL.md")
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=f"gogcli-{COMMIT}/.agents/skills")
    script = tmp_path / "installer.sh"
    script.write_text(INSTALLER.read_text().replace(PIN, hashlib.sha256(archive.read_bytes()).hexdigest()))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$CURL_LOG"\n'
        'while [ "$1" != "-o" ]; do shift; done\n'
        '[ "${FAIL_DOWNLOAD:-}" != 1 ] || exit 22\ncp "$ARCHIVE" "$2"\n'
    )
    curl.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\nprintf "%s\\n" "$NPM_ROOT"\n')
    npm.chmod(0o755)
    destination = tmp_path / "npm root/openclaw/skills"
    (destination / "gog").mkdir(parents=True)
    (destination / "gog/stale").write_text("old")
    (destination / "officecli").mkdir()
    (destination / "officecli/SKILL.md").write_text("officecli")
    stages = tmp_path / "stages"
    stages.mkdir()
    env = os.environ | {
        "PATH": f'{bin_dir}:{os.environ["PATH"]}',
        "ARCHIVE": str(archive),
        "NPM_ROOT": str(tmp_path / "npm root"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "TMPDIR": str(stages),
    }
    return script, env


def test_verified_archive_replaces_gog_and_preserves_companions(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    assert (skills / "gog/SKILL.md").read_text() == "gog upstream\n"
    assert not (skills / "gog/stale").exists()
    assert (skills / "gog/agents/openai.yaml").read_text() == "interface: {}\n"
    assert (skills / "gog-calendar/sibling").is_symlink()
    assert (skills / "gog-calendar/sibling").read_text() == "gog upstream\n"
    assert (skills / "crabbox/SKILL.md").read_text() == "crabbox upstream\n"
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"
    assert (skills / "gog/SKILL.md").stat().st_mode & 0o444 == 0o444
    assert (skills / "gog/agents").stat().st_mode & 0o111 == 0o111
    curl = Path(env["CURL_LOG"]).read_text()
    assert "--disable --config /dev/null" in curl
    assert "--proto =https --proto-redir =https" in curl
    assert "--noproxy *" in curl
    assert curl.strip().endswith(f"/tar.gz/{COMMIT}")
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("failure", ["download", "digest"])
def test_untrusted_archive_installs_nothing_and_cleans_stage(
    install_fixture: tuple[Path, dict[str, str]], failure: str
) -> None:
    script, env = install_fixture
    if failure == "digest":
        Path(env["ARCHIVE"]).write_bytes(b"untrusted")
    result = subprocess.run(
        ["sh", str(script)], env=env | {"FAIL_DOWNLOAD": "1" if failure == "download" else ""},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    assert (skills / "gog/stale").read_text() == "old"
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"
    assert not (skills / "gog-calendar").exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_docker_context_includes_both_build_installers() -> None:
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    dockerfile = (ROOT / "Dockerfile.openclaw").read_text()
    for name in ("install-officecli.sh", "install-gogcli-skills.sh"):
        assert f"!docker/openclaw/{name}" in ignore
        assert f"COPY docker/openclaw/{name} /tmp/{name}" in dockerfile
        assert f"sh /tmp/{name}" in dockerfile
        assert f"rm /tmp/{name}" in dockerfile


def test_local_archive_records_installed_roots_without_network(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    ownership = script.parent / "owned roots.txt"
    result = subprocess.run(
        ["sh", str(script), "--archive-file", env["ARCHIVE"], "--ownership-output", str(ownership)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(ownership.read_text().splitlines()) == {"gog", "gog-calendar", "crabbox"}
    assert not Path(env["CURL_LOG"]).exists()


@pytest.mark.parametrize("failure", ["missing", "digest"])
def test_bad_local_archive_preserves_ownership_without_network(install_fixture: tuple[Path, dict[str, str]], failure: str) -> None:
    script, env = install_fixture
    ownership = script.parent / "owned roots.txt"
    ownership.write_text("previous\n")
    archive = Path(env["ARCHIVE"]) if failure == "digest" else script.parent / "missing"
    if failure == "digest":
        archive.write_bytes(b"bad")
    result = subprocess.run(["sh", str(script), "--archive-file", str(archive), "--ownership-output", str(ownership)],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert ownership.read_text() == "previous\n"
    assert not Path(env["CURL_LOG"]).exists()


def test_ownership_output_is_preserved_when_installation_fails(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    ownership = script.parent / "owned roots.txt"
    ownership.write_text("previous\n")
    chmod = script.parent / "bin/chmod"
    chmod.write_text('#!/bin/sh\nexit 1\n')
    chmod.chmod(0o755)
    result = subprocess.run(["sh", str(script), "--archive-file", env["ARCHIVE"], "--ownership-output", str(ownership)],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert ownership.read_text() == "previous\n"


def test_unsafe_skill_name_is_not_recorded(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    archive = Path(env["ARCHIVE"])
    original_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    skills = script.parent / f"gogcli-{COMMIT}/.agents/skills"
    (skills / "unsafe\nname").mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(skills, arcname=f"gogcli-{COMMIT}/.agents/skills")
    script.write_text(script.read_text().replace(original_digest, hashlib.sha256(archive.read_bytes()).hexdigest()))
    ownership = script.parent / "owned roots.txt"
    result = subprocess.run(["sh", str(script), "--archive-file", str(archive), "--ownership-output", str(ownership)],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert not ownership.exists()
