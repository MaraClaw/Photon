# shellcheck shell=sh
# Shared staging, backup and rollback mechanics for the install-*.sh build installers.
# Sourced after `set -eu`, never executed. Provides:
#   install_stage_init TEMPLATE FAILURE_MESSAGE [CLEANUP_MESSAGE]
#       Create $stage, wire the EXIT/HUP/INT/TERM traps (exit 129/130/143).
#       On a nonzero exit, install_rollback runs; if it fails the stage is kept and
#       "FAILURE_MESSAGE retained at $stage" is printed. Otherwise the stage is removed,
#       and if that fails "CLEANUP_MESSAGE $stage" (when given) is printed. Both exit 1.
#   install_rollback
#       Hook run on failure. Default: install_restore_changed. Installers redefine it.
#   install_cp_flags
#       cp flags for backup and restore (default -a).
#   install_save SRC BACKUP / install_restore DEST BACKUP
#       Copy an existing SRC aside / replace DEST with BACKUP (or just remove it if no BACKUP).
#   install_mark_changed NAME / install_restore_changed
#       Mark "$stage/changed/NAME" before touching "$destination/NAME"; on rollback restore each
#       marked entry from "$stage/backup/NAME". The installer creates $stage/changed and $stage/backup.
#   install_fetch OUTPUT URL, install_fetch_archive LOCAL_FILE URL SHA256 ARCHIVE,
#   install_require_commit LABEL VALUE

install_rollback() {
    install_restore_changed
}

install_finish() {
    install_status=$?
    trap - EXIT
    trap '' HUP INT TERM
    set +e
    if [ "$install_status" -ne 0 ] && ! install_rollback; then
        printf '%s retained at %s\n' "$install_failure_message" "$stage" >&2
        exit 1
    fi
    if ! rm -rf -- "$stage"; then
        if [ -n "$install_cleanup_message" ]; then
            printf '%s %s\n' "$install_cleanup_message" "$stage" >&2
        fi
        exit 1
    fi
    exit "$install_status"
}

install_stage_init() {
    stage="$(mktemp -d "${TMPDIR:-/tmp}/$1")"
    install_failure_message=$2
    install_cleanup_message=${3-}
    install_cp_flags=-a
    trap install_finish EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

install_save() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        cp "$install_cp_flags" -- "$1" "$2"
    fi
}

install_restore() {
    rm -rf -- "$1" || return 1
    if [ -e "$2" ] || [ -L "$2" ]; then
        cp "$install_cp_flags" -- "$2" "$1" || return 1
    fi
}

install_mark_changed() {
    : > "$stage/changed/$1"
}

install_restore_changed() {
    install_restored=0
    for install_marker in "$stage"/changed/*; do
        [ -f "$install_marker" ] || continue
        install_name=${install_marker##*/}
        install_restore "${destination:?}/${install_name:?}" "$stage/backup/$install_name" || install_restored=1
    done
    return "$install_restored"
}

install_fetch() {
    curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
        -o "$1" "$2"
}

install_fetch_archive() {
    if [ -n "$1" ]; then
        cp -- "$1" "$4"
    else
        install_fetch "$4" "$2"
    fi
    printf '%s  %s\n' "$3" "$4" | sha256sum -c -
    tar -xzf "$4" -C "$stage"
}

install_require_commit() {
    case $2 in
        *[!0123456789abcdef]*) printf 'invalid %s commit: expected 40 lowercase hex characters\n' "$1" >&2; exit 2 ;;
    esac
    [ "${#2}" -eq 40 ] || { printf 'invalid %s commit: expected 40 lowercase hex characters\n' "$1" >&2; exit 2; }
}
