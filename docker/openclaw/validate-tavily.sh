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

if ! probe_run python3 -B "$probe_dir/installed-assets.py" verify --component tavily --inventory-dir /usr/local/share/openclaw-integrity --skills-root "$HOME/.openclaw/skills"; then
    printf '%s\n' 'Installed assets failed verification: tavily' >&2
    exit 1
fi

fail() {
    printf 'Required skill failed verification: %s\n' "$1" >&2
    exit 1
}

skills="$HOME/.openclaw/skills"
for name in tavily-search tavily-crawl; do
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
    valid = isinstance(env, list) and all(isinstance(item, str) for item in env) and {"TAVILY_API_BASE", "TAVILY_API_KEY"} <= set(env)
except (ValueError, KeyError, TypeError):
    valid = False
sys.exit(not valid)
' "$metadata"; then
        fail "$name"
    fi
    case "$name" in
        tavily-search) references="TAVILY_SEARCH_API_REFERENCE.md" ;;
        tavily-crawl) references="TAVILY_CRAWL_API_REFERENCE.md" ;;
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
