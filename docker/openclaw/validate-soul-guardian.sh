#!/bin/sh
set -eu

if ! npm_root="$(npm root -g 2>/dev/null)" || [ -z "$npm_root" ]; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

skill_dir="$npm_root/openclaw/skills/soul-guardian"
for asset in SKILL.md CHANGELOG.md skill.json LICENSE scripts/soul_guardian.py scripts/onboard_state_dir.py; do
    if [ ! -f "$skill_dir/$asset" ] || [ ! -r "$skill_dir/$asset" ] || [ ! -s "$skill_dir/$asset" ]; then
        printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
        exit 1
    fi
done

if ! python3 -B -c '
import json
import sys
from pathlib import Path

with open(sys.argv[1], encoding="utf-8") as metadata:
    json.load(metadata)
compile(Path(sys.argv[2]).read_bytes(), sys.argv[2], "exec")
' "$skill_dir/skill.json" "$skill_dir/scripts/onboard_state_dir.py" >/dev/null 2>&1; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

if ! python3 -B "$skill_dir/scripts/soul_guardian.py" --help >/dev/null 2>&1; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

exec "$@"
