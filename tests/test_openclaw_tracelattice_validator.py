import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from openclaw_probe_test_support import clean_policy, exercise_script_lookup, prepare_integrity, python_launches, rebaseline

VALIDATOR: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/validate-tracelattice.sh"


@pytest.fixture
def tracelattice(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    skill = tmp_path / "home/.openclaw/skills/tracelattice-skill"
    skill.mkdir(parents=True)
    for asset in ("SKILL.md", "LICENSE"):
        (skill / asset).write_bytes(b"fixture\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    env = os.environ | {"PATH": f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', "CHILD_LOG": str(tmp_path / "child.json")}
    clean_policy(env)
    prepare_integrity(tmp_path, skill.parent, env, VALIDATOR)
    return skill, env


@pytest.mark.parametrize("status", [0, 37])
def test_valid_skill_executes_child_with_arguments_environment_and_status(tracelattice: tuple[Path, dict[str, str]], status: int) -> None:
    _, env = tracelattice
    args = ["space value", "", "$(literal);*", "--flag=value"]
    child = "import json,os,sys; print(json.dumps([sys.argv[1:],os.environ['PASSTHROUGH']])); sys.exit(int(os.environ['STATUS']))"
    result = subprocess.run(["sh", env["VALIDATOR"], sys.executable, "-c", child, *args], env=env | {"PASSTHROUGH": "value", "STATUS": str(status)}, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == status
    assert json.loads(result.stdout) == [args, "value"]
    assert result.stderr == ""


@pytest.mark.parametrize("local", [False, True])
def test_slashless_lookup(tracelattice: tuple[Path, dict[str, str]], tmp_path: Path, local: bool) -> None:
    _, env = tracelattice
    exercise_script_lookup((Path(env["VALIDATOR"]), env), tmp_path, (local, False))


@pytest.mark.parametrize("asset", ["SKILL.md", "LICENSE"])
@pytest.mark.parametrize("failure", ["missing", "empty", "tamper"])
def test_invalid_or_tampered_skill_blocks_child(tracelattice: tuple[Path, dict[str, str]], asset: str, failure: str) -> None:
    skill, env = tracelattice
    path = skill / asset
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"" if failure == "empty" else b"tampered\n")
    if failure != "tamper":
        rebaseline(env)
    result = subprocess.run(["sh", env["VALIDATOR"], "sh", "-c", 'touch "$CHILD_LOG"'], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()


def test_tampered_package_blocks_child(tracelattice: tuple[Path, dict[str, str]]) -> None:
    _, env = tracelattice
    (Path(env["INTEGRITY_PACKAGE_TRACELATTICE"]) / "node_modules/@iworkforces/tracelattice/dist/cli.js").write_bytes(b"tampered\n")
    result = subprocess.run(["sh", env["VALIDATOR"], "sh", "-c", 'touch "$CHILD_LOG"'], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()
    assert "Installed assets failed verification: tracelattice" in result.stderr


def test_missing_inventory_blocks_child(tracelattice: tuple[Path, dict[str, str]]) -> None:
    _, env = tracelattice
    for path in Path(env["INTEGRITY_DIR"]).iterdir():
        path.unlink()
    result = subprocess.run(["sh", env["VALIDATOR"], "sh", "-c", 'touch "$CHILD_LOG"'], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()


def test_tracelattice_starts_one_interpreter(tracelattice: tuple[Path, dict[str, str]]) -> None:
    _, env = tracelattice
    assert python_launches(Path(env["VALIDATOR"]), env) == ["startup-probe.py"]
