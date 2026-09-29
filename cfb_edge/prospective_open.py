"""Prospective, immutable opening-line lock for CFB Edge.

Consumes the exact live Kalshi quote snapshot produced by cfb_edge.watch.
No historical endpoint is consulted. The first valid two-sided line observed
for each slate game either locks as audit-grade true_open inside the frozen
-60/+900 second venue window, or locks as missed when first observed too late.

A terminal row is never upgraded on a later poll. Prospective evidence can be
incomplete, but it cannot be rewritten by hindsight.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .watch import (
    TRUE_OPEN_MAX_LAG_SECONDS,
    TRUE_OPEN_CLOCK_SKEW_SECONDS,
    classify_open_provenance,
)

CONTRACT = "CFB_EDGE_PROSPECTIVE_OPEN_V1"
TERMINAL = {"CAPTURED_TRUE_OPEN", "MISSED_TRUE_OPEN_WINDOW"}
REQUIRED_QUOTE_FIELDS = (
    "game", "book", "market", "line", "seen_at", "commence_time",
    "venue_open_time", "event_ticker", "market_tickers", "quote_inputs",
    "poll_time", "first_valid_two_sided_quote_time", "code_revision",
)


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _read_slate(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [
            {"game": str(r.get("game") or "").strip(),
             "kickoff": str(r.get("kickoff") or "").strip()}
            for r in csv.DictReader(fh)
            if str(r.get("game") or "").strip()
        ]


def _slate_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _archive(path: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _json_bytes(payload)
    sha = hashlib.sha256(raw).hexdigest()
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{sha}.json.gz"
    if not target.exists():
        with target.open("wb") as fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as z:
                z.write(raw)
    try:
        visible = target.relative_to("capture-data")
    except ValueError:
        visible = target
    return {"sha256": sha, "bytes": len(raw), "path": str(visible)}


def _load_json(path: str | Path | None, default: Any) -> Any:
    if not path:
        return default
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _quote_complete(q: Mapping[str, Any]) -> bool:
    if any(q.get(k) in (None, "") for k in REQUIRED_QUOTE_FIELDS):
        return False
    tickers = q.get("market_tickers") or []
    inputs = q.get("quote_inputs") or []
    if not isinstance(tickers, (list, tuple)) or len(set(map(str, tickers))) < 2:
        return False
    if not isinstance(inputs, (list, tuple)) or len(inputs) < 2:
        return False
    seen = _time(q.get("seen_at"))
    first_valid = _time(q.get("first_valid_two_sided_quote_time"))
    poll = _time(q.get("poll_time"))
    venue = _time(q.get("venue_open_time"))
    kickoff = _time(q.get("commence_time"))
    if not all((seen, first_valid, poll, venue, kickoff)):
        return False
    if seen != first_valid:
        return False
    if poll < seen:
        return False
    if seen >= kickoff:
        return False
    return True


def _terminal_row(
    *,
    game: str,
    kickoff: str,
    quote: Mapping[str, Any],
    state: str,
    lag: float | None,
    evidence_dir: str | Path,
    revision: str | None,
    first_event_seen_at: str | None,
) -> dict[str, Any]:
    evidence = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "state": state,
        "game": game,
        "kickoff": kickoff,
        "observedAt": quote.get("seen_at"),
        "venueOpenTime": quote.get("venue_open_time"),
        "openLagSeconds": lag,
        "eventTicker": quote.get("event_ticker"),
        "marketTickers": list(quote.get("market_tickers") or []),
        "quoteInputs": list(quote.get("quote_inputs") or []),
        "pollTime": quote.get("poll_time"),
        "firstValidTwoSidedQuoteTime": quote.get("first_valid_two_sided_quote_time"),
        "codeRevision": quote.get("code_revision") or revision,
        "openingLine": quote.get("line"),
        "firstEventSeenAt": first_event_seen_at,
        "source": "LIVE_KALSHI_POLL",
        "historicalRecoveryUsed": False,
    }
    stored = _archive(evidence_dir, evidence)
    return {
        **evidence,
        "evidenceSha256": stored["sha256"],
        "evidencePath": stored["path"],
        "auditGrade": state == "CAPTURED_TRUE_OPEN",
        "locked": True,
    }


def update(
    *,
    slate: Sequence[Mapping[str, Any]],
    slate_sha256: str,
    snapshot: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    evidence_dir: str | Path,
    revision: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    stamp = now.astimezone(timezone.utc).isoformat()

    if existing and existing.get("slateSha256") not in (None, slate_sha256):
        raise ValueError("prospective-open status belongs to a different slate")

    old = {
        str(r.get("game")): dict(r)
        for r in (existing or {}).get("rows") or []
        if r.get("game")
    }
    quotes_by_game: dict[str, list[dict[str, Any]]] = {}
    for raw in snapshot.get("quotes") or []:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("book") != "kalshi" or raw.get("market") != "spread":
            continue
        game = str(raw.get("game") or "").strip()
        if game:
            quotes_by_game.setdefault(game, []).append(dict(raw))

    rows: list[dict[str, Any]] = []
    transitions: list[dict[str, str]] = []
    for fixture in slate:
        game = str(fixture.get("game") or "").strip()
        kickoff = str(fixture.get("kickoff") or "").strip()
        prior = old.get(game)
        if prior and prior.get("state") in TERMINAL:
            rows.append(prior)
            continue

        candidates = quotes_by_game.get(game) or []
        base = dict(prior or {
            "game": game,
            "kickoff": kickoff,
            "state": "PENDING",
            "locked": False,
            "auditGrade": False,
            "firstEventSeenAt": None,
        })

        if len(candidates) > 1:
            base["state"] = "AMBIGUOUS_LIVE_EVENT"
            base["lastPollAt"] = snapshot.get("polled_at") or stamp
            rows.append(base)
            continue
        if not candidates:
            base["lastPollAt"] = snapshot.get("polled_at") or stamp
            rows.append(base)
            continue

        q = candidates[0]
        first_event_seen = base.get("firstEventSeenAt") or q.get("poll_time") or q.get("seen_at")
        base["firstEventSeenAt"] = first_event_seen
        base["eventTicker"] = q.get("event_ticker")
        base["lastPollAt"] = snapshot.get("polled_at") or stamp

        source, lag = classify_open_provenance(
            str(q.get("seen_at") or ""),
            str(q.get("venue_open_time") or ""),
        )
        if source == "true_open" and _quote_complete(q):
            locked = _terminal_row(
                game=game, kickoff=kickoff, quote=q,
                state="CAPTURED_TRUE_OPEN", lag=lag,
                evidence_dir=evidence_dir, revision=revision,
                first_event_seen_at=first_event_seen,
            )
            rows.append(locked)
            transitions.append({"game": game, "state": "CAPTURED_TRUE_OPEN"})
        elif lag is not None and lag > TRUE_OPEN_MAX_LAG_SECONDS:
            # The shared provenance classifier intentionally calls a
            # venue-timestamped observation beyond 900s "first_seen" because
            # that label is useful in the legacy research log. The prospective
            # lock has a stricter state machine: once the exact venue open_time
            # proves our first valid live observation missed the frozen window,
            # that miss is terminal and can never be repaired later.
            locked = _terminal_row(
                game=game, kickoff=kickoff, quote=q,
                state="MISSED_TRUE_OPEN_WINDOW", lag=lag,
                evidence_dir=evidence_dir, revision=revision,
                first_event_seen_at=first_event_seen,
            )
            rows.append(locked)
            transitions.append({"game": game, "state": "MISSED_TRUE_OPEN_WINDOW"})
        else:
            base["state"] = "PROVENANCE_INCOMPLETE" if not _quote_complete(q) else "PENDING"
            base["openingLineObserved"] = q.get("line")
            base["venueOpenTime"] = q.get("venue_open_time")
            base["openLagSeconds"] = lag
            rows.append(base)

    rows.sort(key=lambda r: r["game"])
    captured = sum(r.get("state") == "CAPTURED_TRUE_OPEN" for r in rows)
    missed = sum(r.get("state") == "MISSED_TRUE_OPEN_WINDOW" for r in rows)
    pending = len(rows) - captured - missed
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": stamp,
        "slateSha256": slate_sha256,
        "pollIntervalTargetSeconds": 60,
        "trueOpenMaximumLagSeconds": TRUE_OPEN_MAX_LAG_SECONDS,
        "clockSkewSeconds": -TRUE_OPEN_CLOCK_SKEW_SECONDS,
        "historicalRecoveryAllowed": False,
        "decisionEffect": "NONE",
        "stakingEffect": "NONE",
        "deliveryEffect": "NONE",
        "summary": {
            "slateRows": len(rows),
            "capturedTrueOpenRows": int(captured),
            "missedTrueOpenRows": int(missed),
            "pendingRows": int(pending),
            "terminalRows": int(captured + missed),
            "newTransitions": len(transitions),
        },
        "transitions": transitions,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--slate", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--latest-out")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--revision")
    args = p.parse_args(argv)

    snapshot = _load_json(args.snapshot, {})
    if not isinstance(snapshot, dict):
        raise SystemExit("--snapshot must be a JSON object")
    slate = _read_slate(args.slate)
    current = _load_json(args.out, None)
    report = update(
        slate=slate,
        slate_sha256=_slate_sha(args.slate),
        snapshot=snapshot,
        existing=current,
        evidence_dir=args.evidence_dir,
        revision=args.revision,
    )

    for path in [args.out, args.latest_out]:
        if not path:
            continue
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    s = report["summary"]
    print(
        f"prospective opens: true={s['capturedTrueOpenRows']}/"
        f"{s['slateRows']} missed={s['missedTrueOpenRows']} pending={s['pendingRows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
