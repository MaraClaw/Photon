#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

OFFICECLI_VERSION="${OFFICECLI_VERSION:-v1.0.160}"
OFFICECLI_ARM64_SHA256="${OFFICECLI_ARM64_SHA256:-e8f06aff6c59f11a73a1a1218bda3ce170299201fd56bce6d62ecd4d0c024e53}"
OFFICECLI_AMD64_SHA256="${OFFICECLI_AMD64_SHA256:-baa084b64ec634bd9d41a0592db3ed85f11658b358bab898679537ea7f3e73a9}"
OFFICECLI_SKILL_SHA256="${OFFICECLI_SKILL_SHA256:-c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4}"
readonly OFFICECLI_VERSION OFFICECLI_ARM64_SHA256 OFFICECLI_AMD64_SHA256 OFFICECLI_SKILL_SHA256

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

binary_destination=/usr/local/bin/officecli
skill_destination=
binary_touched=0
skill_touched=0
committed=0
restore_failed=0
restore_path() {
    if ! rm -rf -- "$1"; then
        printf 'OfficeCLI rollback could not remove %s\n' "$1" >&2
        restore_failed=1
    elif { [ -e "$2" ] || [ -L "$2" ]; } && ! cp -a -- "$2" "$1"; then
        printf 'OfficeCLI rollback could not restore %s\n' "$1" >&2
        restore_failed=1
    fi
}
install_rollback() {
    [ "$committed" = 0 ] || return 0
    if [ "$skill_touched" = 1 ]; then
        restore_path "$skill_destination" "$stage/backup/skill"
    fi
    if [ "$binary_touched" = 1 ]; then
        restore_path "$binary_destination" "$stage/backup/officecli"
    fi
    return "$restore_failed"
}
install_stage_init officecli.XXXXXXXXXX 'OfficeCLI rollback failed; backups' 'OfficeCLI temporary data could not be removed:'
if [ -n "$binary_file" ]; then
    cp -- "$binary_file" "$stage/officecli"
    cp -- "$skill_file" "$stage/SKILL.md"
else
    install_fetch "$stage/officecli" "https://github.com/iOfficeAI/OfficeCLI/releases/download/$OFFICECLI_VERSION/$asset"
    install_fetch "$stage/SKILL.md" "https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/$OFFICECLI_VERSION/SKILL.md"
fi
printf '%s  %s\n%s  %s\n' "${digest#sha256:}" "$stage/officecli" "${OFFICECLI_SKILL_SHA256#sha256:}" "$stage/SKILL.md" | sha256sum -c -
install -d -m 0755 "$stage/payload/skill" "$stage/backup"
install -m 0755 "$stage/officecli" "$stage/payload/officecli"
install -m 0644 "$stage/SKILL.md" "$stage/payload/skill/SKILL.md"
skill_destination="$HOME/.openclaw/skills/officecli"
install_save "$binary_destination" "$stage/backup/officecli"
install_save "$skill_destination" "$stage/backup/skill"
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
