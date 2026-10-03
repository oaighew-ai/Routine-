"""The loop records the last price before kickoff for the week being played (D44).

From Friday evening the open loop polls next week's slate. Nothing then
watched this week's games as they kicked off, and the Week 6 cohort's closes
depended on a scheduled job starting within fifteen minutes of each kickoff.
On 3 October 2026 it started none, and no frozen game had a gradeable close.

A game now joins the loop's poll shortly before it kicks off. These tests pin
which games join, that a team on both slates has each of its markets read as
the right game, that the result is a close a grader accepts, and that the
workflow asks for it.
"""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cfb_edge import watch, week6_clv
from cfb_edge.providers import kalshi
from cfb_edge.providers.kalshi import board_quotes

ROOT = Path(__file__).resolve().parents[1]
LOOP = (ROOT / ".github/workflows/capture-open-loop.yml").read_text()
HEADER = "game,projected_margin,side,posted_line,total,kickoff\n"
NOW = datetime(2026, 10, 3, 19, 45, tzinfo=timezone.utc)


def slate(path, rows):
    path.write_text(HEADER + "".join(f"{game},-3.0,,,52.0,{kickoff}\n" for game, kickoff in rows),
                    encoding="utf-8")
    return path


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ladder(event, team, rungs=((3.5, 54), (7.5, 44))):
    return [dict(event_ticker=event, ticker=f"{event}-{team[:3].upper()}{strike}",
                 yes_sub_title=f"{team} wins by over {strike} points",
                 yes_bid=bid, yes_ask=bid + 2) for strike, bid in rungs]


class WhichGamesJoinThePoll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = slate(Path(self._tmp.name) / "playing.csv", [
            ("Maryland @ Nebraska", "2026-10-03T20:00:00.000Z"),       # 15 min away
            ("Arkansas @ Texas A&M", "2026-10-03T23:00:00.000Z"),      # hours away
            ("Michigan @ Minnesota", "2026-10-03T16:00:00.000Z"),      # being played
            ("Iowa State @ BYU", "2026-10-03T19:45:00.000Z"),          # kicking off now
            ("Texas @ Oklahoma", "2026-10-03T20:05:00.000Z"),          # exactly at the edge
            ("UCLA @ Oregon", "2026-10-03T20:05:01.000Z"),             # one second past it
            ("Navy @ Army", "to be announced"),
        ])

    def test_only_games_about_to_kick_off(self):
        near = watch.closing_fixtures(self.path, now=NOW)
        self.assertEqual(near, {
            "Maryland @ Nebraska": "2026-10-03T20:00:00.000Z",
            "Texas @ Oklahoma": "2026-10-03T20:05:00.000Z",
        })

    def test_the_window_covers_the_close_tolerance_with_room(self):
        self.assertEqual(watch.CLOSE_MAX_AGE_SECONDS, 900)
        self.assertGreater(watch.CLOSING_WINDOW_SECONDS, watch.CLOSE_MAX_AGE_SECONDS)
        # A narrower window is honoured when asked for.
        self.assertEqual(watch.closing_fixtures(self.path, now=NOW, window_seconds=900),
                         {"Maryland @ Nebraska": "2026-10-03T20:00:00.000Z"})

    def test_a_game_leaves_the_poll_at_kickoff(self):
        at_kickoff = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)
        self.assertNotIn("Maryland @ Nebraska",
                         watch.closing_fixtures(self.path, now=at_kickoff))

    def test_local_time_is_read_as_the_same_moment(self):
        eastern = NOW.astimezone(timezone(timedelta(hours=-4)))
        self.assertEqual(watch.closing_fixtures(self.path, now=eastern),
                         watch.closing_fixtures(self.path, now=NOW))


