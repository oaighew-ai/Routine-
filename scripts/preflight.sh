#!/usr/bin/env bash
# One poll with fresh scratch state and a meaningful exit status.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
exec python3 -m cfb_edge.preflight "$@"
