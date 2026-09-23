"""Audit-grade automatic grading for S04_ES2 prospective signals.

This module never creates a signal and never changes the Week 5 decision rule.
It takes the frozen signal report plus the append-only capture report and grades
only rows that were already audit-grade at decision time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT = "CFB_EDGE_S04_ES2_SIGNAL_GRADES_V1"
MAX_CLOSE_AGE_SECONDS = 1800


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _capture_games(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("game") or "").strip(): row
        for row in report.get("games") or []
        if str(row.get("game") or "").strip()
    }


def _last_pre_kickoff_observation(
    row: Mapping[str, Any], kickoff: datetime
) -> Mapping[str, Any] | None:
    candidates = []
    for obs in row.get("observations") or []:
        at = _time(obs.get("observedAt") or obs.get("observed_at"))
        line = _number(obs.get("derivedHomeLine"))
        if at is None or line is None or at > kickoff:
            continue
        candidates.append((at, obs))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def _signal_rows(signal_report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Return both the directional family and the exact executable-shadow rule.

    The two cohorts are intentionally separate. Promotion evidence for S04_ES2
    must use EXECUTABLE_SHADOW, while DIRECTIONAL_SIGNAL is useful for diagnosing
    whether the football opinion or the execution gates are responsible.
    """
    rows: list[tuple[str, Mapping[str, Any]]] = []
    for row in signal_report.get("candidateAuditRows") or []:
        if row.get("auditGrade") is True:
            rows.append(("DIRECTIONAL_SIGNAL", row))
    for row in signal_report.get("inspectedLiveRows") or []:
        if row.get("status") == "EXECUTABLE_SHADOW":
            rows.append(("EXECUTABLE_SHADOW", row))
    return rows


def grade(
    signal_report: Mapping[str, Any],
    capture_report: Mapping[str, Any],
    *,
    now: datetime | None = None,
    maximum_close_age_seconds: int = MAX_CLOSE_AGE_SECONDS,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    games = _capture_games(capture_report)
    graded_rows = []

    for cohort, signal in _signal_rows(signal_report):
        game = str(signal.get("game") or "")
        kickoff = _time(signal.get("kickoff"))
        opening = _number(signal.get("openingHomeLine"))
        side = str(signal.get("side") or "")
        exclusions: list[str] = []

        if str(signal.get("source") or "") != "true_open":
            exclusions.append("ENTRY_NOT_TRUE_OPEN")
        if kickoff is None:
            exclusions.append("KICKOFF_MISSING")
        elif now < kickoff:
            exclusions.append("GAME_NOT_STARTED")
        if opening is None:
            exclusions.append("OPEN_LINE_MISSING")
        if "@" not in game:
            exclusions.append("INVALID_GAME")

        close_obs = None
        close_home_line = None
        close_at = None
        close_age = None
        if kickoff is not None:
            source_game = games.get(game)
            if source_game is None:
                exclusions.append("CAPTURE_GAME_MISSING")
            else:
                close_obs = _last_pre_kickoff_observation(source_game, kickoff)
                if close_obs is None:
                    exclusions.append("PRE_KICKOFF_CLOSE_MISSING")
                else:
                    close_home_line = _number(close_obs.get("derivedHomeLine"))
                    close_at = _time(close_obs.get("observedAt") or close_obs.get("observed_at"))
                    if close_at is not None:
                        close_age = (kickoff - close_at).total_seconds()
                        if close_age < -1 or close_age > maximum_close_age_seconds:
                            exclusions.append("CLOSE_NOT_FRESH_ENOUGH")

        line_clv = None
        beat_close = None
        if not exclusions and opening is not None and close_home_line is not None:
            away, home = (x.strip() for x in game.split("@", 1))
            if side == home:
                entry_side = opening
                close_side = close_home_line
            elif side == away:
                entry_side = -opening
                close_side = -close_home_line
            else:
                exclusions.append("SIDE_NOT_IN_GAME")
                entry_side = close_side = None
            if entry_side is not None:
                line_clv = entry_side - close_side
                beat_close = line_clv > 0

        provenance = {
            "entrySource": signal.get("source"),
            "firstSeen": signal.get("firstSeen"),
            "venueOpenTime": signal.get("venueOpenTime"),
            "openLagSeconds": signal.get("openLagSeconds"),
            "closeObservedAt": None if close_at is None else close_at.isoformat(),
            "closeAgeSeconds": close_age,
        }
        evidence_hash = hashlib.sha256(
            json.dumps(
                {
                    "game": game,
                    "side": side,
                    "openingHomeLine": opening,
                    "closeHomeLine": close_home_line,
                    "provenance": provenance,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        graded_rows.append({
            "cohort": cohort,
            "game": game,
            "side": side,
            "kickoff": signal.get("kickoff"),
            "openingHomeLine": opening,
            "closingHomeLine": close_home_line,
            "lineClvPoints": line_clv,
            "beatClose": beat_close,
            "gradeable": not exclusions,
            "exclusions": sorted(set(exclusions)),
            "provenance": provenance,
            "evidenceSha256": evidence_hash,
        })

    gradeable = [r for r in graded_rows if r["gradeable"]]
    executable = [
        r for r in gradeable if r.get("cohort") == "EXECUTABLE_SHADOW"
    ]
    directional = [
        r for r in gradeable if r.get("cohort") == "DIRECTIONAL_SIGNAL"
    ]
    clv = [float(r["lineClvPoints"]) for r in gradeable]

    def cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(r["lineClvPoints"]) for r in rows]
        return {
            "gradeableRows": len(rows),
            "meanLineClvPoints": sum(values) / len(values) if values else None,
            "beatCloseRate": (
                sum(1 for r in rows if r["beatClose"]) / len(rows)
                if rows else None
            ),
        }
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "modelId": "S04_ES2",
        "status": "RESEARCH_ONLY",
        "generatedAt": now.isoformat(),
        "gradingPolicy": {
            "entryMustBeTrueOpen": True,
            "maximumCloseAgeSeconds": maximum_close_age_seconds,
            "closeDefinition": "last captured derived home line at or before kickoff",
            "decisionEffect": "NONE",
            "promotionEffect": "NONE",
        },
        "summary": {
            "signalRows": len(graded_rows),
            "gradeableRows": len(gradeable),
            "pendingOrExcludedRows": len(graded_rows) - len(gradeable),
            "meanLineClvPoints": (sum(clv) / len(clv)) if clv else None,
            "beatCloseRate": (
                sum(1 for r in gradeable if r["beatClose"]) / len(gradeable)
                if gradeable else None
            ),
            "directionalSignal": cohort_summary(directional),
            "executableShadow": cohort_summary(executable),
        },
        "rows": graded_rows,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signals", required=True)
    p.add_argument("--capture-report", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--maximum-close-age-seconds", type=int, default=MAX_CLOSE_AGE_SECONDS)
    args = p.parse_args(argv)
    signal_report = json.loads(Path(args.signals).read_text(encoding="utf-8"))
    capture_report = json.loads(Path(args.capture_report).read_text(encoding="utf-8"))
    report = grade(
        signal_report,
        capture_report,
        maximum_close_age_seconds=args.maximum_close_age_seconds,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    s = report["summary"]
    print(
        f"S04_ES2 grades: {s['gradeableRows']}/{s['signalRows']} gradeable; "
        f"mean CLV={s['meanLineClvPoints']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
