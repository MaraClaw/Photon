# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
# How to run: python3 patch-mcp-reconnect.py /opt/openclaw/node_modules/openclaw
"""Patch the pinned beta's disconnected-session wait, refusing source drift."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

VERSION: Final = "2026.11.5-beta.3"
RUNTIME_NAME: Final = re.compile(r"agent-bundle-mcp-runtime(?:-[\w-]{8})?\.(?:js|mjs)")
ORIGINAL: Final = '''\t\tconst n = m;
\t\tg = void 0;
\t\tloadCatalog(t).catch(() => {
\t\t\tif (!d && m === n && g === void 0) g = monotonicDeadlineAfter(BUNDLE_MCP_CATALOG_FAILURE_RETRY_MS);
\t\t});
\t\treturn n;
\t};
\tconst getActiveSession = async (e) => {
\t\tif (e !== i) throw new Error(`bundle-mcp server "${e}" is not connected`);
\t\tconst t = getSessionMcpRequestSignal();
\t\tt?.throwIfAborted();
\t\tawait racePromiseWithAbortSignal(getCatalog(), t);
\t\tif (S && _) await racePromiseWithAbortSignal(_, t);
\t\treturn requireConnectedSession();
\t};'''
PATCHED: Final = ORIGINAL.replace("if (S && _)", "if ((S || !v?.connected) && _)")
CLOSE_HANDLER: Final = '''\t\t\t\tr.onclose = () => {
\t\t\t\t\tconst e = o.connected;
\t\t\t\t\to.connected = false;
\t\t\t\t\to.disconnectReason = "mcp transport closed";
\t\t\t\t\tif (e) o.nativeBridge?.emit({ kind: "closed" });
\t\t\t\t\tif (e && !d && !o.retiring && v === o) {
\t\t\t\t\t\tS ||= o.transportType === "sse";
\t\t\t\t\t\tscheduleCatalogServerRetry("mcp transport closed");
\t\t\t\t\t\tlogWarn(`bundle-mcp: server "${i}" closed; next request reconnects`);
\t\t\t\t\t}
\t\t\t\t};'''


@dataclass(frozen=True, slots=True)
class PatchMismatchError(Exception):
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"MCP reconnect patch refused {self.path}: {self.reason}"


def patch_package(package_root: Path) -> tuple[Path, ...]:
    """Validate every runtime variant before changing any installed source."""
    manifest = package_root / "package.json"
    if json.loads(manifest.read_text(encoding="utf-8")).get("version") != VERSION:
        raise PatchMismatchError(manifest, f"expected OpenClaw {VERSION}")
    dist = package_root / "dist"
    paths = tuple(sorted(
        path for path in dist.rglob("agent-bundle-mcp-runtime*")
        if RUNTIME_NAME.fullmatch(path.name)
    ))
    entry = dist / "agents" / "agent-bundle-mcp-runtime.js"
    if entry not in paths:
        raise PatchMismatchError(entry, "missing exported runtime")
    changes: list[tuple[Path, str]] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if (
            source.count(ORIGINAL) + source.count(PATCHED) != 1
            or source.count("const getActiveSession =") != 1
            or source.count(CLOSE_HANDLER) != 1
        ):
            raise PatchMismatchError(path, "expected exactly one known catalog/session/close implementation")
        if ORIGINAL in source:
            changes.append((path, source.replace(ORIGINAL, PATCHED, 1)))
    for path, source in changes:
        path.write_text(source, encoding="utf-8")
    return tuple(path for path, _ in changes)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-mcp-reconnect.py OPENCLAW_PACKAGE_ROOT")
    for changed in patch_package(Path(sys.argv[1])):
        print(f"Patched MCP reconnect wait: {changed}")
