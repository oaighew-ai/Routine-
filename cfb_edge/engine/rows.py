"""Turning a `Decision` into the ledger row BUILD_PROMPT §5 specifies.

Kept apart from `posterior.py` on purpose. The decision logic should not know
what a row looks like, and the row builder should not be able to change a
decision. Everything here is transcription.
"""

from __future__ import annotations

from typing import Any, Mapping

from .posterior import Decision
from .version import ENGINE_VERSION, spec_hash


def candidate_row(
    decision: Decision,
    *,
    candidate_id: str,
    logged_at: str,
    phase: str,
    time_seen: str | None = None,
    sample: bool = False,
    model_id: str | None = None,
    model_version: str | None = None,
    model_sha256: str | None = None,
    protocol: str | None = None,
    capture_id: str | None = None,
) -> dict[str, Any]:
    """One `candidates.jsonl` row. Facts only; no aggregate is stored."""
    q = decision.quote
    row = {
        "id": candidate_id,
        "loggedAt": logged_at,
        "timeSeen": time_seen,
        "phase": phase,
        "sport": q.sport,
        "eventId": q.event_id,
        "startsAt": q.starts_at,
        "market": q.market,
        "side": q.side,
        "line": q.line,
        "price": q.price,
        "venue": q.venue,
        "bookSet": list(q.book_set),
        "priceSource": q.price_source,
        "priceKind": q.price_kind,
        "inputs": {
            "p_baseline": decision.p_baseline,
            "devig": dict(decision.devig),
            "signals": [
                {
                    "systemId": s.system_id,
                    "family": s.family,
                    "side": s.side,
                    "p_signal": s.p_signal,
                    "w_sig": s.w_sig,
                    "nProvider": s.n_provider,
                    "nOwn": s.n_own,
                    "method": s.method,
                    "flags": list(s.flags),
                }
                for s in decision.signals
            ],
            "p_model": decision.p_model,
            "w_mod": decision.w_mod,
        },
        "outputs": {
            "p_post": decision.p_post,
            "ev": decision.ev,
            "f_full": decision.f_full,
            "c": decision.c,
            "portfolioScale": decision.portfolio_scale,
            "stake": decision.stake_units,
            "maxPlayablePrice": decision.max_playable_price,
        },
        "decision": decision.decision,
        "reasonCodes": list(decision.reason_codes),
        "engineVersion": ENGINE_VERSION,
        "specHash": spec_hash(),
        **({"sample": True} if sample else {}),
    }
    provenance = {
        "modelId": model_id,
        "modelVersion": model_version,
        "modelSha256": model_sha256,
        "protocol": protocol,
        "captureId": capture_id,
    }
    row.update({key: value for key, value in provenance.items() if value is not None})
    return row


def grade_row(grade, *, sample: bool = False) -> dict[str, Any]:
    """One `grades.jsonl` row from an `engine.clv2.Grade`."""
    return {
        "candidateId": grade.candidate_id,
        "closeRef": grade.close_ref,
        "closeLine": grade.close_line,
        "closePrice": grade.close_price,
        "closeFairProb": grade.close_fair_prob,
        "clvPct": grade.clv_pct,
        "clvLagPct": grade.clv_lag_pct,
        "clvMethod": grade.clv_method,
        "rawMove": grade.raw_move,
        "result": grade.result,
        "units": grade.units,
        "gradedAt": grade.graded_at,
        **({"sample": True} if sample else {}),
    }
