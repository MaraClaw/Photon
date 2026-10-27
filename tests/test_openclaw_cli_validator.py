import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / "docker/openclaw/validate-gogcli.sh"


@pytest.fixture
def validator(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("gog", "officecli"):
        binary = bin_dir / name
        binary.write_text('#!/bin/sh\nprintf "%s:%s\\n" "${0##*/}" "$*" >> "$PROBE_LOG"\n')
        binary.chmod(0o755)
    script = tmp_path / "validator.sh"
    script.write_text(VALIDATOR.read_text().replace("/usr/local/bin/gog", str(bin_dir / "gog")).replace("/usr/local/bin/officecli", str(bin_dir / "officecli")))
    env = os.environ.copy()
    for key in ("GOG_HOME", "GOG_KEYRING_PASSWORD_FILE", "GOG_KEYRING_PASSWORD"):
        env.pop(key, None)
    env.update({"PROBE_LOG": str(tmp_path / "probes"), "CHILD_LOG": str(tmp_path / "child")})
    return script, env


def run_validator(fixture: tuple[Path, dict[str, str]], *arguments: str, **env: str) -> subprocess.CompletedProcess[str]:
    script, environment = fixture
    return subprocess.run(["/bin/sh", str(script), *arguments], env=environment | env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("name", ["gog", "officecli"])
@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_missing_or_broken_cli_blocks_child(validator: tuple[Path, dict[str, str]], name: str, failure: str) -> None:
    script, env = validator
    binary = script.parent / "bin" / name
    if failure == "missing":
        binary.unlink()
    else:
        binary.write_text('#!/bin/sh\nprintf "%s" "$GOG_KEYRING_PASSWORD" >&2\nexit 37\n')
    result = run_validator(validator, "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value")
    assert result.returncode != 0
    assert name in result.stderr
    assert "secret-value" not in result.stdout + result.stderr
    assert not Path(env["CHILD_LOG"]).exists()
    assert Path(env["PROBE_LOG"]).read_text().startswith(("officecli:" if name == "gog" else "gog:"))


@pytest.mark.parametrize("exit_code", [0, 37])
def test_no_keyring_executes_exact_argv_and_status(validator: tuple[Path, dict[str, str]], exit_code: int) -> None:
    script, env = validator
    child = script.parent / "child.py"
    child.write_text('import json, os, sys\nfrom pathlib import Path\nPath(os.environ["CHILD_LOG"]).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ["EXIT_CODE"]))\n')
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    result = run_validator(validator, sys.executable, str(child), *arguments, EXIT_CODE=str(exit_code))
    assert result.returncode == exit_code
    assert json.loads(Path(env["CHILD_LOG"]).read_text()) == arguments
    assert Path(env["PROBE_LOG"]).read_text().splitlines() == ["gog:--version", "officecli:--version"]


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
    (script.parent / "bin/gog").write_text("#!/bin/sh\nexit 9\n")
    result = run_validator(validator, "/bin/sh", "-c", 'touch "$CHILD_LOG"', **credentials)
    assert result.returncode != 0
    assert not Path(env["CHILD_LOG"]).exists()
    assert Path(env["PROBE_LOG"]).read_text().splitlines().count("officecli:--version") == 2
