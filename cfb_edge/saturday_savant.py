"""Capture Saturday Savant forecasts as a prospective, non-authoritative feed.

Only public /games and /game/<id> HTML pages are requested. The site disallows
query and API routes, so this collector never uses either. Requests are paced
to the site's published five-second crawl delay and archived by content hash.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

SITE = "https://saturdaysavant.com"
SCHEDULE_URL = f"{SITE}/games"
CRAWL_DELAY_SECONDS = 5.1
CONTRACT = "CFB_EDGE_SATURDAY_SAVANT_SHADOW_V1"
MODEL_ID = "SATURDAY_SAVANT"
MODEL_VERSION = "savant-forecast-public-site"
_GAME_PATH = re.compile(r"^/game/([0-9]+)$")
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("capture time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _fetch(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "saturdaysavant.com":
        raise ValueError(f"refusing non-Saturday-Savant URL: {url}")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": (
                "cfb-edge/1.0 (+https://github.com/oaighew-ai/Routine-)"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError(f"Saturday Savant page exceeded 2 MB: {url}")
    return body


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.game_ids: list[str] = []
        self.game_card_count = 0
        self.names: dict[str, list[str]] = {"away": [], "home": []}
        self.probabilities: dict[str, list[str]] = {"away": [], "home": []}
        self.fact_labels: list[str] = []
        self.fact_values: list[str] = []
        self.json_ld: list[str] = []
        self.text: list[str] = []
        self._active: list[dict[str, Any]] = []
        self._elements: list[tuple[str, frozenset[str]]] = []

    def _inside(self, class_name: str) -> bool:
        return any(class_name in classes for _, classes in self._elements)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = frozenset((values.get("class") or "").split())
        for capture in self._active:
            capture["depth"] += 1

        if "game-card-v2" in classes:
            self.game_card_count += 1
            match = _GAME_PATH.fullmatch(urlparse(values.get("href") or "").path)
            if match and "is-upcoming" in classes:
                self.game_ids.append(match.group(1))

        parent_classes = [item[1] for item in self._elements]
        side = next(
            (
                name for name in ("away", "home")
                if any(f"gx-side--{name}" in parent for parent in parent_classes)
            ),
            None,
        )
        targets: list[str] = []
        if "gx-name" in classes and side:
            targets.append(f"name:{side}")
        if "gx-num--pct" in classes and side:
            targets.append(f"probability:{side}")
        if "gx-k" in classes:
            targets.append("fact_label")
        if tag == "b" and self._inside("gx-facts"):
            targets.append("fact_value")
        if tag == "script" and values.get("type") == "application/ld+json":
            targets.append("json_ld")
        for kind in targets:
            self._active.append({"kind": kind, "depth": 1, "text": []})

        if tag not in _VOID_TAGS:
            self._elements.append((tag, classes))

    def handle_endtag(self, tag: str) -> None:
        completed: list[dict[str, Any]] = []
        for capture in self._active:
            capture["depth"] -= 1
            if capture["depth"] == 0:
                completed.append(capture)
        self._active = [capture for capture in self._active if capture["depth"] > 0]
        for capture in completed:
            value = "".join(capture["text"]).strip()
            kind = capture["kind"]
            if kind.startswith("name:"):
                self.names[kind.split(":", 1)[1]].append(value)
            elif kind.startswith("probability:"):
                self.probabilities[kind.split(":", 1)[1]].append(value)
            elif kind == "fact_label":
                self.fact_labels.append(value.rstrip(":").strip().lower())
            elif kind == "fact_value":
                self.fact_values.append(value)
            elif kind == "json_ld":
                self.json_ld.append(value)

        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == tag:
                del self._elements[index:]
                break

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        for capture in self._active:
            capture["text"].append(data)


def _archive(
    raw: bytes, archive_root: Path, *, url: str, observed_at: str
) -> dict[str, Any]:
    sha = hashlib.sha256(raw).hexdigest()
    relative = Path("data") / "saturday-savant" / "raw" / f"{sha}.html.gz"
    target = archive_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with gzip.open(target, "rb") as existing:
            if hashlib.sha256(existing.read()).hexdigest() != sha:
                raise ValueError(f"content-addressed archive is corrupt: {target}")
    else:
        target.write_bytes(gzip.compress(raw, mtime=0))
    return {
        "sourceUrl": url,
        "observedAt": observed_at,
        "sha256": sha,
        "archivePath": relative.as_posix(),
        "bytes": len(raw),
    }


def _probability(value: str, *, field: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", value)
    if not match:
        raise ValueError(f"{field}: invalid published probability {value!r}")
    result = float(match.group(1)) / 100.0
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field}: probability outside [0, 1]")
    return result


def _event_metadata(parser: _PageParser, *, game_id: str) -> Mapping[str, Any]:
    for raw in parser.json_ld:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("@type") == "SportsEvent":
            url = str(value.get("mainEntityOfPage") or value.get("url") or "")
            match = _GAME_PATH.fullmatch(urlparse(url).path)
            if match and match.group(1) == game_id:
                return value
    raise ValueError(f"game {game_id}: matching SportsEvent metadata not found")


def parse_game_page(
    raw: bytes, *, game_id: str, source_url: str, observed_at: datetime
) -> dict[str, Any]:
    parser = _PageParser()
    parser.feed(raw.decode("utf-8"))
    metadata = _event_metadata(parser, game_id=game_id)
    kickoff = datetime.fromisoformat(
        str(metadata.get("startDate") or "").replace("Z", "+00:00")
    )
    if kickoff.tzinfo is None:
        raise ValueError(f"game {game_id}: kickoff timestamp lacks timezone")

    competitors = metadata.get("competitor") or []
    if len(competitors) != 2:
        raise ValueError(f"game {game_id}: expected two competitors")
    away = parser.names["away"]
    home = parser.names["home"]
    away_probability = parser.probabilities["away"]
    home_probability = parser.probabilities["home"]
    if len(away) != 1 or len(home) != 1:
        raise ValueError(f"game {game_id}: expected one away and one home team label")
    if len(away_probability) != 1 or len(home_probability) != 1:
        raise ValueError(f"game {game_id}: expected one probability for each team")
    if (
        away[0].casefold() != str(competitors[0].get("name") or "").casefold()
        or home[0].casefold() != str(competitors[1].get("name") or "").casefold()
    ):
        raise ValueError(f"game {game_id}: page team labels disagree with event metadata")

    p_away = _probability(away_probability[0], field=f"game {game_id} away")
    p_home = _probability(home_probability[0], field=f"game {game_id} home")
    if abs((p_away + p_home) - 1.0) > 0.011:
        raise ValueError(f"game {game_id}: published win probabilities do not sum to 1")
    if "locked before kickoff" not in " ".join(parser.text).lower():
        raise ValueError(f"game {game_id}: published pre-kickoff lock claim is missing")

    if len(parser.fact_labels) != len(parser.fact_values):
        raise ValueError(f"game {game_id}: incomplete published game facts")
    facts = dict(zip(parser.fact_labels, parser.fact_values))
    margin_text = facts.get("margin")
    expected_home_margin = None
    if margin_text:
        margin_match = re.fullmatch(
            r"\s*(.+?)\s+by\s+~\s*(\d+(?:\.\d+)?)\s*",
            margin_text,
            flags=re.IGNORECASE,
        )
        if margin_match:
            favorite, points = margin_match.groups()
            if favorite.strip().casefold() == home[0].casefold():
                expected_home_margin = float(points)
            elif favorite.strip().casefold() == away[0].casefold():
                expected_home_margin = -float(points)

    return {
        "gameId": game_id,
        "canonicalGameId": f"cfbd:{game_id}",
        "sourceUrl": source_url,
        "observedAt": _utc(observed_at),
        "kickoffAt": _utc(kickoff),
        "awayTeam": away[0],
        "homeTeam": home[0],
        "awayWinProbability": p_away,
        "homeWinProbability": p_home,
        "approxExpectedHomeMargin": expected_home_margin,
        "reportedMargin": margin_text,
        "forecastLockClaim": "SITE_CLAIMS_LOCKED_BEFORE_KICKOFF",
    }


def capture_upcoming(
    *,
    archive_root: Path | str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    fetch: Callable[[str], bytes] = _fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    root = Path(archive_root)
    schedule_raw = fetch(SCHEDULE_URL)
    schedule_at = _utc(now())
    schedule_parser = _PageParser()
    schedule_parser.feed(schedule_raw.decode("utf-8"))
    if schedule_parser.game_card_count == 0:
        raise ValueError("schedule page contained no game cards; parser may be stale")
    schedule_manifest = _archive(
        schedule_raw, root, url=SCHEDULE_URL, observed_at=schedule_at
    )
    game_ids = list(dict.fromkeys(schedule_parser.game_ids))
    forecasts: list[dict[str, Any]] = []
    manifests = [schedule_manifest]
    excluded: list[dict[str, str]] = []

    for game_id in game_ids:
        sleep(CRAWL_DELAY_SECONDS)
        source_url = f"{SITE}/game/{game_id}"
        raw = fetch(source_url)
        observed = now()
        forecast = parse_game_page(
            raw, game_id=game_id, source_url=source_url, observed_at=observed
        )
        manifests.append(
            _archive(raw, root, url=source_url, observed_at=_utc(observed))
        )
        kickoff = datetime.fromisoformat(forecast["kickoffAt"])
        if observed.astimezone(timezone.utc) >= kickoff.astimezone(timezone.utc):
            excluded.append({
                "gameId": game_id,
                "reason": "FORECAST_CAPTURED_AT_OR_AFTER_KICKOFF",
            })
            continue
        forecasts.append(forecast)

    captured_at = _utc(now())
    return {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": captured_at,
        "status": (
            "CAPTURED" if forecasts
            else "NO_PROSPECTIVE_FORECASTS" if excluded
            else "NO_UPCOMING_GAMES"
        ),
        "source": {
            "site": SITE,
            "scheduleUrl": SCHEDULE_URL,
            "methodologyUrl": f"{SITE}/savant-forecast",
            "crawlDelaySeconds": CRAWL_DELAY_SECONDS,
            "apiRoutesUsed": False,
            "queryRoutesUsed": False,
        },
        "model": {
            "id": MODEL_ID,
            "version": MODEL_VERSION,
            "deliveryEligible": False,
            "promotionEffect": "NONE",
            "stakeUnits": 0,
        },
        "forecasts": forecasts,
        "excluded": excluded,
        "sourceManifest": manifests,
        "evaluation": {
            "probabilityMetrics": None,
            "spreadMarketComparison": "NOT_EVALUATED",
            "samePointExecutablePrices": "NOT_CAPTURED",
            "settledResults": "NOT_YET_AVAILABLE",
        },
        "limitations": [
            "The published win probability is not a spread-cover probability.",
            "The expected margin is approximate and the page does not provide a same-point executable price.",
            "The Site's lock label is an external claim; captured HTML and observedAt preserve what this collector saw before kickoff.",
            "This feed cannot authorize picks, promotion, staking, or delivery.",
        ],
        "decisionEffect": "NONE",
        "deliveryEffect": "NONE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = capture_upcoming(archive_root=args.archive_root)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "forecasts": len(report["forecasts"]),
        "excluded": len(report["excluded"]),
        "generatedAt": report["generatedAt"],
        "deliveryEffect": report["deliveryEffect"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
