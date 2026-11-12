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

if ! probe_verify tavily /usr/local/share/openclaw-integrity "$HOME/.openclaw/skills"; then
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
    if [ ! -f "$file" ] || [ ! -r "$file" ] || [ ! -s "$file" ]; then
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
