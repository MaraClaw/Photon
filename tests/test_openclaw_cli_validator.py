import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATORS = Path(__file__).resolve().parents[1] / "docker/openclaw"


def make_validator(tmp_path: Path, name: str) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin with spaces"
    bin_dir.mkdir()
    binary = bin_dir / name
    binary.write_text('#!/bin/sh\nprintf "%s:%s\\n" "${0##*/}" "$*" >> "$PROBE_LOG"\nprintf "probe output\\n"\nprintf "probe error\\n" >&2\n')
    binary.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\n[ "$*" = "root -g" ] || exit 2\nprintf "%s\\n" "$NPM_ROOT"\nprintf "%s\\n" "$GOG_KEYRING_PASSWORD" >&2\nexit "${NPM_STATUS:-0}"\n')
    npm.chmod(0o755)
    npm_root = tmp_path / "npm root with spaces"
    skill_dir = npm_root / "openclaw" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("installed skill\n")
    script = tmp_path / "validator with spaces.sh"
    script.write_text((VALIDATORS / f"validate-{name if name == 'officecli' else 'gogcli'}.sh").read_text().replace(f"/usr/local/bin/{name}", str(binary)))
    env = os.environ.copy()
    for key in ("GOG_HOME", "GOG_KEYRING_PASSWORD_FILE", "GOG_KEYRING_PASSWORD", "NPM_STATUS"):
        env.pop(key, None)
    env.update({"PROBE_LOG": str(tmp_path / "probes"), "CHILD_LOG": str(tmp_path / "child"), "NPM_ROOT": str(npm_root), "PATH": f'{bin_dir}{os.pathsep}{env["PATH"]}'})
    return script, env


