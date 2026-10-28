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

if ! probe_run "/usr/local/bin/officecli" --version; then
    printf 'Required CLI failed verification: %s\n' "/usr/local/bin/officecli" >&2
    exit 1
fi

if ! probe_capture npm root -g || [ -z "$probe_output" ]; then
    printf '%s\n' 'Required skill failed verification: officecli' >&2
    exit 1
fi
npm_root=$probe_output

skill_dir="$npm_root/openclaw/skills/officecli"
if [ ! -f "$skill_dir/SKILL.md" ] || [ ! -r "$skill_dir/SKILL.md" ] || [ ! -s "$skill_dir/SKILL.md" ]; then
    printf '%s\n' 'Required skill failed verification: officecli' >&2
    exit 1
fi

probe_finish
exec "$@"
