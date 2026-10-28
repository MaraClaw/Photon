#!/bin/sh
set -eu

source=/tmp/linkup-skills
stage=$(mktemp -d "${TMPDIR:-/tmp}/linkup.XXXXXXXX")
cleanup() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$status" -ne 0 ]; then
        failed=0
        for marker in "$stage"/changed/*; do
            [ -f "$marker" ] || continue
            name=${marker##*/}
            if ! rm -rf -- "${destination:?}/${name:?}"; then
                failed=1
            elif [ -e "$stage/backup/$name" ] || [ -L "$stage/backup/$name" ]; then
                cp -a -- "$stage/backup/$name" "$destination/$name" || failed=1
            fi
        done
        if [ "$failed" -ne 0 ]; then
            printf 'Linkup restoration failed; backups retained at %s\n' "$stage" >&2
            exit 1
        fi
    fi
    rm -rf -- "$stage" || exit 1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$stage/incoming" "$stage/backup" "$stage/changed"
python3 - "$source/manifest.json" > "$stage/names" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as manifest:
    names = json.load(manifest)["skills"]
if not isinstance(names, list) or not names or any(
    not isinstance(name, str) or not re.fullmatch(r"linkup-[a-z0-9]+(?:-[a-z0-9]+)*", name)
    for name in names
) or len(set(names)) != len(names):
    sys.exit("invalid Linkup skills manifest")
print("\n".join(names))
PY
while IFS= read -r name; do
    if [ ! -d "$source/$name" ] || [ -L "$source/$name" ] ||
       [ ! -f "$source/$name/SKILL.md" ] || [ ! -s "$source/$name/SKILL.md" ]; then
        printf 'invalid Linkup skill payload: %s\n' "$name" >&2
        exit 1
    fi
    cp -a -- "$source/$name" "$stage/incoming/$name"
    chmod -R a+rX "$stage/incoming/$name"
done < "$stage/names"
python3 - "$stage/incoming" <<'PY'
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
for directory, directories, files in os.walk(root, followlinks=False):
    for name in directories + files:
        path = Path(directory, name)
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(root)
            except (OSError, RuntimeError, ValueError):
                sys.exit(f"invalid Linkup payload symlink: {path}")
PY
destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
while IFS= read -r name; do
    if [ -e "$destination/$name" ] || [ -L "$destination/$name" ]; then
        cp -a -- "$destination/$name" "$stage/backup/$name"
    fi
done < "$stage/names"
while IFS= read -r name; do
    : > "$stage/changed/$name"
    rm -rf -- "${destination:?}/${name:?}"
    cp -a -- "$stage/incoming/$name" "$destination/$name"
done < "$stage/names"
rm -rf -- "$source"
