import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from openclaw_probe_test_support import (
    INVALID_POLICY, POLICY_KEYS, clean_policy, exercise_final_child,
    exercise_invalid_policy, exercise_script_lookup, exercise_signal, exercise_success_cleanup, exercise_timeout, install_hang, prepare_integrity, python_launches, readiness, rebaseline, validator_process,
)

VALIDATORS = Path(__file__).resolve().parents[1] / "docker/openclaw"


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_cli_slashless_script_lookup(cli_validator: tuple[Path, dict[str, str], str], tmp_path: Path, local: bool, invalid: bool) -> None:
    script, env, _ = cli_validator
    exercise_script_lookup((script, env), tmp_path, (local, invalid))


def make_validator(tmp_path: Path, name: str) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin with spaces"
    bin_dir.mkdir()
    binary = bin_dir / name
    binary.write_text('#!/bin/sh\nprintf "%s:%s\\n" "${0##*/}" "$*" >> "$PROBE_LOG"\nprintf "probe output\\n"\nprintf "probe error\\n" >&2\n')
    binary.chmod(0o755)
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\nexit 99\n')
    npm.chmod(0o755)
    npm_root = tmp_path / "npm root with spaces"
    skill_dir = npm_root / ".openclaw" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("installed skill\n")
    script = tmp_path / "validator with spaces.sh"
    script.write_text((VALIDATORS / f"validate-{name if name == 'officecli' else 'gogcli'}.sh").read_text().replace(f"/usr/local/bin/{name}", str(binary)))
    for helper in ("startup-probe.sh", "startup-probe.py", "installed-assets.py"):
        if (VALIDATORS / helper).exists():
            shutil.copy2(VALIDATORS / helper, tmp_path / helper)
    env = os.environ.copy()
    clean_policy(env)
    for key in ("GOG_HOME", "GOG_KEYRING_PASSWORD_FILE", "GOG_KEYRING_PASSWORD", "NPM_STATUS"):
        env.pop(key, None)
    env.update({"PROBE_LOG": str(tmp_path / "probes"), "CHILD_LOG": str(tmp_path / "child"), "NPM_ROOT": str(npm_root), "PATH": f'{bin_dir}{os.pathsep}{env["PATH"]}'})
    env[f"INTEGRITY_BINARY_{name.upper()}"] = str(binary.resolve())
    script = prepare_integrity(tmp_path, skill_dir.parent, env, script)
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
        rebaseline(env)
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (f"Installed assets failed verification: {'gogcli' if name == 'gog' else name}\n" if failure == "missing" else f"Required CLI failed verification: {binary}\n")
    assert not Path(env["CHILD_LOG"]).exists()


@pytest.mark.parametrize("failure", ["missing-file", "empty-file", "dangling-symlink", "missing-directory", "file-is-directory"])
def test_invalid_skill_blocks_child(cli_validator: tuple[Path, dict[str, str], str], failure: str) -> None:
    script, env, name = cli_validator
    skill_dir = Path(env["HOME"]) / ".openclaw" / "skills" / name
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
    assert result.stderr == f"Installed assets failed verification: {'gogcli' if name == 'gog' else name}\n"
    assert not Path(env["CHILD_LOG"]).exists()


