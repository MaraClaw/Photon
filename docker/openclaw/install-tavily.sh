#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

source=/tmp/tavily-skills
install_stage_init tavily.XXXXXXXX 'Tavily restoration failed; backups'
mkdir "$stage/incoming" "$stage/backup" "$stage/changed"
python3 - "$source/manifest.json" > "$stage/names" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as manifest:
    names = json.load(manifest)["skills"]
if not isinstance(names, list) or not names or any(
    not isinstance(name, str) or not re.fullmatch(r"tavily-[a-z0-9]+(?:-[a-z0-9]+)*", name)
    for name in names
) or len(set(names)) != len(names):
    sys.exit("invalid Tavily skills manifest")
print("\n".join(names))
PY
while IFS= read -r name; do
    if [ ! -d "$source/$name" ] || [ -L "$source/$name" ] ||
       [ ! -f "$source/$name/SKILL.md" ] || [ ! -s "$source/$name/SKILL.md" ]; then
        printf 'invalid Tavily skill payload: %s\n' "$name" >&2
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
                sys.exit(f"invalid Tavily payload symlink: {path}")
PY
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
while IFS= read -r name; do
    install_save "$destination/$name" "$stage/backup/$name"
done < "$stage/names"
while IFS= read -r name; do
    install_mark_changed "$name"
    rm -rf -- "${destination:?}/${name:?}"
    cp -a -- "$stage/incoming/$name" "$destination/$name"
done < "$stage/names"
rm -rf -- "$source"
