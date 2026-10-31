import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-soul-guardian.sh"
COMMIT: Final = "03c19784f6c1e83ac81da2857f996ac9557dcceb"
PIN: Final = "2bef41a7e6a071ad4342c80de08802afdfe2f35c29ad623d36e7f56fd6baa2d6"
OTHER_COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"
COMMIT_KEY: Final = "CLAWSEC_COMMIT"
SHA_KEY: Final = "CLAWSEC_SHA256"


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
    shutil.copy(INSTALLER, script)
    script.chmod(0o755)
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
    env = {key: value for key, value in os.environ.items() if key not in (COMMIT_KEY, SHA_KEY)} | {
        SHA_KEY: hashlib.sha256(archive.read_bytes()).hexdigest(),
        "PATH": f'{bin_dir}:{os.environ["PATH"]}',
        "ARCHIVE": str(archive),
        "NPM_ROOT": str(tmp_path / "npm root"),
        "CURL_LOG": str(tmp_path / "curl.log"),
        "TMPDIR": str(stages),
    }
    return script, env


def test_verified_archive_installs_only_soul_guardian(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, check=False, timeout=10)
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
        capture_output=True, text=True, check=False, timeout=10,
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
    assert "sh /tmp/install-soul-guardian.sh" in dockerfile
    assert "rm /tmp/install-soul-guardian.sh" in dockerfile


