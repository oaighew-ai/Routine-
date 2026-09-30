"""The weekly card: one disposition per game, from the one decision engine.

These tests pin the properties the card exists for rather than this week's
numbers: a blocked authority can never produce a BET, a price edge without
registered evidence is at most a LEAN, confidence is the calibrated market
posterior and nothing more, a provider label cannot be mapped onto the wrong
school, and the same inputs always produce the same card.
"""

from __future__ import annotations

import csv
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cfb_edge import card_render, weekly_card as wc
from cfb_edge.market import devig_multiplicative

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
KICK_THU = "2026-10-02T00:00:00.000Z"
KICK_SAT = "2026-10-03T16:00:00.000Z"


def spreads(home, away, pairs):
    """Odds API event: pairs = {book: (home_line, home_price, away_price)}."""
    books = []
    for key, (hl, hp, ap) in pairs.items():
        books.append({"key": key, "markets": [{"key": "spreads", "lastUpdate": "2026-10-01T11:55:00Z",
                      "outcomes": [{"name": home, "point": hl, "price": hp},
                                   {"name": away, "point": -hl, "price": ap}]}]})
    return books


class Fixture:
    def __init__(self, tmp: Path, *, board_age_minutes: float = 5, authority_open: bool = False,
                 extra_events=None, slate_rows=None):
        self.tmp = tmp
        self.slate = tmp / "slate.csv"
        rows = slate_rows or [
            ("Western Kentucky @ New Mexico State", -1.9, KICK_THU),
            ("Georgia State @ Troy", 2.0, KICK_SAT),
            ("Arkansas @ Texas A&M", 11.4, KICK_SAT),
            ("Temple @ Hawai'i", 3.0, KICK_SAT),
        ]
        with self.slate.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["game", "projected_margin", "side", "posted_line", "total", "kickoff"])
            for g, m, k in rows:
                w.writerow([g, m, "", "", 52.0, k])
        fetched = AS_OF - timedelta(minutes=board_age_minutes)
        events = [
            # Pinnacle -2.5 at -114/-103; FanDuel NMSU -2.5 at +105 beats the no-vig price.
            {"home": "New Mexico State Aggies", "away": "Western Kentucky Hilltoppers",
             "commenceTime": "2026-10-02T00:00:00Z",
             "bookmakers": spreads("New Mexico State Aggies", "Western Kentucky Hilltoppers", {
                 "pinnacle": (-2.5, -114, -103), "fanduel": (-2.5, 105, -125),
                 "draftkings": (-2.5, -112, -108), "betmgm": (-2.5, -110, -110)})},
            # A&M -14 everywhere at worse-than-fair prices: no edge on either side.
            {"home": "Texas A&M Aggies", "away": "Arkansas Razorbacks",
             "commenceTime": "2026-10-03T16:00:00Z",
             "bookmakers": spreads("Texas A&M Aggies", "Arkansas Razorbacks", {
                 "pinnacle": (-14.0, -108, -108), "draftkings": (-14.0, -112, -108),
                 "fanduel": (-14.0, -110, -110), "betmgm": (-14.0, -115, -105)})},
            # Hawai'i spelled without the okina, and a mascot: must still map.
            {"home": "Hawaii Rainbow Warriors", "away": "Temple Owls",
             "commenceTime": "2026-10-03T16:00:00Z",
             "bookmakers": spreads("Hawaii Rainbow Warriors", "Temple Owls", {
                 "pinnacle": (-3.0, -110, -110), "draftkings": (-3.5, -105, -115)})},
            # "Georgia State Panthers" stripped of two words is "Georgia"; there is
            # no Georgia @ Troy fixture, and the real pair is on the slate.
            {"home": "Troy Trojans", "away": "Georgia State Panthers",
             "commenceTime": "2026-10-03T16:00:00Z",
             "bookmakers": spreads("Troy Trojans", "Georgia State Panthers", {
                 "pinnacle": (-7.0, -110, -110), "fanduel": (-7.0, -110, -110),
                 "betmgm": (-7.0, -110, -110), "draftkings": (-7.0, -110, -110)})},
            # Next week's game: not on this slate, must stay unmapped.
            {"home": "Oklahoma Sooners", "away": "Texas Longhorns",
             "commenceTime": "2026-10-10T19:30:00Z",
             "bookmakers": spreads("Oklahoma Sooners", "Texas Longhorns", {
                 "pinnacle": (3.0, -110, -110)})},
        ] + list(extra_events or [])
        self.board = tmp / "board.json.gz"
        with gzip.open(self.board, "wt", encoding="utf-8") as fh:
            json.dump({"fetched_at": fetched.isoformat(), "events": events,
                       "markets": ["spreads"], "sport": "americanfootball_ncaaf"}, fh)
        auth = json.loads((ROOT / "config" / "delivery_authority.json").read_text())
        auth["allowPaperDelivery"] = authority_open
        self.authority = tmp / "authority.json"
        self.authority.write_text(json.dumps(auth))
        self.cohort = tmp / "cohort.json"
        self.cohort.write_text(json.dumps({"gameWindow": {"startsAt": "2026-10-02T23:00:00Z",
                                                          "endsAt": "2026-10-04T12:00:00Z"},
                                           "prospectiveWindow": {"startsAt": "2026-09-27T00:00:00Z",
                                                                 "endsAt": "2026-10-02T23:00:00Z"}}))

    def inputs(self, **kw) -> wc.Inputs:
        base = dict(slate=str(self.slate), board=str(self.board), authority=str(self.authority),
                    registry=str(ROOT / "config" / "model_registry.json"),
                    edge_config=str(ROOT / "config" / "edge_os.json"),
                    market_quality=str(ROOT / "config" / "s03_m1.json"), cohort=str(self.cohort))
        base.update(kw)
        return wc.Inputs(**base)


