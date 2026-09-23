"""Compact evidence bridge for the authoritative private CFB Edge Site.

This is not a second picks endpoint. The private Site keeps sole authority for
/api/picks. This contract gives that Site one public, read-only payload for the
GitHub evidence plane: Week 5 audit state, close grading, BR2 readiness, and
model-governance status.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "CFB_EDGE_PRIVATE_SITE_BRIDGE_V1"
AUTHORITATIVE_PICKS_URL = "https://cfb-edge-research.oaighew.chatgpt.site/api/picks"


def _json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p=Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _feature_summary(br2: Mapping[str, Any] | None) -> dict[str, Any]:
    if not br2:
        return {
            "status":"PENDING","games":0,"populatedFamilies":[],
            "missingFamilies":[],"sourceManifestComplete":False,
            "generatedAt":None,
        }
    s=br2.get("summary") or {}
    cov=s.get("featureCoverageRows") or {}
    return {
        "status": br2.get("status") or "UNKNOWN",
        "games": int(s.get("games") or 0),
        "populatedFamilies": sorted(k for k,v in cov.items() if int(v or 0)>0),
        "missingFamilies": sorted(k for k,v in cov.items() if int(v or 0)==0),
        "sourceManifestComplete": bool(br2.get("sourceManifest")) and all(
            x.get("sha256") and x.get("path") and x.get("kind")
            for x in (br2.get("sourceManifest") or [])
        ),
        "modelingEligible": bool(s.get("modelingEligible")),
        "generatedAt": br2.get("generatedAt"),
    }


def build(
    *,
    authority: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
    capture_health: Mapping[str, Any] | None,
    es2: Mapping[str, Any] | None,
    grades: Mapping[str, Any] | None,
    br2: Mapping[str, Any] | None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now=generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")

    cs=(capture_health or {}).get("summary") or {}
    gs=(grades or {}).get("summary") or {}
    exec_grade=gs.get("executableShadow") or {}
    power=readiness or {}
    metrics=power.get("metrics") or {}
    open_stage=(power.get("stages") or {}).get("openProvenance") or {}

    return {
        "schemaVersion":1,
        "contract":CONTRACT,
        "generatedAt":now.isoformat(),
        "authority":{
            "picksSource":"PRIVATE_SITE_LOCAL",
            "picksUrl":AUTHORITATIVE_PICKS_URL,
            "githubCanPublishPicks":False,
            "modelId":(authority or {}).get("modelId"),
            "modelVersion":(authority or {}).get("modelVersion"),
            "validationStatus":(authority or {}).get("status"),
            "allowPaperDelivery":bool((authority or {}).get("allowPaperDelivery")),
        },
        "research":{
            "status":power.get("status") or "PENDING",
            "week5DecisionRuleChanged":False,
            "deliveryEffect":"NONE",
            "stakingEffect":"NONE",
            "freezeManifestSha256":power.get("freezeManifestSha256"),
            "warnings":list(power.get("warnings") or []),
            "nextUnlocks":list(power.get("nextUnlocks") or []),
        },
        "week5":{
            "captureStatus":(
                (capture_health or {}).get("status")
                or open_stage.get("status")
                or "PENDING"
            ),
            "slateRows":int(cs.get("slateRows") or 0),
            "auditGradeOpenRows":int(cs.get("provenanceCompleteTrueOpenRows") or 0),
            "qualifiedExecutableShadowRows":int((es2 or {}).get("qualifiedCount") or 0),
            "gradedExecutableShadowRows":int(exec_grade.get("gradeableRows") or 0),
            "meanExecutableShadowClvPoints":exec_grade.get("meanLineClvPoints"),
            "beatCloseRate":exec_grade.get("beatCloseRate"),
            "es2Status":(es2 or {}).get("status") or "PENDING",
        },
        "br2":_feature_summary(br2),
        "runway":power.get("stages") or {
            "openProvenance":{"status":"PENDING","detail":"Week 5 capture-window state is unavailable."},
            "s04Es2":{"status":"PENDING","detail":"Frozen Week 5 shadow not active yet."},
            "closeGrading":{"status":"PENDING","detail":"No gradeable closes yet."},
            "br2Warehouse":{"status":"PENDING","detail":"BR2 snapshot not published yet."},
        },
        "consistency":{
            "readinessBr2Games":int(metrics.get("br2Games") or 0),
            "readinessSourceManifestComplete":bool(metrics.get("br2SourceManifestComplete")),
        },
    }


def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--authority", default="config/delivery_authority.json")
    p.add_argument("--readiness")
    p.add_argument("--capture-health")
    p.add_argument("--es2")
    p.add_argument("--grades")
    p.add_argument("--br2")
    p.add_argument("--out", required=True)
    args=p.parse_args(argv)
    report=build(
        authority=_json(args.authority),
        readiness=_json(args.readiness),
        capture_health=_json(args.capture_health),
        es2=_json(args.es2),
        grades=_json(args.grades),
        br2=_json(args.br2),
    )
    out=Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(
        f"{report['research']['status']} | Week5 opens "
        f"{report['week5']['auditGradeOpenRows']}/{report['week5']['slateRows']} "
        f"| BR2 {len(report['br2']['populatedFamilies'])}/"
        f"{len(report['br2']['populatedFamilies'])+len(report['br2']['missingFamilies'])}"
    )
    return 0


if __name__=="__main__":
    raise SystemExit(main())
