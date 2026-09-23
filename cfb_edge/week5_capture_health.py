"""Audit Week 5 opening-capture quality without reading model signals.

This is an evidence-integrity contract, not a model gate. A TRUE_OPEN row is
valid only when the capture preserved enough lineage to replay which venue
event and bracketing contracts produced the opening line.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

TRUE_OPEN = "true_open"
MAX_LAG_SECONDS = 900
MIN_LAG_SECONDS = -60
PROVENANCE_FIELDS = (
    "first_seen",
    "venue_open_time",
    "event_ticker",
    "market_tickers",
    "first_valid_two_sided_quote_time",
    "poll_time",
    "code_revision",
)


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    pos = (len(rows) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return rows[lo]
    weight = pos - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def _present(row: dict[str, str], field: str) -> bool:
    return bool(str(row.get(field) or "").strip())


def _evidence_hash(game: str, row: dict[str, str]) -> str:
    payload = {"game": game}
    for field in ("opening_line", "source", "open_lag_seconds", *PROVENANCE_FIELDS):
        payload[field] = row.get(field) or None
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build(slate_path: str | Path, opens_path: str | Path) -> dict[str, Any]:
    with Path(slate_path).open(newline="", encoding="utf-8") as fh:
        slate = [r for r in csv.DictReader(fh) if (r.get("game") or "").strip()]
    with Path(opens_path).open(newline="", encoding="utf-8") as fh:
        opens = {
            r["game"].strip(): r for r in csv.DictReader(fh)
            if (r.get("game") or "").strip()
        }

    rows = []
    source_counts: dict[str, int] = {}
    true_lags: list[float] = []
    invalid_true = 0
    observed = 0
    provenance_complete = 0

    for s in slate:
        game = s["game"].strip()
        op = opens.get(game)
        raw = op or {}
        source = str(raw.get("source") or "missing")
        source_counts[source] = source_counts.get(source, 0) + 1
        if op is not None:
            observed += 1

        lag = _number(raw.get("open_lag_seconds"))
        issues: list[str] = []
        missing_provenance = [f for f in PROVENANCE_FIELDS if not _present(raw, f)]

        if source == TRUE_OPEN:
            for field in missing_provenance:
                issues.append("TRUE_OPEN_MISSING_" + field.upper())
            if lag is None:
                issues.append("TRUE_OPEN_MISSING_LAG")
            elif lag < MIN_LAG_SECONDS or lag > MAX_LAG_SECONDS:
                issues.append("TRUE_OPEN_LAG_OUTSIDE_FROZEN_TOLERANCE")
            else:
                true_lags.append(lag)
            if issues:
                invalid_true += 1
            else:
                provenance_complete += 1

        rows.append({
            "game": game,
            "kickoff": s.get("kickoff") or None,
            "source": source,
            "openingLine": _number(raw.get("opening_line")),
            "firstSeen": raw.get("first_seen") or None,
            "venueOpenTime": raw.get("venue_open_time") or None,
            "openLagSeconds": lag,
            "eventTicker": raw.get("event_ticker") or None,
            "marketTickers": raw.get("market_tickers") or None,
            "firstValidTwoSidedQuoteTime": raw.get("first_valid_two_sided_quote_time") or None,
            "pollTime": raw.get("poll_time") or None,
            "codeRevision": raw.get("code_revision") or None,
            "provenanceComplete": source == TRUE_OPEN and not issues,
            "missingProvenanceFields": missing_provenance,
            "integrityIssues": sorted(set(issues)),
            "evidenceSha256": _evidence_hash(game, raw) if op else None,
        })

    total = len(rows)
    true_open = source_counts.get(TRUE_OPEN, 0)
    coverage = (true_open / total) if total else 0.0
    proven_coverage = (provenance_complete / total) if total else 0.0

    if invalid_true:
        status = "INTEGRITY_FAILURE"
    elif total and provenance_complete == total:
        status = "AUDIT_GRADE_CAPTURE_COMPLETE"
    elif observed == total:
        status = "CAPTURE_COMPLETE_WITH_NON_TRUE_OPEN_ROWS"
    else:
        status = "COLLECTING"

    return {
        "schemaVersion": 2,
        "contract": "CFB_EDGE_WEEK5_CAPTURE_HEALTH_V2",
        "season": 2026,
        "week": 5,
        "status": status,
        "policy": {
            "trueOpenRequiredForProspectiveEvidence": True,
            "maximumOpenLagSeconds": MAX_LAG_SECONDS,
            "minimumClockSkewSeconds": MIN_LAG_SECONDS,
            "requiredProvenanceFields": list(PROVENANCE_FIELDS),
            "exactContractLineageRequired": True,
            "modelSignalsConsulted": False,
            "decisionEffect": "NONE",
        },
        "summary": {
            "slateRows": total,
            "observedRows": observed,
            "trueOpenRows": true_open,
            "trueOpenCoveragePct": coverage,
            "provenanceCompleteTrueOpenRows": provenance_complete,
            "auditGradeCoveragePct": proven_coverage,
            "invalidTrueOpenRows": invalid_true,
            "sourceCounts": source_counts,
            "trueOpenLagSeconds": {
                "min": min(true_lags) if true_lags else None,
                "median": _percentile(true_lags, 0.5),
                "p95": _percentile(true_lags, 0.95),
                "max": max(true_lags) if true_lags else None,
            },
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slate", required=True)
    p.add_argument("--opens", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    report = build(args.slate, args.opens)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    s = report["summary"]
    print(
        f"Week 5 capture: {s['provenanceCompleteTrueOpenRows']}/{s['slateRows']} "
        f"audit-grade ({s['auditGradeCoveragePct']:.1%}); status={report['status']}"
    )
    return 2 if report["status"] == "INTEGRITY_FAILURE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
