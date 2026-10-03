"""A market belongs to a game only if it is dated on that game's day (D43).

On 3 October 2026 the capture loop began polling the following week's slate
while that Saturday's markets were still open. Five favourites had a ladder
quoted on one side only, so the exchange event named one team, and the slate
lookup found that team's fixture a week later. Vanderbilt at Georgia was
written down as the opening line of Georgia at Alabama and locked that game as
a missed open. The week before, the same thing put another market's line in
the first-seen slot of fourteen games.

These tests pin the rule that stops it, the places that enforce it, and the
five real rows.
"""
import csv
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge import capture_report, prospective_open, week6_clv, weekly_card
from cfb_edge.active_market_state import _fixture_for_event
from cfb_edge.providers.kalshi import board_quotes
from cfb_edge.watch import (
    OpeningBook,
    Quote,
    event_matches_kickoff,
    event_ticker_date,
)

# The eight rows the lock held at 2026-10-03T15:50Z, exactly as recorded:
# (game, kickoff, event ticker, venue open, right market?)
LOCKED_2026_10_03 = (
    ("Georgia @ Alabama", "2026-10-10T04:00:00.000Z",
     "KXNCAAFSPREAD-26OCT03VANUGA", "2026-09-27T04:06:00+00:00", False),
    ("Indiana @ Nebraska", "2026-10-10T04:00:00.000Z",
     "KXNCAAFSPREAD-26OCT03INDRUTG", "2026-09-27T07:06:00+00:00", False),
    ("LSU @ Kentucky", "2026-10-10T04:00:00.000Z",
     "KXNCAAFSPREAD-26OCT03MCNSLSU", "2026-09-30T01:06:00+00:00", False),
    ("Stanford @ Notre Dame", "2026-10-10T19:30:00.000Z",
     "KXNCAAFSPREAD-26OCT03NDUNC", "2026-09-27T01:06:00+00:00", False),
    ("UAB @ Memphis", "2026-10-10T04:00:00.000Z",
     "KXNCAAFSPREAD-26OCT03SAMUAB", "2026-09-30T01:06:00+00:00", False),
    ("Iowa @ Washington", "2026-10-10T01:00:00.000Z",
     "KXNCAAFSPREAD-26OCT09IOWAWASH", "2026-10-03T01:06:00+00:00", True),
    ("Jacksonville State @ Kennesaw State", "2026-10-07T23:00:00.000Z",
     "KXNCAAFSPREAD-26OCT07JVSTKENN", "2026-10-01T01:06:00+00:00", True),
    ("Southern Miss @ Troy", "2026-10-07T00:00:00.000Z",
     "KXNCAAFSPREAD-26OCT06USMTROY", "2026-09-30T19:06:00+00:00", True),
)
LOCKED_AT = "2026-10-03T15:50:27.803601+00:00"


def ladder(event, team, rungs=((3.5, 54), (7.5, 44)), open_time=None):
    """A spread ladder written on one team: the line sits where it crosses 50."""
    out = []
    for strike, bid in rungs:
        market = dict(event_ticker=event, ticker=f"{event}-{team[:3].upper()}{strike}",
                      yes_sub_title=f"{team} wins by over {strike} points",
                      yes_bid=bid, yes_ask=bid + 2)
        if open_time:
            market["open_time"] = open_time
        out.append(market)
    return out


def poll(markets, games, kickoffs, seen_at="2026-10-03T15:50:27+00:00"):
    payload = json.dumps({"markets": markets}).encode()
    return board_quotes(games=games, kickoffs=kickoffs, opener=lambda _: payload,
                        seen_at=seen_at)


def quote(game, event, line, seen_at, kickoff, venue_open=None, **more):
    return Quote(game=game, book="kalshi", market="spread", line=line, price=None,
                 seen_at=seen_at, commence_time=kickoff, venue_open_time=venue_open,
                 event_ticker=event, market_tickers=(f"{event}-A", f"{event}-B"),
                 quote_inputs=({"ticker": f"{event}-A"}, {"ticker": f"{event}-B"}),
                 poll_time=seen_at, first_valid_two_sided_quote_time=seen_at,
                 code_revision="test", **more)


def write_log(path, polls):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for polled_at, quotes in polls:
            fh.write(json.dumps({"polled_at": polled_at,
                                 "quotes": [q.__dict__ for q in quotes]}) + "\n")


