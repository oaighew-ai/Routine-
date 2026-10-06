"""Deterministic rerun of the registered CFB Edge delivery-authority gates.

This report does not alter authority. It recomputes the exact registered gates
from config/delivery_authority.json and records any additional research evidence
as context only.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .source_of_truth import _validation_reasons

CONTRACT="CFB_EDGE_AUTHORITY_GATE_AUDIT_V1"


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return None
    return float(value)


def audit(authority: Mapping[str,Any], *, now: datetime,
          clv_grades: Mapping[str,Any] | None=None) -> dict[str,Any]:
    req=authority.get("requirements") or {}
    obs=authority.get("observed") or {}
    rows=[]

    def add(name: str, passed: bool | None, observed: Any, required: Any):
        rows.append({
            "gate":name,
            "passed":passed,
            "observed":observed,
            "required":required,
        })

    add("MINIMUM_FORECASTS",
        None if _num(obs.get("nonPushForecasts")) is None else
        _num(obs["nonPushForecasts"]) >= float(req.get("minimumNonPushForecasts",0)),
        obs.get("nonPushForecasts"),req.get("minimumNonPushForecasts"))
    add("MINIMUM_WEEKS",
        None if _num(obs.get("weekClusters")) is None else
        _num(obs["weekClusters"]) >= float(req.get("minimumWeekClusters",0)),
        obs.get("weekClusters"),req.get("minimumWeekClusters"))
    add("OUTCOME_COVERAGE",
        None if _num(obs.get("outcomeCoverage")) is None else
        _num(obs["outcomeCoverage"]) >= float(req.get("minimumOutcomeCoverage",0)),
        obs.get("outcomeCoverage"),req.get("minimumOutcomeCoverage"))

    model_ll=_num(obs.get("modelLogLoss")); market_ll=_num(obs.get("marketLogLoss"))
    ll_adv=None if model_ll is None or market_ll is None else market_ll-model_ll
    add("LOG_LOSS_ADVANTAGE",
        None if ll_adv is None else ll_adv >= float(req.get("minimumLogLossAdvantage",0)),
        ll_adv,req.get("minimumLogLossAdvantage"))

    model_brier=_num(obs.get("modelBrier")); market_brier=_num(obs.get("marketBrier"))
    add("BRIER_NO_WORSE",
        None if model_brier is None or market_brier is None else model_brier <= market_brier,
        None if model_brier is None or market_brier is None else model_brier-market_brier,
        "<= 0")

    model_ece=_num(obs.get("modelEce")); market_ece=_num(obs.get("marketEce"))
    ece_delta=None if model_ece is None or market_ece is None else model_ece-market_ece
    add("ECE_DISADVANTAGE",
        None if ece_delta is None else ece_delta <= float(req.get("maximumEceDisadvantage",1)),
        ece_delta,req.get("maximumEceDisadvantage"))

    evalue=_num(obs.get("maximumAnytimeEValue"))
    add("ANYTIME_E_VALUE",
        None if evalue is None else evalue >= float(req.get("minimumAnytimeEValue",0)),
        evalue,req.get("minimumAnytimeEValue"))

    asof=_time(authority.get("asOf"))
    max_age=int(req.get("maximumValidationAgeSeconds",604800))
    age=None if asof is None else (now-asof).total_seconds()
    add("VALIDATION_FRESHNESS",
        None if age is None else 0 <= age <= max_age,
        age,max_age)
    add("DETERMINISTIC_REPLAY",
        authority.get("deterministicReplay")=="VERIFIED",
        authority.get("deterministicReplay"),"VERIFIED")

    add("VALIDATION_COMPLETED",
        authority.get("validationCompleted") is True,
        authority.get("validationCompleted"),True)

    failed=[r["gate"] for r in rows if r["passed"] is False]
    incomplete=[r["gate"] for r in rows if r["passed"] is None]
    registered_failed=sorted(set(authority.get("failedGates") or []))
    exact_validation_reasons=_validation_reasons(authority,now)
    authority_conditions=[]
    if authority.get("status") != "PASSED":
        authority_conditions.append("VALIDATION_NOT_PASSED")
    if authority.get("allowPaperDelivery") is not True:
        authority_conditions.append("PAPER_DELIVERY_BLOCKED")
    if registered_failed:
        authority_conditions.append("REGISTERED_GATES_FAILED")
    recomputed_failed=sorted(set(failed))

    clv=(clv_grades or {}).get("summary") or {}
    return {
        "schemaVersion":1,
        "contract":CONTRACT,
        "generatedAt":now.astimezone(timezone.utc).isoformat(),
        "authorityId":authority.get("authorityId"),
        "modelId":authority.get("modelId"),
        "modelVersion":authority.get("modelVersion"),
        "registeredStatus":authority.get("status"),
        "registeredAllowPaperDelivery":authority.get("allowPaperDelivery"),
        "gateRows":rows,
        "summary":{
            "registeredFailedGates":registered_failed,
            "recomputedFailedGates":recomputed_failed,
            "sourceOfTruthValidationReasons":exact_validation_reasons,
            "authorityConditionBlockers":sorted(set(authority_conditions)),
            "incompleteGates":incomplete,
            "allRegisteredGatesPass":(
                not exact_validation_reasons
                and not authority_conditions
                and not incomplete
            ),
            "authorityCanChangeFromThisReport":False,
        },
        "researchContext":{
            "week6ClvContract":(clv_grades or {}).get("contract"),
            "week6GradeableRows":clv.get("gradeableRows"),
            "week6ExecutableShadow":clv.get("executableShadow"),
            "note":"Week 6 CLV is research context only. It is not an input to the registered S02 delivery-authority gates."
        },
        "decisionEffect":"NONE",
        "stakingEffect":"NONE",
        "deliveryEffect":"NONE",
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--authority",required=True)
    p.add_argument("--clv-grades")
    p.add_argument("--out",required=True)
    args=p.parse_args(argv)
    authority=json.loads(Path(args.authority).read_text(encoding="utf-8"))
    clv={}
    if args.clv_grades and Path(args.clv_grades).exists():
        clv=json.loads(Path(args.clv_grades).read_text(encoding="utf-8"))
    result=audit(authority,now=datetime.now(timezone.utc),clv_grades=clv)
    target=Path(args.out); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result["summary"],sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
