#!/bin/sh
set -eu

GOGCLI_SKILLS_COMMIT=${GOGCLI_SKILLS_COMMIT:-b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4}
GOGCLI_SKILLS_SHA256=${GOGCLI_SKILLS_SHA256:-e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25}
readonly GOGCLI_SKILLS_COMMIT GOGCLI_SKILLS_SHA256
case $GOGCLI_SKILLS_COMMIT in
    *[!0123456789abcdef]*) printf 'invalid gogcli skills commit: expected 40 lowercase hex characters\n' >&2; exit 2 ;;
esac
[ "${#GOGCLI_SKILLS_COMMIT}" -eq 40 ] || { printf 'invalid gogcli skills commit: expected 40 lowercase hex characters\n' >&2; exit 2; }

archive_file=
ownership_output=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        --ownership-output) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$ownership_output" ] || exit 2; ownership_output=$2; shift 2 ;;
        *) printf 'invalid gogcli skills argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

stage="$(mktemp -d "${TMPDIR:-/tmp}/gogcli-skills.XXXXXX")"
ownership_tmp=
cleanup() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    set +e
    recovery_failed=0
    if [ "$status" -ne 0 ]; then
        for entry in "$stage"/journal/*; do
            [ -f "$entry" ] || continue
            name=${entry##*/}
            if ! rm -rf -- "${destination:?}/${name:?}"; then
                recovery_failed=1
            elif [ -e "$stage/backup/$name" ] || [ -L "$stage/backup/$name" ]; then
                cp -RPp -- "$stage/backup/$name" "$destination/$name" || recovery_failed=1
            fi
        done
        if [ -f "$stage/ownership-journal" ]; then
            if ! rm -rf -- "$ownership_output"; then
                recovery_failed=1
            elif [ -e "$stage/ownership-backup" ] || [ -L "$stage/ownership-backup" ]; then
                cp -RPp -- "$stage/ownership-backup" "$ownership_output" || recovery_failed=1
            fi
        fi
    fi
    if [ -n "$ownership_tmp" ]; then
        rm -f -- "$ownership_tmp" || recovery_failed=1
    fi
    if [ "$recovery_failed" -ne 0 ]; then
        printf 'gogcli skills recovery failed; backups retained at %s\n' "$stage" >&2
        exit 1
    fi
    if ! rm -rf -- "$stage"; then
        printf 'gogcli skills temporary files retained at %s\n' "$stage" >&2
        exit 1
    fi
    exit "$status"
}
trap cleanup EXIT
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
mkdir "$stage/prepared" "$stage/backup" "$stage/journal"
: > "$stage/ownership"
for skill in "$source"/*; do
    name="${skill##*/}"
    case "$name" in
        ''|.|..|*[!a-zA-Z0-9_.-]*) printf 'invalid gogcli skill name: %s\n' "$name" >&2; exit 1 ;;
    esac
    [ -d "$skill" ] || { printf 'invalid gogcli skill directory: %s\n' "$skill" >&2; exit 1; }
    cp -R "$skill" "$stage/prepared/$name"
    chmod -R a+rX "$stage/prepared/$name"
    printf '%s\n' "$name" >> "$stage/ownership"
done
while IFS= read -r name; do
    if [ -e "$destination/$name" ] || [ -L "$destination/$name" ]; then
        cp -RPp -- "$destination/$name" "$stage/backup/$name"
    fi
done < "$stage/ownership"
if [ -n "$ownership_output" ]; then
    install -d -m 0755 "$(dirname "$ownership_output")"
    [ ! -d "$ownership_output" ] || { printf 'invalid ownership output: %s\n' "$ownership_output" >&2; exit 1; }
    if [ -e "$ownership_output" ] || [ -L "$ownership_output" ]; then
        cp -RPp -- "$ownership_output" "$stage/ownership-backup"
    fi
    ownership_tmp="$(mktemp "${ownership_output}.tmp.XXXXXX")"
    cat "$stage/ownership" > "$ownership_tmp"
    chmod 0644 "$ownership_tmp"
fi
install -d -m 0755 "$destination"
while IFS= read -r name; do
    : > "$stage/journal/$name"
    rm -rf -- "${destination:?}/${name:?}"
    cp -RPp -- "$stage/prepared/$name" "$destination/$name"
done < "$stage/ownership"
if [ -n "$ownership_output" ]; then
    : > "$stage/ownership-journal"
    mv -f -- "$ownership_tmp" "$ownership_output"
    ownership_tmp=
fi
