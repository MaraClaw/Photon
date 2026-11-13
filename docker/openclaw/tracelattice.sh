#!/bin/sh
set -eu

# The published bin asks for bun; the image runs the same entry file with node.
# OpenClaw starts this as a stdio MCP server, so an inherited transport choice must not apply.
TRACELATTICE_TRANSPORT_TYPE=stdio
export TRACELATTICE_TRANSPORT_TYPE
exec node /opt/tracelattice/node_modules/@iworkforces/tracelattice/dist/cli.js "$@"