class TheRule(unittest.TestCase):
    def test_a_ticker_names_its_game_day(self):
        self.assertEqual(str(event_ticker_date("KXNCAAFSPREAD-26OCT03VANUGA")), "2026-10-03")
        # A contract ticker carries the same day as its event.
        self.assertEqual(str(event_ticker_date("KXNCAAFSPREAD-26OCT03VANUGA-UGA28")), "2026-10-03")
        self.assertEqual(str(event_ticker_date("kxncaafspread-26sep26cmumia")), "2026-09-26")

    def test_a_ticker_that_names_no_day_says_so(self):
        for ticker in (None, "", "E", "26SEP19KYTAM", "KXNCAAFSPREAD-26XXX03AB",
                       "KXNCAAFSPREAD-26FEB30AB", "KXNCAAFSPREAD-VANUGA"):
            with self.subTest(ticker=ticker):
                self.assertIsNone(event_ticker_date(ticker))

    def test_the_eight_rows_locked_on_3_october(self):
        for game, kickoff, event, _opened, right in LOCKED_2026_10_03:
            with self.subTest(game=game):
                self.assertIs(event_matches_kickoff(event, kickoff), right)

    def test_an_evening_game_is_the_next_day_in_utc(self):
        # Friday 9 October, 9 pm Eastern. The ticker says the 9th, UTC the 10th.
        self.assertTrue(event_matches_kickoff(
            "KXNCAAFSPREAD-26OCT09IOWAWASH", "2026-10-10T01:00:00.000Z"))

    def test_the_week_before_and_the_week_after_are_both_refused(self):
        self.assertFalse(event_matches_kickoff(
            "KXNCAAFSPREAD-26SEP26CMUMIA", "2026-10-03T19:30:00Z"))
        self.assertFalse(event_matches_kickoff(
            "KXNCAAFSPREAD-26OCT03INDRUTG", "2026-09-26T19:30:00Z"))
        # One day out is not this game either: a ticker never leads kickoff.
        self.assertFalse(event_matches_kickoff(
            "KXNCAAFSPREAD-26OCT10INDNEB", "2026-10-09T23:00:00Z"))

    def test_nothing_to_check_is_not_an_answer(self):
        self.assertIsNone(event_matches_kickoff(None, "2026-10-10T04:00:00Z"))
        self.assertIsNone(event_matches_kickoff("E", "2026-10-10T04:00:00Z"))
        self.assertIsNone(event_matches_kickoff("KXNCAAFSPREAD-26OCT10INDNEB", None))
        self.assertIsNone(event_matches_kickoff("KXNCAAFSPREAD-26OCT10INDNEB", "soon"))


class TheBoardReader(unittest.TestCase):
    GAME = "Georgia @ Alabama"
    KICKOFFS = {GAME: "2026-10-10T04:00:00.000Z"}

    def test_this_weeks_market_is_not_next_weeks_game(self):
        """The defect itself: Vanderbilt at Georgia, read as Georgia at Alabama."""
        markets = ladder("KXNCAAFSPREAD-26OCT03VANUGA", "Georgia",
                         rungs=((24.5, 54), (28.5, 44)))
        self.assertEqual(poll(markets, [self.GAME], self.KICKOFFS), [])

    def test_the_games_own_one_sided_market_is_still_read(self):
        markets = ladder("KXNCAAFSPREAD-26OCT10UGAALA", "Alabama")
        quotes = poll(markets, [self.GAME], self.KICKOFFS)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].game, self.GAME)
        self.assertEqual(quotes[0].event_ticker, "KXNCAAFSPREAD-26OCT10UGAALA")
        self.assertEqual(quotes[0].line, -5.5)

    def test_with_both_listed_only_the_games_own_market_is_read(self):
        markets = (ladder("KXNCAAFSPREAD-26OCT03VANUGA", "Georgia",
                          rungs=((24.5, 54), (28.5, 44)))
                   + ladder("KXNCAAFSPREAD-26OCT10UGAALA", "Alabama"))
        quotes = poll(markets, [self.GAME], self.KICKOFFS)
        self.assertEqual([q.event_ticker for q in quotes],
                         ["KXNCAAFSPREAD-26OCT10UGAALA"])

    def test_a_one_sided_market_with_no_kickoff_to_check_is_skipped(self):
        markets = ladder("KXNCAAFSPREAD-26OCT10UGAALA", "Alabama")
        self.assertEqual(poll(markets, [self.GAME], {}), [])

    def test_a_one_sided_market_with_no_day_in_its_ticker_is_skipped(self):
        markets = ladder("UGAALA", "Alabama")
        self.assertEqual(poll(markets, [self.GAME], self.KICKOFFS), [])

    def test_a_rematch_a_week_apart_is_not_confused(self):
        """Both teams named, wrong day: a regular-season meeting is not the
        conference title game the same two teams play a week later."""
        event = "KXNCAAFSPREAD-26NOV28UGAALA"
        markets = ladder(event, "Alabama") + ladder(event, "Georgia",
                                                    rungs=((3.5, 20), (7.5, 12)))
        title_game = {self.GAME: "2026-12-05T21:00:00Z"}
        self.assertEqual(poll(markets, [self.GAME], title_game), [])
        regular_season = {self.GAME: "2026-11-28T20:30:00Z"}
        self.assertEqual(len(poll(markets, [self.GAME], regular_season)), 1)

    def test_both_teams_named_and_nothing_to_check_is_still_read(self):
        """Two names identify a fixture; an undated ticker is not a reason to
        drop the whole board if the exchange changes its ticker format."""
        markets = ladder("UGAALA", "Alabama") + ladder("UGAALA", "Georgia",
                                                       rungs=((3.5, 20), (7.5, 12)))
        self.assertEqual(len(poll(markets, [self.GAME], self.KICKOFFS)), 1)


