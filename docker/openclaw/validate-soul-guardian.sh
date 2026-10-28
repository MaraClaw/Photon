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

if ! probe_capture npm root -g || [ -z "$probe_output" ]; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi
npm_root=$probe_output

skill_dir="$npm_root/openclaw/skills/soul-guardian"
for asset in SKILL.md CHANGELOG.md skill.json LICENSE scripts/soul_guardian.py scripts/onboard_state_dir.py; do
    if [ ! -f "$skill_dir/$asset" ] || [ ! -r "$skill_dir/$asset" ] || [ ! -s "$skill_dir/$asset" ]; then
        printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
        exit 1
    fi
done

if ! probe_run python3 -B -c '
import json
import sys
from pathlib import Path

with open(sys.argv[1], encoding="utf-8") as metadata:
    json.load(metadata)
compile(Path(sys.argv[2]).read_bytes(), sys.argv[2], "exec")
' "$skill_dir/skill.json" "$skill_dir/scripts/onboard_state_dir.py"; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

if ! probe_run python3 -B "$skill_dir/scripts/soul_guardian.py" --help; then
    printf '%s\n' 'Required skill failed verification: soul-guardian' >&2
    exit 1
fi

probe_finish
exec "$@"
