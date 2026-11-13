import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Final

import pytest

INSTALLER: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/install-tracelattice-skill.sh"
COMMIT: Final = "3c4c16fc2af4ccb265ce4d74b933745925bc2b40"
REFERENCES: Final = ("references/errors.md", "references/tool-calls.md")


@pytest.fixture
def installation(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    source = tmp_path / f"TraceLattice-{COMMIT}"
    skill = source / "skills/tracelattice-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"fixture skill\n")
    (skill / "references").mkdir()
    for reference in REFERENCES:
        (skill / reference).write_bytes(f"fixture {reference}\n".encode())
    (skill / "references/extra.md").write_bytes(b"not installed\n")
    (source / "LICENSE").write_bytes(b"fixture license\n")
    (source / "extra").write_bytes(b"not installed\n")
    (skill / "extra").write_bytes(b"not installed\n")
    archive = tmp_path / "archive with spaces.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=source.name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CURL_LOG"\nwhile [ "$1" != "-o" ]; do shift; done\ncp "$ARCHIVE" "$2"\n')
    curl.chmod(0o755)
    stages = tmp_path / "stages"
    stages.mkdir()
    env = os.environ | {
        "HOME": str(tmp_path / "empty home"), "TMPDIR": str(stages),
        "PATH": f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', "ARCHIVE": str(archive),
        "CURL_LOG": str(tmp_path / "curl.log"), "TRACELATTICE_SKILL_COMMIT": "",
        "TRACELATTICE_SKILL_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    return source, env


def run_install(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(INSTALLER), *args], env=env, capture_output=True, text=True, timeout=10, check=False)


@pytest.mark.parametrize("local", [True, False])
def test_installs_only_required_files_and_reruns(installation: tuple[Path, dict[str, str]], local: bool) -> None:
    source, env = installation
    skill = source / "skills/tracelattice-skill"
    origins = {"SKILL.md": skill / "SKILL.md", "LICENSE": source / "LICENSE"} | {reference: skill / reference for reference in REFERENCES}
    args = ("--archive-file", env["ARCHIVE"]) if local else ()
    for _ in range(2):
        result = run_install(env, *args)
        assert result.returncode == 0, result.stderr
    target = Path(env["HOME"]) / ".openclaw/skills/tracelattice-skill"
    assert {p.relative_to(target).as_posix() for p in target.rglob("*")} == {"SKILL.md", "LICENSE", "references", *REFERENCES}
    for asset, origin in origins.items():
        assert (target / asset).read_bytes() == origin.read_bytes()
        assert (target / asset).stat().st_mode & 0o444 == 0o444
    if local:
        assert not Path(env["CURL_LOG"]).exists()
    else:
        curl = Path(env["CURL_LOG"]).read_text()
        assert "--disable --config /dev/null" in curl
        assert "--proto =https --proto-redir =https" in curl
        assert "--noproxy *" in curl
        assert curl.strip().endswith(f"https://codeload.github.com/iworkforces/TraceLattice/tar.gz/{COMMIT}")
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("bad", ["digest", "missing-skill", "missing-license", "missing-reference", "empty-skill", "empty-license", "empty-reference", "commit"])
def test_bad_input_preserves_existing_install(installation: tuple[Path, dict[str, str]], bad: str) -> None:
    source, env = installation
    target = Path(env["HOME"]) / ".openclaw/skills/tracelattice-skill"
    target.mkdir(parents=True)
    (target / "old").write_bytes(b"old")
    if bad.startswith(("missing-", "empty-")):
        asset = source / {"skill": "skills/tracelattice-skill/SKILL.md", "license": "LICENSE", "reference": f"skills/tracelattice-skill/{REFERENCES[1]}"}[bad.split("-")[1]]
        if bad.startswith("missing-"):
            asset.unlink()
        else:
            asset.write_bytes(b"")
        with tarfile.open(env["ARCHIVE"], "w:gz") as tar:
            tar.add(source, arcname=source.name)
        env["TRACELATTICE_SKILL_SHA256"] = hashlib.sha256(Path(env["ARCHIVE"]).read_bytes()).hexdigest()
    if bad == "digest":
        env["TRACELATTICE_SKILL_SHA256"] = "0" * 64
    if bad == "commit":
        env["TRACELATTICE_SKILL_COMMIT"] = COMMIT.upper()
    result = run_install(env, "--archive-file", env["ARCHIVE"])
    assert result.returncode != 0
    assert (target / "old").read_bytes() == b"old"
    assert not Path(env["CURL_LOG"]).exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("failure", ["error", "HUP", "INT", "TERM"])
def test_postwrite_failure_restores_previous_tree(installation: tuple[Path, dict[str, str]], failure: str) -> None:
    _, env = installation
    target = Path(env["HOME"]) / ".openclaw/skills/tracelattice-skill"
    target.mkdir(parents=True)
    (target / "old").write_bytes(b"old")
    (target / "link").symlink_to("old")
    real_cp = shutil.which("cp")
    assert real_cp is not None
    cp = Path(env["PATH"].split(os.pathsep)[0]) / "cp"
    cp.write_text(
        f'#!/bin/sh\n"{real_cp}" "$@" || exit $?\n'
        'case "${3:-}" in */incoming)\n'
        '  : > "$MARKER"\n  if [ "$FAILURE" = error ]; then exit 1; fi\n'
        '  kill -s "$FAILURE" "$PPID" ;;\nesac\n'
    )
    cp.chmod(0o755)
    marker = target.parent / "triggered"
    result = run_install(env | {"FAILURE": failure, "MARKER": str(marker)}, "--archive-file", env["ARCHIVE"])
    assert result.returncode != 0
    assert marker.exists()
    assert (target / "old").read_bytes() == b"old"
    assert os.readlink(target / "link") == "old"
    assert not list(Path(env["TMPDIR"]).iterdir())
