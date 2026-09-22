"""Offline capture diagnostics. Derived exchange lines are never executable picks."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def digest(path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def build(log, slate, now, outcome="success", revision="unknown"):
    from .watch import OpeningBook, classify_open_provenance

    if now.tzinfo is None:
        raise ValueError("Report time must include timezone")
    log, slate = Path(log), Path(slate)
    latest = None
    if log.exists():
        opener = gzip.open if log.suffix == ".gz" else open
        with opener(log, "rt", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    latest = json.loads(line)
    games = []
    if slate.exists():
        with slate.open(newline="", encoding="utf-8") as stream:
            games = sorted({r["game"] for r in csv.DictReader(stream)})
    poll = stamp(latest.get("polled_at")) if latest else None
    fresh = bool(outcome == "success" and poll and 0 <= (now-poll).total_seconds() <= 900)
    by_game = {}
    for q in (latest or {}).get("quotes", []):
        if q.get("market") == "spread":
            by_game.setdefault(q.get("game"), []).append(q)
    book = OpeningBook.load(log)
    first_quotes = book.first_quotes("spread")
    rows = []
    class_counts = {}
    for game in games:
        reasons = []
        if outcome != "success":
            reasons.append("CAPTURE_NOT_SUCCESSFUL")
        if not fresh:
            reasons.append("POLL_STALE_OR_INVALID")
        quotes = by_game.get(game, [])
        if not quotes:
            reasons.append("ABSENT_FROM_LATEST_POLL")
        observations = []
        for q in quotes:
            seen, kickoff = stamp(q.get("seen_at")), stamp(q.get("commence_time"))
            if not seen or not poll or not (seen <= poll <= now) or (now-seen).total_seconds() > 900:
                reasons.append("QUOTE_STALE_OR_INVALID")
            if not kickoff:
                reasons.append("KICKOFF_UNVERIFIED")
            elif not seen or not (seen < kickoff and now < kickoff):
                reasons.append("NOT_VERIFIED_PRE_KICKOFF")
            value = q.get("line")
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                reasons.append("INVALID_LINE")
                value = None
            observations.append({"book": q.get("book"), "derivedHomeLine": value,
                                 "observedAt": q.get("seen_at"), "kickoff": q.get("commence_time")})
        first = first_quotes.get(game)
        first_seen = str(getattr(first, "seen_at", "") or "") if first else ""
        venue_open = str(getattr(first, "venue_open_time", "") or "") if first else ""
        open_class, open_lag = classify_open_provenance(first_seen, venue_open)
        class_counts[open_class] = class_counts.get(open_class, 0) + 1
        if open_class != "true_open":
            reasons.append("OPEN_NOT_AUDIT_GRADE_TRUE_OPEN")
        reasons.extend(["EXECUTABLE_CONTRACT_UNVERIFIED", "DECISION_EVIDENCE_UNVERIFIED"])
        rows.append({
            "game": game, "action": "NO BET", "stakeUnits": 0,
            "openClassification": open_class,
            "auditGradeOpen": open_class == "true_open",
            "firstSeen": first_seen or None,
            "venueOpenTime": venue_open or None,
            "openLagSeconds": open_lag,
            "marketTickers": list(getattr(first, "market_tickers", ()) or ()) if first else [],
            "observations": observations,
            "exclusions": sorted(set(reasons)),
        })
    return {"schemaVersion": 1, "cohort": "ROUTINE_KALSHI_CAPTURE",
            "generatedAt": now.isoformat(), "pollAt": poll.isoformat() if poll else None,
            "captureOutcome": outcome, "freshAtGeneration": fresh,
            "codeRevision": revision, "logSha256": digest(log), "slateSha256": digest(slate),
            "allowDelivery": False, "actualStakeUnits": 0, "games": rows,
            "openEvidence": {
                "trueOpenToleranceSeconds": 900,
                "classificationCounts": class_counts,
                "auditGradeCount": class_counts.get("true_open", 0),
                "policy": "Only true_open may enter CLV promotion or stopping evidence."
            },
            "limitations": ["Separate from S02/S01/F03/S03; not their evidence.",
                            "Derived exchange lines are not executable sportsbook spreads.",
                            "Freshness is measured at generation; this report is a dated snapshot.",
                            "No verified fills, profit, betting edge or stakes."]}


def render(report):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = "".join("<tr><td>"+esc(r["game"])+"</td><td>NO BET · 0u</td><td>"+
                   esc(", ".join(r["exclusions"]))+"</td></tr>" for r in report["games"])
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>CFB Edge Capture Research</title><style>"
            ":root{color-scheme:light dark}body{font:15px system-ui;max-width:1200px;margin:32px auto;padding:0 18px;"
            "background:#101417;color:#edf2f4}h1{font-size:30px;margin-bottom:4px}p{color:#b8c4ca}"
            ".status{display:inline-block;padding:6px 10px;border:1px solid #58656b;border-radius:999px;font-weight:700}"
            "table{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:24px}td,th{text-align:left;"
            "padding:12px 6px;border-bottom:1px solid #344047;overflow-wrap:anywhere}th{color:#9eabb1}"
            "</style><h1>CFB Edge · Capture Research</h1><span class='status'>DELIVERY BLOCKED · 0u</span><p>Generated: "+
            esc(report["generatedAt"])+" · Latest poll: "+esc(report["pollAt"])+
            " · Capture: "+esc(report["captureOutcome"])+"</p><p>"+
            esc(" ".join(report["limitations"]))+
            "</p><table><thead><tr><th>Game</th><th>Decision</th><th>Exclusions</th></tr></thead><tbody>"+
            rows+"</tbody></table></html>")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--slate", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--capture-outcome", default="success")
    parser.add_argument("--revision", default="unknown")
    args = parser.parse_args(argv)
    report = build(args.log, args.slate, datetime.now(timezone.utc), args.capture_outcome, args.revision)
    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/"capture-report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (directory/"capture-report.html").write_text(render(report), encoding="utf-8")
    print(f"{len(report['games'])} games inspected; delivery blocked; actual stakes 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