def test_unreadable_skill_blocks_child(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    skill = Path(env["HOME"]) / ".openclaw" / "skills" / name / "SKILL.md"
    skill.chmod(0)
    if os.access(skill, os.R_OK):
        pytest.skip("current user can read mode-000 files")
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', GOG_KEYRING_PASSWORD="secret-value")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Installed assets failed verification: {'gogcli' if name == 'gog' else name}\n"
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


def test_hanging_cli_probe_times_out(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    install_hang(script.parent, env, script.parent / "bin with spaces" / name)
    exercise_timeout(script, env)


@pytest.mark.parametrize("key", POLICY_KEYS)
@pytest.mark.parametrize("value", INVALID_POLICY)
def test_invalid_policy_prevents_cli_probes(cli_validator: tuple[Path, dict[str, str], str], key: str, value: str) -> None:
    script, env, name = cli_validator
    install_hang(script.parent, env, script.parent / "bin with spaces" / name)
    exercise_invalid_policy(script, env, key, value)


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_cli_signal_cleans_version_probe_group(cli_validator: tuple[Path, dict[str, str], str], signum: int) -> None:
    script, env, name = cli_validator
    install_hang(script.parent, env, script.parent / "bin with spaces" / name)
    exercise_signal(script, env, signum)


def test_cli_restart_times_out_with_old_marker(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    (script.parent / ".gog-version-checked").touch()
    assert run_validator((script, env), "/bin/sh", "-c", ":").returncode == 0
    install_hang(script.parent, env, script.parent / "bin with spaces" / name)
    exercise_timeout(script, env)


def test_cli_final_child_outlives_probe_deadlines(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, _ = cli_validator
    env["PROBE_READY"] = str(script.parent / "child-ready")
    os.mkfifo(env["PROBE_READY"])
    exercise_final_child(script, env)


def test_successful_cli_probe_cleans_residual_descendant(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    install_hang(script.parent, env, script.parent / "bin with spaces" / name)
    exercise_success_cleanup(script, env)


@pytest.mark.parametrize("asset", ["binary", "skill"])
def test_valid_looking_tampering_blocks_cli_before_any_probe(cli_validator: tuple[Path, dict[str, str], str], asset: str) -> None:
    script, env, name = cli_validator
    path = script.parent / "bin with spaces" / name if asset == "binary" else Path(env["HOME"]) / ".openclaw/skills" / ("crabbox" if name == "gog" else name) / ("fixture" if name == "gog" else "SKILL.md")
    path.write_text("#!/bin/sh\nexit 0\n" if asset == "binary" else "valid skill content\n")
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"')
    assert result.returncode == 1
    assert not Path(env["PROBE_LOG"]).exists()
    assert not Path(env["CHILD_LOG"]).exists()


def test_untouched_cli_restart_uses_same_inventory(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    for _ in range(2):
        assert run_validator((script, env), "/bin/sh", "-c", ":").returncode == 0
    assert Path(env["PROBE_LOG"]).read_text().splitlines() == [f"{name}:--version"] * 2


def test_home_cannot_redirect_cli_to_unverified_skills(cli_validator: tuple[Path, dict[str, str], str], tmp_path: Path) -> None:
    script, env, name = cli_validator
    other = tmp_path / "other/.openclaw/skills" / name
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("valid skill\n")
    result = run_validator((script, env), "/bin/sh", "-c", 'touch "$CHILD_LOG"', HOME=str(other.parents[2]))
    assert result.returncode == 1
    assert not Path(env["PROBE_LOG"]).exists()
    assert not Path(env["CHILD_LOG"]).exists()


def test_cli_validator_without_keyring_starts_one_interpreter(cli_validator: tuple[Path, dict[str, str], str]) -> None:
    script, env, name = cli_validator
    assert python_launches(script, env) == ["startup-probe.py"]
    assert Path(env["PROBE_LOG"]).read_text().splitlines() == [f"{name}:--version"]


@pytest.mark.parametrize("suffix", ["control", "1"])
def test_abandoned_predictable_probe_paths_do_not_block_or_get_removed(
    validator: tuple[Path, dict[str, str]], tmp_path: Path, suffix: str,
) -> None:
    script, env = validator
    temporary = tmp_path / "probe-tmp"
    temporary.mkdir()
    password = tmp_path / "password"
    password.write_text("private keyring value\n")
    env.update({
        "TMPDIR": str(temporary), "STALE_SUFFIX": suffix,
        "GOG_KEYRING_PASSWORD_FILE": str(password), "GOG_HOME": str(tmp_path / "gog-home"),
    })
    child = (
        "import json, os, sys; from pathlib import Path; "
        "Path(os.environ['CHILD_LOG']).write_text(json.dumps({"
        "'pid': os.getpid(), 'stale': os.environ['STALE_PATH'], "
        "'password': os.environ['GOG_KEYRING_PASSWORD'], 'argv': sys.argv[1:]})); "
        "sys.exit(37)"
    )
    result = subprocess.run(
        [
            "/bin/sh", "-c",
            'stale=$TMPDIR/openclaw-probe.$$.$STALE_SUFFIX; '
            'printf "%s" abandoned >"$stale"; export STALE_PATH=$stale; exec "$@"',
            "sh", "/bin/sh", str(script), sys.executable, "-c", child, "space value", "",
        ],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 37, result.stderr
    assert result.stdout == result.stderr == ""
    report = json.loads(Path(env["CHILD_LOG"]).read_text())
    stale = Path(report["stale"])
    assert stale == temporary / f"openclaw-probe.{report['pid']}.{suffix}"
    assert report["password"] == "private keyring value"
    assert report["argv"] == ["space value", ""]
    assert stale.read_text() == "abandoned"
    assert list(temporary.iterdir()) == [stale]


def test_concurrent_captures_are_private_and_cancellation_removes_only_owned_storage(
    validator: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    script, env = validator
    temporary = tmp_path / "probe-tmp"
    temporary.mkdir()
    password = tmp_path / "password"
    password.write_text("private captured value\n")
    actor = tmp_path / "capture_actor.py"
    actor.write_text(
        "import os, sys\nfrom pathlib import Path\n"
        "sys.stdout.write(Path(sys.argv[1]).read_text()); sys.stdout.flush()\n"
        "Path(os.environ['PROBE_PID_FILE']).write_text(str(os.getpid()))\n"
        "with open(os.environ['PROBE_READY'], 'w') as ready:\n"
        "    ready.write(str(os.getpid()) + '\\n')\n"
        "with open(os.environ['CAPTURE_RELEASE']) as release: release.read(1)\n"
    )
    cat = script.parent / "bin with spaces" / "cat"
    cat.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{actor}" "$@"\n')
    cat.chmod(0o755)
    environments = []
    for index in range(2):
        ready = tmp_path / f"capture-ready-{index}"
        release = tmp_path / f"capture-release-{index}"
        os.mkfifo(ready)
        os.mkfifo(release)
        environments.append(env | {
            "TMPDIR": str(temporary), "GOG_KEYRING_PASSWORD_FILE": str(password),
            "GOG_HOME": str(tmp_path / f"gog-home-{index}"),
            "PROBE_READY": str(ready), "PROBE_PID_FILE": str(tmp_path / f"capture-pid-{index}"),
            "CAPTURE_RELEASE": str(release), "OPENCLAW_PROBE_TIMEOUT_SECONDS": "30",
            "OPENCLAW_PROBE_GRACE_SECONDS": "0.1",
        })
    with validator_process(script, environments[0], sys.executable, "-c", "pass") as (first, first_ready):
        readiness(first_ready)
        first_directory, = temporary.iterdir()
        with validator_process(script, environments[1], sys.executable, "-c", "pass") as (second, second_ready):
            readiness(second_ready)
            directories = set(temporary.iterdir())
            assert len(directories) == 2
            for directory in directories:
                assert directory.is_dir()
                assert directory.stat().st_mode & 0o777 == 0o700
                files = list(directory.iterdir())
                assert len(files) == 2
                assert all(path.stat().st_mode & 0o077 == 0 for path in files)
                assert any(path.read_text() == password.read_text() for path in files)
            first.send_signal(signal.SIGTERM)
            assert first.wait(timeout=5) == 143
            assert set(temporary.iterdir()) == directories - {first_directory}
            assert second.poll() is None
            with open(environments[1]["CAPTURE_RELEASE"], "r+b", buffering=0) as release:
                release.write(b"x")
                assert second.wait(timeout=5) == 0
            assert not list(temporary.iterdir())
