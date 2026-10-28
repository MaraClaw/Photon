#!/bin/sh
set -eu

CLAWSEC_COMMIT="03c19784f6c1e83ac81da2857f996ac9557dcceb"
CLAWSEC_SHA256="2bef41a7e6a071ad4342c80de08802afdfe2f35c29ad623d36e7f56fd6baa2d6"
readonly CLAWSEC_COMMIT CLAWSEC_SHA256

archive_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive-file) [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$archive_file" ] || exit 2; archive_file=$2; shift 2 ;;
        *) printf 'invalid soul-guardian argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

stage="$(mktemp -d "${TMPDIR:-/tmp}/soul-guardian.XXXXXXXX")"
changed=0
cleanup() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$changed" -eq 1 ]; then
        failed=0
        if ! rm -rf -- "$destination/soul-guardian"; then
            failed=1
        elif [ -e "$stage/backup" ] || [ -L "$stage/backup" ]; then
            cp -a -- "$stage/backup" "$destination/soul-guardian" || failed=1
        fi
        if [ "$failed" -ne 0 ]; then
            printf 'soul-guardian restoration failed; backup retained at %s\n' "$stage" >&2
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
    cp -- "$archive_file" "$stage/clawsec.tar.gz"
else
    curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
        -o "$stage/clawsec.tar.gz" "https://codeload.github.com/prompt-security/clawsec/tar.gz/$CLAWSEC_COMMIT"
fi
printf '%s  %s\n' "$CLAWSEC_SHA256" "$stage/clawsec.tar.gz" | sha256sum -c -
tar -xzf "$stage/clawsec.tar.gz" -C "$stage"
source="$stage/clawsec-$CLAWSEC_COMMIT"
[ -d "$source/skills/soul-guardian" ]
[ -s "$source/skills/soul-guardian/SKILL.md" ]
[ -f "$source/LICENSE" ]
[ -s "$source/LICENSE" ]
cp -a -- "$source/skills/soul-guardian" "$stage/incoming"
rm -f -- "$stage/incoming/LICENSE"
cp -- "$source/LICENSE" "$stage/incoming/LICENSE"
chmod -R a+rX "$stage/incoming"
destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
if [ -e "$destination/soul-guardian" ] || [ -L "$destination/soul-guardian" ]; then
    cp -a -- "$destination/soul-guardian" "$stage/backup"
fi
changed=1
rm -rf -- "$destination/soul-guardian"
cp -a -- "$stage/incoming" "$destination/soul-guardian"
