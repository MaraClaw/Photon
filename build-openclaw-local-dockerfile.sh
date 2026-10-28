#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./build-openclaw-local-dockerfile.sh [-h|--help]

Build and load one local OpenClaw image. Environment settings:
  DOCKER_PLATFORM              linux/arm64 (default) or linux/amd64
  MARACLAW_OPENCLAW_IMAGE      image name and tag (default: openclaw:local)
  ENV_FILE                     env file to source (default: .env)
  OPENCLAW_ASSET_MODE          online (default) or staged
  OPENCLAW_ASSET_DIR           asset directory required in staged mode
  OPENCLAW_VERSION             default: 2026.10.8
  OPENCLAW_SHA256              default: sha256:7e36685a0b5fd9c8c3afcfa93ee33fad871f7c6c8b6e695cae6720d14a765f18
  GOGCLI_VERSION               default: 0.47.1
  GOGCLI_AMD64_SHA256          default: sha256:279bc3a03c1919d774e268ef229949f45b753a0e21257f9fb315b05be7a40ea9
  GOGCLI_ARM64_SHA256          default: sha256:c677ac6abf5e187e987a87fe1b588f454b002a7d7019de81b9e8db02e9e902b6
  OFFICECLI_VERSION            default: v1.0.159
  OFFICECLI_ARM64_SHA256       default: a27778548f2820771f54a70887af3c3ec214fe60e63b32836b09b3f3e2c4c493
  OFFICECLI_AMD64_SHA256       default: 92d9aeed324d91ed2a107f7f8dc7468829b14390a66cf2bbdfb25c40beac50ea
  OFFICECLI_SKILL_SHA256       default: c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4

The selected env file is sourced from the script directory if it exists;
its assignments override inherited environment values. Empty values use defaults.
The target architecture is prepended literally to the image name:
  ./build-openclaw-local-dockerfile.sh  -> arm64-openclaw:local
  DOCKER_PLATFORM=linux/amd64 MARACLAW_OPENCLAW_IMAGE=workspace/openclaw:custom \
    ./build-openclaw-local-dockerfile.sh  -> amd64-workspace/openclaw:custom
Examples assume the selected env file does not override those settings.
EOF
}

if [ "$#" -gt 0 ]; then
    if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
        usage
        exit 0
    fi
    echo '[openclaw-build] Unknown arguments' >&2
    usage >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${ENV_FILE:-.env}"
if [ -f "$ENV_FILE" ]; then
    echo "[openclaw-build] Sourcing $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.10.8}"
OPENCLAW_SHA256="${OPENCLAW_SHA256:-sha256:7e36685a0b5fd9c8c3afcfa93ee33fad871f7c6c8b6e695cae6720d14a765f18}"
GOGCLI_VERSION="${GOGCLI_VERSION:-0.47.1}"
GOGCLI_AMD64_SHA256="${GOGCLI_AMD64_SHA256:-sha256:279bc3a03c1919d774e268ef229949f45b753a0e21257f9fb315b05be7a40ea9}"
GOGCLI_ARM64_SHA256="${GOGCLI_ARM64_SHA256:-sha256:c677ac6abf5e187e987a87fe1b588f454b002a7d7019de81b9e8db02e9e902b6}"
OFFICECLI_VERSION="${OFFICECLI_VERSION:-v1.0.159}"
OFFICECLI_ARM64_SHA256="${OFFICECLI_ARM64_SHA256:-a27778548f2820771f54a70887af3c3ec214fe60e63b32836b09b3f3e2c4c493}"
OFFICECLI_AMD64_SHA256="${OFFICECLI_AMD64_SHA256:-92d9aeed324d91ed2a107f7f8dc7468829b14390a66cf2bbdfb25c40beac50ea}"
OFFICECLI_SKILL_SHA256="${OFFICECLI_SKILL_SHA256:-c950d285ce60021712b4753fb2d9f592308d5622bab776229061dfecb1ce55d4}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"

case "$DOCKER_PLATFORM" in
    linux/amd64|linux/arm64) ;;
    *)
        echo "[openclaw-build] DOCKER_PLATFORM must select exactly one supported target: linux/amd64 or linux/arm64 (got '$DOCKER_PLATFORM')." >&2
        exit 2
        ;;
esac

LOCAL_IMAGE="${DOCKER_PLATFORM#linux/}-${MARACLAW_OPENCLAW_IMAGE:-openclaw:local}"

ASSET_ARGS=()
case "${OPENCLAW_ASSET_MODE:-online}" in
    online) ;;
    staged)
        if [ -z "${OPENCLAW_ASSET_DIR:-}" ] || [ ! -d "$OPENCLAW_ASSET_DIR" ]; then
            echo '[openclaw-build] OPENCLAW_ASSET_DIR must name an existing directory in staged mode' >&2
            exit 2
        fi
        ASSET_DIR="$(cd "$OPENCLAW_ASSET_DIR" && pwd -P)"
        for asset in "linux-${DOCKER_PLATFORM#linux/}/gogcli.tar.gz" "linux-${DOCKER_PLATFORM#linux/}/officecli" \
            common/officecli-SKILL.md common/gogcli-skills.tar.gz common/soul-guardian.tar.gz; do
            if [ ! -f "$ASSET_DIR/$asset" ]; then
                echo "[openclaw-build] missing staged asset: $ASSET_DIR/$asset" >&2
                exit 2
            fi
        done
        ASSET_ARGS=(--build-arg OPENCLAW_ASSET_MODE=staged --build-context "openclaw_assets=$ASSET_DIR")
        ;;
    *) echo "[openclaw-build] invalid OPENCLAW_ASSET_MODE: $OPENCLAW_ASSET_MODE" >&2; exit 2 ;;
esac

echo "[openclaw-build] Building $LOCAL_IMAGE for $DOCKER_PLATFORM with OpenClaw $OPENCLAW_VERSION"
docker buildx build \
    --platform "$DOCKER_PLATFORM" \
    -f Dockerfile.openclaw \
    --build-arg OPENCLAW_VERSION="$OPENCLAW_VERSION" \
    --build-arg OPENCLAW_SHA256="$OPENCLAW_SHA256" \
    --build-arg GOGCLI_VERSION="$GOGCLI_VERSION" \
    --build-arg GOGCLI_AMD64_SHA256="$GOGCLI_AMD64_SHA256" \
    --build-arg GOGCLI_ARM64_SHA256="$GOGCLI_ARM64_SHA256" \
    --build-arg OFFICECLI_VERSION="$OFFICECLI_VERSION" \
    --build-arg OFFICECLI_ARM64_SHA256="$OFFICECLI_ARM64_SHA256" \
    --build-arg OFFICECLI_AMD64_SHA256="$OFFICECLI_AMD64_SHA256" \
    --build-arg OFFICECLI_SKILL_SHA256="$OFFICECLI_SKILL_SHA256" \
    ${ASSET_ARGS[@]+"${ASSET_ARGS[@]}"} \
    -t "$LOCAL_IMAGE" \
    --load \
    .
echo "[openclaw-build] Successfully built local Docker image $LOCAL_IMAGE"
