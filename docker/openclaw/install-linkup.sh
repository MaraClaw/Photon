#!/bin/sh
set -eu

destination="$(npm root -g)/openclaw/skills"
install -d -m 0755 "$destination"
rm -rf "$destination"/linkup-*
cp -R /tmp/linkup-skills/linkup-* "$destination/"
chmod -R a+rX "$destination"/linkup-*
rm -rf /tmp/linkup-skills