class ATeamOnBothSlates(unittest.TestCase):
    """Nebraska hosts Maryland today and Indiana next week. Each of its two
    markets belongs to one of those games, and the game day says which."""

    GAMES = ["Indiana @ Nebraska", "Maryland @ Nebraska"]
    KICKOFFS = {"Indiana @ Nebraska": "2026-10-10T04:00:00.000Z",
                "Maryland @ Nebraska": "2026-10-03T20:00:00.000Z"}

    def poll(self, markets):
        payload = json.dumps({"markets": markets}).encode()
        return board_quotes(games=self.GAMES, kickoffs=self.KICKOFFS,
                            opener=lambda _: payload, seen_at="2026-10-03T19:50:00+00:00")

    def test_each_one_sided_market_is_read_as_its_own_game(self):
        quotes = self.poll(
            ladder("KXNCAAFSPREAD-26OCT03MDNEB", "Nebraska", rungs=((13.5, 54), (15.5, 44)))
            + ladder("KXNCAAFSPREAD-26OCT10INDNEB", "Indiana", rungs=((8.5, 54), (10.5, 44))))
        by_game = {q.game: q for q in quotes}
        self.assertEqual(sorted(by_game), sorted(self.GAMES))
        self.assertEqual(by_game["Maryland @ Nebraska"].event_ticker, "KXNCAAFSPREAD-26OCT03MDNEB")
        self.assertEqual(by_game["Maryland @ Nebraska"].line, -14.5)
        self.assertEqual(by_game["Indiana @ Nebraska"].event_ticker, "KXNCAAFSPREAD-26OCT10INDNEB")
        self.assertEqual(by_game["Indiana @ Nebraska"].line, 9.5)

    def test_with_only_this_weeks_market_listed_next_weeks_game_has_no_line(self):
        quotes = self.poll(ladder("KXNCAAFSPREAD-26OCT03MDNEB", "Nebraska"))
        self.assertEqual([q.game for q in quotes], ["Maryland @ Nebraska"])

    def test_two_fixtures_on_the_same_day_are_still_a_guess(self):
        """The day can only choose between weeks. Same day, two candidates:
        skipped, as before."""
        payload = json.dumps({"markets": ladder("KXNCAAFSPREAD-26OCT03MDNEB", "Nebraska")}).encode()
        quotes = board_quotes(
            games=["Maryland @ Nebraska", "Nebraska @ Iowa"],
            kickoffs={"Maryland @ Nebraska": "2026-10-03T20:00:00.000Z",
                      "Nebraska @ Iowa": "2026-10-03T23:30:00.000Z"},
            opener=lambda _: payload, seen_at="2026-10-03T19:50:00+00:00")
        self.assertEqual(quotes, [])


class TheCommand(unittest.TestCase):
    def run_once(self, closing_rows):
        asked = {}

        def fake_board(*, games, kickoffs, **_):
            asked["games"], asked["kickoffs"] = list(games), dict(kickoffs)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            opening = slate(tmp / "opening.csv", [("Indiana @ Nebraska", "2026-10-10T04:00:00.000Z")])
            args = ["--source", "kalshi", "--slate", str(opening),
                    "--log", str(tmp / "opens.jsonl.gz"), "--once"]
            if closing_rows is not None:
                args += ["--closing-slate", str(slate(tmp / "playing.csv", closing_rows))]
            with mock.patch.object(kalshi, "board_quotes", side_effect=fake_board):
                self.assertEqual(watch.main(args), 0)
        return asked

    def test_without_a_closing_slate_only_the_opening_week_is_polled(self):
        asked = self.run_once(None)
        self.assertEqual(asked["games"], ["Indiana @ Nebraska"])

    def test_a_game_about_to_kick_off_joins_the_same_poll(self):
        soon = iso(datetime.now(timezone.utc) + timedelta(minutes=10))
        later = iso(datetime.now(timezone.utc) + timedelta(hours=3))
        started = iso(datetime.now(timezone.utc) - timedelta(hours=1))
        asked = self.run_once([("Maryland @ Nebraska", soon),
                               ("Arkansas @ Texas A&M", later),
                               ("Michigan @ Minnesota", started)])
        self.assertEqual(asked["games"], ["Indiana @ Nebraska", "Maryland @ Nebraska"])
        self.assertEqual(asked["kickoffs"]["Maryland @ Nebraska"], soon)
        self.assertEqual(asked["kickoffs"]["Indiana @ Nebraska"], "2026-10-10T04:00:00.000Z")

    def test_a_game_on_both_slates_keeps_the_opening_slates_kickoff(self):
        soon = iso(datetime.now(timezone.utc) + timedelta(minutes=10))
        asked = self.run_once([("Indiana @ Nebraska", soon)])
        self.assertEqual(asked["games"], ["Indiana @ Nebraska"])
        self.assertEqual(asked["kickoffs"]["Indiana @ Nebraska"], "2026-10-10T04:00:00.000Z")

    def test_a_missing_closing_slate_does_not_cost_the_opening_poll(self):
        asked = {}

        def fake_board(*, games, kickoffs, **_):
            asked["games"] = list(games)
            return []

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            opening = slate(tmp / "opening.csv", [("Indiana @ Nebraska", "2026-10-10T04:00:00.000Z")])
            with mock.patch.object(kalshi, "board_quotes", side_effect=fake_board):
                rc = watch.main(["--source", "kalshi", "--slate", str(opening),
                                 "--closing-slate", str(tmp / "absent.csv"),
                                 "--log", str(tmp / "opens.jsonl.gz"), "--once"])
        self.assertEqual(rc, 0)
        self.assertEqual(asked["games"], ["Indiana @ Nebraska"])


