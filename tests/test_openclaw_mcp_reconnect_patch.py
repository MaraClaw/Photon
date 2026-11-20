from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

PATCH: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/patch-mcp-reconnect.py"
# Small source-format fixture: the patcher must recognize the complete release seam,
# preserve unrelated code, and refuse ambiguous or changed implementations.
SOURCE: Final = '''const unrelated = async () => {
\tif (S && _) await racePromiseWithAbortSignal(_, t);
};
function runtime() {
\t\t\t\tr.onclose = () => {
\t\t\t\t\tconst e = o.connected;
\t\t\t\t\to.connected = false;
\t\t\t\t\to.disconnectReason = "mcp transport closed";
\t\t\t\t\tif (e) o.nativeBridge?.emit({ kind: "closed" });
\t\t\t\t\tif (e && !d && !o.retiring && v === o) {
\t\t\t\t\t\tS ||= o.transportType === "sse";
\t\t\t\t\t\tscheduleCatalogServerRetry("mcp transport closed");
\t\t\t\t\t\tlogWarn(`bundle-mcp: server "${i}" closed; next request reconnects`);
\t\t\t\t\t}
\t\t\t\t};
\tconst getCatalog = async (t) => {
\t\tconst n = m;
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
\t};
}
'''


def run_patch(package: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATCH), str(package)],
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=10, check=False,
    )


@pytest.fixture
def package(tmp_path: Path) -> Path:
    (tmp_path / "dist/agents").mkdir(parents=True)
    (tmp_path / "package.json").write_text(json.dumps({"version": "2026.11.6"}), encoding="utf-8")
    (tmp_path / "dist/agents/agent-bundle-mcp-runtime.js").write_text(SOURCE, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("variant", ["agents/agent-bundle-mcp-runtime.mjs", "agent-bundle-mcp-runtime-Ab12cd_3.mjs"])
def test_patch_changes_only_session_wait_and_is_idempotent(package: Path, variant: str) -> None:
    extra = package / "dist" / variant
    extra.write_text(SOURCE, encoding="utf-8")
    primary = package / "dist/agents/agent-bundle-mcp-runtime.js"

    result = run_patch(package)

    assert result.returncode == 0, result.stderr
    expected = SOURCE.replace("\t\tif (S && _)", "\t\tif ((S || !v?.connected) && _)")
    assert primary.read_text(encoding="utf-8") == expected
    assert extra.read_text(encoding="utf-8") == expected
    repeated = run_patch(package)
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stdout == ""
    assert primary.read_text(encoding="utf-8") == expected
    assert extra.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("source", [
    SOURCE.replace("getCatalog(), t", "getCatalog(), otherSignal"),
    SOURCE.replace('o.transportType === "sse"', 'o.transportType === "stdio"'),
    SOURCE + SOURCE,
    "export {};\n",
])
def test_patch_refuses_source_drift_before_any_variant_is_written(package: Path, source: str) -> None:
    extra = package / "dist/agents/agent-bundle-mcp-runtime.mjs"
    extra.write_text(source, encoding="utf-8")
    primary = package / "dist/agents/agent-bundle-mcp-runtime.js"

    result = run_patch(package)

    assert result.returncode != 0
    assert "MCP reconnect patch refused" in result.stderr
    assert primary.read_text(encoding="utf-8") == SOURCE
    assert extra.read_text(encoding="utf-8") == source


def test_patch_refuses_different_openclaw_version(package: Path) -> None:
    (package / "package.json").write_text('{"version":"2026.11.7"}', encoding="utf-8")

    result = run_patch(package)

    assert result.returncode != 0
    assert "expected OpenClaw 2026.11.6" in result.stderr
    assert (package / "dist/agents/agent-bundle-mcp-runtime.js").read_text(encoding="utf-8") == SOURCE


def test_patch_refuses_missing_exported_runtime(package: Path) -> None:
    (package / "dist/agents/agent-bundle-mcp-runtime.js").rename(package / "dist/agents/unknown.js")

    result = run_patch(package)

    assert result.returncode != 0
    assert "missing exported runtime" in result.stderr
