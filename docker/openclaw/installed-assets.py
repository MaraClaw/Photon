#!/usr/bin/env python3
import argparse
import contextlib
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Final

COMPONENTS: Final = ("gogcli", "officecli", "soul-guardian", "linkup")
DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class InventoryError(Exception):
    pass


def scan_entry(parent: int, name: str, relative: str, entries: dict[str, str]) -> None:
    mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
    if stat.S_ISDIR(mode):
        entries[relative] = "directory"
        with contextlib.ExitStack() as stack:
            directory = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            stack.callback(os.close, directory)
            with os.scandir(directory) as children:
                names = sorted(child.name for child in children)
            for child in names:
                scan_entry(directory, child, f"{relative}/{child}", entries)
    elif stat.S_ISREG(mode):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as contents:
            entries[relative] = "sha256:" + hashlib.file_digest(contents, "sha256").hexdigest()
    elif stat.S_ISLNK(mode):
        target = os.readlink(name, dir_fd=parent)
        if os.path.isabs(target):
            raise InventoryError(f"absolute symlink: {relative}")
        entries[relative] = "symlink:" + target
    else:
        raise InventoryError(f"unsupported asset: {relative}")


def scan_root(path: str) -> dict[str, str]:
    root = Path(path)
    if not root.is_absolute() or ".." in root.parts or root == Path("/"):
        raise InventoryError(f"invalid asset root: {path}")
    entries: dict[str, str] = {}
    with contextlib.ExitStack() as stack:
        parent = os.open("/", DIRECTORY_FLAGS)
        stack.callback(os.close, parent)
        for part in root.parts[1:-1]:
            parent = os.open(part, DIRECTORY_FLAGS, dir_fd=parent)
            stack.callback(os.close, parent)
        scan_entry(parent, root.name, ".", entries)
    return entries


def create(skills_root: Path, roots_file: Path, output_dir: Path) -> None:
    if not skills_root.is_absolute() or ".." in skills_root.parts:
        raise InventoryError("skills root must be an absolute path")
    names = roots_file.read_text(encoding="utf-8").splitlines()
    if not names or len(names) != len(set(names)) or any(not name or name in {".", ".."} or "/" in name for name in names):
        raise InventoryError("invalid gog skill ownership list")
    roots = {
        "gogcli": [Path("/usr/local/bin/gog"), *(skills_root / name for name in names)],
        "officecli": [Path("/usr/local/bin/officecli"), skills_root / "officecli"],
        "soul-guardian": [skills_root / "soul-guardian"],
        "linkup": [skills_root / "linkup-search", skills_root / "linkup-fetch"],
    }
    inventories = {
        component: [{"path": str(root), "entries": scan_root(str(root))} for root in paths]
        for component, paths in roots.items()
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for component, inventory in inventories.items():
        record = {"skills_root": str(skills_root), "roots": inventory}
        (output_dir / f"{component}.json").write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")


def verify(component: str, inventory_dir: Path, skills_root: Path | None) -> None:
    inventory = json.loads((inventory_dir / f"{component}.json").read_text(encoding="utf-8"))
    if skills_root is not None and str(skills_root) != inventory["skills_root"]:
        raise InventoryError("discovered skills root differs from inventory")
    roots = inventory["roots"]
    if not isinstance(roots, list) or not roots:
        raise InventoryError("invalid inventory")
    for root in roots:
        if scan_root(root["path"]) != root["entries"]:
            raise InventoryError(f"installed assets changed: {root['path']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--skills-root", type=Path, required=True)
    create_parser.add_argument("--gog-roots-file", type=Path, required=True)
    create_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--component", choices=COMPONENTS, required=True)
    verify_parser.add_argument("--inventory-dir", type=Path, required=True)
    verify_parser.add_argument("--skills-root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create(args.skills_root, args.gog_roots_file, args.output_dir)
        else:
            verify(args.component, args.inventory_dir, args.skills_root)
    except (OSError, InventoryError, ValueError, KeyError, TypeError) as error:
        print(f"Installed asset verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
