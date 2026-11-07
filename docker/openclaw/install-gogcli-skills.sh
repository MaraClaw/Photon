#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

GOGCLI_SKILLS_COMMIT=${GOGCLI_SKILLS_COMMIT:-b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4}
GOGCLI_SKILLS_SHA256=${GOGCLI_SKILLS_SHA256:-e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25}
readonly GOGCLI_SKILLS_COMMIT GOGCLI_SKILLS_SHA256
install_require_commit "gogcli skills" "$GOGCLI_SKILLS_COMMIT"

archive_file=
ownership_output=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        --ownership-output) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$ownership_output" ] || exit 2; ownership_output=$2; shift 2 ;;
        *) printf 'invalid gogcli skills argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

ownership_tmp=
install_rollback() {
    rollback_failed=0
    install_restore_changed || rollback_failed=1
    if [ -f "$stage/ownership-journal" ]; then
        install_restore "$ownership_output" "$stage/ownership-backup" || rollback_failed=1
    fi
    if [ -n "$ownership_tmp" ]; then
        rm -f -- "$ownership_tmp" || rollback_failed=1
    fi
    return "$rollback_failed"
}
install_stage_init gogcli-skills.XXXXXX 'gogcli skills recovery failed; backups' 'gogcli skills temporary files retained at'
install_cp_flags=-RPp
trap 'exit 1' HUP INT TERM
install_fetch_archive "$archive_file" "https://codeload.github.com/openclaw/gogcli/tar.gz/$GOGCLI_SKILLS_COMMIT" "$GOGCLI_SKILLS_SHA256" "$stage/gogcli.tar.gz"
source="$stage/gogcli-$GOGCLI_SKILLS_COMMIT/.agents/skills"
destination="$HOME/.openclaw/skills"
mkdir "$stage/prepared" "$stage/backup" "$stage/changed"
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
    install_save "$destination/$name" "$stage/backup/$name"
done < "$stage/ownership"
if [ -n "$ownership_output" ]; then
    install -d -m 0755 "$(dirname "$ownership_output")"
    [ ! -d "$ownership_output" ] || { printf 'invalid ownership output: %s\n' "$ownership_output" >&2; exit 1; }
    install_save "$ownership_output" "$stage/ownership-backup"
    ownership_tmp="$(mktemp "${ownership_output}.tmp.XXXXXX")"
    cat "$stage/ownership" > "$ownership_tmp"
    chmod 0644 "$ownership_tmp"
fi
install -d -m 0755 "$destination"
while IFS= read -r name; do
    install_mark_changed "$name"
    rm -rf -- "${destination:?}/${name:?}"
    cp -RPp -- "$stage/prepared/$name" "$destination/$name"
done < "$stage/ownership"
if [ -n "$ownership_output" ]; then
    : > "$stage/ownership-journal"
    mv -f -- "$ownership_tmp" "$ownership_output"
    ownership_tmp=
fi
