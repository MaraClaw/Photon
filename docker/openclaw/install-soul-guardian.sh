#!/bin/sh
set -eu

# shellcheck source=install-common.sh
. "$(dirname -- "$0")/install-common.sh"

CLAWSEC_COMMIT=${CLAWSEC_COMMIT:-03c19784f6c1e83ac81da2857f996ac9557dcceb}
CLAWSEC_SHA256=${CLAWSEC_SHA256:-2bef41a7e6a071ad4342c80de08802afdfe2f35c29ad623d36e7f56fd6baa2d6}
readonly CLAWSEC_COMMIT CLAWSEC_SHA256
install_require_commit clawsec "$CLAWSEC_COMMIT"

archive_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        *) printf 'invalid soul-guardian argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

changed=0
install_rollback() {
    [ "$changed" -eq 0 ] || install_restore "$destination/soul-guardian" "$stage/backup"
}
install_stage_init soul-guardian.XXXXXXXX 'soul-guardian restoration failed; backup'
install_fetch_archive "$archive_file" "https://codeload.github.com/prompt-security/clawsec/tar.gz/$CLAWSEC_COMMIT" "$CLAWSEC_SHA256" "$stage/clawsec.tar.gz"
source="$stage/clawsec-$CLAWSEC_COMMIT"
[ -d "$source/skills/soul-guardian" ]
[ -s "$source/skills/soul-guardian/SKILL.md" ]
[ -f "$source/LICENSE" ]
[ -s "$source/LICENSE" ]
cp -a -- "$source/skills/soul-guardian" "$stage/incoming"
rm -f -- "$stage/incoming/LICENSE"
cp -- "$source/LICENSE" "$stage/incoming/LICENSE"
chmod -R a+rX "$stage/incoming"
destination="$HOME/.openclaw/skills"
install -d -m 0755 "$destination"
install_save "$destination/soul-guardian" "$stage/backup"
changed=1
rm -rf -- "$destination/soul-guardian"
cp -a -- "$stage/incoming" "$destination/soul-guardian"
