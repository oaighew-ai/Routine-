#!/usr/bin/env bash
# Capture opening lines continuously. See scripts/capture.bat for the notes.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ODDS_API_KEY:?set ODDS_API_KEY first: export ODDS_API_KEY=your-key}"
mkdir -p data
echo "Capturing to data/opens.jsonl.gz  (Ctrl+C to stop)"
exec python3 -m cfb_edge.watch --log data/opens.jsonl.gz --out data/opens.csv
