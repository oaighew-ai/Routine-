"""Offline regressions for one-shot failure reporting and fresh diagnostics."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb_edge import preflight, watch
from cfb_edge.clv import CAPTURED


def quote():
    return watch.Quote("Away @ Home", "fixture", "spread", -3.5, None,
                       "2026-09-01T00:00:00+00:00")


class TestOneShot(unittest.TestCase):
    def test_failed_once_cannot_export_old_observations(self):
        from cfb_edge.providers.oddsapi import OddsApiUnreachable
        for error in (OddsApiUnreachable("transport denied"),
                      ValueError("malformed payload")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as d:
                log, out = Path(d) / "old.gz", Path(d) / "old.csv"
                book = watch.OpeningBook.load(log)
                book.record([quote()])
                book.write_opens_csv(out)
                old_log, old_out = log.read_bytes(), out.read_bytes()
                text = io.StringIO()
                with patch("cfb_edge.providers.oddsapi.fetch_board",
                           side_effect=error), contextlib.redirect_stdout(text):
                    status = watch.main(["--source", "oddsapi", "--once",
                                         "--log", str(log), "--out", str(out)])
                self.assertNotEqual(status, 0)
                self.assertIn(str(error), text.getvalue())
                self.assertEqual(log.read_bytes(), old_log)
                self.assertEqual(out.read_bytes(), old_out)

    def test_continuous_capture_recovers_after_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            book = watch.OpeningBook.load(Path(d) / "poll.gz")
            with patch("cfb_edge.providers.oddsapi.fetch_board",
                       side_effect=[RuntimeError("offline"), [quote()]]) as fetch:
                self.assertEqual(watch.watch(book, fetch, max_polls=2,
                                            sleep=lambda _: None), 2)
            self.assertEqual(book.consensus_opens(), {"Away @ Home": -3.5})


class TestPreflight(unittest.TestCase):
    def fake_slate(self, argv):
        path = Path(argv[argv.index("--out") + 1])
        self.paths.append(path.parent)
        self.assertFalse((path.parent / "poll.jsonl.gz").exists())
        self.assertFalse((path.parent / "opens.csv").exists())
        path.write_text("game\nAway @ Home\n", encoding="utf-8")
        return 0

    def setUp(self):
        self.paths = []

    def run_preflight(self, quotes):
        text = io.StringIO()
        with patch("cfb_edge.preflight.slate.main", side_effect=self.fake_slate), \
             patch("cfb_edge.providers.oddsapi.fetch_board", return_value=quotes), \
             contextlib.redirect_stdout(text):
            result = preflight.main(["2026", "3", "oddsapi"])
        return result, text.getvalue()

    def test_success_then_empty_poll_does_not_reuse_prior_success(self):
        status, output = self.run_preflight([quote()])
        self.assertEqual(status, 0)
        self.assertIn("PASS: 1", output)
        status, output = self.run_preflight([])
        self.assertNotEqual(status, 0)
        self.assertNotIn("PASS:", output)
        self.assertIn("0 usable lines", output)
        self.assertNotEqual(self.paths[0], self.paths[1])
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_transport_failure_keeps_original_error(self):
        text = io.StringIO()
        with patch("cfb_edge.preflight.slate.main", side_effect=self.fake_slate), \
             patch("cfb_edge.providers.oddsapi.fetch_board",
                   side_effect=RuntimeError("fixture transport denied")), \
             contextlib.redirect_stdout(text):
            status = preflight.main(["2026", "3", "oddsapi"])
        self.assertNotEqual(status, 0)
        self.assertIn("fixture transport denied", text.getvalue())
        self.assertNotIn("PASS:", text.getvalue())

    def test_failed_slate_never_polls(self):
        with patch("cfb_edge.preflight.slate.main", return_value=2), \
             patch("cfb_edge.preflight.watch.main") as poll:
            self.assertNotEqual(preflight.main([]), 0)
            poll.assert_not_called()

    def test_invalid_output_cannot_pass_even_when_capture_returns_zero(self):
        header = "game,opening_line,source\n"
        cases = [None, "", header, "nonsense\none row\n",
                 header + f"Away @ Home,nan,{CAPTURED}\n",
                 header + "Away @ Home,-3.5,unverified\n",
                 header + f"Away @ Home,-3.5,{CAPTURED},extra\n",
                 header + "Away @ Home\n"]
        for content in cases:
            with self.subTest(content=content):
                def fake_watch(argv):
                    if content is not None:
                        Path(argv[argv.index("--out") + 1]).write_text(
                            content, encoding="utf-8")
                    return 0
                text = io.StringIO()
                with patch("cfb_edge.preflight.slate.main",
                           side_effect=self.fake_slate), \
                     patch("cfb_edge.preflight.watch.main",
                           side_effect=fake_watch), \
                     contextlib.redirect_stdout(text):
                    self.assertNotEqual(preflight.main([]), 0)
                self.assertNotIn("PASS:", text.getvalue())

    def test_csv_count_handles_quoted_game_names(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "opens.csv"
            path.write_text('game,opening_line,source\n'
                            f'"Away, State @ Home",-3.5,{CAPTURED}\n',
                            encoding="utf-8")
            self.assertEqual(preflight.count_lines(path), 1)
