#!/bin/sh
set -eu

GOGCLI_SKILLS_COMMIT="b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4"
GOGCLI_SKILLS_SHA256="e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25"
readonly GOGCLI_SKILLS_COMMIT GOGCLI_SKILLS_SHA256

archive_file=
ownership_output=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        --ownership-output) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$ownership_output" ] || exit 2; ownership_output=$2; shift 2 ;;
        *) printf 'invalid gogcli skills argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

stage="$(mktemp -d)"
ownership_tmp=
trap 'rm -rf -- "$stage"; [ -z "$ownership_tmp" ] || rm -f -- "$ownership_tmp"' EXIT
trap 'exit 1' HUP INT TERM
if [ -n "$archive_file" ]; then
    cp -- "$archive_file" "$stage/gogcli.tar.gz"
else
    curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
        -o "$stage/gogcli.tar.gz" "https://codeload.github.com/openclaw/gogcli/tar.gz/$GOGCLI_SKILLS_COMMIT"
fi
printf '%s  %s\n' "$GOGCLI_SKILLS_SHA256" "$stage/gogcli.tar.gz" | sha256sum -c -
tar -xzf "$stage/gogcli.tar.gz" -C "$stage"
source="$stage/gogcli-$GOGCLI_SKILLS_COMMIT/.agents/skills"
destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
: > "$stage/ownership"
for skill in "$source"/*; do
    name="${skill##*/}"
    case "$name" in
        ''|.|..|*[!a-zA-Z0-9_.-]*) printf 'invalid gogcli skill name: %s\n' "$name" >&2; exit 1 ;;
    esac
    [ -d "$skill" ] || { printf 'invalid gogcli skill directory: %s\n' "$skill" >&2; exit 1; }
    rm -rf -- "$destination/$name"
    cp -R "$skill" "$destination/$name"
    chmod -R a+rX "$destination/$name"
    printf '%s\n' "$name" >> "$stage/ownership"
done
if [ -n "$ownership_output" ]; then
    install -d -m 0755 "$(dirname "$ownership_output")"
    ownership_tmp="$(mktemp "${ownership_output}.tmp.XXXXXX")"
    cat "$stage/ownership" > "$ownership_tmp"
    chmod 0644 "$ownership_tmp"
    mv -f -- "$ownership_tmp" "$ownership_output"
    ownership_tmp=
fi
