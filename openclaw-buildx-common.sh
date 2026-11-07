# shellcheck shell=bash
# shellcheck disable=SC2034  # PIN_ARGS and ASSET_ARGS are consumed by the sourcing scripts
# Shared helpers for build-openclaw-local-dockerfile.sh and publish-openclaw-local-dockerfile.sh.
# Source this file after changing to the script directory; it is not meant to be executed.
#
# Dockerfile.openclaw owns every pin default: a pin is forwarded as --build-arg only
# when it is set and nonempty after the env file has been sourced.

OPENCLAW_PIN_NAMES=(
    OPENCLAW_VERSION OPENCLAW_SHA256 DEBIAN_SNAPSHOT
    GOGCLI_VERSION GOGCLI_AMD64_SHA256 GOGCLI_ARM64_SHA256
    OFFICECLI_VERSION OFFICECLI_ARM64_SHA256 OFFICECLI_AMD64_SHA256 OFFICECLI_SKILL_SHA256
    GOGCLI_SKILLS_COMMIT GOGCLI_SKILLS_SHA256
    CLAWSEC_COMMIT CLAWSEC_SHA256
    HUMANIZER_COMMIT HUMANIZER_SHA256
)

# openclaw_source_env_file LOG_PREFIX
# Sources ENV_FILE (default .env, relative to the current directory) with export enabled,
# so its assignments override inherited environment values. A nonexistent path is skipped.
openclaw_source_env_file() {
    ENV_FILE="${ENV_FILE:-.env}"
    if [ -f "$ENV_FILE" ]; then
        echo "[$1] Sourcing $ENV_FILE"
        set -a
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        set +a
    fi
}

# openclaw_collect_pin_args
# Fills PIN_ARGS with --build-arg NAME=VALUE for each pin that is set and nonempty.
openclaw_collect_pin_args() {
    local pin_name pin_value
    PIN_ARGS=()
    for pin_name in "${OPENCLAW_PIN_NAMES[@]}"; do
        pin_value="${!pin_name:-}"
        if [ -n "$pin_value" ]; then
            PIN_ARGS+=(--build-arg "$pin_name=$pin_value")
        fi
    done
}

# openclaw_collect_asset_args LOG_PREFIX REQUIRED_ASSET...
# Fills ASSET_ARGS from OPENCLAW_ASSET_MODE (online by default). Staged mode requires
# OPENCLAW_ASSET_DIR to contain every REQUIRED_ASSET. Invalid input exits 2.
openclaw_collect_asset_args() {
    local log_prefix="$1" asset_dir asset
    shift
    ASSET_ARGS=()
    case "${OPENCLAW_ASSET_MODE:-online}" in
        online) ;;
        staged)
            if [ -z "${OPENCLAW_ASSET_DIR:-}" ] || [ ! -d "$OPENCLAW_ASSET_DIR" ]; then
                echo "[$log_prefix] OPENCLAW_ASSET_DIR must name an existing directory in staged mode" >&2
                exit 2
            fi
            asset_dir="$(cd "$OPENCLAW_ASSET_DIR" && pwd -P)"
            for asset in "$@"; do
                if [ ! -f "$asset_dir/$asset" ]; then
                    echo "[$log_prefix] missing staged asset: $asset_dir/$asset" >&2
                    exit 2
                fi
            done
            ASSET_ARGS=(--build-arg OPENCLAW_ASSET_MODE=staged --build-context "openclaw_assets=$asset_dir")
            ;;
        *) echo "[$log_prefix] invalid OPENCLAW_ASSET_MODE: $OPENCLAW_ASSET_MODE" >&2; exit 2 ;;
    esac
}