def by_game(card):
    return {r["game"]: r for r in card["plays"] + card["passList"]}


class TestMapping(unittest.TestCase):
    def test_pairs_and_kickoff_decide_identity(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(Path(d))
            card = wc.build(f.inputs(), as_of=AS_OF)
            rows = by_game(card)
            self.assertIsNotNone(rows["Georgia State @ Troy"]["market"],
                                 "the real pair must map")
            self.assertIsNotNone(rows["Temple @ Hawai'i"]["market"],
                                 "an alias plus a mascot must map")
            self.assertIn("Texas Longhorns @ Oklahoma Sooners",
                          card["summary"]["boardMapping"]["unmappedEvents"])

    def test_a_stripped_name_cannot_land_on_a_different_school(self):
        slate = [("Georgia State @ Troy", 2.0, KICK_SAT), ("Georgia @ Troy", 20.0, "2026-10-10T16:00:00Z")]
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(Path(d), slate_rows=slate)
            card = wc.build(f.inputs(), as_of=AS_OF)
            rows = by_game(card)
            # Kickoff disambiguates: the Oct 3 board event belongs to Georgia State.
            self.assertIsNotNone(rows["Georgia State @ Troy"]["market"])
            self.assertIsNone(rows["Georgia @ Troy"]["market"])


class TestDisposition(unittest.TestCase):
    def test_blocked_authority_never_bets_and_a_price_edge_is_a_lean(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(Path(d))
            card = wc.build(f.inputs(), as_of=AS_OF)
            self.assertEqual(card["summary"]["bet"], 0)
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            self.assertEqual(row["decision"]["disposition"], wc.LEAN)
            self.assertEqual(row["decision"]["pick"]["team"], "New Mexico State")
            self.assertEqual(row["decision"]["stakeUnits"], 0.0)
            self.assertIn("NO_EVIDENCE", row["decision"]["reasonCodes"])
            self.assertIn(wc.AUTHORITY_BLOCKED, row["decision"]["reasonCodes"])

    def test_an_open_authority_still_cannot_bet_without_registered_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(Path(d), authority_open=True)
            card = wc.build(f.inputs(), as_of=AS_OF)
            self.assertEqual(card["summary"]["bet"], 0)
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            self.assertEqual(row["decision"]["disposition"], wc.LEAN)
            self.assertEqual(row["decision"]["engineDecision"], "PASS")

    def test_no_edge_is_a_pass_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            row = by_game(card)["Arkansas @ Texas A&M"]
            self.assertEqual(row["decision"]["disposition"], wc.PASS)
            self.assertIn(wc.NO_PRICE_EDGE, row["decision"]["reasonCodes"])
            self.assertIsNone(row["decision"]["pick"])

    def test_a_different_number_is_a_different_bet(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            row = by_game(card)["Temple @ Hawai'i"]
            self.assertEqual(row["decision"]["disposition"], wc.PASS)
            self.assertIn("LINE_MISMATCH", row["decision"]["reasonCodes"])

    def test_confidence_is_the_sharp_no_vig_probability(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            expected = devig_multiplicative([-114, -103])[0]
            self.assertAlmostEqual(row["decision"]["confidence"], round(expected, 4), places=4)
            self.assertLess(row["decision"]["confidence"], 0.55)

    def test_stale_quotes_are_flagged_and_never_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d), board_age_minutes=24 * 60).inputs(), as_of=AS_OF)
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            self.assertEqual(row["gates"]["execution"]["state"], wc.GATE_FAIL)
            self.assertIn(wc.QUOTE_STALE, row["decision"]["reasonCodes"])

    def test_a_started_game_is_not_a_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            self.assertEqual(row["decision"]["disposition"], wc.PASS)
            self.assertIn(wc.STARTED, row["decision"]["reasonCodes"])

    def test_thursday_game_outside_the_frozen_window_is_still_evaluated(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            row = by_game(card)["Western Kentucky @ New Mexico State"]
            self.assertFalse(row["inFrozenCohort"])
            self.assertEqual(row["gates"]["cohortIdentity"]["state"], wc.GATE_DEGRADED)
            self.assertIsNotNone(row["market"])

    def test_every_game_gets_exactly_one_disposition(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            s = card["summary"]
            self.assertEqual(s["bet"] + s["lean"] + s["pass"], s["games"])
            self.assertEqual(len(card["plays"]) + len(card["passList"]), s["games"])


class TestDeterminismAndTrace(unittest.TestCase):
    def test_same_inputs_same_card(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fixture(Path(d))
            a = wc.build(f.inputs(), as_of=AS_OF)
            b = wc.build(f.inputs(), as_of=AS_OF)
            self.assertEqual(a["cardSha256"], b["cardSha256"])
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_every_input_is_hashed(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            names = {i["name"]: i for i in card["inputs"]}
            self.assertTrue(names["board"]["sha256"])
            self.assertTrue(names["authority"]["sha256"])

    def test_ranking_is_labelled_a_heuristic(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            self.assertFalse(card["rankingRule"]["validated"])
            ranks = [r["decision"]["rank"] for r in card["plays"]]
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)))


class TestComputedEvidence(unittest.TestCase):
    def test_clv_gate_refuses_first_seen_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "opens.csv"
            p.write_text("game,opening_line,source\nA @ B,-3,first_seen\nC @ D,2.5,late\n")
            rep = wc.clv_gate_report(p)
            self.assertEqual(rep["verdict"], "INSUFFICIENT")
            self.assertEqual(rep["gradeable"], 0)
            self.assertEqual(rep["sources"], {"first_seen": 1, "late": 1})

    def test_a_missed_scheduled_capture_is_reported(self):
        state = wc.scheduler_state(
            as_of=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc),
            br2_generated=datetime(2026, 9, 29, 18, 44, tzinfo=timezone.utc),
            kalshi_last=datetime(2026, 9, 29, 21, 6, tzinfo=timezone.utc),
            prospective_window={"startsAt": "2026-09-27T00:00:00Z", "endsAt": "2026-10-02T23:00:00Z"})
        self.assertEqual(state["state"], wc.GATE_DEGRADED)
        self.assertTrue(any("BR2" in m for m in state["missed"]))

    def test_a_landed_capture_is_not_reported_missed(self):
        state = wc.scheduler_state(
            as_of=datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc),
            br2_generated=datetime(2026, 9, 30, 13, 41, tzinfo=timezone.utc),
            kalshi_last=datetime(2026, 9, 29, 21, 6, tzinfo=timezone.utc),
            prospective_window={"startsAt": "2026-09-27T00:00:00Z", "endsAt": "2026-10-02T23:00:00Z"})
        self.assertEqual(state["state"], wc.GATE_PASS)

    def test_in_season_clv_counts_week_clusters_honestly(self):
        learning = {"contract": "X", "season": 2026, "rows": [
            {"week": 1, "directional_clv": -1.0, "projection_gap_vs_open": 5},
            {"week": 3, "directional_clv": 1.0, "projection_gap_vs_open": 5},
            {"week": 3, "directional_clv": 0.5, "projection_gap_vs_open": 2}]}
        out = wc.in_season_clv(learning)
        self.assertEqual(out["allGames"]["n"], 3)
        self.assertEqual(out["registeredRule"]["n"], 1)


class TestRender(unittest.TestCase):
    def test_fragment_shape_and_escaping(self):
        with tempfile.TemporaryDirectory() as d:
            card = wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF)
            page = card_render.render(card)
            self.assertTrue(page.startswith("<title>"))
            self.assertNotIn("<html", page.lower().split("<script>")[0])
            self.assertIn("Hawai&#x27;i", page)
            self.assertIn("Texas A&amp;M", page)
            for r in card["passList"] + card["plays"]:
                self.assertIn(card_render.e(r["game"]), page)

    def test_no_external_request_but_fonts(self):
        import re
        with tempfile.TemporaryDirectory() as d:
            page = card_render.render(wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF))
            hosts = set(re.findall(r'(?:src|href)=["\']https?://([^/"\']+)', page))
            self.assertLessEqual(hosts, {"fonts.googleapis.com"})

    def test_standalone_wraps_a_document(self):
        with tempfile.TemporaryDirectory() as d:
            page = card_render.render(wc.build(Fixture(Path(d)).inputs(), as_of=AS_OF), standalone=True)
            self.assertTrue(page.lower().startswith("<!doctype html>"))


if __name__ == "__main__":
    unittest.main()
