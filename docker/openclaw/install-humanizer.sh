#!/bin/sh
set -eu

HUMANIZER_COMMIT=${HUMANIZER_COMMIT:-0ef9fe5bc3572734fd3ae6c448fe00a30cd5dcf0}
HUMANIZER_SHA256=${HUMANIZER_SHA256:-1828d04338ddde6e65f6188a32dd61a9edff74be7acf337c2cbd949ffb312cf8}
readonly HUMANIZER_COMMIT HUMANIZER_SHA256
case $HUMANIZER_COMMIT in
    *[!0123456789abcdef]*) printf 'invalid humanizer commit: expected 40 lowercase hex characters\n' >&2; exit 2 ;;
esac
[ "${#HUMANIZER_COMMIT}" -eq 40 ] || { printf 'invalid humanizer commit: expected 40 lowercase hex characters\n' >&2; exit 2; }

archive_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        *) printf 'invalid humanizer argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

stage="$(mktemp -d "${TMPDIR:-/tmp}/humanizer.XXXXXXXX")"
changed=0
cleanup() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$changed" -eq 1 ]; then
        failed=0
        if ! rm -rf -- "$destination/humanizer"; then
            failed=1
        elif [ -e "$stage/backup" ] || [ -L "$stage/backup" ]; then
            cp -a -- "$stage/backup" "$destination/humanizer" || failed=1
        fi
        if [ "$failed" -ne 0 ]; then
            printf 'humanizer restoration failed; backup retained at %s\n' "$stage" >&2
            exit 1
        fi
    fi
    rm -rf -- "$stage" || exit 1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -n "$archive_file" ]; then
    cp -- "$archive_file" "$stage/humanizer.tar.gz"
else
    curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
        -o "$stage/humanizer.tar.gz" "https://codeload.github.com/blader/humanizer/tar.gz/$HUMANIZER_COMMIT"
fi
printf '%s  %s\n' "$HUMANIZER_SHA256" "$stage/humanizer.tar.gz" | sha256sum -c -
tar -xzf "$stage/humanizer.tar.gz" -C "$stage"
source="$stage/humanizer-$HUMANIZER_COMMIT"
for asset in SKILL.md LICENSE; do
    [ -f "$source/$asset" ] && [ -r "$source/$asset" ] && [ -s "$source/$asset" ]
done
mkdir "$stage/incoming"
cp -- "$source/SKILL.md" "$source/LICENSE" "$stage/incoming/"
chmod -R a+rX "$stage/incoming"
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
if [ -e "$destination/humanizer" ] || [ -L "$destination/humanizer" ]; then
    cp -a -- "$destination/humanizer" "$stage/backup"
fi
changed=1
rm -rf -- "$destination/humanizer"
cp -a -- "$stage/incoming" "$destination/humanizer"
