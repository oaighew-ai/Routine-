#!/usr/bin/env bash
# Ten seconds that tell you whether the capture will work.
#
#   scripts/preflight.sh
#
# The capture is a long-running poller. Left to itself, a parser that does not
# match Kalshi's real field names looks exactly like a quiet board: it runs all
# night and records nothing, and you find out when the release window has
# already closed. This does one poll and says plainly which it is.
set -uo pipefail
cd "$(dirname "$0")/.."
SEASON="${1:-2026}"
WEEK="${2:-current}"
SOURCE="${3:-kalshi}"
mkdir -p data

echo "1/3  Building the slate ($SEASON, week $WEEK)..."
if ! python3 -m cfb_edge.slate --season "$SEASON" --week "$WEEK" \
        --out data/slate_current.csv; then
  echo
  echo "FAIL: could not build the slate."
  echo "  The schedule comes from raw.githubusercontent.com and needs no key."
  echo "  If that host is unreachable, nothing downstream can run."
  exit 1
fi
GAMES=$(( $(wc -l < data/slate_current.csv) - 1 ))
echo "     $GAMES games."

echo
echo "2/3  One poll against $SOURCE..."
python3 -m cfb_edge.watch --source "$SOURCE" --slate data/slate_current.csv \
  --log data/preflight.jsonl.gz --out data/preflight.csv --once

echo
echo "3/3  Counting what landed..."
LINES=$(( $(wc -l < data/preflight.csv 2>/dev/null || echo 1) - 1 ))
rm -f data/preflight.jsonl.gz data/preflight.csv

echo
if [ "$LINES" -gt 0 ]; then
  echo "PASS: $LINES opening lines parsed from $SOURCE."
  echo "The chain works end to end. Start the real capture:"
  echo "    scripts/capture.sh"
else
  echo "FAIL: 0 lines parsed."
  echo
  echo "The slate built, so this is the exchange call or the parser, not the"
  echo "schedule. Two things it is most likely to be:"
  echo "  - the board has no open NCAAF spread markets right now"
  echo "  - Kalshi's field names differ from what the parser expects"
  echo
  echo "Send one market's raw JSON to tell those apart:"
  echo "    curl -s 'https://api.elections.kalshi.com/trade-api/v2/markets?limit=3&status=open&series_ticker=KXNCAAFSPREAD'"
  exit 1
fi
