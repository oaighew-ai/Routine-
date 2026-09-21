"""Derived operating-health contract for CFB Edge.

This module is deliberately not a second decision engine. It reads the committed
authority, model registry and append-only evidence and reports whether the system
is healthy enough to make a decision. It never promotes a model, changes a gate,
or manufactures a pick.

The private Site remains the only live/paper delivery contract. This file makes
the control plane observable and machine-readable.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .ledger import CANDIDATE, FORECAST, GRADE, load
from .ledger.writer import write_json
from .source_of_truth import _capture_is_fresh, _validation_reasons

CONTRACT = "CFB_EDGE_OPS_HEALTH_V1"
SCHEMA_VERSION = 1
LOCAL_TZ = ZoneInfo("America/New_York")

# Monday=0. The workflow fires at both the EDT and EST UTC equivalents and this
# table decides which one is real. That avoids a silent one-hour shift when DST
# changes during the season.
_STAGE_BY_LOCAL_HOUR = {
    (0, 8): "model_review",
    (1, 9): "opening_board",
    (3, 7): "midweek_refresh",
    (4, 17): "pre_final",
    (5, 8): "final_board",
}


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("time must include a timezone")
    return result.astimezone(timezone.utc)


def cadence_stage(at: datetime | None = None) -> str | None:
    """Return the operating stage for this local hour, or None."""
    local = _aware(at).astimezone(LOCAL_TZ)
    return _STAGE_BY_LOCAL_HOUR.get((local.weekday(), local.hour))


def _json(path: Path | str | None, default: Any) -> Any:
    if path is None:
        return default
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _rows(path: Path | str | None, schema) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    return load(path, schema)


def _latest_receipt(directory: Path | str | None) -> dict | None:
    if directory is None:
        return None
    d = Path(directory)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "corrupt", "file": files[-1].name}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _gate(
    code: str,
    current: float | None,
    threshold: float | None,
    passed: bool | None,
    *,
    comparator: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "current": current,
        "threshold": threshold,
        "comparator": comparator,
        "passed": passed,
        "detail": detail,
    }


def gate_table(authority: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Display-only rendering of the committed validation requirements.

    Delivery still re-computes its own gates in source_of_truth. This table is
    intentionally descriptive so the dashboard can show progress without
    becoming an alternate authority.
    """
    req = authority.get("requirements") or {}
    obs = authority.get("observed") or {}

    n = _number(obs.get("nonPushForecasts"))
    n_req = _number(req.get("minimumNonPushForecasts"))
    weeks = _number(obs.get("weekClusters"))
    weeks_req = _number(req.get("minimumWeekClusters"))
    coverage = _number(obs.get("outcomeCoverage"))
    coverage_req = _number(req.get("minimumOutcomeCoverage"))
    model_ll = _number(obs.get("modelLogLoss"))
    market_ll = _number(obs.get("marketLogLoss"))
    ll_adv = None if model_ll is None or market_ll is None else market_ll - model_ll
    ll_req = _number(req.get("minimumLogLossAdvantage"))
    model_brier = _number(obs.get("modelBrier"))
    market_brier = _number(obs.get("marketBrier"))
    model_ece = _number(obs.get("modelEce"))
    market_ece = _number(obs.get("marketEce"))
    ece_delta = None if model_ece is None or market_ece is None else model_ece - market_ece
    ece_req = _number(req.get("maximumEceDisadvantage"))
    e_value = _number(obs.get("maximumAnytimeEValue"))
    e_req = _number(req.get("minimumAnytimeEValue"))

    return [
        _gate(
            "MINIMUM_FORECASTS", n, n_req,
            None if n is None or n_req is None else n >= n_req,
            comparator=">=", detail="Prospective non-push forecasts",
        ),
        _gate(
            "MINIMUM_WEEKS", weeks, weeks_req,
            None if weeks is None or weeks_req is None else weeks >= weeks_req,
            comparator=">=", detail="Independent week clusters",
        ),
        _gate(
            "OUTCOME_COVERAGE", coverage, coverage_req,
            None if coverage is None or coverage_req is None else coverage >= coverage_req,
            comparator=">=", detail="Forecasts with known outcomes",
        ),
        _gate(
            "LOG_LOSS_ADVANTAGE", ll_adv, ll_req,
            None if ll_adv is None or ll_req is None else ll_adv >= ll_req,
            comparator=">=", detail="Market log loss minus model log loss",
        ),
        _gate(
            "BRIER_NO_WORSE", model_brier, market_brier,
            None if model_brier is None or market_brier is None
            else model_brier <= market_brier,
            comparator="<=", detail="Model Brier versus market Brier",
        ),
        _gate(
            "ECE_DISADVANTAGE", ece_delta, ece_req,
            None if ece_delta is None or ece_req is None else ece_delta <= ece_req,
            comparator="<=", detail="Model ECE minus market ECE",
        ),
        _gate(
            "ANYTIME_E_VALUE", e_value, e_req,
            None if e_value is None or e_req is None else e_value >= e_req,
            comparator=">=", detail="Sequential evidence threshold",
        ),
    ]


