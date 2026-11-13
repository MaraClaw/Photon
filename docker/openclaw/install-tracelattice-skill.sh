#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

TRACELATTICE_SKILL_COMMIT=${TRACELATTICE_SKILL_COMMIT:-3c4c16fc2af4ccb265ce4d74b933745925bc2b40}
TRACELATTICE_SKILL_SHA256=${TRACELATTICE_SKILL_SHA256:-46a9a866dffd202c66cd9791cf1aea4cf81da38e9c304ff712d76eed9edf4601}
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
skill="skills/tracelattice-skill"
references="references/errors.md references/tool-calls.md"
for asset in "$skill/SKILL.md" LICENSE; do
    [ -f "$source/$asset" ] && [ -r "$source/$asset" ] && [ -s "$source/$asset" ]
done
for asset in $references; do
    [ -f "$source/$skill/$asset" ] && [ -r "$source/$skill/$asset" ] && [ -s "$source/$skill/$asset" ]
done
mkdir "$stage/incoming" "$stage/incoming/references"
cp -- "$source/$skill/SKILL.md" "$source/LICENSE" "$stage/incoming/"
for asset in $references; do
    cp -- "$source/$skill/$asset" "$stage/incoming/references/"
done
chmod -R a+rX "$stage/incoming"
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
install_save "$destination/tracelattice-skill" "$stage/backup"
changed=1
rm -rf -- "$destination/tracelattice-skill"
cp -a -- "$stage/incoming" "$destination/tracelattice-skill"
