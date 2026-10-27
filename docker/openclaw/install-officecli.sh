#!/bin/sh
set -eu

OFFICECLI_TAG="v1.0.158"
OFFICECLI_ARM64_SHA256="b1d5e74d93a8471a47b037cc7774ac29c559940e226f99b9fdf788966cc395b8"
OFFICECLI_AMD64_SHA256="9ee41d5558caea7bcd5aab1b3e76b2bd70f0535f09cf65dda3668130510cc719"
OFFICECLI_SKILL_SHA256="c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4"
readonly OFFICECLI_TAG OFFICECLI_ARM64_SHA256 OFFICECLI_AMD64_SHA256 OFFICECLI_SKILL_SHA256

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
curl_safe -o "$stage/officecli" "https://github.com/iOfficeAI/OfficeCLI/releases/download/$OFFICECLI_TAG/$asset"
curl_safe -o "$stage/SKILL.md" "https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/$OFFICECLI_TAG/SKILL.md"
printf '%s  %s\n%s  %s\n' "$digest" "$stage/officecli" "$OFFICECLI_SKILL_SHA256" "$stage/SKILL.md" | sha256sum -c -
install -m 0755 "$stage/officecli" /usr/local/bin/officecli
install -d -m 0755 "$(npm root -g)/openclaw/skills/officecli"
install -m 0644 "$stage/SKILL.md" "$(npm root -g)/openclaw/skills/officecli/SKILL.md"
/usr/local/bin/officecli --version
