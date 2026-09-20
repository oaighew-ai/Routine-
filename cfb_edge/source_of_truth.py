"""Build the one authoritative picks contract.

Everything upstream is evidence or a candidate.  Only this module can publish
``picks.json``.  It is intentionally stricter than the individual scanners:
an upstream row may be useful research while still being ineligible for the
card.  Missing data, conflicting sides, stale quotes, unknown provenance, or a
blocked model all resolve to NO BET and zero exposure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .engine import reasons
from .ledger import CANDIDATE, load

SCHEMA_VERSION = 1
CONTRACT = "CFB_EDGE_PICKS_V1"
MAX_PICKS = 5

BLOCKING_REASONS = frozenset({
    reasons.NO_EVIDENCE,
    reasons.AGGREGATE_ONLY,
    reasons.CLV_KILL,
    reasons.LUCK_RISK,
    reasons.OPPOSED,
    reasons.NEG_EV,
    reasons.DEVIG_SENSITIVE,
    reasons.DOMINATED,
    reasons.SUB_MIN,
    reasons.STALE,
    reasons.UNMAPPED,
    reasons.DEVIG_IMPLAUSIBLE,
    reasons.BOOK_DISAGREE,
    reasons.LOOKAHEAD,
    reasons.UNGRADEABLE_PRICE,
    reasons.LINE_MISMATCH,
    reasons.IN_PLAY,
})


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _sha(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_reasons(
    row: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    generated_at: datetime,
    capture_fresh: bool,
) -> list[str]:
    out: list[str] = []
    if row.get("sample"):
        out.append("SAMPLE_ROW")
    if row.get("decision") != reasons.BET:
        out.append("NOT_BET_DECISION")
    if float((row.get("outputs") or {}).get("stake") or 0.0) <= 0.0:
        out.append("ZERO_STAKE")
    out.extend(sorted(BLOCKING_REASONS.intersection(row.get("reasonCodes") or [])))
    if not capture_fresh:
        out.append("CAPTURE_NOT_CURRENT")
    if row.get("priceSource") not in {"capture", "fill"}:
        out.append("PRICE_SOURCE_NOT_EXECUTABLE")
    if not row.get("venue") or row.get("price") is None or row.get("line") is None:
        out.append("QUOTE_INCOMPLETE")
    minimum_books = int((authority.get("requirements") or {}).get(
        "minimumSamePointBooks", 0
    ))
    if len(set(row.get("bookSet") or [])) < minimum_books:
        out.append("INSUFFICIENT_SAME_POINT_BOOKS")

    logged = _time(row.get("loggedAt"))
    kickoff = _time(row.get("startsAt"))
    max_age = int((authority.get("requirements") or {}).get(
        "maximumQuoteAgeSeconds", 900
    ))
    if logged is None or logged > generated_at:
        out.append("INVALID_DECISION_TIME")
    elif (generated_at - logged).total_seconds() > max_age:
        out.append("QUOTE_STALE")
    if kickoff is None or kickoff <= generated_at:
        out.append("KICKOFF_NOT_FUTURE")

    expected = {
        "modelId": authority.get("modelId"),
        "modelVersion": authority.get("modelVersion"),
        "modelSha256": authority.get("modelSha256"),
        "protocol": authority.get("protocol"),
    }
    for field, value in expected.items():
        if not value or row.get(field) != value:
            out.append(f"{field.upper()}_MISMATCH")
    return sorted(set(out))


def _capture_is_fresh(capture: Mapping[str, Any], now: datetime,
                      max_age: int) -> bool:
    poll = _time(capture.get("pollAt"))
    return bool(
        capture.get("captureOutcome") == "success"
        and capture.get("freshAtGeneration") is True
        and poll is not None
        and 0 <= (now - poll).total_seconds() <= max_age
    )


def _validation_reasons(authority: Mapping[str, Any], now: datetime) -> list[str]:
    """Recompute every registered evidence gate from the authority metrics."""
    requirements = authority.get("requirements") or {}
    observed = authority.get("observed") or {}
    out: list[str] = []

    as_of = _time(authority.get("asOf"))
    maximum_age = int(requirements.get("maximumValidationAgeSeconds", 604800))
    if as_of is None or as_of > now:
        out.append("INVALID_VALIDATION_TIME")
    elif (now - as_of).total_seconds() > maximum_age:
        out.append("VALIDATION_STALE")
    if authority.get("validationCompleted") is not True:
        out.append("VALIDATION_NOT_COMPLETED")
    if authority.get("deterministicReplay") != "VERIFIED":
        out.append("REPLAY_NOT_VERIFIED")

    def values(*names: str) -> list[float] | None:
        result: list[float] = []
        for name in names:
            value = observed.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            result.append(float(value))
        return result

    checks: list[tuple[str, bool | None]] = []
    v = values("nonPushForecasts")
    checks.append(("MINIMUM_FORECASTS", None if v is None else
                   v[0] >= float(requirements.get("minimumNonPushForecasts", 0))))
    v = values("weekClusters")
    checks.append(("MINIMUM_WEEKS", None if v is None else
                   v[0] >= float(requirements.get("minimumWeekClusters", 0))))
    v = values("outcomeCoverage")
    checks.append(("OUTCOME_COVERAGE", None if v is None else
                   v[0] >= float(requirements.get("minimumOutcomeCoverage", 0))))
    v = values("modelLogLoss", "marketLogLoss")
    checks.append(("LOG_LOSS_ADVANTAGE", None if v is None else
                   v[1] - v[0] >= float(requirements.get("minimumLogLossAdvantage", 0))))
    if requirements.get("brierNoWorseThanMarket") is True:
        v = values("modelBrier", "marketBrier")
        checks.append(("BRIER_NO_WORSE", None if v is None else v[0] <= v[1]))
    v = values("modelEce", "marketEce")
    checks.append(("ECE_DISADVANTAGE", None if v is None else
                   v[0] - v[1] <= float(requirements.get("maximumEceDisadvantage", 1))))
    v = values("maximumAnytimeEValue")
    checks.append(("ANYTIME_E_VALUE", None if v is None else
                   v[0] >= float(requirements.get("minimumAnytimeEValue", 0))))

    if any(passed is None for _, passed in checks):
        out.append("VALIDATION_METRICS_INCOMPLETE")
    out.extend(name for name, passed in checks if passed is False)
    return sorted(set(out))


def build(
    *,
    authority: Mapping[str, Any],
    registry: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]] = (),
    capture_report: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    revision: str = "unknown",
    previous: Mapping[str, Any] | None = None,
    file_hashes: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Return one fail-closed, deterministic delivery envelope."""
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("generated_at must include a timezone")
    now = now.astimezone(timezone.utc)
    capture = dict(capture_report or {})
    max_age = int((authority.get("requirements") or {}).get(
        "maximumQuoteAgeSeconds", 900
    ))
    capture_fresh = _capture_is_fresh(capture, now, max_age)

    authority_reasons: list[str] = []
    if authority.get("authorityId") != CONTRACT:
        authority_reasons.append("AUTHORITY_ID_MISMATCH")
    if authority.get("status") != "PASSED":
        authority_reasons.append("VALIDATION_NOT_PASSED")
    if authority.get("allowPaperDelivery") is not True:
        authority_reasons.append("PAPER_DELIVERY_BLOCKED")
    if authority.get("failedGates"):
        authority_reasons.append("REGISTERED_GATES_FAILED")
    authority_reasons.extend(_validation_reasons(authority, now))
    if not capture_fresh:
        authority_reasons.append("CAPTURE_NOT_CURRENT")
    delivery_models = [
        model.get("id") for model in (registry.get("models") or [])
        if model.get("deliveryEligible") is True
    ]
    if delivery_models != [authority.get("modelId")]:
        authority_reasons.append("REGISTRY_AUTHORITY_MISMATCH")

    rows = list(candidates)
    inspected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        exclusions = _candidate_reasons(
            row, authority=authority, generated_at=now,
            capture_fresh=capture_fresh,
        )
        item = {
            "candidateId": row.get("id"),
            "eventId": row.get("eventId"),
            "startsAt": row.get("startsAt"),
            "market": row.get("market"),
            "selection": row.get("side"),
            "line": row.get("line"),
            "price": row.get("price"),
            "book": row.get("venue"),
            "netEdge": (row.get("outputs") or {}).get("ev"),
            "paperStakeUnits": (row.get("outputs") or {}).get("stake", 0.0),
            "modelId": row.get("modelId"),
            "exclusions": exclusions,
        }
        inspected.append(item)
        if not exclusions and not authority_reasons:
            eligible.append(item)

    # One action per game and market.  Any opposing selections invalidate the
    # whole market; silently choosing the higher number would hide conflict.
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        grouped[(item["eventId"], item["market"])].append(item)
    selected: list[dict[str, Any]] = []
    for key, group in grouped.items():
        sides = {item["selection"] for item in group}
        if len(sides) != 1:
            for item in group:
                item["exclusions"] = ["CONFLICTING_SELECTIONS"]
            continue
        selected.append(max(group, key=lambda x: float(x["netEdge"] or 0.0)))

    selected.sort(key=lambda x: (-float(x["netEdge"] or 0.0), str(x["candidateId"])))
    selected = selected[:MAX_PICKS]
    for rank, item in enumerate(selected, 1):
        item["rank"] = rank
        item["status"] = "PROVISIONAL_LEAN"

    card_basis = {
        "authoritySha256": _canonical_sha(authority),
        "picks": selected,
    }
    card_hash = _canonical_sha(card_basis)
    previous_hash = (previous or {}).get("cardHash")
    allow = bool(not authority_reasons and selected)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "contract": CONTRACT,
        "generatedAt": now.isoformat(),
        "sourceRevision": revision,
        "status": "PROVISIONAL_PAPER_CARD" if allow else "NO_BET",
        "allowPaperDelivery": allow,
        "authorityReasons": sorted(set(authority_reasons)),
        "cardHash": card_hash,
        "previousCardHash": previous_hash,
        "changedSincePreviousCard": bool(previous_hash and previous_hash != card_hash),
        "model": {
            "id": authority.get("modelId"),
            "version": authority.get("modelVersion"),
            "sha256": authority.get("modelSha256"),
            "protocol": authority.get("protocol"),
        },
        "validation": {
            "asOf": authority.get("asOf"),
            "status": authority.get("status"),
            "failedGates": authority.get("failedGates", []),
            "observed": authority.get("observed", {}),
            "requirements": authority.get("requirements", {}),
        },
        "capture": {
            "pollAt": capture.get("pollAt"),
            "outcome": capture.get("captureOutcome"),
            "current": capture_fresh,
        },
        "registry": registry,
        "picks": selected if allow else [],
        "candidatesInspected": len(inspected),
        "candidateAudit": inspected,
        "paperExposureUnits": sum(float(p["paperStakeUnits"]) for p in selected) if allow else 0.0,
        "actualExposureUnits": 0.0,
        "actualBetsRecorded": bool(authority.get("actualBetsRecorded", False)),
        "fileHashes": dict(file_hashes or {}),
        "limitations": [
            "Research and paper display only; no wager is placed by this system.",
            "Injuries, rosters, weather, executable fills, CLV and betting edge remain unverified unless explicitly present in the authority export.",
            "Raw captures, preview rows, historical cards, S01, F03, S03 inputs and monitor-only systems cannot supply picks."
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--capture-report")
    parser.add_argument("--candidates")
    parser.add_argument("--previous")
    parser.add_argument("--out", required=True)
    parser.add_argument("--revision", default="unknown")
    args = parser.parse_args(argv)

    authority_path = Path(args.authority)
    registry_path = Path(args.registry)
    capture_path = Path(args.capture_report) if args.capture_report else None
    candidate_path = Path(args.candidates) if args.candidates else None
    previous_path = Path(args.previous) if args.previous else None
    rows = load(candidate_path, CANDIDATE) if candidate_path and candidate_path.exists() else []
    result = build(
        authority=_read_json(authority_path, {}),
        registry=_read_json(registry_path, {}),
        candidates=rows,
        capture_report=_read_json(capture_path, {}),
        revision=args.revision,
        previous=_read_json(previous_path, {}),
        file_hashes={
            "authority": _sha(authority_path),
            "registry": _sha(registry_path),
            "captureReport": _sha(capture_path),
            "candidates": _sha(candidate_path),
        },
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True,
                              allow_nan=False) + "\n", encoding="utf-8")
    print(f"{result['status']}: {len(result['picks'])} picks, "
          f"{result['paperExposureUnits']:.2f}u paper, 0.00u actual")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
