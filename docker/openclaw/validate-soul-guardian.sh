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

skill_dir="$HOME/.openclaw/skills/soul-guardian"
if ! probe_verify soul-guardian /usr/local/share/openclaw-integrity "$HOME/.openclaw/skills" python3 -B "$skill_dir/scripts/soul_guardian.py" --help; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

for asset in SKILL.md CHANGELOG.md skill.json LICENSE scripts/soul_guardian.py scripts/onboard_state_dir.py; do
    if [ ! -f "$skill_dir/$asset" ] || [ ! -r "$skill_dir/$asset" ] || [ ! -s "$skill_dir/$asset" ]; then
        printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
        exit 1
    fi
done

probe_finish
exec "$@"
