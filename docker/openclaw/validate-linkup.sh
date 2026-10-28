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

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component linkup --inventory-dir /usr/local/share/openclaw-integrity; then
    printf '%s\n' 'Installed assets failed verification: linkup' >&2
    exit 1
fi

fail() {
    printf 'Required skill failed verification: %s\n' "$1" >&2
    exit 1
}

if ! probe_capture npm root -g || [ -z "$probe_output" ]; then
    fail linkup
fi
npm_root=$probe_output

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component linkup --inventory-dir /usr/local/share/openclaw-integrity --skills-root "$npm_root/openclaw/skills"; then
    fail linkup
fi

skills="$npm_root/openclaw/skills"
for name in linkup-search linkup-fetch; do
    skill="$skills/$name"
    file="$skill/SKILL.md"
    if [ ! -f "$file" ] || [ ! -r "$file" ] || [ ! -s "$file" ] || ! probe_capture awk -v name="$name" '
        NR == 1 { if ($0 != "---") exit 1; next }
        $0 == "---" { closed = 1; exit }
        /^name: / { if ($0 != "name: " name || found_name++) exit 1; next }
        /^metadata: / { if (found_metadata++) exit 1; print substr($0, 11); next }
        /^description: [[:alpha:]]/ {
            description = substr($0, 14)
            scalar = tolower(description)
            if (found_description++ || description ~ /:[[:space:]]/ || description ~ /:$/ ||
                scalar ~ /^(null|true|false|yes|no|on|off)([[:space:]]*(#.*)?)?$/) exit 1
            next
        }
        { exit 1 }
        END { if (!closed || !found_name || !found_description || !found_metadata) exit 1 }
    ' "$file"; then
        fail "$name"
    fi
    metadata=$probe_output
    if ! probe_run python3 -B -c '
import json
import sys

try:
    env = json.loads(sys.argv[1], parse_constant=int)["openclaw"]["requires"]["env"]
    valid = isinstance(env, list) and all(isinstance(item, str) for item in env) and {"LINKUP_API_BASE", "LINKUP_PROXY_TOKEN"} <= set(env)
except (ValueError, KeyError, TypeError):
    valid = False
sys.exit(not valid)
' "$metadata"; then
        fail "$name"
    fi
    case "$name" in
        linkup-search) references="LINKUP_AGENT_QUERY_MENTAL_MODEL.md LINKUP_PROMPT_OPTIMIZER_KNOWLEDGE.md LINKUP_API_REFERENCE.md" ;;
        linkup-fetch) references="LINKUP_API_REFERENCE.md" ;;
    esac
    for reference in $references; do
        file="$skill/references/$reference"
        if [ ! -f "$file" ] || [ ! -r "$file" ] || [ ! -s "$file" ]; then
            fail "$name"
        fi
    done
done

probe_finish
exec "$@"
