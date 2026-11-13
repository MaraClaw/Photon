#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

TRACELATTICE_SKILL_COMMIT=${TRACELATTICE_SKILL_COMMIT:-8357477bdc01abb0338af1a69482d3a79a0946ef}
TRACELATTICE_SKILL_SHA256=${TRACELATTICE_SKILL_SHA256:-8ead1856da6f9a52490f04776c47447edbf899847c2ba9aaedb1af224bd48aac}
readonly TRACELATTICE_SKILL_COMMIT TRACELATTICE_SKILL_SHA256
install_require_commit tracelattice-skill "$TRACELATTICE_SKILL_COMMIT"

archive_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        *) printf 'invalid tracelattice-skill argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

changed=0
install_rollback() {
    [ "$changed" -eq 0 ] || install_restore "$destination/tracelattice-skill" "$stage/backup"
}
install_stage_init tracelattice-skill.XXXXXXXX 'tracelattice-skill restoration failed; backup'
install_fetch_archive "$archive_file" "https://codeload.github.com/iworkforces/TraceLattice/tar.gz/$TRACELATTICE_SKILL_COMMIT" "$TRACELATTICE_SKILL_SHA256" "$stage/tracelattice-skill.tar.gz"
source="$stage/TraceLattice-$TRACELATTICE_SKILL_COMMIT"
for asset in skills/tracelattice-skill/SKILL.md LICENSE; do
    [ -f "$source/$asset" ] && [ -r "$source/$asset" ] && [ -s "$source/$asset" ]
done
mkdir "$stage/incoming"
cp -- "$source/skills/tracelattice-skill/SKILL.md" "$source/LICENSE" "$stage/incoming/"
chmod -R a+rX "$stage/incoming"
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
install_save "$destination/tracelattice-skill" "$stage/backup"
changed=1
rm -rf -- "$destination/tracelattice-skill"
cp -a -- "$stage/incoming" "$destination/tracelattice-skill"