def test_local_archive_installs_without_network(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    result = subprocess.run(["sh", str(script), "--archive-file", env["ARCHIVE"]],
                            env=env, capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian/SKILL.md").read_text() == "soul-guardian upstream\n"
    assert not Path(env["CURL_LOG"]).exists()


def test_successful_rerun_replaces_existing_install(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    for _ in range(2):
        result = subprocess.run([str(script), "--archive-file", env["ARCHIVE"]], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian/LICENSE").read_text() == "upstream license\n"
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("failure", ["missing", "digest"])
def test_bad_local_archive_fails_without_network(install_fixture: tuple[Path, dict[str, str]], failure: str) -> None:
    script, env = install_fixture
    archive = Path(env["ARCHIVE"]) if failure == "digest" else script.parent / "missing"
    if failure == "digest":
        archive.write_bytes(b"bad")
    result = subprocess.run(["sh", str(script), "--archive-file", str(archive)],
                            env=env, capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode != 0
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian/stale").exists()
    assert not Path(env["CURL_LOG"]).exists()


@pytest.mark.parametrize("failure", ["error", "HUP", "INT", "TERM"])
@pytest.mark.parametrize("prior", [True, False])
def test_replacement_recovers_after_write(
    install_fixture: tuple[Path, dict[str, str]], failure: str, prior: bool
) -> None:
    script, env = install_fixture
    target = Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian"
    if prior:
        (target / "LICENSE").write_bytes(b"old license\x00\n")
        (target / "link").symlink_to("stale")
        (target / "stale").chmod(0o640)
    else:
        shutil.rmtree(target)
    cp = script.parent / "bin/cp"
    real_cp = shutil.which("cp")
    assert real_cp is not None
    cp.write_text(
        f'#!/bin/sh\n"{real_cp}" "$@" || exit $?\n'
        'case "${3:-}" in */incoming)\n'
        '  : > "$NPM_ROOT/triggered"\n'
        '  if [ "$FAILURE" = error ]; then exit 1; fi\n'
        '  kill -s "$FAILURE" "$PPID" ;;\nesac\n'
    )
    cp.chmod(0o755)
    result = subprocess.run([str(script)], env=env | {"FAILURE": failure}, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert (Path(env["NPM_ROOT"]) / "triggered").exists(), result.stderr
    if prior:
        assert (target / "LICENSE").read_bytes() == b"old license\x00\n"
        assert (target / "stale").read_text() == "old"
        assert (target / "stale").stat().st_mode & 0o777 == 0o640
        assert os.readlink(target / "link") == "stale"
    else:
        assert not target.exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_restoration_failure_retains_backup(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    cp = script.parent / "bin/cp"
    real_cp = shutil.which("cp")
    assert real_cp is not None
    cp.write_text(
        f'#!/bin/sh\ncase "${{3:-}}" in */backup) exit 1 ;; esac\n'
        f'"{real_cp}" "$@" || exit $?\ncase "${{3:-}}" in */incoming) exit 1 ;; esac\n'
    )
    cp.chmod(0o755)
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    stages = list(Path(env["TMPDIR"]).iterdir())
    assert len(stages) == 1 and str(stages[0]) in result.stderr, result.stderr
    assert (stages[0] / "backup/stale").read_text() == "old"


def alternate_archive(script: Path, commit: str) -> Path:
    archive = script.parent / "alternate.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(script.parent / f"clawsec-{COMMIT}", arcname=f"clawsec-{commit}")
    return archive


def install_tar_marker(script: Path) -> Path:
    marker = script.parent / "tar-ran"
    real_tar = shutil.which("tar")
    assert real_tar is not None
    tar = script.parent / "bin/tar"
    tar.write_text(f'#!/bin/sh\n: > "{marker}"\nexec "{real_tar}" "$@"\n')
    tar.chmod(0o755)
    return marker


def run_installer(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(script), *args], env=env, capture_output=True, text=True, check=False, timeout=10)


def unpinned(env: dict[str, str], **pins: str) -> dict[str, str]:
    return {key: value for key, value in env.items() if key not in (COMMIT_KEY, SHA_KEY)} | pins


@pytest.mark.parametrize("mode", ["download", "local"])
def test_overridden_pins_select_archive_and_digest(install_fixture: tuple[Path, dict[str, str]], mode: str) -> None:
    script, env = install_fixture
    archive = alternate_archive(script, OTHER_COMMIT)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert digest != PIN
    args = ["--archive-file", str(archive)] if mode == "local" else []
    result = run_installer(script, env | {"ARCHIVE": str(archive), COMMIT_KEY: OTHER_COMMIT, SHA_KEY: digest}, *args)
    assert result.returncode == 0, result.stderr
    installed = Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian"
    assert (installed / "LICENSE").read_text() == "upstream license\n"
    assert not (installed / "stale").exists()
    if mode == "download":
        assert Path(env["CURL_LOG"]).read_text().strip().endswith(f"/tar.gz/{OTHER_COMMIT}")
    else:
        assert not Path(env["CURL_LOG"]).exists()


@pytest.mark.parametrize("empty", [False, True])
def test_default_pins_apply_when_unset_or_empty(install_fixture: tuple[Path, dict[str, str]], empty: bool) -> None:
    script, env = install_fixture
    result = run_installer(script, unpinned(env, **({COMMIT_KEY: "", SHA_KEY: ""} if empty else {})))
    assert result.returncode != 0  # the default digest rejects the fixture archive
    assert Path(env["CURL_LOG"]).read_text().strip().endswith(f"/tar.gz/{COMMIT}")
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian/stale").read_text() == "old"


def test_partial_resolution_resolves_each_pin_independently(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    curl_log = Path(env["CURL_LOG"])
    commit_only = run_installer(script, unpinned(env, **{COMMIT_KEY: OTHER_COMMIT, SHA_KEY: ""}))
    assert commit_only.returncode != 0  # default digest still applies
    assert curl_log.read_text().strip().endswith(f"/tar.gz/{OTHER_COMMIT}")
    digest_only = run_installer(script, unpinned(env, **{COMMIT_KEY: "", SHA_KEY: env[SHA_KEY]}))
    assert digest_only.returncode == 0, digest_only.stderr
    assert curl_log.read_text().strip().endswith(f"/tar.gz/{COMMIT}")


@pytest.mark.parametrize(
    "commit",
    [OTHER_COMMIT[:-1], OTHER_COMMIT + "0", OTHER_COMMIT.upper(), OTHER_COMMIT[:-1] + "g", OTHER_COMMIT[:-1] + "\n",
     OTHER_COMMIT[:-2] + "$x", OTHER_COMMIT[:-2] + "/.", OTHER_COMMIT[:-2] + "*?", OTHER_COMMIT[:-2] + ";x"],
)
@pytest.mark.parametrize("mode", ["download", "local"])
def test_malformed_commit_is_rejected_before_acquisition(
    install_fixture: tuple[Path, dict[str, str]], commit: str, mode: str
) -> None:
    script, env = install_fixture
    cp = script.parent / "bin/cp"
    cp.write_text('#!/bin/sh\n: > "$CP_LOG"\n')
    cp.chmod(0o755)
    marker = install_tar_marker(script)
    args = ["--archive-file", env["ARCHIVE"]] if mode == "local" else []
    result = run_installer(script, env | {COMMIT_KEY: commit, "CP_LOG": str(script.parent / "cp.log")}, *args)
    assert result.returncode == 2
    assert not Path(env["CURL_LOG"]).exists()
    assert not (script.parent / "cp.log").exists()
    assert not marker.exists()
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/soul-guardian/stale").read_text() == "old"
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("mode", ["download", "local"])
@pytest.mark.parametrize("digest", [PIN, "0" * 64])
def test_wrong_digest_is_rejected_before_extraction_or_mutation(
    install_fixture: tuple[Path, dict[str, str]], mode: str, digest: str
) -> None:
    script, env = install_fixture
    marker = install_tar_marker(script)
    args = ["--archive-file", env["ARCHIVE"]] if mode == "local" else []
    result = run_installer(script, env | {SHA_KEY: digest}, *args)
    assert result.returncode != 0
    assert not marker.exists()
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    assert (skills / "soul-guardian/stale").read_text() == "old"
    assert not list(Path(env["TMPDIR"]).iterdir())
