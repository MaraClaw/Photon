import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
INSTALLER: Final = ROOT / "docker/openclaw/install-linkup.sh"
PAYLOAD: Final = ROOT / "docker/openclaw/linkup-skills"


@pytest.fixture
def linkup_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    source = tmp_path / "linkup payload"
    shutil.copytree(PAYLOAD, source, symlinks=True)
    script = tmp_path / "installer.sh"
    script.write_text(INSTALLER.read_text().replace("/tmp/linkup-skills", f'"{source}"'))
    script.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\nprintf "%s\\n" "$NPM_ROOT"\n')
    npm.chmod(0o755)
    skills = tmp_path / "npm root/openclaw/skills"
    for name in ("linkup-search", "linkup-fetch", "linkup-custom"):
        (skills / name).mkdir(parents=True)
        (skills / name / "old").write_text(name)
    (skills / "linkup-search/link").symlink_to("old")
    (skills / "linkup-search/old").chmod(0o640)
    stages = tmp_path / "stages"
    stages.mkdir()
    env = os.environ | {"PATH": f'{bin_dir}:{os.environ["PATH"]}', "NPM_ROOT": str(tmp_path / "npm root"), "TMPDIR": str(stages)}
    return script, env, source, skills


def test_success_and_rerun_preserve_unrelated_skill(linkup_fixture: tuple[Path, dict[str, str], Path, Path]) -> None:
    script, env, source, skills = linkup_fixture
    for _ in range(2):
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert (skills / "linkup-search/SKILL.md").is_file()
        assert (skills / "linkup-fetch/SKILL.md").is_file()
        assert (skills / "linkup-custom/old").read_text() == "linkup-custom"
        assert not source.exists()
        shutil.copytree(PAYLOAD, source, symlinks=True)
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("failure", ["error", "HUP", "INT", "TERM"])
@pytest.mark.parametrize("prior", ["all", "mixed", "none"])
def test_post_write_failure_restores_owned_packages(
    linkup_fixture: tuple[Path, dict[str, str], Path, Path], failure: str, prior: str
) -> None:
    script, env, source, skills = linkup_fixture
    if prior != "all":
        shutil.rmtree(skills / "linkup-fetch")
    if prior == "none":
        shutil.rmtree(skills / "linkup-search")
    cp = script.parent / "bin/cp"
    real_cp = shutil.which("cp")
    assert real_cp is not None
    cp.write_text(
        f'#!/bin/sh\n"{real_cp}" "$@" || exit $?\n'
        'case "${3:-}" in */incoming/linkup-fetch)\n'
        '  : > "$NPM_ROOT/triggered"\n'
        '  if [ "$FAILURE" = error ]; then exit 1; fi\n'
        '  kill -s "$FAILURE" "$PPID" ;;\nesac\n'
    )
    cp.chmod(0o755)
    result = subprocess.run([str(script)], env=env | {"FAILURE": failure}, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert (Path(env["NPM_ROOT"]) / "triggered").exists(), result.stderr
    if prior == "none":
        assert not (skills / "linkup-search").exists()
    else:
        assert (skills / "linkup-search/old").read_text() == "linkup-search"
        assert os.readlink(skills / "linkup-search/link") == "old"
        assert (skills / "linkup-search/old").stat().st_mode & 0o777 == 0o640
    assert (skills / "linkup-fetch/old").exists() is (prior == "all")
    assert (skills / "linkup-custom/old").read_text() == "linkup-custom"
    assert source.exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("invalid", ["json", "missing", "traversal", "empty", "duplicate", "package", "skill", "symlink"])
def test_invalid_payload_is_rejected_before_mutation(
    linkup_fixture: tuple[Path, dict[str, str], Path, Path], invalid: str
) -> None:
    script, env, source, skills = linkup_fixture
    manifest = source / "manifest.json"
    if invalid == "json":
        manifest.write_text("{")
    elif invalid in {"missing", "traversal", "empty", "duplicate"}:
        values = {"missing": {}, "traversal": {"skills": ["../other"]}, "empty": {"skills": []},
                  "duplicate": {"skills": ["linkup-search", "linkup-search"]}}
        manifest.write_text(json.dumps(values[invalid]))
    elif invalid == "package":
        shutil.rmtree(source / "linkup-fetch")
    elif invalid == "skill":
        (source / "linkup-fetch/SKILL.md").unlink()
    else:
        skill = source / "linkup-fetch/SKILL.md"
        skill.unlink()
        skill.symlink_to(source / "linkup-search/SKILL.md")
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    for name in ("linkup-search", "linkup-fetch", "linkup-custom"):
        assert (skills / name / "old").read_text() == name
    assert source.exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_failed_restoration_keeps_backups(linkup_fixture: tuple[Path, dict[str, str], Path, Path]) -> None:
    script, env, source, skills = linkup_fixture
    cp = script.parent / "bin/cp"
    real_cp = shutil.which("cp")
    assert real_cp is not None
    cp.write_text(
        f'#!/bin/sh\ncase "${{3:-}}" in */backup/linkup-search) exit 1 ;; esac\n'
        f'"{real_cp}" "$@" || exit $?\ncase "${{3:-}}" in */incoming/linkup-fetch) exit 1 ;; esac\n'
    )
    cp.chmod(0o755)
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    stages = list(Path(env["TMPDIR"]).iterdir())
    assert len(stages) == 1 and str(stages[0]) in result.stderr, result.stderr
    assert (stages[0] / "backup/linkup-search/old").read_text() == "linkup-search"
    assert (skills / "linkup-custom/old").read_text() == "linkup-custom"
    assert source.exists()
