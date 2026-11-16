"""Built-in MCP servers of the image, registered in the active OpenClaw config right before the gateway starts.

OpenClaw reads MCP servers only from mcp.servers of the active config. The engine writes that file as strict JSON (json.dumps,
indent 2) and keeps guest-written mcp.servers entries. Missing built-in servers are added with enabled true; existing objects
gain only a missing enabled flag, preserving explicit choices and all custom settings. Immutable modes and includes on the
MCP settings path are silent no-ops; other unsafe configs are reported and left untouched. Registration never blocks the gateway.
"""

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, NoReturn

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

SERVERS: Final[dict[str, dict[str, str | bool]]] = {"tracelattice": {"command": "/usr/local/bin/tracelattice", "transport": "stdio", "enabled": True}}
CONFIG_NAME: Final = "openclaw.json"
TEMP_PREFIX: Final = f".{CONFIG_NAME}."


def expand(value: str, home: str) -> Path:
    expanded = home + value[1:] if value == "~" or value.startswith(("~/", "~\\")) else value
    return Path(os.path.abspath(expanded))


def config_path(environ: Mapping[str, str]) -> Path:
    """Select the active config: explicit config, explicit state, then the normalized profile under the effective home."""
    base = environ.get("HOME", "").strip() or os.path.expanduser("~")
    configured = environ.get("OPENCLAW_HOME", "").strip()
    home = str(expand(configured, base)) if configured else os.path.abspath(base)
    if explicit := environ.get("OPENCLAW_CONFIG_PATH", "").strip():
        return expand(explicit, home)
    if state := environ.get("OPENCLAW_STATE_DIR", "").strip():
        return expand(state, home) / CONFIG_NAME
    profile = environ.get("OPENCLAW_PROFILE", "").strip()
    named = profile.lower() != "default" and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", profile) is not None
    return Path(home) / (f".openclaw-{profile}" if named else ".openclaw") / CONFIG_NAME


def unique_pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    document = dict(pairs)
    if len(document) != len(pairs):
        raise ValueError("duplicate object key")
    return document


def reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


def parse(raw: bytes) -> dict[str, JsonValue]:
    """Strict JSON only: re-dumping what OpenClaw's JSON5 reader accepts but this cannot round-trip would lose data."""
    document: JsonValue = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    if not isinstance(document, dict):
        raise ValueError("top level is not an object")
    return document


def object_at(parent: dict[str, JsonValue], key: str, label: str) -> dict[str, JsonValue]:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def add_missing_servers(document: dict[str, JsonValue]) -> bool:
    """Add absent servers and enabled flags without overriding settings inherited through relevant includes."""
    if "$include" in document:
        return False
    mcp = object_at(document, "mcp", "mcp")
    if "$include" in mcp:
        return False
    servers = object_at(mcp, "servers", "mcp.servers")
    if "$include" in servers or any(isinstance(entry := servers.get(name), dict) and "$include" in entry for name in SERVERS):
        return False
    missing: dict[str, JsonValue] = {name: {**server} for name, server in SERVERS.items() if name not in servers}
    changed = bool(missing)
    for name in SERVERS:
        existing = servers.get(name)
        if isinstance(existing, dict) and "enabled" not in existing:
            existing["enabled"] = True
            changed = True
    if not changed:
        return False
    servers.update(missing)
    mcp["servers"] = servers
    document["mcp"] = mcp
    return True


def signature(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_ino, info.st_mtime_ns, info.st_size


def replace_atomically(path: Path, content: str, before: os.stat_result) -> None:
    """Write a sibling file with the original mode, then rename it over the config unless the config changed meanwhile."""
    descriptor, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if signature(os.lstat(path)) != signature(before):
            raise ValueError("changed while being updated")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update(path: Path) -> None:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("not a regular file")
    if before.st_uid != os.geteuid():
        raise ValueError("owned by another user")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        document = parse(stream.read())
    if add_missing_servers(document):
        replace_atomically(path, json.dumps(document, indent=2, allow_nan=False), before)


def register(report: Callable[[str], None], *, environ: Mapping[str, str] | None = None) -> None:
    """Register in the selected environment; immutable modes and include-backed settings are silent no-ops."""
    environment = os.environ if environ is None else environ
    if environment.get("OPENCLAW_CONFIG_READONLY") == "1" or environment.get("OPENCLAW_NIX_MODE") == "1":
        return
    path = config_path(environment)
    try:
        update(path)
    except (OSError, ValueError, RecursionError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
        report(f"built-in MCP server {', '.join(SERVERS)} not registered: {path}: {reason}")
