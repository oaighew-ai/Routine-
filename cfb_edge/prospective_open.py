"""Prospective, immutable opening-line lock for CFB Edge.

Consumes the exact live Kalshi quote snapshot produced by cfb_edge.watch.
No historical endpoint is consulted. The first valid two-sided line observed
for each slate game either locks as audit-grade true_open inside the frozen
-60/+900 second venue window, or locks as missed when first observed too late.

A terminal row is never upgraded on a later poll. Prospective evidence can be
incomplete, but it cannot be rewritten by hindsight.

One thing is not a terminal row at all: a lock written against another game's
market. The exchange lists next week's board while this week's is still open,
and a market matched on a shared team once locked Georgia at Alabama against
Vanderbilt at Georgia (D43). The event ticker names its game day, so a row
whose ticker names a different day than the kickoff it was locked with never
observed that game. It is moved to ``voidedRows`` with everything it recorded.
A row whose ticker agrees with its kickoff is never reopened by this or
anything else.

While that row stood, the lock ignored the game, but the poller did not: every
poll went into the append-only capture log with its own time. So the game's
row is then recomputed from those logged polls, oldest first, through the same
state machine, and says what was actually observed and when. Without that the
next live poll would lock the game as first seen "now", hours after its own
market was in the log. Nothing but our own live polls is read, and a row
rebuilt this way says so (``rebuiltFromLog``).
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
    event_matches_kickoff,
)

CONTRACT = "CFB_EDGE_PROSPECTIVE_OPEN_V1"
TERMINAL = {"CAPTURED_TRUE_OPEN", "MISSED_TRUE_OPEN_WINDOW"}
VOID_REASON = "EVENT_DAY_DOES_NOT_MATCH_KICKOFF"
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
    if event_matches_kickoff(str(q.get("event_ticker")), str(q.get("commence_time"))) is False:
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


def _wrong_event(row: Mapping[str, Any] | None, slate_kickoff: str = "") -> bool:
    """Whether a row was written against another game day's market.

    Judged by the kickoff the row was locked with, so a game rescheduled
    afterwards cannot void a lock that was right when it was made.
    """
    if not row or not row.get("eventTicker"):
        return False
    return event_matches_kickoff(
        str(row.get("eventTicker")), str(row.get("kickoff") or slate_kickoff)
    ) is False


def logged_polls(
    log_path: str | Path, since: datetime, until: datetime
) -> list[dict[str, Any]]:
    """Polls in the append-only capture log with since <= polled_at < until.

    Oldest first by the poll's own time, which is not always file order: a
    poll replayed after a lost push is appended later with its original time.
    """
    path = Path(log_path)
    out: list[tuple[datetime, dict[str, Any]]] = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = _time(rec.get("polled_at")) if isinstance(rec, dict) else None
            if at is None or at < since or at >= until:
                continue
            out.append((at, rec))
    out.sort(key=lambda pair: pair[0])
    return [rec for _, rec in out]


def _rebuilt_rows(
    *,
    wrong: Mapping[str, Mapping[str, Any]],
    slate: Sequence[Mapping[str, Any]],
    slate_sha256: str,
    cohort_id: str,
    history: Sequence[Mapping[str, Any]],
    until: datetime,
    evidence_dir: str | Path,
    revision: str | None,
) -> dict[str, dict[str, Any]]:
    """What the lock would hold for these games had it not been blinded.

    Each game is run alone through ``update``, poll by poll, from the moment
    its false row was written up to the poll now being processed, and stops
    at its first terminal state, exactly as the live lock does.
    """
    out: dict[str, dict[str, Any]] = {}
    for game, row in wrong.items():
        since = _time(row.get("observedAt") or row.get("firstEventSeenAt")
                      or row.get("lastPollAt"))
        fixture = [f for f in slate if str(f.get("game") or "").strip() == game]
        if since is None or not fixture:
            continue
        state: dict[str, Any] | None = None
        for rec in history:
            at = _time(rec.get("polled_at"))
            if at is None or at < since or at >= until:
                continue
            state = update(
                slate=fixture, slate_sha256=slate_sha256, cohort_id=cohort_id,
                snapshot=rec, existing=state, evidence_dir=evidence_dir,
                revision=revision, generated_at=at,
            )
            if state["rows"][0].get("state") in TERMINAL:
                break
        if state is not None:
            out[game] = dict(state["rows"][0])
    return out


def update(
    *,
    slate: Sequence[Mapping[str, Any]],
    slate_sha256: str,
    cohort_id: str,
    snapshot: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    evidence_dir: str | Path,
    revision: str | None = None,
    generated_at: datetime | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    stamp = now.astimezone(timezone.utc).isoformat()

    if not str(cohort_id or "").strip():
        raise ValueError("cohort_id is required")
    if existing and existing.get("cohortId") not in (None, cohort_id):
        raise ValueError("prospective-open status belongs to a different cohort")

    old = {
        str(r.get("game")): dict(r)
        for r in (existing or {}).get("rows") or []
        if r.get("game")
    }
    kickoff_of = {
        str(f.get("game") or "").strip(): str(f.get("kickoff") or "").strip()
        for f in slate
    }
    quotes_by_game: dict[str, list[dict[str, Any]]] = {}
    refused: list[dict[str, Any]] = []
    for raw in snapshot.get("quotes") or []:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("book") != "kalshi" or raw.get("market") != "spread":
            continue
        game = str(raw.get("game") or "").strip()
        if not game:
            continue
        # The schedule's kickoff decides, with the quote's own as the fallback
        # for a game the slate has no time for.
        kickoff = kickoff_of.get(game) or str(raw.get("commence_time") or "")
        if event_matches_kickoff(str(raw.get("event_ticker") or ""), kickoff) is False:
            refused.append({"game": game, "eventTicker": raw.get("event_ticker")})
            continue
        quotes_by_game.setdefault(game, []).append(dict(raw))

    voided: list[dict[str, Any]] = [
        dict(r) for r in (existing or {}).get("voidedRows") or []
    ]
    # Rows written against another game day's market, and what the logged
    # polls say those games' rows should have been.
    wrong = {game: row for game, row in old.items()
             if game in kickoff_of and _wrong_event(row, kickoff_of[game])}
    rebuilt: dict[str, dict[str, Any]] = {}
    rebuild_error: str | None = None
    if wrong and history is not None:
        try:
            rebuilt = _rebuilt_rows(
                wrong=wrong, slate=slate, slate_sha256=slate_sha256,
                cohort_id=cohort_id, history=history,
                until=_time(snapshot.get("polled_at")) or now,
                evidence_dir=evidence_dir, revision=revision,
            )
        except Exception as exc:  # noqa: BLE001
            # A repair must never cost the live poll. Without the rebuild the
            # game simply starts again as pending, which is the weaker result
            # and not a wrong one.
            rebuild_error = f"{type(exc).__name__}: {exc}"
            rebuilt = {}

    newly_voided = 0
    rows: list[dict[str, Any]] = []
    transitions: list[dict[str, str]] = []
    for fixture in slate:
        game = str(fixture.get("game") or "").strip()
        kickoff = str(fixture.get("kickoff") or "").strip()
        prior = old.get(game)
        if game in wrong:
            record = {**prior, "voidReason": VOID_REASON, "voidedAt": stamp,
                      "rebuiltFromLog": game in rebuilt}
            if rebuild_error:
                record["rebuildError"] = rebuild_error
            voided.append(record)
            transitions.append({"game": game, "state": "VOIDED_WRONG_EVENT"})
            newly_voided += 1
            prior = rebuilt.get(game)
            if prior and prior.get("state") in TERMINAL:
                prior = {**prior, "rebuiltFromLog": True, "rebuiltAt": stamp}
                transitions.append({"game": game, "state": str(prior["state"])})
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
    voided.sort(key=lambda r: (str(r.get("game")), str(r.get("voidedAt"))))
    captured = sum(r.get("state") == "CAPTURED_TRUE_OPEN" for r in rows)
    missed = sum(r.get("state") == "MISSED_TRUE_OPEN_WINDOW" for r in rows)
    pending = len(rows) - captured - missed
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": stamp,
        "cohortId": cohort_id,
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
            "voidedRows": len(voided),
            "newlyVoidedRows": newly_voided,
            "refusedWrongEventQuotes": len(refused),
        },
        "transitions": transitions,
        "rows": rows,
        "voidedRows": voided,
        "refusedWrongEventQuotes": refused,
    }


def _default_log(out: str | Path) -> Path | None:
    """`data/opens.jsonl.gz`, given `data/open-capture/<cohort>/status.json`."""
    parents = Path(out).resolve().parents
    return parents[2] / "opens.jsonl.gz" if len(parents) > 2 else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--slate", required=True)
    p.add_argument("--cohort-id", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--latest-out")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--revision")
    p.add_argument("--log",
                   help="the append-only capture log, read only when a row has to "
                        "be voided and rebuilt. Default: data/opens.jsonl.gz beside "
                        "the open-capture directory that holds --out.")
    args = p.parse_args(argv)

    snapshot = _load_json(args.snapshot, {})
    if not isinstance(snapshot, dict):
        raise SystemExit("--snapshot must be a JSON object")
    slate = _read_slate(args.slate)
    current = _load_json(args.out, None)

    # The log is read only on the poll that voids a row, which is once.
    history = None
    kickoffs = {f["game"]: f["kickoff"] for f in slate}
    wrong = [r for r in (current or {}).get("rows") or []
             if r.get("game") in kickoffs and _wrong_event(r, kickoffs[r["game"]])]
    if wrong:
        log = Path(args.log) if args.log else _default_log(args.out)
        starts = [t for t in (_time(r.get("observedAt") or r.get("firstEventSeenAt")
                                    or r.get("lastPollAt")) for r in wrong) if t]
        if log is not None and log.exists() and starts:
            until = _time(snapshot.get("polled_at")) or datetime.now(timezone.utc)
            try:
                history = logged_polls(log, min(starts), until)
            except OSError as exc:
                print(f"capture log unreadable, voiding without a rebuild: {exc}")

    report = update(
        slate=slate,
        slate_sha256=_slate_sha(args.slate),
        cohort_id=args.cohort_id,
        snapshot=snapshot,
        existing=current,
        evidence_dir=args.evidence_dir,
        revision=args.revision,
        history=history,
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
        f" voided={s['voidedRows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
