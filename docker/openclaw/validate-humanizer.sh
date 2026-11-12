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
# shellcheck source=startup-probe.sh
. "$probe_dir/startup-probe.sh"

if ! probe_verify humanizer /usr/local/share/openclaw-integrity "$HOME/.openclaw/skills"; then
    printf '%s\n' 'Required skill failed verification: humanizer' >&2
    exit 1
fi

skill_dir="$HOME/.openclaw/skills/humanizer"
for asset in SKILL.md LICENSE; do
    if [ ! -f "$skill_dir/$asset" ] || [ ! -r "$skill_dir/$asset" ] || [ ! -s "$skill_dir/$asset" ]; then
        printf '%s\n' 'Required skill failed verification: humanizer' >&2
        exit 1
    fi
done

probe_finish
exec "$@"
