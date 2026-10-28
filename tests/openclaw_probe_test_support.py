import contextlib
import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

POLICY_KEYS = ("OPENCLAW_PROBE_TIMEOUT_SECONDS", "OPENCLAW_PROBE_GRACE_SECONDS")
INVALID_POLICY = ("", "0", "-1", "NaN", "inf", " 1", "1 ", "1e2")
INTEGRITY_HELPERS = ("startup-probe.sh", "startup-probe.py", "installed-assets.py")


def prepare_integrity(root: Path, skills: Path, env: dict[str, str], script: Path) -> Path:
    root = root.resolve()
    skills = skills.resolve()
    env["NPM_ROOT"] = str(skills.parent.parent)
    binaries = root / "integrity-binaries"
    binaries.mkdir(exist_ok=True)
    for name in ("gog", "officecli"):
        binary = binaries / name
        if not binary.exists():
            binary.write_text("fixture\n")
    for name in ("gog", "crabbox", "officecli", "soul-guardian", "linkup-search", "linkup-fetch"):
        directory = skills / name
        directory.mkdir(parents=True, exist_ok=True)
        if not any(directory.iterdir()):
            (directory / "fixture").write_text("fixture\n")
    roots = root / "gog-roots.txt"
    roots.write_text("gog\ncrabbox\n")
    inventory = root / "integrity"
    validator = root / script.name
    text = script.read_text().replace("/usr/local/share/openclaw-integrity", str(inventory))
    validator.write_text(text)
    for helper in INTEGRITY_HELPERS:
        source = script.parent / helper
        destination = root / helper
        if source != destination:
            shutil.copy2(source, destination)
    helper = root / "installed-assets.py"
    helper.write_text(helper.read_text().replace("/usr/local/bin/gog", env.get("INTEGRITY_BINARY_GOG", str(binaries / "gog"))).replace("/usr/local/bin/officecli", env.get("INTEGRITY_BINARY_OFFICECLI", str(binaries / "officecli"))))
    env.update({"INTEGRITY_HELPER": str(helper), "INTEGRITY_SKILLS": str(skills), "INTEGRITY_ROOTS": str(roots), "INTEGRITY_DIR": str(inventory), "VALIDATOR": str(validator)})
    rebaseline(env)
    return validator


def rebaseline(env: dict[str, str]) -> None:
    subprocess.run([sys.executable, env["INTEGRITY_HELPER"], "create", "--skills-root", env["INTEGRITY_SKILLS"], "--gog-roots-file", env["INTEGRITY_ROOTS"], "--output-dir", env["INTEGRITY_DIR"]], check=True, capture_output=True, text=True)


def exercise_script_lookup(
    validator: tuple[Path, dict[str, str]], root: Path, scenario: tuple[bool, bool],
) -> None:
    script, env = validator
    local, invalid = scenario
    path_dir = root / "path directory"
    path_dir.mkdir()
    cwd = root / "working directory"
    cwd.mkdir()
    shutil.copy2(script, path_dir / script.name)
    (path_dir / script.name).chmod(0o755)
    for helper in INTEGRITY_HELPERS:
        shutil.copy2(script.parent / helper, path_dir / helper)
    if local:
        shutil.copy2(script, cwd / script.name)
        (cwd / script.name).chmod(0o644)
        for helper in INTEGRITY_HELPERS:
            shutil.copy2(script.parent / helper, cwd / helper)
        (path_dir / "startup-probe.sh").write_text("exit 61\n")
    npm = Path(env["PATH"].split(os.pathsep)[0]) / "npm"
    marker = root / "npm-probe"
    if invalid:
        npm.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    result = subprocess.run(
        ["/bin/sh", script.name, sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(37)", *arguments],
        cwd=cwd, env=env | {"PATH": f'{path_dir}{os.pathsep}{env["PATH"]}'} | ({POLICY_KEYS[0]: "invalid"} if invalid else {}),
        capture_output=True, text=True, timeout=10, check=False,
    )
    if invalid:
        assert result.returncode == 2, result.stderr
        assert POLICY_KEYS[0] in result.stderr
        assert result.stdout == ""
        assert not marker.exists()
        if "PROBE_LOG" in env:
            assert not Path(env["PROBE_LOG"]).exists()
    else:
        assert result.returncode == 37, result.stderr
        assert json.loads(result.stdout) == arguments
        assert result.stderr == ""


def clean_policy(env: dict[str, str]) -> None:
    for key in (*POLICY_KEYS, "PROBE_READY", "PROBE_PID_FILE", "PROBE_EXIT"):
        env.pop(key, None)


def install_hang(root: Path, env: dict[str, str], target: Path, *, python_probe: str = "") -> None:
    actor = root / "probe_actor.py"
    actor.write_text(
        "import os, signal\n"
        "read_fd, write_fd = os.pipe()\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(read_fd)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    os.write(write_fd, b'x')\n"
        "    while True: signal.pause()\n"
        "os.close(write_fd)\n"
        "with open(os.environ['PROBE_PID_FILE'], 'w') as pid_file:\n"
        "    pid_file.write(f'{os.getpid()} {child}\\n')\n"
        "os.read(read_fd, 1)\n"
        "with open(os.environ['PROBE_READY'], 'w') as ready:\n"
        "    ready.write(f'{os.getpid()} {child}\\n')\n"
        "if os.environ.get('PROBE_EXIT'):\n"
        "    print('probe output', flush=True)\n"
        "    os._exit(0)\n"
        "while True: signal.pause()\n"
    )
    env["PROBE_READY"] = str(root / "probe-ready")
    env["PROBE_PID_FILE"] = str(root / "probe-pids")
    os.mkfifo(env["PROBE_READY"])
    command = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(actor))}"
    if python_probe:
        target.unlink(missing_ok=True)
        pattern = "-c" if python_probe == "compile" else "*soul_guardian.py"
        target.write_text(
            f'#!/bin/sh\nif [ "$1" = "-B" ]; then\n'
            f'  case "$2" in {pattern}) {command} ;; esac\nfi\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n'
        )
    else:
        target.write_text(f"#!/bin/sh\n{command}\n")
    target.chmod(0o755)
    if target.name in {"gog", "officecli"} and "INTEGRITY_DIR" in env:
        rebaseline(env)


