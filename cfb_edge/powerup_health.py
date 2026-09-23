"""Cross-layer evidence readiness for CFB Edge.

This contract is observability only. It cannot create picks, alter S04_ES2,
promote BR2, or authorize staking. Its job is to say exactly which evidence
layer is complete, pending, stale, or blocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "CFB_EDGE_POWERUP_HEALTH_V1"


def _json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(value: Any, now: datetime) -> float | None:
    dt = _time(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _stage(status: str, detail: str, *, complete: bool | None = None) -> dict[str, Any]:
    return {"status": status, "detail": detail, "complete": complete}


def build(
    *,
    capture_health: Mapping[str, Any] | None,
    es2: Mapping[str, Any] | None,
    grades: Mapping[str, Any] | None,
    br2: Mapping[str, Any] | None,
    freeze_manifest: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    # 1. Week 5 open provenance
    if capture_health is None:
        open_stage = _stage("PRE_WINDOW", "Week 5 capture-health artifact not published yet.", complete=None)
        audit_open_rows = 0
        slate_rows = 0
    else:
        cs = capture_health.get("summary") or {}
        audit_open_rows = int(cs.get("provenanceCompleteTrueOpenRows") or 0)
        slate_rows = int(cs.get("slateRows") or 0)
        status = str(capture_health.get("status") or "UNKNOWN")
        if status == "AUDIT_GRADE_CAPTURE_COMPLETE":
            open_stage = _stage("COMPLETE", f"{audit_open_rows}/{slate_rows} Week 5 opens have replay-complete provenance.", complete=True)
        elif status == "INTEGRITY_FAILURE":
            open_stage = _stage("BLOCKED", f"{cs.get('invalidTrueOpenRows', 0)} TRUE_OPEN rows fail the provenance contract.", complete=False)
        else:
            open_stage = _stage("COLLECTING", f"{audit_open_rows}/{slate_rows} Week 5 opens are replay-complete.", complete=False)

    # 2. Frozen ES2 shadow execution
    qualified = int((es2 or {}).get("qualifiedCount") or 0)
    if es2 is None:
        es2_stage = _stage("PENDING", "S04_ES2 Week 5 shadow artifact has not published.", complete=None)
    else:
        es2_stage = _stage(
            "ACTIVE",
            f"{qualified} executable-shadow rows currently qualify; stake and delivery remain zero.",
            complete=True,
        )

    # 3. Close grading
    gs = (grades or {}).get("summary") or {}
    executable = gs.get("executableShadow") or {}
    exec_graded = int(executable.get("gradeableRows") or 0)
    total_signal_rows = int(gs.get("signalRows") or 0)
    if grades is None:
        grade_stage = _stage("PENDING", "No Week 5 grading artifact yet; close capture has not produced gradeable rows.", complete=None)
    elif exec_graded:
        grade_stage = _stage(
            "ACTIVE",
            f"{exec_graded} executable-shadow rows have fresh pre-kickoff close grades.",
            complete=True,
        )
    else:
        grade_stage = _stage(
            "WAITING_FOR_KICKOFF",
            f"0 executable-shadow rows graded; {total_signal_rows} total signal rows are currently present.",
            complete=False,
        )

    # 4. BR2 point-in-time warehouse and source lineage
    bs = (br2 or {}).get("summary") or {}
    coverage = bs.get("featureCoverageRows") or {}
    games = int(bs.get("games") or 0)
    planned = len(coverage) or 10
    populated = sum(1 for v in coverage.values() if int(v or 0) > 0)
    manifest = (br2 or {}).get("sourceManifest") or []
    manifest_complete = bool(manifest) and all(
        x.get("sha256") and x.get("kind") and x.get("path")
        for x in manifest
    )
    if br2 is None:
        br2_stage = _stage("PENDING", "BR2 feature snapshot has not published.", complete=None)
    else:
        br2_stage = _stage(
            "COLLECTING",
            f"{populated}/{planned} feature families populated across {games} games; raw-source lineage {'complete' if manifest_complete else 'incomplete'}.",
            complete=False,
        )

    freeze_hash = None
    if freeze_manifest is not None:
        freeze_hash = hashlib.sha256(
            json.dumps(freeze_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    ages = {
        "captureHealthHours": _age_hours((capture_health or {}).get("generatedAt"), now),
        "es2Hours": _age_hours((es2 or {}).get("generatedAt"), now),
        "gradesHours": _age_hours((grades or {}).get("generatedAt"), now),
        "br2Hours": _age_hours((br2 or {}).get("generatedAt"), now),
    }

    warnings: list[str] = []
    if capture_health and open_stage["status"] == "BLOCKED":
        warnings.append("WEEK5_OPEN_PROVENANCE_FAILURE")
    if br2 and not manifest_complete:
        warnings.append("BR2_SOURCE_LINEAGE_INCOMPLETE")
    if ages["br2Hours"] is not None and ages["br2Hours"] > 72:
        warnings.append("BR2_SNAPSHOT_STALE")

    if open_stage["status"] == "BLOCKED":
        overall = "EVIDENCE_BLOCKED"
    elif capture_health is None:
        overall = "PRE_WINDOW"
    elif exec_graded:
        overall = "PROSPECTIVE_GRADING_ACTIVE"
    else:
        overall = "PROSPECTIVE_COLLECTION_ACTIVE"

    next_unlocks: list[str] = []
    if capture_health is None:
        next_unlocks.append("Publish the first Week 5 capture-health contract when the opening window begins.")
    elif open_stage["status"] != "COMPLETE":
        next_unlocks.append("Increase replay-complete TRUE_OPEN coverage; do not infer missing opens.")
    if es2 is None:
        next_unlocks.append("Publish S04_ES2 after the Week 5 frozen slate becomes active.")
    if grades is None or not exec_graded:
        next_unlocks.append("Capture fresh pre-kickoff closes and grade the executable-shadow cohort automatically.")
    if br2 is not None and populated < planned:
        missing = [k for k, v in coverage.items() if int(v or 0) == 0]
        next_unlocks.append("Add timestamp-clean adapters for: " + ", ".join(missing) + ".")
    if not manifest_complete and br2 is not None:
        next_unlocks.append("Preserve SHA-256 lineage for every raw BR2 source payload.")
    next_unlocks.append("Do not fit or promote BR2 until a separate feature freeze and future chronological holdout are registered.")

    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": now.isoformat(),
        "status": overall,
        "week5DecisionRuleChanged": False,
        "deliveryEffect": "NONE",
        "stakingEffect": "NONE",
        "freezeManifestSha256": freeze_hash,
        "stages": {
            "openProvenance": open_stage,
            "s04Es2": es2_stage,
            "closeGrading": grade_stage,
            "br2Warehouse": br2_stage,
        },
        "metrics": {
            "auditGradeOpenRows": audit_open_rows,
            "slateRows": slate_rows,
            "qualifiedExecutableShadowRows": qualified,
            "gradedExecutableShadowRows": exec_graded,
            "br2Games": games,
            "br2PopulatedFeatureFamilies": populated,
            "br2PlannedFeatureFamilies": planned,
            "br2SourceManifestComplete": manifest_complete,
            "artifactAgeHours": ages,
        },
        "warnings": warnings,
        "nextUnlocks": next_unlocks,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture-health")
    p.add_argument("--es2")
    p.add_argument("--grades")
    p.add_argument("--br2")
    p.add_argument("--freeze", default="config/week5_freeze.json")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    report = build(
        capture_health=_json(args.capture_health),
        es2=_json(args.es2),
        grades=_json(args.grades),
        br2=_json(args.br2),
        freeze_manifest=_json(args.freeze),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{report['status']} | opens {report['metrics']['auditGradeOpenRows']}/"
        f"{report['metrics']['slateRows']} | BR2 "
        f"{report['metrics']['br2PopulatedFeatureFamilies']}/"
        f"{report['metrics']['br2PlannedFeatureFamilies']} features"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