@pytest.fixture
def validator(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    return make_validator(tmp_path, "gog")


@pytest.fixture(params=["gog", "officecli"])
def cli_validator(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, dict[str, str], str]:
    script, env = make_validator(tmp_path, request.param)
    return script, env, request.param


def run_validator(fixture: tuple[Path, dict[str, str]], *arguments: str, **env: str) -> subprocess.CompletedProcess[str]:
    script, environment = fixture
    return subprocess.run(["/bin/sh", str(script), *arguments], env=environment | env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_missing_or_broken_cli_blocks_child(cli_validator: tuple[Path, dict[str, str], str], failure: str) -> None:
    script, env, name = cli_validator
    binary = script.parent / "bin with spaces" / name
    if failure == "missing":
        binary.unlink()
    else:
        binary.write_text('#!/bin/sh\nprintf "%s" "$GOG_KEYRING_PASSWORD" >&2\nexit 37\n')
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == f"Required CLI failed verification: {binary}\n"
    assert not Path(env["CHILD_LOG"]).exists()


@pytest.mark.parametrize("failure", ["missing-file", "empty-file", "dangling-symlink", "missing-directory", "file-is-directory"])
def test_invalid_skill_blocks_child(cli_validator: tuple[Path, dict[str, str], str], failure: str) -> None:
    script, env, name = cli_validator
    skill_dir = Path(env["NPM_ROOT"]) / "openclaw" / "skills" / name
    skill = skill_dir / "SKILL.md"
    if failure != "empty-file":
        skill.unlink()
    if failure == "empty-file":
        skill.write_text("")
    if failure == "dangling-symlink":
        skill.symlink_to(skill_dir / "missing")
    if failure == "file-is-directory":
        skill.mkdir()
    if failure == "missing-directory":
        skill_dir.rmdir()
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value", GOG_KEYRING_PASSWORD_FILE=str(script.parent / "missing-password"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Required skill failed verification: {name}\n"
    assert not Path(env["CHILD_LOG"]).exists()


def test_unreadable_skill_blocks_child(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    skill = Path(env["NPM_ROOT"]) / "openclaw" / "skills" / name / "SKILL.md"
    skill.chmod(0)
    if os.access(skill, os.R_OK):
        pytest.skip("current user can read mode-000 files")
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Required skill failed verification: {name}\n"
    assert not Path(env["CHILD_LOG"]).exists()


@pytest.mark.parametrize("failure", ["failed", "empty"])
def test_unavailable_npm_root_blocks_child(cli_validator: tuple[Path, dict[str, str], str], failure: str) -> None:
    script, env, name = cli_validator
    npm_env = {"NPM_STATUS": "37"} if failure == "failed" else {"NPM_ROOT": ""}
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value", **npm_env)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Required skill failed verification: {name}\n"
    assert not Path(env["CHILD_LOG"]).exists()


@pytest.mark.parametrize("exit_code", [0, 37])
def test_no_keyring_executes_exact_argv_and_status(cli_validator: tuple[Path, dict[str, str], str], exit_code: int) -> None:
    script, env, name = cli_validator
    child = script.parent / "child with spaces.py"
    child.write_text('import json, os, sys\nfrom pathlib import Path\nPath(os.environ["CHILD_LOG"]).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ["EXIT_CODE"]))\n')
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    result = run_validator((script, env), sys.executable, str(child), *arguments, EXIT_CODE=str(exit_code))
    assert result.returncode == exit_code
    assert json.loads(Path(env["CHILD_LOG"]).read_text()) == arguments
    assert result.stdout == result.stderr == ""
    assert Path(env["PROBE_LOG"]).read_text().splitlines() == [f"{name}:--version"]


def test_officecli_ignores_gog_credentials(tmp_path: Path) -> None:
    validator = make_validator(tmp_path, "officecli")
    result = run_validator(validator, "/bin/sh", "-c", ":", GOG_KEYRING_PASSWORD_FILE=str(tmp_path / "missing"))
    assert result.returncode == 0


def test_credentials_file_is_loaded_without_leaking(validator: tuple[Path, dict[str, str]]) -> None:
    script, env = validator
    password = script.parent / "password file"
    password.write_text("sensitive password\n")
    home = script.parent / "gog home"
    result = run_validator(validator, "/bin/sh", "-c", 'printf %s "$GOG_KEYRING_PASSWORD" > "$CHILD_LOG"', GOG_KEYRING_PASSWORD_FILE=str(password), GOG_HOME=str(home))
    assert result.returncode == 0, result.stderr
    assert Path(env["CHILD_LOG"]).read_text() == "sensitive password"
    assert "sensitive password" not in result.stdout + result.stderr
    assert home.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("failure", ["missing-file", "missing-home"])
def test_invalid_credentials_block_child(validator: tuple[Path, dict[str, str]], failure: str) -> None:
    script, env = validator
    password = script.parent / "password"
    if failure == "missing-home":
        password.write_text("secret\n")
    result = run_validator(validator, "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD_FILE=str(password))
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()
    assert "secret" not in result.stdout + result.stderr


def test_restart_rechecks_clis_even_with_old_version_marker(validator: tuple[Path, dict[str, str]]) -> None:
    script, env = validator
    home = script.parent / "home"
    home.mkdir()
    (home / ".gog-version-checked").touch()
    password = script.parent / "password"
    password.write_text("secret\n")
    credentials = {"GOG_HOME": str(home), "GOG_KEYRING_PASSWORD_FILE": str(password)}
    assert run_validator(validator, "/bin/sh", "-c", ":", **credentials).returncode == 0
    (script.parent / "bin with spaces/gog").write_text("#!/bin/sh\nexit 9\n")
    result = run_validator(validator, "/bin/sh", "-c", 'touch "$CHILD_LOG"', **credentials)
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()
    assert Path(env["PROBE_LOG"]).read_text().splitlines() == ["gog:--version"]


def test_restart_rechecks_each_cli(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    assert run_validator((script, env), "/bin/sh", "-c", ":").returncode == 0
    (script.parent / "bin with spaces" / name).write_text("#!/bin/sh\nexit 9\n")
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"')
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()
