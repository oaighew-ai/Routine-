#!/usr/bin/env bash
# Build the weekly card. One command for the workflow, the scheduled task and a
# person at a terminal, so the three cannot disagree (DECISIONS.md D39, D40).
#
#   scripts/build_weekly_card.sh EVIDENCE_DIR OUT_DIR [AS_OF]
#
# EVIDENCE_DIR  a checkout of the capture-data branch
# OUT_DIR       receives slate.csv, weekly-card.json and weekly-card.html
# AS_OF         decision time, ISO 8601 UTC; default now
#
# Environment, all optional:
#   SEASON  season year (default: the year of AS_OF)
#   WEEK    provider week to build (default: the earliest unfinished week)
#   SLATE   an existing slate CSV to use instead of building one. Building one
#           reads the public schedule over the network; everything else here
#           is a file.
#
# Exit 3 means no regular-season week is left to build, which is an answer and
# not a failure. Any other non-zero exit is a failure and writes no card.
set -euo pipefail

EV=${1:?usage: build_weekly_card.sh EVIDENCE_DIR OUT_DIR [AS_OF]}
OUT=${2:?usage: build_weekly_card.sh EVIDENCE_DIR OUT_DIR [AS_OF]}
AS_OF=${3:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}
EV=$(cd "$EV" && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "$(dirname "$0")/.."

SEASON=${SEASON:-${AS_OF:0:4}}
WEEK=${WEEK:-current}

# 1. The slate for the week being played. data/slate_current.csv on the
#    evidence branch is NOT that: the capture rewrites it for the week whose
#    lines are opening, which is next week from Friday evening on.
if [ -n "${SLATE:-}" ]; then
  cp "$SLATE" "$OUT/slate.csv"
  if ! [[ "$WEEK" =~ ^[0-9]+$ ]]; then
    echo "SLATE was given, so WEEK must be the provider week number it holds." >&2
    exit 2
  fi
else
  set +e
  python3 -m cfb_edge.slate --season "$SEASON" --week "$WEEK" --out "$OUT/slate.csv" > "$OUT/slate.log" 2>&1
  rc=$?
  set -e
  cat "$OUT/slate.log"
  if [ "$rc" -eq 3 ]; then exit 3; fi
  if [ "$rc" -ne 0 ]; then echo "slate build failed; no card written" >&2; exit "$rc"; fi
  if ! [[ "$WEEK" =~ ^[0-9]+$ ]]; then
    WEEK=$(awk '/^week [0-9]+ / {print $2; exit}' "$OUT/slate.log")
  fi
  if ! [[ "$WEEK" =~ ^[0-9]+$ ]]; then
    echo "could not read the provider week from the slate output" >&2
    exit 2
  fi
fi

# 2. The registered cohort for that provider week, if there is one. Its
#    product week is the card's name; without a contract the card is labelled
#    by provider week only and says so.
COHORT=$(python3 -m cfb_edge.cohort for-week --season "$SEASON" --provider-week "$WEEK" \
           config/cohorts config/br2_active_cohort.json config/week5_cohort.json || true)
PRODUCT=""
if [ -n "$COHORT" ]; then
  PRODUCT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["productWeek"])' "$COHORT")
fi

# 3. The freshest sportsbook board fetched at or before AS_OF. A board fetched
#    after the decision time is information the decision did not have.
BOARD=$(python3 - "$EV/data/features/br2/market/raw" "$AS_OF" <<'PY'
import gzip, json, os, sys
from datetime import datetime, timezone

def when(text):
    try:
        t = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

folder, as_of = sys.argv[1], when(sys.argv[2])
best = None
for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
    try:
        with gzip.open(os.path.join(folder, name), "rt") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        continue
    if not (isinstance(payload, dict) and "events" in payload):
        continue
    fetched = when(payload.get("fetched_at"))
    if fetched is None or fetched > as_of:
        continue
    if best is None or fetched > best[0]:
        best = (fetched, os.path.join(folder, name))
print(best[1] if best else "")
PY
)

args=(--slate "$OUT/slate.csv" --as-of "$AS_OF"
      --evidence-root "$EV" --evidence-revision "$(git -C "$EV" rev-parse HEAD 2>/dev/null || echo unknown)"
      --code-revision "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
      --calibration docs/research/walkforward_2026.json
      --out "$OUT/weekly-card.json" --html "$OUT/weekly-card.html")
add () { if [ -e "$2" ]; then args+=("$1" "$2"); fi; }

[ -n "$BOARD" ] && args+=(--board "$BOARD")
[ -n "$COHORT" ] && args+=(--cohort "$COHORT")
add --kalshi-log     "$EV/data/opens.jsonl.gz"
add --opens-csv      "$EV/data/opens.csv"
add --capture-status "$EV/data/status.txt"
add --br2-status     "$EV/data/br2-feature-status.json"
add --br2-health     "$EV/data/br2-source-health.json"
add --early-season   "$EV/data/early-season-learning.json"
# The prospective lock is filed per provider-week cohort, so a card never reads
# another week's opens.
add --prospective    "$EV/data/open-capture/CFB_${SEASON}_PROVIDER_WEEK_${WEEK}/status.json"
if [ -n "$PRODUCT" ]; then
  # Week-specific shadow artifacts exist only for cohorts that registered them.
  add --recovered-freeze "config/week${PRODUCT}_clv_freeze.json"
  add --shadow-decision  "$EV/data/week${PRODUCT}-s04-es2-decision.json"
  if [ -e "$EV/data/week${PRODUCT}-clv-grades.json" ]; then
    args+=(--grades "$EV/data/week${PRODUCT}-clv-grades.json")
  else
    add --grades "$EV/data/s04-es2-grades.json"
  fi
  LABEL=$(printf '{"season":%s,"productWeek":%s,"providerWeek":%s,"title":"CFB Edge Weekly Card"}' "$SEASON" "$PRODUCT" "$WEEK")
else
  add --grades "$EV/data/s04-es2-grades.json"
  LABEL=$(printf '{"season":%s,"providerWeek":%s,"title":"CFB Edge Weekly Card"}' "$SEASON" "$WEEK")
fi
args+=(--label "$LABEL")

python3 -m cfb_edge.weekly_card "${args[@]}"
echo "cohort: ${COHORT:-none registered for provider week $WEEK}"
echo "board:  ${BOARD:-none at or before $AS_OF}"