class TheOpeningBook(unittest.TestCase):
    GAME = "Georgia @ Alabama"
    KICKOFF = "2026-10-10T04:00:00.000Z"

    def wrong(self, seen_at="2026-10-03T15:17:15+00:00"):
        return quote(self.GAME, "KXNCAAFSPREAD-26OCT03VANUGA", 24.8, seen_at,
                     self.KICKOFF, "2026-09-27T04:06:00+00:00")

    def right(self, seen_at="2026-10-04T01:07:00+00:00"):
        return quote(self.GAME, "KXNCAAFSPREAD-26OCT10UGAALA", -2.5, seen_at,
                     self.KICKOFF, "2026-10-04T01:06:00+00:00")

    def test_another_games_market_is_never_the_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = OpeningBook(path=Path(tmp) / "opens.jsonl.gz")
            self.assertEqual(book.record([self.wrong()]), [])
            self.assertEqual(book.opens, {})
            fresh = book.record([self.right()])
            self.assertEqual([q.event_ticker for q in fresh],
                             ["KXNCAAFSPREAD-26OCT10UGAALA"])
            self.assertEqual(book.consensus_opens(), {self.GAME: -2.5})
            self.assertEqual(book.wrong_event,
                             {(self.GAME, "KXNCAAFSPREAD-26OCT03VANUGA"): 1})

    def test_a_log_that_already_holds_one_is_read_the_same_way(self):
        """The log is append-only and keeps what was polled. The views built
        from it do not use the wrong market, so the first real sighting is the
        open even though it is not the first line in the file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opens.jsonl.gz"
            write_log(path, [
                ("2026-10-03T15:17:15+00:00", [self.wrong()]),
                ("2026-10-03T15:21:17+00:00", [self.wrong("2026-10-03T15:21:17+00:00")]),
                ("2026-10-04T01:07:00+00:00", [self.right()]),
            ])
            book = OpeningBook.load(path)
            first = book.first_quotes()[self.GAME]
            self.assertEqual(first.event_ticker, "KXNCAAFSPREAD-26OCT10UGAALA")
            self.assertEqual(first.seen_at, "2026-10-04T01:07:00+00:00")
            self.assertEqual(book.latest[first.key].line, -2.5)
            self.assertEqual(book.closing[first.key].line, -2.5)
            self.assertEqual(sum(book.wrong_event.values()), 2)
            # Nothing was taken out of the log.
            with gzip.open(path, "rt") as fh:
                self.assertEqual(sum(1 for _ in fh), 3)

    def test_a_game_seen_only_through_another_market_has_no_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opens.jsonl.gz"
            write_log(path, [("2026-10-03T15:17:15+00:00", [self.wrong()])])
            out = Path(tmp) / "opens.csv"
            self.assertEqual(OpeningBook.load(path).write_opens_csv(out), 0)
            with out.open(newline="") as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 0)

    def test_a_quote_with_nothing_to_check_is_treated_as_before(self):
        """Logs written before the ticker was recorded carry none."""
        old = Quote(game=self.GAME, book="kalshi", market="spread", line=-3.0,
                    price=None, seen_at="2026-09-18T14:50:11+00:00",
                    commence_time=self.KICKOFF)
        with tempfile.TemporaryDirectory() as tmp:
            book = OpeningBook(path=Path(tmp) / "opens.jsonl.gz")
            self.assertEqual(book.record([old]), [old])
            self.assertEqual(book.wrong_event, {})


class TheLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.evidence = Path(self._tmp.name) / "evidence"

    def run_update(self, slate, quotes, existing, at):
        return prospective_open.update(
            slate=slate, slate_sha256="s", cohort_id="CFB_2026_PROVIDER_WEEK_6",
            snapshot={"polled_at": at, "quotes": [q.__dict__ for q in quotes]},
            existing=existing, evidence_dir=self.evidence, revision="test",
            generated_at=datetime.fromisoformat(at),
        )

    def locked_status(self):
        """The status file as it stood on 3 October 2026, 15:50 UTC."""
        slate = [{"game": g, "kickoff": k} for g, k, *_ in LOCKED_2026_10_03]
        quotes = [quote(g, ev, 1.5, LOCKED_AT, k, opened)
                  for g, k, ev, opened, _right in LOCKED_2026_10_03]
        # Built the way it was built: the reader that produced these quotes
        # had no day check, and neither did the lock. Bypass today's refusal
        # to recreate what is on the evidence branch.
        rows = []
        for q in quotes:
            lag = (datetime.fromisoformat(LOCKED_AT)
                   - datetime.fromisoformat(q.venue_open_time)).total_seconds()
            rows.append({
                "game": q.game, "kickoff": q.commence_time,
                "state": "MISSED_TRUE_OPEN_WINDOW", "locked": True,
                "auditGrade": False, "eventTicker": q.event_ticker,
                "venueOpenTime": q.venue_open_time, "openLagSeconds": lag,
                "observedAt": LOCKED_AT, "firstEventSeenAt": LOCKED_AT,
                "marketTickers": list(q.market_tickers),
                "evidenceSha256": "0" * 64, "codeRevision": "13b0daeb",
            })
        return slate, {"cohortId": "CFB_2026_PROVIDER_WEEK_6", "rows": rows}

    def test_the_five_locks_on_another_games_market_are_voided(self):
        slate, status = self.locked_status()
        report = self.run_update(slate, [], status, "2026-10-03T19:00:00+00:00")
        state = {r["game"]: r["state"] for r in report["rows"]}
        wrong = [g for g, *_rest, right in LOCKED_2026_10_03 if not right]
        right = [g for g, *_rest, ok in LOCKED_2026_10_03 if ok]
        self.assertEqual(len(wrong), 5)
        for game in wrong:
            self.assertEqual(state[game], "PENDING", game)
        for game in right:
            self.assertEqual(state[game], "MISSED_TRUE_OPEN_WINDOW", game)
        s = report["summary"]
        self.assertEqual((s["missedTrueOpenRows"], s["pendingRows"], s["voidedRows"],
                          s["newlyVoidedRows"]), (3, 5, 5, 5))
        self.assertEqual(sorted(r["game"] for r in report["voidedRows"]), sorted(wrong))
        for row in report["voidedRows"]:
            # Everything the lock recorded is kept, with why it does not stand.
            self.assertEqual(row["voidReason"], prospective_open.VOID_REASON)
            self.assertEqual(row["state"], "MISSED_TRUE_OPEN_WINDOW")
            self.assertEqual(row["evidenceSha256"], "0" * 64)
            self.assertTrue(row["eventTicker"].startswith("KXNCAAFSPREAD-26OCT03"))
        self.assertEqual(sorted(t["game"] for t in report["transitions"]), sorted(wrong))

    def test_voiding_happens_once(self):
        slate, status = self.locked_status()
        first = self.run_update(slate, [], status, "2026-10-03T19:00:00+00:00")
        second = self.run_update(slate, [], first, "2026-10-03T19:01:00+00:00")
        self.assertEqual(second["summary"]["voidedRows"], 5)
        self.assertEqual(second["summary"]["newlyVoidedRows"], 0)
        self.assertEqual(second["transitions"], [])
        self.assertEqual(second["voidedRows"], first["voidedRows"])

    def test_a_voided_game_can_then_capture_its_own_open(self):
        slate, status = self.locked_status()
        cleared = self.run_update(slate, [], status, "2026-10-03T19:00:00+00:00")
        own = quote("Georgia @ Alabama", "KXNCAAFSPREAD-26OCT10UGAALA", -2.5,
                    "2026-10-04T01:07:00+00:00", "2026-10-10T04:00:00.000Z",
                    "2026-10-04T01:06:00+00:00")
        report = self.run_update(slate, [own], cleared, "2026-10-04T01:07:00+00:00")
        row = next(r for r in report["rows"] if r["game"] == "Georgia @ Alabama")
        self.assertEqual(row["state"], "CAPTURED_TRUE_OPEN")
        self.assertEqual(row["eventTicker"], "KXNCAAFSPREAD-26OCT10UGAALA")
        self.assertEqual(row["openLagSeconds"], 60.0)
        self.assertEqual(report["summary"]["capturedTrueOpenRows"], 1)
        self.assertEqual(report["summary"]["voidedRows"], 5)

    def test_a_real_miss_is_never_reopened(self):
        """Iowa at Washington opened Friday night and was first seen Saturday.
        That is a miss on its own market and stays one whatever arrives later."""
        slate, status = self.locked_status()
        late = quote("Iowa @ Washington", "KXNCAAFSPREAD-26OCT09IOWAWASH", -0.8,
                     "2026-10-03T19:00:00+00:00", "2026-10-10T01:00:00.000Z",
                     "2026-10-03T18:59:00+00:00")
        report = self.run_update(slate, [late], status, "2026-10-03T19:00:00+00:00")
        row = next(r for r in report["rows"] if r["game"] == "Iowa @ Washington")
        self.assertEqual(row["state"], "MISSED_TRUE_OPEN_WINDOW")
        self.assertEqual(row["observedAt"], LOCKED_AT)
        self.assertNotIn("Iowa @ Washington", [r["game"] for r in report["voidedRows"]])

    def test_a_reschedule_after_the_lock_does_not_void_it(self):
        """The row is judged by the kickoff it was locked with."""
        slate, status = self.locked_status()
        moved = [dict(f, kickoff="2026-10-17T01:00:00.000Z")
                 if f["game"] == "Iowa @ Washington" else f for f in slate]
        report = self.run_update(moved, [], status, "2026-10-03T19:00:00+00:00")
        row = next(r for r in report["rows"] if r["game"] == "Iowa @ Washington")
        self.assertEqual(row["state"], "MISSED_TRUE_OPEN_WINDOW")

    def test_another_games_market_cannot_lock_a_game(self):
        game, kickoff = "Georgia @ Alabama", "2026-10-10T04:00:00.000Z"
        slate = [{"game": game, "kickoff": kickoff}]
        stray = quote(game, "KXNCAAFSPREAD-26OCT03VANUGA", 24.8, LOCKED_AT, kickoff,
                      "2026-09-27T04:06:00+00:00")
        report = self.run_update(slate, [stray], None, LOCKED_AT)
        self.assertEqual(report["rows"][0]["state"], "PENDING")
        self.assertNotIn("eventTicker", report["rows"][0])
        self.assertEqual(report["summary"]["refusedWrongEventQuotes"], 1)
        self.assertEqual(report["refusedWrongEventQuotes"],
                         [{"game": game, "eventTicker": "KXNCAAFSPREAD-26OCT03VANUGA"}])

    def test_a_stray_market_beside_the_real_one_is_not_an_ambiguity(self):
        game, kickoff = "Georgia @ Alabama", "2026-10-10T04:00:00.000Z"
        slate = [{"game": game, "kickoff": kickoff}]
        at = "2026-10-04T01:07:00+00:00"
        stray = quote(game, "KXNCAAFSPREAD-26OCT03VANUGA", 24.8, at, kickoff,
                      "2026-09-27T04:06:00+00:00")
        own = quote(game, "KXNCAAFSPREAD-26OCT10UGAALA", -2.5, at, kickoff,
                    "2026-10-04T01:06:00+00:00")
        report = self.run_update(slate, [stray, own], None, at)
        self.assertEqual(report["rows"][0]["state"], "CAPTURED_TRUE_OPEN")
        self.assertEqual(report["rows"][0]["eventTicker"], "KXNCAAFSPREAD-26OCT10UGAALA")


class TheReaders(unittest.TestCase):
    GAME = "Maryland @ Nebraska"
    KICKOFF = "2026-10-03T20:00:00.000Z"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.log = self.tmp / "opens.jsonl.gz"
        # The game's own market ten minutes before kickoff, and a market for
        # next week's Nebraska game seen five minutes later in the same log.
        own = quote(self.GAME, "KXNCAAFSPREAD-26OCT03MDNEB", -14.4,
                    "2026-10-03T19:50:00+00:00", self.KICKOFF,
                    "2026-09-27T10:06:00+00:00")
        stray = quote(self.GAME, "KXNCAAFSPREAD-26OCT10INDNEB", 9.5,
                      "2026-10-03T19:55:00+00:00", self.KICKOFF,
                      "2026-10-03T16:06:00+00:00")
        write_log(self.log, [("2026-10-03T19:50:00+00:00", [own]),
                             ("2026-10-03T19:55:00+00:00", [stray])])

    def test_the_close_is_the_games_own_market(self):
        freeze = {"cohortId": "C", "rows": [{
            "game": self.GAME, "kickoff": self.KICKOFF, "openingHomeLine": -12.9,
            "openObservedAt": "2026-09-27T10:11:00+00:00",
            "venueOpenTime": "2026-09-27T10:06:00+00:00", "openLagSeconds": 300.0}]}
        report = week6_clv.build_close_report(
            freeze=freeze, log_path=self.log,
            now=datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc))
        row = report["games"][0]
        self.assertEqual(row["observations"][-1]["derivedHomeLine"], -14.4)
        self.assertEqual(row["closeObservedAt"], "2026-10-03T19:50:00+00:00")
        self.assertTrue(row["gradeableClose"])
        self.assertEqual(row["wrongEventQuotesIgnored"], 1)
        self.assertEqual(report["summary"]["wrongEventQuotesIgnored"], 1)

    def test_the_card_shows_the_games_own_line(self):
        latest = weekly_card.kalshi_latest(self.log)
        self.assertEqual(latest[self.GAME]["line"], -14.4)
        self.assertEqual(latest[self.GAME]["eventTicker"], "KXNCAAFSPREAD-26OCT03MDNEB")

    def test_the_capture_report_counts_what_it_refused(self):
        slate = self.tmp / "slate.csv"
        slate.write_text("game,kickoff\n" + f"{self.GAME},{self.KICKOFF}\n", encoding="utf-8")
        report = capture_report.build(
            self.log, slate, datetime(2026, 10, 3, 19, 56, tzinfo=timezone.utc))
        row = report["games"][0]
        self.assertEqual(row["firstSeen"], "2026-10-03T19:50:00+00:00")
        # The latest poll held only the stray market, so the game is absent
        # from it rather than present at another game's number.
        self.assertEqual(row["observations"], [])
        self.assertIn("ABSENT_FROM_LATEST_POLL", row["exclusions"])
        self.assertEqual(report["wrongEventQuotes"]["ignored"], 1)
        self.assertEqual(report["wrongEventQuotes"]["pairs"],
                         [{"game": self.GAME, "eventTicker": "KXNCAAFSPREAD-26OCT10INDNEB",
                           "quotes": 1}])


class TheFeatureCapture(unittest.TestCase):
    IDENTITIES = {"Georgia @ Alabama": {
        "game": "Georgia @ Alabama", "away": "Georgia", "home": "Alabama",
        "kickoff": "2026-10-10T04:00:00.000Z", "canonicalGameId": "g1"}}
    KNOWN = {"Georgia", "Alabama"}

    def fixture(self, markets):
        return _fixture_for_event(markets, self.IDENTITIES, self.KNOWN)

    def test_a_one_sided_market_from_another_week_matches_nothing(self):
        self.assertIsNone(self.fixture(ladder("KXNCAAFSPREAD-26OCT03VANUGA", "Georgia")))

    def test_the_games_own_one_sided_market_matches(self):
        hit = self.fixture(ladder("KXNCAAFSPREAD-26OCT10UGAALA", "Alabama"))
        self.assertEqual(hit["game"], "Georgia @ Alabama")

    def test_both_teams_on_another_day_match_nothing(self):
        event = "KXNCAAFSPREAD-26NOV28UGAALA"
        self.assertIsNone(self.fixture(ladder(event, "Alabama") + ladder(event, "Georgia")))

    def test_a_one_sided_market_that_cannot_be_dated_matches_nothing(self):
        self.assertIsNone(self.fixture(ladder("UGAALA", "Alabama")))


if __name__ == "__main__":
    unittest.main()
