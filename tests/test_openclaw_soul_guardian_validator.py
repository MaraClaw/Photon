import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from openclaw_probe_test_support import (
    INVALID_POLICY, POLICY_KEYS, clean_policy, exercise_final_child,
    exercise_invalid_policy, exercise_script_lookup, exercise_signal, exercise_timeout, install_hang,
)

VALIDATOR: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/validate-soul-guardian.sh"
ASSETS: Final = (
    "SKILL.md", "CHANGELOG.md", "skill.json", "LICENSE",
    "scripts/soul_guardian.py", "scripts/onboard_state_dir.py",
)


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_guardian_slashless_script_lookup(guardian: tuple[Path, dict[str, str]], tmp_path: Path, local: bool, invalid: bool) -> None:
    _, env = guardian
    exercise_script_lookup((VALIDATOR, env), tmp_path, (local, invalid))


@pytest.fixture
def guardian(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    skill = tmp_path / "npm root with spaces/openclaw/skills/soul-guardian"
    (skill / "scripts").mkdir(parents=True)
    for asset in ASSETS:
        (skill / asset).write_text("fixture\n")
    (skill / "skill.json").write_text("{}\n")
    (skill / "scripts/onboard_state_dir.py").write_text('raise RuntimeError("must not execute")\n')
    (skill / "scripts/helper.py").write_text("VALUE = 1\n")
    (skill / "scripts/soul_guardian.py").write_text(
        "import argparse, os\n"
        "from pathlib import Path\n"
        "import helper\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('command')\n"
        "parser.parse_args()\n"
        "Path(os.environ['HOME'], 'guardian-state').mkdir()\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\n[ "$*" = "root -g" ] || exit 2\n[ "${NPM_FAIL:-}" != 1 ] || { printf "secret diagnostic\\n" >&2; exit 3; }\nprintf "%s\\n" "$NPM_ROOT"\n')
    npm.chmod(0o755)
    (bin_dir / "python3").symlink_to(sys.executable)
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ | {
        "PATH": f'{bin_dir}:{os.environ["PATH"]}',
        "HOME": str(home),
        "NPM_ROOT": str(tmp_path / "npm root with spaces"),
        "CHILD_LOG": str(tmp_path / "child.json"),
    }
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    clean_policy(env)
    return skill, env


@pytest.mark.parametrize("exit_code", [0, 37])
def test_exec_preserves_arguments_environment_and_status_when_skill_is_valid(
    guardian: tuple[Path, dict[str, str]], exit_code: int,
) -> None:
    skill, env = guardian
    child = skill.parent / "child with spaces.py"
    child.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CHILD_LOG']).write_text(json.dumps([sys.argv[1:], os.environ['PASSTHROUGH']]))\n"
        "sys.exit(int(os.environ['EXIT_CODE']))\n"
    )
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    result = subprocess.run(
        ["/bin/sh", str(VALIDATOR), sys.executable, str(child), *arguments],
        env=env | {"PASSTHROUGH": "environment value", "EXIT_CODE": str(exit_code)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == exit_code
    assert json.loads(Path(env["CHILD_LOG"]).read_text()) == [arguments, "environment value"]
    assert result.stdout == result.stderr == ""
    assert not list(Path(env["HOME"]).iterdir())
    assert not list(skill.rglob("__pycache__"))
    assert not list(skill.rglob("*.pyc"))


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("failure", ["missing", "empty", "broken-link", "unreadable"])
def test_invalid_asset_blocks_child(guardian: tuple[Path, dict[str, str]], asset: str, failure: str) -> None:
    skill, env = guardian
    path = skill / asset
    if failure == "empty":
        path.write_text("")
    elif failure == "unreadable":
        path.chmod(0)
        if os.access(path, os.R_OK):
            pytest.skip("current user can read mode 000 files")
    else:
        path.unlink()
        if failure == "broken-link":
            path.symlink_to("missing-target")
    result = subprocess.run(
        ["/bin/sh", str(VALIDATOR), "/bin/sh", "-c", 'touch "$CHILD_LOG"'],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "Required skill failed verification: soul-guardian\n"
    assert not Path(env["CHILD_LOG"]).exists()
    assert not list(Path(env["HOME"]).iterdir())


@pytest.mark.parametrize("failure", ["npm-failure", "empty-root", "broken-python", "malformed-json", "broken-onboarding"])
def test_probe_or_discovery_failure_blocks_child(guardian: tuple[Path, dict[str, str]], failure: str) -> None:
    skill, env = guardian
    if failure == "broken-python":
        (skill / "scripts/soul_guardian.py").write_text('raise RuntimeError("secret diagnostic")\n')
    if failure == "npm-failure":
        env["NPM_FAIL"] = "1"
    if failure == "empty-root":
        env["NPM_ROOT"] = ""
    if failure == "malformed-json":
        (skill / "skill.json").write_text("{broken\n")
    if failure == "broken-onboarding":
        (skill / "scripts/onboard_state_dir.py").write_text("def broken(:\n")
    result = subprocess.run(
        ["/bin/sh", str(VALIDATOR), "/bin/sh", "-c", 'touch "$CHILD_LOG"'],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "Required skill failed verification: soul-guardian\n"
    assert not Path(env["CHILD_LOG"]).exists()
    assert not list(Path(env["HOME"]).iterdir())
    assert not list(skill.rglob("__pycache__"))
    assert not list(skill.rglob("*.pyc"))


@pytest.mark.parametrize("probe", ["npm", "compile", "help"])
def test_guardian_hanging_probe_times_out(guardian: tuple[Path, dict[str, str]], probe: str) -> None:
    skill, env = guardian
    root = Path(env["HOME"]).parent
    install_hang(root, env, root / "bin" / ("npm" if probe == "npm" else "python3"), python_probe="" if probe == "npm" else probe)
    exercise_timeout(VALIDATOR, env)
    assert not list(Path(env["HOME"]).iterdir())
    assert not list(skill.rglob("__pycache__"))


@pytest.mark.parametrize("key", POLICY_KEYS)
@pytest.mark.parametrize("value", INVALID_POLICY)
def test_invalid_policy_prevents_guardian_probes(guardian: tuple[Path, dict[str, str]], key: str, value: str) -> None:
    _, env = guardian
    root = Path(env["HOME"]).parent
    install_hang(root, env, root / "bin/npm")
    exercise_invalid_policy(VALIDATOR, env, key, value)


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_guardian_signal_cleans_parsing_group(guardian: tuple[Path, dict[str, str]], signum: int) -> None:
    _, env = guardian
    root = Path(env["HOME"]).parent
    install_hang(root, env, root / "bin/python3", python_probe="compile")
    exercise_signal(VALIDATOR, env, signum)


def test_guardian_restart_rechecks_hanging_probe(guardian: tuple[Path, dict[str, str]]) -> None:
    _, env = guardian
    root = Path(env["HOME"]).parent
    assert subprocess.run(["/bin/sh", str(VALIDATOR), "/bin/sh", "-c", ":"], env=env, timeout=5, check=False).returncode == 0
    (Path(env["HOME"]) / ".guardian-checked").touch()
    install_hang(root, env, root / "bin/npm")
    exercise_timeout(VALIDATOR, env)


def test_guardian_final_child_outlives_probe_deadlines(guardian: tuple[Path, dict[str, str]]) -> None:
    _, env = guardian
    env["PROBE_READY"] = str(Path(env["HOME"]).parent / "child-ready")
    os.mkfifo(env["PROBE_READY"])
    exercise_final_child(VALIDATOR, env)
