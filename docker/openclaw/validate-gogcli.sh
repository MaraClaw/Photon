#!/bin/sh
set -eu

validator_path=$0
case $validator_path in
    */*) ;;
    *)
        if [ -f "$validator_path" ]; then
            validator_path=./$validator_path
        else
            validator_path=$(command -v "$validator_path")
            case $validator_path in */*) ;; *) validator_path=./$validator_path ;; esac
        fi
        ;;
esac
probe_dir=${validator_path%/*}
. "$probe_dir/startup-probe.sh"

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component gogcli --inventory-dir /usr/local/share/openclaw-integrity; then
    printf '%s\n' 'Installed assets failed verification: gogcli' >&2
    exit 1
fi

if ! probe_run "/usr/local/bin/gog" --version; then
    printf 'Required CLI failed verification: %s\n' "/usr/local/bin/gog" >&2
    exit 1
fi

if ! probe_capture npm root -g || [ -z "$probe_output" ]; then
    printf '%s\n' 'Required skill failed verification: gog' >&2
    exit 1
fi
npm_root=$probe_output

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component gogcli --inventory-dir /usr/local/share/openclaw-integrity --skills-root "$npm_root/openclaw/skills"; then
    printf '%s\n' 'Required skill failed verification: gog' >&2
    exit 1
fi

skill_dir="$npm_root/openclaw/skills/gog"
if [ ! -f "$skill_dir/SKILL.md" ] || [ ! -r "$skill_dir/SKILL.md" ] || [ ! -s "$skill_dir/SKILL.md" ]; then
    printf '%s\n' 'Required skill failed verification: gog' >&2
    exit 1
fi

if [ -z "${GOG_KEYRING_PASSWORD_FILE:-}" ]; then
    probe_finish
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

if ! probe_capture cat "$GOG_KEYRING_PASSWORD_FILE"; then
    printf '%s\n' 'GOG_KEYRING_PASSWORD_FILE failed verification' >&2
    exit 1
fi
GOG_KEYRING_PASSWORD=$probe_output
export GOG_KEYRING_PASSWORD

if [ "${GOG_HOME:-}" ]; then
    mkdir -p "$GOG_HOME"
    chmod 0700 "$GOG_HOME"
fi

probe_finish
exec "$@"
