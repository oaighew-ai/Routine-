"""Validation contract for BR2 web-derived QB continuity evidence.

Search/crawl/browser tools may discover and capture sources, but they do not
automatically create a model feature. Only a separately validated official
source packet can become audit-grade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

CONTRACT="CFB_EDGE_BR2_QB_EVIDENCE_V1"
OFFICIAL_SOURCE_KINDS={
    "official_depth_chart","official_game_notes","official_availability_report"
}
DISCOVERY_METHODS={"exa_search","firecrawl_search"}
CANDIDATE_METHODS={"firecrawl_scrape","firecrawl_pdf_extract","browser_capture"}


def _time(v: Any) -> datetime | None:
    if not v: return None
    try: dt=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except (TypeError,ValueError): return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validate_packet(packet: Mapping[str,Any]) -> dict[str,Any]:
    p=dict(packet)
    exclusions=[]
    url=str(p.get("sourceUrl") or "")
    domain=(urlparse(url).hostname or "").lower()
    kickoff=_time(p.get("kickoff")); retrieved=_time(p.get("retrievedAt"))
    published=_time(p.get("publishedAt"))
    capture=str(p.get("captureMethod") or "")
    kind=str(p.get("sourceKind") or "")
    content_hash=str(p.get("contentSha256") or "")
    validated=bool(p.get("deterministicallyValidated"))

    if not url or not domain: exclusions.append("SOURCE_URL_MISSING")
    if kind not in OFFICIAL_SOURCE_KINDS: exclusions.append("SOURCE_NOT_OFFICIAL_ELIGIBLE_KIND")
    if retrieved is None: exclusions.append("RETRIEVAL_TIME_MISSING")
    if kickoff is None: exclusions.append("KICKOFF_MISSING")
    if retrieved and kickoff and retrieved >= kickoff: exclusions.append("CAPTURE_NOT_PRE_KICKOFF")
    if published and kickoff and published >= kickoff: exclusions.append("PUBLISHED_NOT_PRE_KICKOFF")
    if not content_hash: exclusions.append("CONTENT_HASH_MISSING")
    if capture in DISCOVERY_METHODS: exclusions.append("DISCOVERY_ONLY")
    if capture in CANDIDATE_METHODS and not validated:
        exclusions.append("EXTRACTION_NOT_DETERMINISTICALLY_VALIDATED")
    if not validated: exclusions.append("FACTS_NOT_VALIDATED")

    first=str(p.get("firstListedQb") or "").strip()
    prior=str(p.get("priorFirstListedQb") or "").strip()
    feature=None
    if not first or not prior:
        exclusions.append("QB_DEPTH_HISTORY_INCOMPLETE")
    elif not exclusions:
        feature=1.0 if first == prior else 0.0

    out={
        **p,
        "sourceDomain":domain or None,
        "auditGrade":not exclusions,
        "featureValue":feature,
        "exclusions":sorted(set(exclusions)),
        "policy":{
            "searchResultsCanBecomeFeatures":False,
            "llmExtractionAutoEligible":False,
            "officialSourceRequired":True,
            "deterministicValidationRequired":True,
        },
    }
    out["packetSha256"]=hashlib.sha256(
        json.dumps(out,sort_keys=True,separators=(",",":")).encode("utf-8")
    ).hexdigest()
    return out


def build(packets: Sequence[Mapping[str,Any]], generated_at: datetime | None=None) -> dict[str,Any]:
    now=generated_at or datetime.now(timezone.utc)
    rows=[validate_packet(p) for p in packets]
    return {
        "schemaVersion":1,"contract":CONTRACT,"status":"DATA_COLLECTION_ONLY",
        "generatedAt":now.isoformat(),"decisionEffect":"NONE",
        "summary":{"packets":len(rows),"auditGradeRows":sum(1 for r in rows if r["auditGrade"])},
        "rows":rows,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--packets",required=True); p.add_argument("--out",required=True)
    a=p.parse_args(argv)
    x=json.loads(Path(a.packets).read_text(encoding="utf-8"))
    if not isinstance(x,list): raise SystemExit("--packets must be JSON array")
    r=build(x); o=Path(a.out); o.parent.mkdir(parents=True,exist_ok=True)
    o.write_text(json.dumps(r,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(r["summary"],sort_keys=True)); return 0


if __name__=="__main__":
    raise SystemExit(main())
