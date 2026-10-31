import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-gogcli-skills.sh"
COMMIT: Final = "b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4"
PIN: Final = "e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25"
OTHER_COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"
COMMIT_KEY: Final = "GOGCLI_SKILLS_COMMIT"
SHA_KEY: Final = "GOGCLI_SKILLS_SHA256"


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
    (destination / "gog").mkdir(parents=True)
    (destination / "gog/stale").write_text("old")
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


def test_verified_archive_replaces_gog_and_preserves_companions(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False, timeout=15)
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
        capture_output=True, text=True, check=False, timeout=15,
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
        env=env, capture_output=True, text=True, check=False, timeout=15,
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
                            env=env, capture_output=True, text=True, check=False, timeout=15)
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
                            env=env, capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode != 0
    assert ownership.read_text() == "previous\n"


def test_unsafe_skill_name_is_not_recorded(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    archive = Path(env["ARCHIVE"])
    skills = script.parent / f"gogcli-{COMMIT}/.agents/skills"
    (skills / "unsafe\nname").mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(skills, arcname=f"gogcli-{COMMIT}/.agents/skills")
    ownership = script.parent / "owned roots.txt"
    result = subprocess.run(["sh", str(script), "--archive-file", str(archive), "--ownership-output", str(ownership)],
                            env=env | {SHA_KEY: hashlib.sha256(archive.read_bytes()).hexdigest()},
                            capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode != 0
    assert not ownership.exists()


def prepare_previous_skills(skills: Path) -> None:
    (skills / "gog/stale").chmod(0o600)
    (skills / "crabbox").mkdir()
    (skills / "crabbox/old").write_text("old crabbox")
    (skills / "gog-calendar").symlink_to("gog")


def assert_previous_skills(skills: Path) -> None:
    assert (skills / "gog/stale").read_text() == "old"
    assert (skills / "gog/stale").stat().st_mode & 0o777 == 0o600
    assert (skills / "crabbox/old").read_text() == "old crabbox"
    assert (skills / "gog-calendar").is_symlink()
    assert os.readlink(skills / "gog-calendar") == "gog"
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"


def install_fault_wrapper(script: Path, command: str) -> None:
    wrapper = script.parent / "bin" / command
    wrapper.write_text(
        f'#!/bin/sh\n'
        'last=\nfor arg do last=$arg; done\n'
        'if [ "${FAIL_SNAPSHOT:-}" = 1 ]; then\n'
        '  case "$last" in */backup/*) exit 1 ;; esac\n'
        'fi\n'
        'if [ "${FAIL_RESTORE:-}" = 1 ] && [ -f "$FAULT_MARKER" ]; then\n'
        '  case "$*" in */backup/*) exit 1 ;; esac\n'
        'fi\n'
        f'/bin/{command} "$@" || exit $?\n'
        'case "$last" in\n'
        '  "$FAULT_DEST")\n'
        '    if [ ! -f "$FAULT_MARKER" ]; then\n'
        '      : > "$FAULT_MARKER"\n'
        '      if [ -n "${FAULT_SIGNAL:-}" ]; then kill -s "$FAULT_SIGNAL" "$PPID"; else exit 1; fi\n'
        '    fi ;;\n'
        'esac\n'
    )
    wrapper.chmod(0o755)


@pytest.mark.parametrize("fault_signal", ["", "HUP", "INT", "TERM"])
def test_partial_write_restores_all_roots_and_ownership(
    install_fixture: tuple[Path, dict[str, str]], fault_signal: str
) -> None:
    script, env = install_fixture
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    prepare_previous_skills(skills)
    ownership = script.parent / "ownership"
    ownership.write_text("previous\n")
    install_fault_wrapper(script, "cp")
    result = subprocess.run(
        ["sh", str(script), "--archive-file", env["ARCHIVE"], "--ownership-output", str(ownership)],
        env=env | {"FAULT_DEST": str(skills / "gog"), "FAULT_MARKER": str(script.parent / "fault"),
                   "FAULT_SIGNAL": fault_signal},
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode != 0
    assert (script.parent / "fault").exists()
    assert_previous_skills(skills)
    assert ownership.read_text() == "previous\n"
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("existing", [False, True])
def test_ownership_publication_failure_restores_mixed_or_absent_roots(
    install_fixture: tuple[Path, dict[str, str]], existing: bool
) -> None:
    script, env = install_fixture
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    if existing:
        prepare_previous_skills(skills)
        (skills / "gog-calendar").unlink()
    else:
        (skills / "gog/stale").unlink()
        (skills / "gog").rmdir()
    ownership = script.parent / "ownership"
    if existing:
        (script.parent / "previous-owner").write_text("previous\n")
        ownership.symlink_to("previous-owner")
    install_fault_wrapper(script, "mv")
    result = subprocess.run(
        ["sh", str(script), "--archive-file", env["ARCHIVE"], "--ownership-output", str(ownership)],
        env=env | {"FAULT_DEST": str(ownership), "FAULT_MARKER": str(script.parent / "fault")},
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode != 0
    assert (script.parent / "fault").exists()
    if existing:
        assert (skills / "gog/stale").read_text() == "old"
        assert (skills / "gog/stale").stat().st_mode & 0o777 == 0o600
        assert (skills / "crabbox/old").read_text() == "old crabbox"
        assert not (skills / "gog-calendar").exists()
        assert (skills / "officecli/SKILL.md").read_text() == "officecli"
        assert ownership.read_text() == "previous\n"
        assert ownership.is_symlink()
        assert os.readlink(ownership) == "previous-owner"
    else:
        assert all(not (skills / name).exists() for name in ("gog", "gog-calendar", "crabbox"))
        assert not ownership.exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_snapshot_failure_does_not_mutate_destination(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    prepare_previous_skills(skills)
    install_fault_wrapper(script, "cp")
    result = subprocess.run(
        ["sh", str(script), "--archive-file", env["ARCHIVE"]],
        env=env | {"FAIL_SNAPSHOT": "1", "FAULT_DEST": "", "FAULT_MARKER": str(script.parent / "fault")},
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode != 0
    assert_previous_skills(skills)
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_failed_restoration_retains_backup_and_reports_path(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    prepare_previous_skills(skills)
    install_fault_wrapper(script, "cp")
    result = subprocess.run(
        ["sh", str(script), "--archive-file", env["ARCHIVE"]],
        env=env | {"FAIL_RESTORE": "1", "FAULT_DEST": str(skills / "gog"),
                   "FAULT_MARKER": str(script.parent / "fault")},
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode != 0
    stages = list(Path(env["TMPDIR"]).iterdir())
    assert len(stages) == 1, (result.stdout, result.stderr, (script.parent / "fault").exists())
    assert str(stages[0]) in result.stderr
    assert (stages[0] / "backup/gog/stale").read_text() == "old"


def test_successful_rerun_replaces_previous_installation(install_fixture: tuple[Path, dict[str, str]]) -> None:
    script, env = install_fixture
    skills = Path(env["NPM_ROOT"]) / "openclaw/skills"
    prepare_previous_skills(skills)
    for _ in range(2):
        result = subprocess.run(["sh", str(script), "--archive-file", env["ARCHIVE"]],
                                env=env, capture_output=True, text=True, check=False, timeout=15)
        assert result.returncode == 0, result.stderr
    assert (skills / "crabbox/SKILL.md").read_text() == "crabbox upstream\n"
    assert (skills / "gog-calendar/sibling").is_symlink()
    assert (skills / "officecli/SKILL.md").read_text() == "officecli"
    assert not list(Path(env["TMPDIR"]).iterdir())


def alternate_archive(script: Path, commit: str) -> Path:
    archive = script.parent / "alternate.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(script.parent / f"gogcli-{COMMIT}/.agents/skills", arcname=f"gogcli-{commit}/.agents/skills")
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
    return subprocess.run(["sh", str(script), *args], env=env, capture_output=True, text=True, check=False, timeout=15)


@pytest.mark.parametrize("mode", ["download", "local"])
def test_overridden_pins_select_archive_and_digest(install_fixture: tuple[Path, dict[str, str]], mode: str) -> None:
    script, env = install_fixture
    archive = alternate_archive(script, OTHER_COMMIT)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert digest != PIN
    args = ["--archive-file", str(archive)] if mode == "local" else []
    result = run_installer(script, env | {"ARCHIVE": str(archive), COMMIT_KEY: OTHER_COMMIT, SHA_KEY: digest}, *args)
    assert result.returncode == 0, result.stderr
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/gog/SKILL.md").read_text() == "gog upstream\n"
    if mode == "download":
        assert Path(env["CURL_LOG"]).read_text().strip().endswith(f"/tar.gz/{OTHER_COMMIT}")
    else:
        assert not Path(env["CURL_LOG"]).exists()


def unpinned(env: dict[str, str], **pins: str | None) -> dict[str, str]:
    base = {key: value for key, value in env.items() if key not in (COMMIT_KEY, SHA_KEY)}
    return base | {key: value for key, value in pins.items() if value is not None}


@pytest.mark.parametrize("empty", [False, True])
def test_default_pins_apply_when_unset_or_empty(install_fixture: tuple[Path, dict[str, str]], empty: bool) -> None:
    script, env = install_fixture
    pins = {COMMIT_KEY: "", SHA_KEY: ""} if empty else {}
    result = run_installer(script, unpinned(env, **pins))
    assert result.returncode != 0  # the default digest rejects the fixture archive
    assert "FAILED" in result.stderr or "did NOT match" in result.stderr
    assert Path(env["CURL_LOG"]).read_text().strip().endswith(f"/tar.gz/{COMMIT}")
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/gog/stale").read_text() == "old"


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
    assert (Path(env["NPM_ROOT"]) / "openclaw/skills/gog/stale").read_text() == "old"
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
    assert (skills / "gog/stale").read_text() == "old"
    assert not (skills / "gog-calendar").exists()
    assert not list(Path(env["TMPDIR"]).iterdir())
