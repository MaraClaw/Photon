#!/bin/sh
set -eu

valid=1
for binary in /usr/local/bin/gog /usr/local/bin/officecli; do
    if ! "$binary" --version >/dev/null 2>&1; then
        printf 'Required CLI failed verification: %s\n' "$binary" >&2
        valid=0
    fi
done
[ "$valid" -eq 1 ] || exit 1

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
