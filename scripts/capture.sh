#!/usr/bin/env bash
# Capture opening lines continuously. See scripts/capture.bat for the notes.
#
#   scripts/capture.sh            Kalshi, free, no key
#   scripts/capture.sh oddsapi    sportsbook opens, needs ODDS_API_KEY
set -euo pipefail
cd "$(dirname "$0")/.."
SOURCE="${1:-kalshi}"

if [ "$SOURCE" = "oddsapi" ]; then
  : "${ODDS_API_KEY:?set ODDS_API_KEY first, or drop the argument to use Kalshi, which needs no key}"
fi

mkdir -p data
echo "Capturing from $SOURCE to data/opens.jsonl.gz  (Ctrl+C to stop)"
exec python3 -m cfb_edge.watch --source "$SOURCE" \
  --log data/opens.jsonl.gz --out data/opens.csv