class TheCloseIsGradeable(unittest.TestCase):
    """What the Week 6 cohort was missing: a price inside fifteen minutes."""

    GAME, KICKOFF = "Maryland @ Nebraska", "2026-10-03T20:00:00.000Z"
    FREEZE = {"cohortId": "C", "rows": [{
        "game": GAME, "kickoff": KICKOFF, "openingHomeLine": -12.9,
        "openObservedAt": "2026-09-27T10:11:00+00:00",
        "venueOpenTime": "2026-09-27T10:06:00+00:00", "openLagSeconds": 300.0}]}

    def report(self, sightings):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "opens.jsonl.gz"
            with gzip.open(log, "wt", encoding="utf-8") as fh:
                for seen_at, line in sightings:
                    q = watch.Quote(game=self.GAME, book="kalshi", market="spread", line=line,
                                    price=None, seen_at=seen_at, commence_time=self.KICKOFF,
                                    event_ticker="KXNCAAFSPREAD-26OCT03MDNEB")
                    fh.write(json.dumps({"polled_at": seen_at, "quotes": [q.__dict__]}) + "\n")
            return week6_clv.build_close_report(
                freeze=self.FREEZE, log_path=log,
                now=datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc))["games"][0]

    def test_what_happened_on_3_october(self):
        row = self.report([("2026-10-03T08:49:39+00:00", -14.4)])
        self.assertFalse(row["gradeableClose"])
        self.assertEqual(row["exclusions"], ["CLOSE_NOT_FRESH_ENOUGH"])

    def test_a_poll_in_the_closing_window_is_a_close(self):
        row = self.report([("2026-10-03T08:49:39+00:00", -14.4),
                           ("2026-10-03T19:41:00+00:00", -14.0),
                           ("2026-10-03T19:59:02+00:00", -13.5)])
        self.assertTrue(row["gradeableClose"])
        self.assertEqual(row["closeAgeSeconds"], 58.0)
        self.assertEqual(row["observations"][-1]["derivedHomeLine"], -13.5)


