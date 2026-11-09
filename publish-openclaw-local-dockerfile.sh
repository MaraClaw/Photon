#!/usr/bin/env bash
# Build Dockerfile.openclaw and push a version-tagged image to Docker Hub.
#
# Build and push linux/amd64 and linux/arm64 together as a manifest:
#   ${DOCKERHUB_NAMESPACE}/openclaw:${IMAGE_TAG}
#
# Usage:
#   DOCKERHUB_NAMESPACE=youruser ./publish-openclaw-local-dockerfile.sh
#   OPENCLAW_PUBLISH_IMAGE=youruser/openclaw IMAGE_TAG=2026.11.2 ./publish-openclaw-local-dockerfile.sh
#   PUSH_LATEST=1 DOCKERHUB_NAMESPACE=youruser ./publish-openclaw-local-dockerfile.sh
#
# Environment (set in .env or on the command line):
#   DOCKERHUB_NAMESPACE     Docker Hub user or org (required unless OPENCLAW_PUBLISH_IMAGE is set)
#   OPENCLAW_PUBLISH_IMAGE  full repo name          (default: ${DOCKERHUB_NAMESPACE}/openclaw)
#   IMAGE_TAG               Hub tag                 (default: $OPENCLAW_VERSION, else the ARG in Dockerfile.openclaw)
#   PUSH_LATEST=1           also tag and push :latest
#   OPENCLAW_VERSION, OPENCLAW_SHA256, GOGCLI_VERSION, GOGCLI_{AMD64,ARM64}_SHA256
#   OFFICECLI_VERSION, OFFICECLI_{AMD64,ARM64,SKILL}_SHA256
#   GOGCLI_SKILLS_{COMMIT,SHA256}, CLAWSEC_{COMMIT,SHA256}, HUMANIZER_{COMMIT,SHA256}
#                           optional pins, forwarded only when set and nonempty;
#                           defaults come from the ARG lines in Dockerfile.openclaw
#
# The pushed manifest carries max-mode provenance and an SBOM attestation.
#
# Requires an existing `docker login` to Docker Hub. This script does not store credentials.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# shellcheck source=openclaw-buildx-common.sh
. "$SCRIPT_DIR/openclaw-buildx-common.sh"

openclaw_source_env_file openclaw-publish

IMAGE_TAG="${IMAGE_TAG:-${OPENCLAW_VERSION:-}}"
if [ -z "$IMAGE_TAG" ]; then
    IMAGE_TAG="$(sed -n 's/^ARG OPENCLAW_VERSION=//p' Dockerfile.openclaw 2>/dev/null | head -n 1 || true)"
    if [ -z "$IMAGE_TAG" ]; then
        echo "[openclaw-publish] ERROR: OPENCLAW_VERSION is unset and no 'ARG OPENCLAW_VERSION=' line could be read from $SCRIPT_DIR/Dockerfile.openclaw" >&2
        exit 2
    fi
fi

if [ -n "${OPENCLAW_PUBLISH_IMAGE:-}" ]; then
    PUBLISH_IMAGE="$OPENCLAW_PUBLISH_IMAGE"
elif [ -n "${DOCKERHUB_NAMESPACE:-}" ]; then
    PUBLISH_IMAGE="${DOCKERHUB_NAMESPACE}/openclaw"
else
    echo "[openclaw-publish] ERROR: set DOCKERHUB_NAMESPACE (Docker Hub user/org) or OPENCLAW_PUBLISH_IMAGE (e.g. youruser/openclaw)" >&2
    exit 2
fi

case "$PUBLISH_IMAGE" in
    */*) ;;
    *)
        echo "[openclaw-publish] ERROR: OPENCLAW_PUBLISH_IMAGE must be namespace/name (got '$PUBLISH_IMAGE')" >&2
        exit 2
        ;;
esac

if printf '%s' "$PUBLISH_IMAGE" | grep -q '[[:upper:][:space:]]'; then
    echo "[openclaw-publish] ERROR: Docker Hub repository '$PUBLISH_IMAGE' must be lowercase with no spaces" >&2
    exit 2
fi

if ! printf '%s' "$IMAGE_TAG" | grep -Eq '^[A-Za-z0-9_][A-Za-z0-9_.-]*$'; then
    echo "[openclaw-publish] ERROR: IMAGE_TAG '$IMAGE_TAG' is not a valid Docker tag" >&2
    exit 2
fi

openclaw_collect_pin_args

openclaw_collect_asset_args openclaw-publish \
    linux-amd64/gogcli.tar.gz linux-amd64/officecli linux-arm64/gogcli.tar.gz linux-arm64/officecli \
    common/officecli-SKILL.md common/gogcli-skills.tar.gz common/soul-guardian.tar.gz common/humanizer.tar.gz

VERSIONED_REF="${PUBLISH_IMAGE}:${IMAGE_TAG}"
echo "[openclaw-publish] Building linux/amd64,linux/arm64 and pushing $VERSIONED_REF"
TAGS=(-t "$VERSIONED_REF")

if [ "${PUSH_LATEST:-0}" = "1" ]; then
    LATEST_REF="${PUBLISH_IMAGE}:latest"
    TAGS+=(-t "$LATEST_REF")
fi

docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -f Dockerfile.openclaw \
    ${PIN_ARGS[@]+"${PIN_ARGS[@]}"} \
    ${ASSET_ARGS[@]+"${ASSET_ARGS[@]}"} \
    "${TAGS[@]}" \
    --provenance=mode=max \
    --sbom=true \
    --push \
    .

echo "[openclaw-publish] Published $VERSIONED_REF"
