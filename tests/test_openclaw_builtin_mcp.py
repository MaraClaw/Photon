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
    monkeypatch.delenv("OPENCLAW_PROFILE", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_READONLY", raising=False)
    monkeypatch.delenv("OPENCLAW_NIX_MODE", raising=False)
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


def run(*, environ: dict[str, str] | None = None) -> list[str]:
    reports: list[str] = []
    builtin_mcp.register(reports.append, environ=environ)
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
        ({"HOME": "/h", "OPENCLAW_PROFILE": " Work_2 "}, "/h/.openclaw-Work_2/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "x" * 64}, f"/h/.openclaw-{'x' * 64}/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": " DEFAULT "}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "  "}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "../other"}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "-bad"}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "x" * 65}, "/h/.openclaw/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "Å"}, "/h/.openclaw/openclaw.json"),
        ({"HOME": " /h ", "OPENCLAW_HOME": " ~/oc ", "OPENCLAW_PROFILE": " Work "}, "/h/oc/.openclaw-Work/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": " ", "OPENCLAW_PROFILE": "Work"}, "/h/.openclaw-Work/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "Work", "OPENCLAW_STATE_DIR": " ~/st "}, "/h/st/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": " /oc ", "OPENCLAW_PROFILE": "Work", "OPENCLAW_STATE_DIR": " ~/st "}, "/oc/st/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "Work", "OPENCLAW_STATE_DIR": "/state", "OPENCLAW_CONFIG_PATH": " /cfg/a.json "}, "/cfg/a.json"),
        ({"HOME": "/h", "OPENCLAW_PROFILE": "Work", "OPENCLAW_STATE_DIR": " ", "OPENCLAW_CONFIG_PATH": " "}, "/h/.openclaw-Work/openclaw.json"),
        ({"HOME": "/h", "OPENCLAW_HOME": "/oc", "OPENCLAW_PROFILE": "Work", "OPENCLAW_STATE_DIR": "/state", "OPENCLAW_CONFIG_PATH": " ~/a.json "}, "/oc/a.json"),
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
    assert result["mcp"]["servers"]["tracelattice"]["enabled"] is True
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


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"command": "other"},
        {"command": "custom", "args": ["--flag", "value"], "env": {"CUSTOM": "kept"}, "transport": "custom", "timeout": 42, "custom": {"option": "kept"}},
    ],
)
def test_existing_tracelattice_object_without_enabled_is_migrated(config: Path, entry: dict[str, str | int | list[str] | dict[str, str]]) -> None:
    other = {"docs": {"url": "https://example.test/mcp"}}
    original = ENGINE | {"mcp": {"sessionIdleTtlMs": 5, "servers": other | {"tracelattice": entry}}}
    put(config, original)

    assert run() == []

    assert json.loads(config.read_bytes()) == ENGINE | {"mcp": {"sessionIdleTtlMs": 5, "servers": other | {"tracelattice": entry | {"enabled": True}}}}
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("entry", [{"enabled": False, "command": "x", "env": {"CUSTOM": "kept"}}, {"enabled": True, "command": "other"}, {"enabled": None}, None, [], "custom"])
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


