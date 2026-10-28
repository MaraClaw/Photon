from __future__ import annotations

import ctypes
import os
from pathlib import Path
import pwd
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading


VALIDATORS = ("gogcli", "officecli", "soul-guardian", "linkup")
SOURCE = Path("/usr/local/bin")


def fake_probe() -> None:
    Path(os.environ["PROBE_MARKER"]).touch()
    mode = os.environ.get("PROBE_MODE", "") if Path(sys.argv[0]).name == os.environ.get("PROBE_TARGET") else ""
    if not mode:
        if Path(sys.argv[0]).name == "npm":
            print(os.environ["REAL_NPM_ROOT"])
        return
    parent_pid = os.getpid()
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with open(os.environ["READY_FIFO"], "w", encoding="utf-8") as ready:
            ready.write(f"{parent_pid} {os.getpid()}\n")
        os.write(write_fd, b"1")
        os.close(write_fd)
        while True:
            signal.pause()
    Path(os.environ["PID_FILE"]).write_text(f"{parent_pid} {child_pid}")
    os.close(write_fd)
    os.read(read_fd, 1)
    os.close(read_fd)
    if mode == "early":
        os._exit(0)
    while True:
        signal.pause()


def prepare(directory: Path) -> tuple[dict[str, Path], dict[str, str]]:
    binary_dir = directory / "bin with spaces"
    binary_dir.mkdir()
    npm_root = subprocess.check_output(["npm", "root", "-g"], text=True, timeout=60).strip()
    for name in ("gog", "officecli", "npm"):
        executable = binary_dir / name
        executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" --fake "$0" "$@"\n')
        executable.chmod(0o755)
    for helper in ("startup-probe.sh", "startup-probe.py"):
        shutil.copy2(SOURCE / helper, directory / helper)
    scripts = {}
    for name in VALIDATORS:
        text = (SOURCE / f"validate-{name}.sh").read_text()
        for binary in ("gog", "officecli"):
            text = text.replace(f"/usr/local/bin/{binary}", str(binary_dir / binary))
        script = directory / f"validate-{name}.sh"
        script.write_text(text)
        scripts[name] = script
    env = os.environ | {
        "PATH": f'{binary_dir}:{os.environ["PATH"]}',
        "REAL_NPM_ROOT": npm_root,
        "PROBE_MARKER": str(directory / "probe-marker"),
        "OPENCLAW_PROBE_GRACE_SECONDS": "0.2",
    }
    return scripts, env


def direct_failure(script: Path, env: dict[str, str], signum: int | None, *, early: bool = False) -> None:
    fifo = script.parent / "ready"
    marker = script.parent / "delegated"
    pid_file = script.parent / "probe-pids"
    fifo.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    pid_file.unlink(missing_ok=True)
    os.mkfifo(fifo)
    read_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    environment = env | {
        "READY_FIFO": str(fifo),
        "PID_FILE": str(pid_file),
        "PROBE_MODE": "early" if early else "hang",
        "PROBE_TARGET": {"validate-gogcli.sh": "gog", "validate-officecli.sh": "officecli"}.get(script.name, "npm"),
        "OPENCLAW_PROBE_TIMEOUT_SECONDS": "15" if signum is None else "30",
    }
    process = subprocess.Popen(
        ["/bin/sh", str(script), sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(marker)],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    probe_pid = 0
    descendant_pid = 0
    try:
        readable, _, _ = select.select([read_fd], [], [], 30)
        assert readable, f"{script.name}: probe never became ready"
        probe_pid, descendant_pid = map(int, os.read(read_fd, 128).decode().split())
        if signum is not None:
            process.send_signal(signum)
        status = process.wait(timeout=40)
        expected = 0 if early else 124 if signum is None else 128 + signum
        assert status == expected, (script.name, status, expected)
        assert marker.exists() == early, script.name
        assert not Path(f"/proc/{probe_pid}").exists(), (script.name, probe_pid)
        assert not Path(f"/proc/{descendant_pid}").exists(), (script.name, descendant_pid)
    finally:
        os.close(read_fd)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if not probe_pid and pid_file.exists():
            probe_pid, descendant_pid = map(int, pid_file.read_text().split())
        if probe_pid and (Path(f"/proc/{probe_pid}").exists() or Path(f"/proc/{descendant_pid}").exists()):
            try:
                os.killpg(probe_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child_pid in (probe_pid, descendant_pid):
            if child_pid:
                try:
                    os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass


def direct_handoff(script: Path, env: dict[str, str]) -> None:
    fifo = script.parent / "handoff"
    os.mkfifo(fifo)
    read_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    code = (
        "import os,signal,sys; "
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(37)); "
        "f=open(sys.argv[1], 'w'); print(os.getpid(), file=f, flush=True); "
        "signal.pause()"
    )
    process = subprocess.Popen(
        ["/bin/sh", str(script), sys.executable, "-c", code, str(fifo)],
        env=env | {"OPENCLAW_PROBE_TIMEOUT_SECONDS": "10"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        readable, _, _ = select.select([read_fd], [], [], 60)
        assert readable, "final child never became ready"
        assert int(os.read(read_fd, 128)) == process.pid
        assert not Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().strip()
        assert process.poll() is None
        assert not select.select([read_fd], [], [], 11)[0]
        assert process.poll() is None, "final child was timed out"
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 37
    finally:
        os.close(read_fd)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--fake":
        sys.argv = sys.argv[2:]
        fake_probe()
        return
    assert sys.argv[1:] == ["space value", "", "$(literal);*"]
    assert pwd.getpwuid(os.getuid()).pw_name == "node"
    assert not Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
    threading.Event().wait(11)
    assert not Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip()
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(36, 1, 0, 0, 0) == 0, ctypes.get_errno()
    with tempfile.TemporaryDirectory(prefix="openclaw-probe-image-") as temporary:
        scripts, env = prepare(Path(temporary))
        for name, script in scripts.items():
            for key in ("OPENCLAW_PROBE_TIMEOUT_SECONDS", "OPENCLAW_PROBE_GRACE_SECONDS"):
                marker = Path(env["PROBE_MARKER"])
                marker.unlink(missing_ok=True)
                result = subprocess.run(
                    ["/bin/sh", str(script), "/bin/false"],
                    env=env | {key: "invalid"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                    check=False,
                )
                assert result.returncode == 2 and not marker.exists(), (name, key, result.returncode)
            for signum in (None, signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                direct_failure(script, env, signum)
            print(f"{name}: invalid policy, timeout, HUP/INT/TERM cleanup passed", flush=True)
        direct_failure(scripts["gogcli"], env, None, early=True)
        direct_handoff(scripts["gogcli"], env)
    print("normal entrypoint and direct final handoff passed", flush=True)


if __name__ == "__main__":
    main()
