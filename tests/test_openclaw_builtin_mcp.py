import importlib.util
import json
import os
import stat
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

MODULE: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/builtin_mcp.py"
MTIME_NS: Final = 1_700_000_000_000_000_000
ENGINE: Final = {"gateway": {"port": 18789, "bind": "loopback", "auth": {"mode": "token", "token": "secret"}}, "skills": {"load": {"extraDirs": ["/skills"]}}}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("builtin_mcp", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builtin_mcp = load()
TRACELATTICE: Final = builtin_mcp.SERVERS["tracelattice"]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(tmp_path / "state/openclaw.json"))
    return tmp_path / "state/openclaw.json"


def put(path: Path, document: object, mode: int = 0o600) -> bytes:
    content = document if isinstance(document, bytes) else json.dumps(document, indent=2).encode()
    path.write_bytes(content)
    path.chmod(mode)
    os.utime(path, ns=(MTIME_NS, MTIME_NS))
    return content


def snapshot(path: Path) -> tuple[bytes, int, int, int]:
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns, stat.S_IMODE(info.st_mode)


def names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def run() -> list[str]:
    reports: list[str] = []
    builtin_mcp.register(reports.append)
    return reports


@pytest.mark.parametrize(
    "environ,expected",
    [
        ({"HOME": "/h"}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": "/oc"}, "/oc/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": "~/sub"}, "/h/sub/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": "/oc", "OPENCLAW_STATE_DIR": "/state"}, "/state/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_STATE_DIR": "~/st"}, "/h/st/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": "/oc", "OPENCLAW_STATE_DIR": "~/st"}, "/oc/st/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_STATE_DIR": "/state", "OPENCLAW_CONFIG_PATH": "/cfg/a.json"}, "/cfg/a.json"),
        ({"HOME": "/h", "OPENCLAW_CONFIG_PATH": "~/a.json"}, "/h/a.json"),
        ({"HOME": "/h", "OPENCLAW_CONFIG_PATH": "~"}, "/h"),
        ({"HOME": "/h", "OPENCLAW_CONFIG_PATH": "", "OPENCLAW_STATE_DIR": "/state"}, "/state/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_CONFIG_PATH": "", "OPENCLAW_STATE_DIR": "", "OPENCLAW_HOME": ""}, "/h/.openclaw/openclaw.json"),
    ],
)
def test_config_path_follows_openclaw_precedence(environ: dict[str, str], expected: str) -> None:
    assert builtin_mcp.config_path(environ) == Path(expected)


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_adds_tracelattice_to_engine_config(config: Path, mode: int) -> None:
    put(config, ENGINE, mode)
    assert run() == []
    result = json.loads(config.read_bytes())
    assert result == ENGINE | {"mcp": {"servers": {"tracelattice": TRACELATTICE}}}
    assert list(result) == [*ENGINE, "mcp"]
    assert config.read_bytes() == json.dumps(result, indent=2).encode()
    assert stat.S_IMODE(config.stat().st_mode) == mode
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("base", [{}, {"mcp": {}}, {"mcp": {"servers": {}}}])
def test_creates_missing_mcp_containers(config: Path, base: dict[str, object]) -> None:
    put(config, base)
    assert run() == []
    assert json.loads(config.read_bytes()) == {"mcp": {"servers": {"tracelattice": TRACELATTICE}}}


def test_other_servers_and_mcp_keys_are_kept(config: Path) -> None:
    original = {"gateway": {"port": 1}, "mcp": {"sessionIdleTtlMs": 5, "servers": {"docs": {"url": "https://example.test/mcp"}, "local": {"command": "x", "args": ["-y"]}}}}
    put(config, original)
    assert run() == []
    result = json.loads(config.read_bytes())
    assert result["mcp"]["servers"].pop("tracelattice") == TRACELATTICE
    assert result == original
    assert list(result["mcp"]["servers"]) == ["docs", "local"]