@pytest.mark.parametrize("modes", [{"OPENCLAW_CONFIG_READONLY": "1"}, {"OPENCLAW_NIX_MODE": "1"}, {"OPENCLAW_CONFIG_READONLY": "1", "OPENCLAW_NIX_MODE": "1"}])
@pytest.mark.parametrize("document", [ENGINE, {"mcp": {"servers": {"tracelattice": {"command": "custom"}}}}, b"not json"])
def test_immutable_modes_are_silent_and_leave_config_snapshot_unchanged(config: Path, monkeypatch: pytest.MonkeyPatch, modes: dict[str, str], document: object) -> None:
    put(config, document)
    before = snapshot(config)
    for name, value in modes.items():
        monkeypatch.setenv(name, value)

    assert run() == []

    assert snapshot(config) == before
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("name", ["OPENCLAW_CONFIG_READONLY", "OPENCLAW_NIX_MODE"])
@pytest.mark.parametrize("value", ["", "0", "true", " 1", "1 "])
def test_immutable_modes_require_exact_one(config: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    put(config, ENGINE)
    monkeypatch.setenv(name, value)

    assert run() == []

    assert json.loads(config.read_bytes()) == ENGINE | {"mcp": {"servers": {"tracelattice": TRACELATTICE}}}


@pytest.mark.parametrize("location", [(), ("mcp",), ("mcp", "servers"), ("mcp", "servers", "tracelattice")])
@pytest.mark.parametrize("inherited", [None, {"enabled": False, "command": "inherited"}, {"command": "inherited"}], ids=["absent", "disabled", "custom"])
@pytest.mark.parametrize("explicit_siblings", [False, True])
def test_relevant_includes_preserve_inherited_settings_and_explicit_siblings(config: Path, location: tuple[str, ...], inherited: dict[str, object] | None, explicit_siblings: bool) -> None:
    inherited_config: dict[str, object] = {"mcp": {"servers": {} if inherited is None else {"tracelattice": inherited}}}
    included: object = inherited_config
    for key in location:
        included = included.get(key, {})
    include_file = config.with_name("inherited.json")
    put(include_file, included)
    document: dict[str, object] = {"$include": "inherited.json"}
    if explicit_siblings:
        sibling: dict[str, object] = {"command": "explicit", "args": ["kept"]}
        for key in reversed(("mcp", "servers", "tracelattice")[len(location):]):
            sibling = {key: sibling}
        document.update(sibling)
    for key in reversed(location):
        document = {key: document}
    put(config, document)
    before = snapshot(config), snapshot(include_file)

    assert run() == []

    assert (snapshot(config), snapshot(include_file)) == before
    assert names(config.parent) == ["inherited.json", "openclaw.json"]


@pytest.mark.parametrize("location", [(), ("mcp",), ("mcp", "servers"), ("mcp", "servers", "tracelattice")])
def test_relevant_includes_are_not_resolved_even_when_missing(config: Path, location: tuple[str, ...]) -> None:
    document: dict[str, object] = {"$include": "missing.json"}
    for key in reversed(location):
        document = {key: document}
    put(config, document)
    before = snapshot(config)

    assert run() == []

    assert snapshot(config) == before
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize(
    "independent",
    [
        {"agents": {"$include": "missing.json"}},
        {"mcp": {"servers": {"other": {"$include": "missing.json"}}}},
        {"mcp": {"servers": {"tracelattice": {"command": "custom", "env": {"$include": "missing.json"}}}}},
    ],
)
def test_independent_includes_do_not_prevent_registration(config: Path, independent: dict[str, object]) -> None:
    put(config, independent)
    expected = json.loads(config.read_bytes())
    servers = expected.setdefault("mcp", {}).setdefault("servers", {})
    if "tracelattice" in servers:
        servers["tracelattice"]["enabled"] = True
    else:
        servers["tracelattice"] = TRACELATTICE

    assert run() == []

    assert json.loads(config.read_bytes()) == expected
    assert names(config.parent) == ["openclaw.json"]


@pytest.mark.parametrize("profile", [" Team_2 ", "default", " DEFAULT ", "", "  ", "../invalid", "-bad", "x" * 65])
@pytest.mark.parametrize("selection", ["home", "openclaw_home", "state", "config"])
def test_registration_only_changes_selected_profile_config(tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch, profile: str, selection: str) -> None:
    home = tmp_path / "home"
    effective_home = tmp_path / "effective-home" if selection != "home" else home
    named = ".openclaw-Team_2" if profile.strip() == "Team_2" else ".openclaw"
    selected = effective_home / named / "openclaw.json"
    environ = {"HOME": f" {home} ", "OPENCLAW_PROFILE": profile}
    if selection != "home":
        environ["OPENCLAW_HOME"] = f" {effective_home} "
    if selection in {"state", "config"}:
        environ["OPENCLAW_STATE_DIR"] = " ~/state "
        selected = effective_home / "state/openclaw.json"
    if selection == "config":
        environ["OPENCLAW_CONFIG_PATH"] = " ~/explicit.json "
        selected = effective_home / "explicit.json"
    candidates = {config, selected, home / ".openclaw/openclaw.json", effective_home / ".openclaw/openclaw.json", effective_home / named / "openclaw.json", effective_home / "state/openclaw.json"}
    for candidate in candidates:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        put(candidate, ENGINE)
    untouched = {candidate: snapshot(candidate) for candidate in candidates if candidate != selected}
    directories = {candidate.parent: names(candidate.parent) for candidate in candidates}
    monkeypatch.setenv("OPENCLAW_CONFIG_READONLY", "1")
    monkeypatch.setenv("OPENCLAW_NIX_MODE", "1")

    assert run(environ=environ) == []

    assert json.loads(selected.read_bytes()) == ENGINE | {"mcp": {"servers": {"tracelattice": TRACELATTICE}}}
    assert {candidate: snapshot(candidate) for candidate in untouched} == untouched
    assert {directory: names(directory) for directory in directories} == directories


@pytest.mark.parametrize("name", ["OPENCLAW_CONFIG_READONLY", "OPENCLAW_NIX_MODE"])
def test_supplied_environment_controls_immutable_policy(config: Path, name: str) -> None:
    put(config, ENGINE)
    before = snapshot(config)

    assert run(environ={"OPENCLAW_CONFIG_PATH": str(config), name: "1"}) == []

    assert snapshot(config) == before


def test_missing_named_profile_never_falls_back_to_default(tmp_path: Path) -> None:
    default = tmp_path / ".openclaw/openclaw.json"
    default.parent.mkdir()
    put(default, ENGINE)
    before = snapshot(default)

    assert run(environ={"HOME": str(tmp_path), "OPENCLAW_PROFILE": " Team "}) == []

    assert snapshot(default) == before
    assert names(tmp_path) == [".openclaw"]
    assert names(default.parent) == ["openclaw.json"]


@pytest.mark.parametrize(
    "environ,relative",
    [
        ({"HOME": " home ", "OPENCLAW_PROFILE": "Team"}, "home/.openclaw-Team/openclaw.json"),
        ({"HOME": "/home", "OPENCLAW_HOME": " effective ", "OPENCLAW_PROFILE": "Team"}, "effective/.openclaw-Team/openclaw.json"),
        ({"HOME": "/home", "OPENCLAW_STATE_DIR": " state ", "OPENCLAW_PROFILE": "Team"}, "state/openclaw.json"),
        ({"HOME": "/home", "OPENCLAW_CONFIG_PATH": " config.json ", "OPENCLAW_PROFILE": "Team"}, "config.json"),
    ],
)
def test_relative_path_overrides_are_invocation_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], relative: str) -> None:
    monkeypatch.chdir(tmp_path)
    selected = tmp_path / relative
    selected.parent.mkdir(parents=True, exist_ok=True)
    put(selected, ENGINE)

    assert run(environ=environ) == []

    assert json.loads(selected.read_bytes()) == ENGINE | {"mcp": {"servers": {"tracelattice": TRACELATTICE}}}
