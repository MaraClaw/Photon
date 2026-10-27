#!/bin/sh
set -eu

if ! "/usr/local/bin/officecli" --version >/dev/null 2>&1; then
    printf 'Required CLI failed verification: %s\n' "/usr/local/bin/officecli" >&2
    exit 1
fi

if ! npm_root="$(npm root -g 2>/dev/null)" || [ -z "$npm_root" ]; then
    printf '%s\n' 'Required skill failed verification: officecli' >&2
    exit 1
fi

skill_dir="$npm_root/openclaw/skills/officecli"
if [ ! -f "$skill_dir/SKILL.md" ] || [ ! -r "$skill_dir/SKILL.md" ] || [ ! -s "$skill_dir/SKILL.md" ]; then
    printf '%s\n' 'Required skill failed verification: officecli' >&2
    exit 1
fi

exec "$@"
