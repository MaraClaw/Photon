"""Built-in MCP servers of the image, registered in the active OpenClaw config right before the gateway starts.

OpenClaw reads MCP servers only from mcp.servers of the active config. The engine writes that file as strict JSON (json.dumps,
indent 2) and keeps guest-written mcp.servers entries, so a missing built-in server is added in place and anything already
there, enabled or not, is the guest's choice and stays as it is. Registration never blocks the gateway: a config that cannot be
updated safely is reported and left untouched.
"""

import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, NoReturn

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

SERVERS: Final[dict[str, dict[str, str]]] = {"tracelattice": {"command": "/usr/local/bin/tracelattice", "transport": "stdio"}}
CONFIG_NAME: Final = "openclaw.json"
TEMP_PREFIX: Final = f".{CONFIG_NAME}."


def expand(value: str, home: str) -> Path:
    return Path(home + value[1:]) if value == "~" or value.startswith("~/") else Path(value)


def config_path(environ: Mapping[str, str]) -> Path:
    """The active config as OpenClaw resolves it; empty values count as unset and a leading ~ is the effective home."""
    base = environ.get("HOME") or os.path.expanduser("~")
    configured = environ.get("OPENCLAW_HOME")
    home = str(expand(configured, base)) if configured else base
    if explicit := environ.get("OPENCLAW_CONFIG_PATH"):
        return expand(explicit, home)
    if state := environ.get("OPENCLAW_STATE_DIR"):
        return expand(state, home) / CONFIG_NAME
    return Path(home) / ".openclaw" / CONFIG_NAME


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
    """Add the absent built-in servers in place, keeping every other key and the key order; False when none was absent."""
    mcp = object_at(document, "mcp", "mcp")
    servers = object_at(mcp, "servers", "mcp.servers")
    missing: dict[str, JsonValue] = {name: {**server} for name, server in SERVERS.items() if name not in servers}
    if not missing:
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


def register(report: Callable[[str], None]) -> None:
    """Add the built-in servers to the active config; every failure becomes one report and never stops the gateway."""
    path = config_path(os.environ)
    try:
        update(path)
    except (OSError, ValueError, RecursionError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
        report(f"built-in MCP server {', '.join(SERVERS)} not registered: {path}: {reason}")
