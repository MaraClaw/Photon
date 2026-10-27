#!/bin/sh
set -eu

GOGCLI_SKILLS_COMMIT="b8298ae6fd5335ddebc4bc8594e3b09aa3020ab4"
GOGCLI_SKILLS_SHA256="e1f4de9af4ef3f1607121ab68aef383dd2803a9450977abbab01cf1112df6d25"
readonly GOGCLI_SKILLS_COMMIT GOGCLI_SKILLS_SHA256

stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 1' HUP INT TERM
curl --disable --config /dev/null --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 120 --retry 2 --retry-delay 1 --retry-max-time 60 --noproxy '*' \
    -o "$stage/gogcli.tar.gz" "https://codeload.github.com/openclaw/gogcli/tar.gz/$GOGCLI_SKILLS_COMMIT"
printf '%s  %s\n' "$GOGCLI_SKILLS_SHA256" "$stage/gogcli.tar.gz" | sha256sum -c -
tar -xzf "$stage/gogcli.tar.gz" -C "$stage"
source="$stage/gogcli-$GOGCLI_SKILLS_COMMIT/.agents/skills"
destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
for skill in "$source"/*; do
    name="${skill##*/}"
    rm -rf -- "$destination/$name"
    cp -R "$skill" "$destination/$name"
    chmod -R a+rX "$destination/$name"
done