def _capture_summary(capture: Mapping[str, Any], now: datetime, max_age: int) -> dict:
    games = list(capture.get("games") or [])
    exclusion_counts: Counter[str] = Counter()
    observed = 0
    for game in games:
        if game.get("observations"):
            observed += 1
        exclusion_counts.update(game.get("exclusions") or [])
    return {
        "outcome": capture.get("captureOutcome"),
        "pollAt": capture.get("pollAt"),
        "freshAtGeneration": capture.get("freshAtGeneration"),
        "freshNow": _capture_is_fresh(capture, now, max_age),
        "codeRevision": capture.get("codeRevision"),
        "logSha256": capture.get("logSha256"),
        "slateSha256": capture.get("slateSha256"),
        "gamesTotal": len(games),
        "gamesObserved": observed,
        "gamesMissing": sum(
            1 for g in games if "ABSENT_FROM_LATEST_POLL" in (g.get("exclusions") or [])
        ),
        "topExclusions": [
            {"code": code, "count": count}
            for code, count in exclusion_counts.most_common(8)
        ],
        "allowDelivery": bool(capture.get("allowDelivery")),
    }


def _implementation_rows(
    registry: Mapping[str, Any], implementations: Mapping[str, Any]
) -> list[dict[str, Any]]:
    impl_by_id = {
        x.get("modelId"): x for x in (implementations.get("implementations") or [])
        if x.get("modelId")
    }
    rows = []
    for model in registry.get("models") or []:
        model_id = model.get("id")
        impl = impl_by_id.get(model_id, {})
        rows.append({
            "id": model_id,
            "role": model.get("role"),
            "deliveryEligible": bool(model.get("deliveryEligible")),
            "modelStatus": model.get("modelStatus"),
            "implementationStatus": impl.get("status", "UNREGISTERED"),
            "implementationLocation": impl.get("location"),
            "reproducibleInRepo": bool(impl.get("reproducibleInRepo")),
            "deliveryContract": impl.get("deliveryContract"),
        })
    return rows


def _ledger_summary(forecasts: Iterable[Mapping[str, Any]],
                    candidates: Iterable[Mapping[str, Any]],
                    grades: Iterable[Mapping[str, Any]]) -> dict:
    forecast_rows = list(forecasts)
    candidate_rows = list(candidates)
    grade_rows = list(grades)
    decision_counts = Counter(str(r.get("decision") or "UNKNOWN") for r in candidate_rows)
    model_counts = Counter(str(r.get("modelId") or "UNSPECIFIED") for r in candidate_rows)
    graded = {str(r.get("candidateId")) for r in grade_rows}
    bet_ids = {
        str(r.get("id")) for r in candidate_rows
        if r.get("decision") == "BET" and r.get("id") is not None
    }
    forecast_models = Counter(
        str(r.get("modelId") or "UNSPECIFIED") for r in forecast_rows
    )
    return {
        "forecastRows": len(forecast_rows),
        "forecastModelCounts": dict(sorted(forecast_models.items())),
        "candidateRows": len(candidate_rows),
        "gradeRows": len(grade_rows),
        "decisionCounts": dict(sorted(decision_counts.items())),
        "modelCounts": dict(sorted(model_counts.items())),
        "betRows": len(bet_ids),
        "ungradedBetRows": len(bet_ids - graded),
    }


