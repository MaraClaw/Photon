import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
PATCHER: Final = ROOT / "docker/openclaw/patch-cron-maintenance.py"
MODULE: Final = Path("dist/maintenance-ClKvIKaH.mjs")
CORRUPTED: Final = b"// corrupted package\n\tscope = scheduler.scope();\n"


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"version": "2026.11.2"}), encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / MODULE).write_bytes(CORRUPTED)
    return tmp_path


def run_patch(package_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATCHER), str(package_root)],
        capture_output=True, text=True, check=False, timeout=10,
    )


@pytest.mark.parametrize(("manifest", "diagnostic"), [
    (None, "package.json"),
    ("{broken", "invalid package.json"),
    ("[]", "invalid package.json"),
    ("{}", "invalid package.json"),
    ('{"version": "2026.11.3"}', "expected OpenClaw version 2026.11.2"),
    ('{"version": null}', "expected OpenClaw version 2026.11.2"),
])
def test_invalid_package_metadata_is_rejected_before_writing(
    package_root: Path, manifest: str | None, diagnostic: str,
) -> None:
    # Given
    metadata = package_root / "package.json"
    if manifest is None:
        metadata.unlink()
    else:
        metadata.write_text(manifest, encoding="utf-8")
    source = (package_root / MODULE).read_bytes()

    # When
    result = run_patch(package_root)

    # Then
    assert result.returncode == 1
    assert result.stdout == ""
    assert "OpenClaw cron patch refused:" in result.stderr
    assert diagnostic in result.stderr
    assert (package_root / MODULE).read_bytes() == source


def test_missing_maintenance_module_is_rejected_without_creating_it(package_root: Path) -> None:
    # Given
    module = package_root / MODULE
    module.unlink()

    # When
    result = run_patch(package_root)

    # Then
    assert result.returncode == 1
    assert str(MODULE) in result.stderr
    assert not module.exists()


@pytest.mark.parametrize(("source", "diagnostic"), [
    (CORRUPTED, "SHA256 mismatch"),
    (b"", "expected exactly one maintenance scope anchor, found 0"),
    (CORRUPTED * 2, "expected exactly one maintenance scope anchor, found 2"),
    (
        b'import { AsyncLocalStorage } from "node:async_hooks";\n'
        b"\tscope = scheduler.scope();\n"
        b"\tscope.schedule = AsyncLocalStorage.bind(scope.schedule);\n",
        "cron patch already applied",
    ),
])
def test_unexpected_maintenance_module_is_rejected_before_writing(
    package_root: Path, source: bytes, diagnostic: str,
) -> None:
    # Given
    module = package_root / MODULE
    module.write_bytes(source)
    metadata = (package_root / "package.json").read_bytes()

    # When
    result = run_patch(package_root)

    # Then
    assert result.returncode == 1
    assert result.stdout == ""
    assert diagnostic in result.stderr
    assert module.read_bytes() == source
    assert (package_root / "package.json").read_bytes() == metadata
