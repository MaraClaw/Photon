#!/bin/sh
set -eu

if ! "/usr/local/bin/gog" --version >/dev/null 2>&1; then
    printf 'Required CLI failed verification: %s\n' "/usr/local/bin/gog" >&2
    exit 1
fi

if ! npm_root="$(npm root -g 2>/dev/null)" || [ -z "$npm_root" ]; then
    printf '%s\n' 'Required skill failed verification: gog' >&2
    exit 1
fi

skill_dir="$npm_root/openclaw/skills/gog"
if [ ! -f "$skill_dir/SKILL.md" ] || [ ! -r "$skill_dir/SKILL.md" ] || [ ! -s "$skill_dir/SKILL.md" ]; then
    printf '%s\n' 'Required skill failed verification: gog' >&2
    exit 1
fi

if [ -z "${GOG_KEYRING_PASSWORD_FILE:-}" ]; then
    exec "$@"
fi

if [ ! -f "$GOG_KEYRING_PASSWORD_FILE" ]; then
    printf '%s\n' "GOG_KEYRING_PASSWORD_FILE does not exist: $GOG_KEYRING_PASSWORD_FILE" >&2
    exit 1
fi

if [ -z "${GOG_HOME:-}" ]; then
    printf '%s\n' "GOG_HOME must be set when GOG_KEYRING_PASSWORD_FILE is used" >&2
    exit 1
fi

GOG_KEYRING_PASSWORD=$(cat "$GOG_KEYRING_PASSWORD_FILE")
export GOG_KEYRING_PASSWORD

if [ "${GOG_HOME:-}" ]; then
    mkdir -p "$GOG_HOME"
    chmod 0700 "$GOG_HOME"
fi

exec "$@"