def _next_actions(failed: set[str], authority: Mapping[str, Any],
                  external_production: bool) -> list[str]:
    req = authority.get("requirements") or {}
    obs = authority.get("observed") or {}
    actions: list[str] = []

    if "MINIMUM_FORECASTS" in failed:
        need = int(req.get("minimumNonPushForecasts", 0) or 0)
        have = int(obs.get("nonPushForecasts", 0) or 0)
        actions.append(f"Collect {max(0, need - have)} more prospective non-push forecasts.")
    if "MINIMUM_WEEKS" in failed:
        need = int(req.get("minimumWeekClusters", 0) or 0)
        have = int(obs.get("weekClusters", 0) or 0)
        actions.append(f"Accumulate {max(0, need - have)} more independent week clusters.")
    if "LOG_LOSS_ADVANTAGE" in failed or "BRIER_NO_WORSE" in failed:
        actions.append(
            "Keep S02 in shadow. Do not tune on the validation cohort; collect "
            "prospective evidence or test a pre-registered challenger."
        )
    if "ANYTIME_E_VALUE" in failed:
        actions.append(
            "Continue sequential evidence collection. Do not lower the evidence threshold."
        )
    if external_production:
        actions.append(
            "Preserve the private Site as S02 authority until its exact implementation "
            "and source hash can be reproduced here. Do not reverse-engineer it from metadata."
        )
    if not actions:
        actions.append("No validation blocker remains. Re-run the delivery preflight.")
    return actions


