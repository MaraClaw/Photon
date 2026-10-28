#!/bin/sh
set -eu

OFFICECLI_TAG="v1.0.159"
OFFICECLI_ARM64_SHA256="a27778548f2820771f54a70887af3c3ec214fe60e63b32836b09b3f3e2c4c493"
OFFICECLI_AMD64_SHA256="92d9aeed324d91ed2a107f7f8dc7468829b14390a66cf2bbdfb25c40beac50ea"
OFFICECLI_SKILL_SHA256="c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4"
readonly OFFICECLI_TAG OFFICECLI_ARM64_SHA256 OFFICECLI_AMD64_SHA256 OFFICECLI_SKILL_SHA256

binary_file=
skill_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --binary-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$binary_file" ] || exit 2; binary_file=$2; shift 2 ;;
        --skill-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$skill_file" ] || exit 2; skill_file=$2; shift 2 ;;
        *) printf 'invalid OfficeCLI argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if { [ -n "$binary_file" ] && [ -z "$skill_file" ]; } || { [ -z "$binary_file" ] && [ -n "$skill_file" ]; }; then
    printf 'both --binary-file and --skill-file are required\n' >&2
    exit 2
fi

case "${TARGETOS:-}/${TARGETARCH:-}" in
    linux/amd64) asset=officecli-linux-x64; digest="$OFFICECLI_AMD64_SHA256" ;;
    linux/arm64) asset=officecli-linux-arm64; digest="$OFFICECLI_ARM64_SHA256" ;;
    *) printf 'unsupported OfficeCLI target: %s/%s\n' "${TARGETOS:-}" "${TARGETARCH:-}" >&2; exit 2 ;;
esac

stage="$(mktemp -d "${TMPDIR:-/tmp}/officecli.XXXXXXXXXX")"
binary_destination=/usr/local/bin/officecli
skill_destination=
binary_touched=0
skill_touched=0
binary_saved=0
skill_saved=0
committed=0
restore_failed=0
restore_path() {
    if ! rm -rf -- "$1"; then
        printf 'OfficeCLI rollback could not remove %s\n' "$1" >&2
        restore_failed=1
    elif [ "$2" = 1 ] && ! cp -a -- "$3" "$1"; then
        printf 'OfficeCLI rollback could not restore %s\n' "$1" >&2
        restore_failed=1
    fi
}
finish() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$committed" = 0 ]; then
        if [ "$skill_touched" = 1 ]; then
            restore_path "$skill_destination" "$skill_saved" "$stage/backup/skill"
        fi
        if [ "$binary_touched" = 1 ]; then
            restore_path "$binary_destination" "$binary_saved" "$stage/backup/officecli"
        fi
    fi
    if [ "$restore_failed" = 1 ]; then
        printf 'OfficeCLI rollback failed; backups retained at %s\n' "$stage" >&2
        exit 1
    fi
    if ! rm -rf -- "$stage"; then
        printf 'OfficeCLI temporary data could not be removed: %s\n' "$stage" >&2
        exit 1
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
curl_safe() {
    curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' "$@"
}
if [ -n "$binary_file" ]; then
    cp -- "$binary_file" "$stage/officecli"
    cp -- "$skill_file" "$stage/SKILL.md"
else
    curl_safe -o "$stage/officecli" "https://github.com/iOfficeAI/OfficeCLI/releases/download/$OFFICECLI_TAG/$asset"
    curl_safe -o "$stage/SKILL.md" "https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/$OFFICECLI_TAG/SKILL.md"
fi
printf '%s  %s\n%s  %s\n' "$digest" "$stage/officecli" "$OFFICECLI_SKILL_SHA256" "$stage/SKILL.md" | sha256sum -c -
install -d -m 0755 "$stage/payload/skill" "$stage/backup"
install -m 0755 "$stage/officecli" "$stage/payload/officecli"
install -m 0644 "$stage/SKILL.md" "$stage/payload/skill/SKILL.md"
skill_destination="$(npm root -g)/openclaw/skills/officecli"
if [ -e "$binary_destination" ] || [ -L "$binary_destination" ]; then
    cp -a -- "$binary_destination" "$stage/backup/officecli"
    binary_saved=1
fi
if [ -e "$skill_destination" ] || [ -L "$skill_destination" ]; then
    cp -a -- "$skill_destination" "$stage/backup/skill"
    skill_saved=1
fi
skill_parent="$(dirname "$skill_destination")"
if [ ! -d "$skill_parent" ]; then
    install -d -m 0755 "$skill_parent"
fi
binary_touched=1
rm -rf -- "$binary_destination"
cp -a -- "$stage/payload/officecli" "$binary_destination"
skill_touched=1
rm -rf -- "$skill_destination"
cp -a -- "$stage/payload/skill" "$skill_destination"
"$binary_destination" --version
committed=1
