import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-soul-guardian.sh"
COMMIT: Final = "03c19784f6c1e83ac81da2857f996ac9557dcceb"
PIN: Final = "2bef41a7e6a071ad4342c80de08802afdfe2f35c29ad623d36e7f56fd6baa2d6"


@pytest.fixture
def install_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    source = tmp_path / f"clawsec-{COMMIT}"
    for name in ("soul-guardian", "openclaw-traffic-guardian", "openclaw-audit-watchdog", "clawsec-suite"):
        directory = source / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name} upstream\n")
    scripts = source / "skills/soul-guardian/scripts"
    scripts.mkdir()
    executable = scripts / "guardian.py"
    executable.write_text("script\n")
    executable.chmod(0o755)
    (scripts / "data.json").write_text("{}\n")
    (source / "LICENSE").write_text("upstream license\n")
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=source.name)
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
    (destination / "soul-guardian").mkdir(parents=True)
    (destination / "soul-guardian/stale").write_text("old")
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


def test_verified_archive_installs_only_soul_guardian(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    assert {path.name for path in skills.iterdir()} == {"soul-guardian", "officecli"}
    assert (skills / "soul-guardian/SKILL.md").read_text() == "soul-guardian upstream\n"
    assert (skills / "soul-guardian/LICENSE").read_text() == "upstream license\n"
    assert (skills / "soul-guardian/scripts/data.json").read_text() == "{}\n"
    assert (skills / "soul-guardian/scripts/guardian.py").stat().st_mode & 0o111 == 0o111
    assert (skills / "soul-guardian/SKILL.md").stat().st_mode & 0o444 == 0o444
    assert not (skills / "soul-guardian/stale").exists()
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"
    curl = Path(env["CURL_LOG"]).read_text()
    assert "--disable --config /dev/null" in curl
    assert "--proto =https --proto-redir =https" in curl
    assert "--noproxy *" in curl
    assert curl.strip().endswith(f"/tar.gz/{COMMIT}")
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("failure", ["download", "digest"])
def test_untrusted_archive_preserves_existing_skills_and_cleans_stage(
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
    assert (skills / "soul-guardian/stale").read_text() == "old"
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"
    assert {path.name for path in skills.iterdir()} == {"soul-guardian", "officecli"}
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_docker_context_includes_soul_guardian_installer() -> None:
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    dockerfile = (ROOT / "Dockerfile.openclaw").read_text()
    assert "!docker/openclaw/install-soul-guardian.sh" in ignore
    assert "COPY docker/openclaw/install-soul-guardian.sh /tmp/install-soul-guardian.sh" in dockerfile
    assert "RUN sh /tmp/install-soul-guardian.sh && rm /tmp/install-soul-guardian.sh" in dockerfile
