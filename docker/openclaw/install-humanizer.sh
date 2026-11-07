#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

HUMANIZER_COMMIT=${HUMANIZER_COMMIT:-0ef9fe5bc3572734fd3ae6c448fe00a30cd5dcf0}
HUMANIZER_SHA256=${HUMANIZER_SHA256:-1828d04338ddde6e65f6188a32dd61a9edff74be7acf337c2cbd949ffb312cf8}
readonly HUMANIZER_COMMIT HUMANIZER_SHA256
install_require_commit humanizer "$HUMANIZER_COMMIT"

archive_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        *) printf 'invalid humanizer argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

changed=0
install_rollback() {
    [ "$changed" -eq 0 ] || install_restore "$destination/humanizer" "$stage/backup"
}
install_stage_init humanizer.XXXXXXXX 'humanizer restoration failed; backup'
install_fetch_archive "$archive_file" "https://codeload.github.com/blader/humanizer/tar.gz/$HUMANIZER_COMMIT" "$HUMANIZER_SHA256" "$stage/humanizer.tar.gz"
source="$stage/humanizer-$HUMANIZER_COMMIT"
for asset in SKILL.md LICENSE; do
    [ -f "$source/$asset" ] && [ -r "$source/$asset" ] && [ -s "$source/$asset" ]
done
mkdir "$stage/incoming"
cp -- "$source/SKILL.md" "$source/LICENSE" "$stage/incoming/"
chmod -R a+rX "$stage/incoming"
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
install_save "$destination/humanizer" "$stage/backup"
changed=1
rm -rf -- "$destination/humanizer"
cp -a -- "$stage/incoming" "$destination/humanizer"
