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

stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
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
install -m 0755 "$stage/officecli" /usr/local/bin/officecli
install -d -m 0755 "$(npm root -g)/openclaw/skills/officecli"
install -m 0644 "$stage/SKILL.md" "$(npm root -g)/openclaw/skills/officecli/SKILL.md"
/usr/local/bin/officecli --version
