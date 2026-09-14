#!/usr/bin/env bash
# Capture opening lines continuously. See scripts/capture.bat for the notes.
#
#   scripts/capture.sh                    Kalshi, free, no key, current week
#   scripts/capture.sh 2026 3             a specific season and week
#   scripts/capture.sh 2026 3 oddsapi     sportsbook opens, needs ODDS_API_KEY
#
# The Kalshi path needs a slate: nothing in a Kalshi market says which team is
# at home, so the schedule is the only thing that can orient the line.
set -euo pipefail
cd "$(dirname "$0")/.."
SEASON="${1:-2026}"
WEEK="${2:-current}"
SOURCE="${3:-kalshi}"

if [ "$SOURCE" = "oddsapi" ]; then
  : "${ODDS_API_KEY:?set ODDS_API_KEY first, or drop the argument to use Kalshi, which needs no key}"
fi

mkdir -p data
echo "Building the slate for $SEASON week $WEEK..."
python3 -m cfb_edge.slate --season "$SEASON" --week "$WEEK" --out data/slate_current.csv

echo
echo "Capturing from $SOURCE to data/opens.jsonl.gz  (Ctrl+C to stop)"
exec python3 -m cfb_edge.watch --source "$SOURCE" --slate data/slate_current.csv \
  --log data/opens.jsonl.gz --out data/opens.csv