@pytest.mark.parametrize("entry", [{"enabled": False, "command": "x"}, {"command": "other"}, None])
def test_existing_tracelattice_entry_is_left_alone(config: Path, entry: object) -> None:
    config.write_text(json.dumps({"mcp": {"servers": {"tracelattice": entry}}}) + "\n")
    config.chmod(0o600)
    os.utime(config, ns=(MTIME_NS, MTIME_NS))
    before = snapshot(config)
    assert run() == []
    assert snapshot(config) == before


def test_second_registration_is_a_byte_identical_no_op(config: Path) -> None:
    put(config, ENGINE)
    assert run() == []
    registered = snapshot(config)
    assert run() == []
    assert snapshot(config) == registered


@pytest.mark.parametrize("missing", ["file", "directory"])
def test_missing_config_is_silent_and_creates_nothing(tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    if missing == "directory":
        monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(tmp_path / "absent/openclaw.json"))
    assert run() == []
    assert names(tmp_path) == ["state"]
    assert names(config.parent) == []


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"",
        b"[]",
        b"null",
        b'{"mcp": []}',
        b'{"mcp": null}',
        b'{"mcp": {"servers": []}}',
        b'{"mcp": {"servers": null}}',
        b'{"a": NaN}',
        b'{"a": Infinity}',
        b'{"a": -Infinity}',
        b'{"a": 1e999}',
        b'{"a": 1, "a": 2}',
        b'{"mcp": {"servers": {}, "servers": {}}}',
        b'{"a": "\xff"}',
        b'\xef\xbb\xbf{"a": 1}',
    ],
)
def test_unusable_content_is_reported_and_left_untouched(config: Path, content: bytes) -> None:
    put(config, content)
    before = snapshot(config)
    reports = run()
    assert len(reports) == 1
    assert str(config) in reports[0]
    assert snapshot(config) == before
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_non_regular_config_is_reported_and_left_untouched(config: Path, kind: str) -> None:
    target = config.with_name("target.json")
    put(target, ENGINE)
    {"symlink": lambda: config.symlink_to(target), "directory": config.mkdir, "fifo": lambda: os.mkfifo(config)}[kind]()
    before = (snapshot(target), os.lstat(config).st_ino, names(config.parent))
    reports = run()
    assert len(reports) == 1
    assert str(config) in reports[0]
    assert (snapshot(target), os.lstat(config).st_ino, names(config.parent)) == before


def test_config_owned_by_another_user_is_reported_and_left_untouched(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(config, ENGINE)
    before = snapshot(config)
    monkeypatch.setattr(builtin_mcp.os, "geteuid", lambda: config.stat().st_uid + 1)
    reports = run()
    assert len(reports) == 1
    assert str(config) in reports[0]
    assert snapshot(config) == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_directory_is_reported_and_config_left_untouched(config: Path) -> None:
    put(config, ENGINE)
    before = snapshot(config)
    config.parent.chmod(0o500)
    try:
        reports = run()
    finally:
        config.parent.chmod(0o700)
    assert len(reports) == 1
    assert str(config) in reports[0]
    assert snapshot(config) == before
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("signal", ["size", "mtime", "inode"])
def test_concurrent_change_wins_over_registration(config: Path, monkeypatch: pytest.MonkeyPatch, signal: str) -> None:
    changed = put(config, ENGINE).replace(b"18789", b"19999")
    fsync = os.fsync

    def concurrent_write(descriptor: int) -> None:
        fsync(descriptor)
        target = config.with_name("concurrent") if signal == "inode" else config
        target.write_bytes(changed + b" " * (signal == "size"))
        mtime = MTIME_NS + 10**9 * (signal == "mtime")
        os.utime(target, ns=(mtime, mtime))
        if signal == "inode":
            os.replace(target, config)

    monkeypatch.setattr(builtin_mcp.os, "fsync", concurrent_write)
    reports = run()
    assert len(reports) == 1
    assert str(config) in reports[0]
    assert config.read_bytes() == changed + b" " * (signal == "size")
    assert names(config.parent) == ["openclaw.json"]
