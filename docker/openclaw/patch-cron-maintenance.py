#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
# Usage: python3 patch-cron-maintenance.py /opt/openclaw/node_modules/openclaw
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

VERSION: Final = "2026.11.2"
MODULE: Final = Path("dist/maintenance-ClKvIKaH.mjs")
PREIMAGE_SHA256: Final = "2436235c6676045be4b339e6c51d5a53688ec80933bcccfc99d550550eaab6ee"
IMPORT: Final = b'import { AsyncLocalStorage } from "node:async_hooks";\n'
ANCHOR: Final = b"\tscope = scheduler.scope();\n"
BINDING: Final = b"\tscope.schedule = AsyncLocalStorage.bind(scope.schedule);\n"


@dataclass(frozen=True, slots=True)
class PatchError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def patch(package_root: Path) -> None:
    """Bind maintenance scheduling only after verifying the pinned package preimage."""
    try:
        version = json.loads((package_root / "package.json").read_text(encoding="utf-8"))["version"]
    except (json.JSONDecodeError, UnicodeError, KeyError, TypeError) as error:
        raise PatchError(f"invalid package.json: {error}") from error
    if version != VERSION:
        raise PatchError(f"expected OpenClaw version {VERSION}, found {version!r}")

    module = package_root / MODULE
    source = module.read_bytes()
    if BINDING in source:
        raise PatchError(f"{module}: cron patch already applied")
    anchors = source.count(ANCHOR)
    if anchors != 1:
        raise PatchError(f"{module}: expected exactly one maintenance scope anchor, found {anchors}")
    digest = hashlib.sha256(source).hexdigest()
    if digest != PREIMAGE_SHA256:
        raise PatchError(f"{module}: SHA256 mismatch: expected {PREIMAGE_SHA256}, found {digest}")

    module.write_bytes(IMPORT + source.replace(ANCHOR, ANCHOR + BINDING, 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the pinned OpenClaw maintenance cron context patch")
    parser.add_argument("package_root", type=Path)
    args = parser.parse_args()
    try:
        patch(args.package_root)
    except (PatchError, OSError) as error:
        print(f"OpenClaw cron patch refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