class TheCount(unittest.TestCase):
    """`watch --close-coverage`: the number the Saturday check reports."""

    KICK = {"Maryland @ Nebraska": "2026-10-03T20:00:00.000Z",
            "Michigan @ Minnesota": "2026-10-03T16:00:00.000Z",
            "Arkansas @ Texas A&M": "2026-10-03T23:00:00.000Z",
            "Iowa State @ BYU": "2026-10-03T19:30:00.000Z"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.slate = slate(tmp / "playing.csv", list(self.KICK.items()))
        self.log = tmp / "opens.jsonl.gz"
        sightings = [
            # Fresh: 58 seconds before kickoff.
            ("Maryland @ Nebraska", "KXNCAAFSPREAD-26OCT03MDNEB", "2026-10-03T19:59:02+00:00", -13.5),
            # Stale: seven hours before kickoff, which is what 3 October looked like.
            ("Michigan @ Minnesota", "KXNCAAFSPREAD-26OCT03MICHMINN", "2026-10-03T08:49:39+00:00", 6.4),
            # After kickoff: an in-play number, never a close.
            ("Michigan @ Minnesota", "KXNCAAFSPREAD-26OCT03MICHMINN", "2026-10-03T16:05:00+00:00", 9.5),
            # Another game day's market, a minute before kickoff: not this game.
            ("Iowa State @ BYU", "KXNCAAFSPREAD-26OCT10BYUARIZ", "2026-10-03T19:29:00+00:00", 3.5),
        ]
        with gzip.open(self.log, "wt", encoding="utf-8") as fh:
            for game, event, seen_at, line in sightings:
                q = watch.Quote(game=game, book="kalshi", market="spread", line=line, price=None,
                                seen_at=seen_at, commence_time=self.KICK[game], event_ticker=event)
                fh.write(json.dumps({"polled_at": seen_at, "quotes": [q.__dict__]}) + "\n")

    def test_fresh_stale_and_missing_are_counted_apart(self):
        report = watch.close_coverage(self.log, self.slate,
                                      now=datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(report["slateGames"], 4)
        self.assertEqual(report["startedGames"], 3)      # the 23:00 game has not started
        self.assertEqual(report["gradeableCloses"], 1)
        self.assertEqual(report["staleCloses"], 1)
        self.assertEqual(report["noPriceBeforeKickoff"], 1)
        by_game = {r["game"]: r for r in report["games"]}
        self.assertEqual(by_game["Maryland @ Nebraska"]["closeAgeSeconds"], 58.0)
        self.assertEqual(by_game["Michigan @ Minnesota"]["closeSeenAt"], "2026-10-03T08:49:39+00:00")
        self.assertIsNone(by_game["Iowa State @ BYU"]["closeSeenAt"])

    def test_the_command_prints_the_count_and_polls_nothing(self):
        import contextlib
        import io

        out = io.StringIO()
        with mock.patch.object(kalshi, "board_quotes", side_effect=AssertionError("polled")):
            with contextlib.redirect_stdout(out):
                rc = watch.main(["--close-coverage", "--log", str(self.log),
                                 "--slate", str(self.slate), "--at", "2026-10-03T21:00:00Z"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(),
                         "closes: 1 of 3 started games have a price within 15 minutes of "
                         "kickoff (1 older, 1 with none; 4 games on the slate)")

    def test_it_needs_a_slate(self):
        self.assertEqual(watch.main(["--close-coverage", "--log", str(self.log)]), 2)


class TheWorkflowAsksForIt(unittest.TestCase):
    def test_the_playing_week_is_built_and_passed_to_every_poll(self):
        self.assertIn("--week current", LOOP)
        self.assertIn('--out "$RUNNER_TEMP/slate_playing.csv"', LOOP)
        self.assertIn("CLOSING=(--closing-slate capture-data/data/slate_playing.csv)", LOOP)
        self.assertIn('${CLOSING[@]+"${CLOSING[@]}"}', LOOP)

    def test_a_failed_build_of_it_cannot_stop_the_open_capture(self):
        start = LOOP.index("- name: Build the slate for the week being played")
        end = LOOP.index("- name: Poll continuously until the launch's slot ends")
        step = LOOP[start:end]
        self.assertNotIn("exit 1", step)
        self.assertNotIn("id:", step)       # nothing downstream is conditional on it
        self.assertIn("set +e", step)
        # Built aside and moved into place only when it succeeded.
        self.assertLess(step.index('"$RUNNER_TEMP/slate_playing.csv"'),
                        step.index("capture-data/data/slate_playing.csv"))

    def test_the_lock_still_reads_only_the_opening_week(self):
        """Closes are for the log. The prospective lock is a statement about
        the week whose lines are opening and takes that slate alone."""
        calls = [chunk.split("--revision")[0] for chunk in LOOP.split("cfb_edge.prospective_open")[1:]]
        self.assertGreaterEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--slate capture-data/data/slate_current.csv", call)
            self.assertNotIn("slate_playing", call)


if __name__ == "__main__":
    unittest.main()