def build_health(
    *,
    config: Mapping[str, Any],
    authority: Mapping[str, Any],
    registry: Mapping[str, Any],
    implementations: Mapping[str, Any],
    capture_report: Mapping[str, Any] | None = None,
    forecasts: Iterable[Mapping[str, Any]] = (),
    candidates: Iterable[Mapping[str, Any]] = (),
    grades: Iterable[Mapping[str, Any]] = (),
    latest_receipt: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    stage: str = "manual",
    revision: str = "unknown",
) -> dict[str, Any]:
    now = _aware(generated_at)
    requirements = authority.get("requirements") or {}
    max_quote_age = int(requirements.get("maximumQuoteAgeSeconds", 900) or 900)
    capture = _capture_summary(capture_report or {}, now, max_quote_age)
    validation_reasons = set(_validation_reasons(authority, now))

    if authority.get("status") != "PASSED":
        validation_reasons.add("VALIDATION_NOT_PASSED")
    if authority.get("allowPaperDelivery") is not True:
        validation_reasons.add("PAPER_DELIVERY_BLOCKED")
    if authority.get("failedGates"):
        validation_reasons.add("REGISTERED_GATES_FAILED")

    model_rows = _implementation_rows(registry, implementations)
    production = next(
        (m for m in model_rows if m.get("id") == authority.get("modelId")), None
    )
    external_production = bool(production and not production["reproducibleInRepo"])

    stage_requires_current_quote = stage in {
        "opening_board", "midweek_refresh", "pre_final", "final_board"
    }
    phase = str(config.get("phase") or "UNKNOWN")
    if validation_reasons:
        state = "SHADOW_BLOCKED"
    elif stage_requires_current_quote and not capture["freshNow"]:
        state = "DATA_BLOCKED"
    elif phase == "LIVE":
        state = "LIVE_READY"
    else:
        state = "PAPER_READY"

    warnings: list[str] = []
    if production is None:
        warnings.append("PRODUCTION_IMPLEMENTATION_UNREGISTERED")
    elif external_production:
        warnings.append("PRODUCTION_MODEL_EXTERNAL_TO_REPO")
    if not capture_report:
        warnings.append("CAPTURE_REPORT_MISSING")
    if stage_requires_current_quote and not capture["freshNow"]:
        warnings.append("CURRENT_QUOTE_REQUIRED")
    if latest_receipt and latest_receipt.get("status") not in {"ok", None}:
        warnings.append("LATEST_RUN_NOT_OK")

    ledger = _ledger_summary(forecasts, candidates, grades)
    if ledger["ungradedBetRows"]:
        warnings.append("UNGRADED_BET_ROWS")

    failed_gate_codes = {
        g["code"] for g in gate_table(authority) if g["passed"] is False
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "contract": CONTRACT,
        "generatedAt": now.isoformat(),
        "revision": revision,
        "stage": stage,
        "phase": phase,
        "state": state,
        "authority": {
            "authorityId": authority.get("authorityId"),
            "modelId": authority.get("modelId"),
            "modelVersion": authority.get("modelVersion"),
            "modelSha256": authority.get("modelSha256"),
            "protocol": authority.get("protocol"),
            "status": authority.get("status"),
            "allowPaperDelivery": bool(authority.get("allowPaperDelivery")),
            "deterministicReplay": authority.get("deterministicReplay"),
            "asOf": authority.get("asOf"),
            "recomputedReasons": sorted(validation_reasons),
            "registeredFailedGates": list(authority.get("failedGates") or []),
        },
        "validation": {
            "requirements": dict(requirements),
            "observed": dict(authority.get("observed") or {}),
            "gates": gate_table(authority),
        },
        "capture": capture,
        "ledger": ledger,
        "models": model_rows,
        "latestRun": dict(latest_receipt or {}),
        "warnings": sorted(set(warnings)),
        "nextActions": _next_actions(
            failed_gate_codes, authority, external_production
        ),
        "delivery": {
            "authoritativeContract": None if production is None
            else production.get("deliveryContract"),
            "githubDiagnosticOnly": True,
            "actualExposureAuthorized": bool(
                phase == "LIVE" and authority.get("allowPaperDelivery")
            ),
        },
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cfb_edge.ops_health", description=__doc__)
    p.add_argument("--stage-only", action="store_true")
    p.add_argument("--stage", default="auto")
    p.add_argument("--config", default="config/edge_os.json")
    p.add_argument("--authority", default="config/delivery_authority.json")
    p.add_argument("--registry", default="config/model_registry.json")
    p.add_argument(
        "--implementations", default="config/model_implementation_registry.json"
    )
    p.add_argument("--capture-report")
    p.add_argument("--forecasts")
    p.add_argument("--candidates")
    p.add_argument("--grades")
    p.add_argument("--runs-dir")
    p.add_argument("--out")
    p.add_argument("--revision", default="unknown")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stage = cadence_stage() if args.stage == "auto" else args.stage
    if args.stage_only:
        print(stage or "none")
        return 0
    if not args.out:
        raise SystemExit("--out is required unless --stage-only is used")
    if stage is None:
        stage = "manual"

    health = build_health(
        config=_json(args.config, {}),
        authority=_json(args.authority, {}),
        registry=_json(args.registry, {}),
        implementations=_json(args.implementations, {}),
        capture_report=_json(args.capture_report, {}),
        forecasts=_rows(args.forecasts, FORECAST),
        candidates=_rows(args.candidates, CANDIDATE),
        grades=_rows(args.grades, GRADE),
        latest_receipt=_latest_receipt(args.runs_dir),
        stage=stage,
        revision=args.revision,
    )
    write_json(args.out, health)
    print(
        f"{health['state']} | {health['stage']} | "
        f"{len(health['authority']['recomputedReasons'])} authority reason(s) | "
        f"{health['capture']['gamesObserved']}/{health['capture']['gamesTotal']} "
        "games observed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