@contextlib.contextmanager
def validator_process(script: Path, env: dict[str, str], *child: str) -> Iterator[tuple[subprocess.Popen[bytes], int]]:
    ready = os.open(env["PROBE_READY"], os.O_RDWR | os.O_NONBLOCK)
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            ["/bin/sh", str(script), *child], env=env, stdout=output, stderr=output,
            start_new_session=True,
        )
        pids: list[int] = []
        try:
            yield process, ready
        except AssertionError:
            output.seek(0)
            print(output.read().decode(errors="replace"), file=sys.stderr)
            raise
        finally:
            pid_file = Path(env.get("PROBE_PID_FILE", ""))
            if env.get("PROBE_PID_FILE") and pid_file.is_file():
                pids.extend(int(value) for value in pid_file.read_text().split())
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
            process.wait(timeout=5)
            os.close(ready)


def readiness(fd: int) -> list[int]:
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        assert selector.select(5), "probe or child did not become ready"
    data = os.read(fd, 256)
    return [int(value) for value in data.split()]


def assert_gone(pids: list[int]) -> None:
    for pid in pids:
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False)
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z"), (pid, result.stdout)
        if sys.platform.startswith("linux"):
            assert not result.stdout.strip(), (pid, result.stdout)


def exercise_timeout(script: Path, env: dict[str, str]) -> None:
    env.update({POLICY_KEYS[0]: "0.5", POLICY_KEYS[1]: "0.1"})
    child_marker = Path(env["PROBE_READY"]).parent / "delegated"
    with validator_process(script, env, "/bin/sh", "-c", f'touch {shlex.quote(str(child_marker))}') as (process, ready):
        pids = readiness(ready)
        assert process.wait(timeout=5) == 124
        assert not child_marker.exists()
        assert_gone(pids)


def exercise_signal(script: Path, env: dict[str, str], signum: int) -> None:
    env.update({POLICY_KEYS[0]: "10", POLICY_KEYS[1]: "0.1"})
    child_marker = Path(env["PROBE_READY"]).parent / "delegated"
    with validator_process(script, env, "/bin/sh", "-c", f'touch {shlex.quote(str(child_marker))}') as (process, ready):
        pids = readiness(ready)
        os.kill(process.pid, signum)
        assert process.wait(timeout=5) == 128 + signum
        assert not child_marker.exists()
        assert_gone(pids)


def exercise_invalid_policy(script: Path, env: dict[str, str], key: str, value: str) -> None:
    marker = Path(env["PROBE_READY"]).parent / "delegated"
    env[key] = value
    with validator_process(script, env, "/bin/sh", "-c", f'touch {shlex.quote(str(marker))}') as (process, ready):
        assert process.wait(timeout=5) == 2
        with selectors.DefaultSelector() as selector:
            selector.register(ready, selectors.EVENT_READ)
            assert not selector.select(0)
        assert not marker.exists()


def exercise_final_child(script: Path, env: dict[str, str]) -> None:
    env.update({POLICY_KEYS[0]: "0.5", POLICY_KEYS[1]: "0.1"})
    fifo = Path(env["PROBE_READY"])
    env["TMPDIR"] = str(fifo.parent)
    child = fifo.parent / "long_child.py"
    child.write_text(
        "import json, os, signal, sys\n"
        "def stop(signum, frame): sys.exit(128 + signum)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "with open(os.environ['PROBE_READY'], 'w') as ready:\n"
        "    ready.write(json.dumps([os.getpid(), sys.argv[1:]]) + '\\n')\n"
        "while True: signal.pause()\n"
    )
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    with validator_process(script, env, sys.executable, str(child), *arguments) as (process, ready):
        with selectors.DefaultSelector() as selector:
            selector.register(ready, selectors.EVENT_READ)
            assert selector.select(5), "final child did not become ready"
        pid, received = json.loads(os.read(ready, 4096))
        assert pid == process.pid
        assert received == arguments
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=1.6)
        processes = subprocess.run(["ps", "-eo", "ppid=,pid="], capture_output=True, text=True, timeout=5, check=True)
        assert not [line for line in processes.stdout.splitlines() if int(line.split()[0]) == process.pid]
        assert not list(fifo.parent.glob("openclaw-probe.*"))
        os.kill(process.pid, signal.SIGTERM)
        assert process.wait(timeout=5) == 128 + signal.SIGTERM


def exercise_success_cleanup(script: Path, env: dict[str, str]) -> None:
    env.update({POLICY_KEYS[0]: "1", POLICY_KEYS[1]: "0.1", "PROBE_EXIT": "1"})
    marker = Path(env["PROBE_READY"]).parent / "delegated"
    with validator_process(script, env, "/bin/sh", "-c", f'touch {shlex.quote(str(marker))}') as (process, ready):
        pids = readiness(ready)
        assert process.wait(timeout=5) == 0
        assert marker.exists()
        assert_gone(pids)
