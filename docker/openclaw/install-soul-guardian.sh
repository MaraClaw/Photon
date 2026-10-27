#!/bin/sh
set -eu

CLAWSEC_COMMIT="03c19784f6c1e83ac81da2857f996ac9557dcceb"
CLAWSEC_SHA256="2bef41a7e6a071ad4342c80de08802afdfe2f35c29ad623d36e7f56fd6baa2d6"
readonly CLAWSEC_COMMIT CLAWSEC_SHA256

stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
    -o "$stage/clawsec.tar.gz" "https://codeload.github.com/prompt-security/clawsec/tar.gz/$CLAWSEC_COMMIT"
printf '%s  %s\n' "$CLAWSEC_SHA256" "$stage/clawsec.tar.gz" | sha256sum -c -
tar -xzf "$stage/clawsec.tar.gz" -C "$stage"
source="$stage/clawsec-$CLAWSEC_COMMIT"
destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
rm -rf -- "$destination/soul-guardian"
cp -R "$source/skills/soul-guardian" "$destination/soul-guardian"
cp "$source/LICENSE" "$destination/soul-guardian/LICENSE"
chmod -R a+rX "$destination/soul-guardian"
