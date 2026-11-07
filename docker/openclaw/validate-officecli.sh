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

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component officecli --inventory-dir /usr/local/share/openclaw-integrity --skills-root "$HOME/.openclaw/skills"; then
    printf '%s\n' 'Installed assets failed verification: officecli' >&2
    exit 1
fi

if ! probe_run "/usr/local/bin/officecli" --version; then
    printf 'Required CLI failed verification: %s\n' "/usr/local/bin/officecli" >&2
    exit 1
fi

skill_dir="$HOME/.openclaw/skills/officecli"
if [ ! -f "$skill_dir/SKILL.md" ] || [ ! -r "$skill_dir/SKILL.md" ] || [ ! -s "$skill_dir/SKILL.md" ]; then
    printf '%s\n' 'Required skill failed verification: officecli' >&2
    exit 1
fi

probe_finish
exec "$@"
