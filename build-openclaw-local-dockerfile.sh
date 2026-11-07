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
  OPENCLAW_VERSION, OPENCLAW_SHA256, GOGCLI_VERSION, GOGCLI_{AMD64,ARM64}_SHA256,
  OFFICECLI_VERSION, OFFICECLI_{ARM64,AMD64,SKILL}_SHA256
                               optional pins; defaults come from Dockerfile.openclaw
  GOGCLI_SKILLS_COMMIT         optional gogcli-skills archive commit (default: installer pin)
  GOGCLI_SKILLS_SHA256         optional gogcli-skills archive digest (default: installer pin)
  CLAWSEC_COMMIT               optional Soul Guardian archive commit (default: installer pin)
  CLAWSEC_SHA256               optional Soul Guardian archive digest (default: installer pin)
  HUMANIZER_COMMIT             optional Humanizer archive commit (default: installer pin)
  HUMANIZER_SHA256             optional Humanizer archive digest (default: installer pin)

The selected env file is sourced from the script directory if it exists;
its assignments override inherited environment values. Empty values use defaults.
Every pin is forwarded as a build argument only when set and nonempty; unset pins
use the ARG defaults in Dockerfile.openclaw.
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

# shellcheck source=openclaw-buildx-common.sh
. "$SCRIPT_DIR/openclaw-buildx-common.sh"

openclaw_source_env_file openclaw-build

DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"

case "$DOCKER_PLATFORM" in
    linux/amd64|linux/arm64) ;;
    *)
        echo "[openclaw-build] DOCKER_PLATFORM must select exactly one supported target: linux/amd64 or linux/arm64 (got '$DOCKER_PLATFORM')." >&2
        exit 2
        ;;
esac

LOCAL_IMAGE="${DOCKER_PLATFORM#linux/}-${MARACLAW_OPENCLAW_IMAGE:-openclaw:local}"

openclaw_collect_pin_args

openclaw_collect_asset_args openclaw-build \
    "linux-${DOCKER_PLATFORM#linux/}/gogcli.tar.gz" "linux-${DOCKER_PLATFORM#linux/}/officecli" \
    common/officecli-SKILL.md common/gogcli-skills.tar.gz common/soul-guardian.tar.gz common/humanizer.tar.gz

echo "[openclaw-build] Building $LOCAL_IMAGE for $DOCKER_PLATFORM with OpenClaw ${OPENCLAW_VERSION:-(Dockerfile.openclaw default)}"
docker buildx build \
    --platform "$DOCKER_PLATFORM" \
    -f Dockerfile.openclaw \
    ${PIN_ARGS[@]+"${PIN_ARGS[@]}"} \
    ${ASSET_ARGS[@]+"${ASSET_ARGS[@]}"} \
    -t "$LOCAL_IMAGE" \
    --load \
    .
echo "[openclaw-build] Successfully built local Docker image $LOCAL_IMAGE"
