from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cfb_edge.active_market_state import (
    OPEN_CONTRACT,
    _odds_quotes,
    _prospective_open_evidence,
    recover_event_open,
)
from cfb_edge.information_state import build_state
from cfb_edge.providers.oddsapi import Pull


def market(ticker, team, strike, opened="2026-09-28T12:00:00Z"):
    return {
        "ticker": ticker,
        "event_ticker": "E",
        "floor_strike": strike,
        "yes_sub_title": f"{team} wins by over {strike} points",
        "open_time": opened,
    }


def candle(at, bid, ask):
    ts=int(datetime.fromisoformat(at.replace("Z","+00:00")).timestamp())
    return {
        "end_period_ts": ts,
        "yes_bid": {"close_dollars": str(bid)},
        "yes_ask": {"close_dollars": str(ask)},
    }


class ActiveMarketStateTests(unittest.TestCase):
    def test_candle_open_recovers_only_inside_frozen_tolerance(self):
        markets=[
            market("E-H2","H",2.5),
            market("E-H4","H",3.5),
        ]
        candles={
            "E-H2":[candle("2026-09-28T12:01:00Z",.58,.62)],
            "E-H4":[candle("2026-09-28T12:01:00Z",.38,.42)],
        }
        r=recover_event_open(
            markets,candles,home="H",away="A",
            retrieved_at="2026-09-29T00:00:00Z",
            source_hashes=["a"*64],
        )
        self.assertEqual(r["contract"],OPEN_CONTRACT)
        self.assertTrue(r["auditGrade"])
        self.assertTrue(r["openAuditGrade"])
        self.assertEqual(r["openLagSeconds"],60)
        self.assertAlmostEqual(r["openingLine"],-3.0)
        self.assertEqual(set(r["marketTickers"]),{"E-H2","E-H4"})

    def test_candle_open_after_900_seconds_fails_closed(self):
        markets=[
            market("E-H2","H",2.5),
            market("E-H4","H",3.5),
        ]
        candles={
            "E-H2":[candle("2026-09-28T12:16:00Z",.58,.62)],
            "E-H4":[candle("2026-09-28T12:16:00Z",.38,.42)],
        }
        r=recover_event_open(
            markets,candles,home="H",away="A",
            retrieved_at="2026-09-29T00:00:00Z",
            source_hashes=["a"*64],
        )
        self.assertFalse(r["auditGrade"])
        self.assertGreater(r["openLagSeconds"],900)
        self.assertEqual(r["reason"],"FIRST_VALID_LINE_OUTSIDE_TRUE_OPEN_TOLERANCE")

    def test_information_state_accepts_derived_line_without_fake_price(self):
        decision="2026-09-29T12:10:00Z"
        kickoff="2026-10-03T16:00:00Z"
        open_q={
            "canonicalGameId":"cfbd:1","book":"kalshi","market":"spread",
            "period":"full_game","side":"home","quoteKind":"derived_line",
            "sourceSha256":"a"*64,"observedAt":"2026-09-28T12:01:00Z",
            "retrievedAt":"2026-09-29T12:00:00Z","spread":-3.0,
            "provenance":"true_open","openAuditGrade":True,"role":"opening",
        }
        current={
            **open_q,
            "sourceSha256":"b"*64,
            "observedAt":"2026-09-29T12:09:00Z",
            "retrievedAt":"2026-09-29T12:09:05Z",
            "spread":-4.0,"provenance":"current","openAuditGrade":False,"role":"decision",
        }
        pinnacle={
            "canonicalGameId":"cfbd:1","book":"pinnacle","market":"spread",
            "period":"full_game","side":"home","quoteKind":"book_quote",
            "sourceSha256":"c"*64,"observedAt":"2026-09-29T12:08:30Z",
            "retrievedAt":"2026-09-29T12:09:05Z","spread":-3.5,
            "decimalPrice":1.91,"provenance":"current","openAuditGrade":False,
            "role":"decision",
        }
        draftkings={**pinnacle,"book":"draftkings","sourceSha256":"d"*64,"spread":-4.0}
        r=build_state(
            canonical_game_id="cfbd:1",decision_time=decision,kickoff=kickoff,
            quotes=[open_q,current,pinnacle,draftkings],
        )
        self.assertNotIn("FRESH_MARKET_EVIDENCE_MISSING",r["exclusions"])
        self.assertNotIn("AUDIT_GRADE_OPEN_MISSING",r["exclusions"])
        self.assertEqual(r["features"]["freshBookCount"],2)
        self.assertAlmostEqual(r["features"]["openToDecisionSpread"],-1.0)
        self.assertIsNotNone(r["features"]["crossBookDispersion"])
        self.assertIsNone(r["features"]["unchangedSpreadPriceMove"])
        self.assertIsNone(r["features"]["secondsSinceLastMove"])

    def test_odds_quotes_resolve_catalog_mascots_and_small_schedule_delta(self):
        pull=Pull(
            sport="americanfootball_ncaaf",
            fetched_at="2026-09-29T12:00:10Z",
            markets=("spreads",),
            book_set=("pinnacle","draftkings"),
            events=({
                "id":"x","commenceTime":"2026-10-03T16:30:00Z",
                "home":"Central Michigan Chippewas","away":"Akron Zips",
                "bookmakers":[
                    {"key":"pinnacle","markets":[{
                        "key":"spreads","lastUpdate":"2026-09-29T12:00:00Z",
                        "outcomes":[
                            {"name":"Central Michigan Chippewas","price":-108,"point":-4.5},
                            {"name":"Akron Zips","price":-112,"point":4.5},
                        ],
                    }]},
                    {"key":"draftkings","markets":[{
                        "key":"spreads","lastUpdate":"2026-09-29T11:59:30Z",
                        "outcomes":[
                            {"name":"Central Michigan Chippewas","price":-110,"point":-4.5},
                            {"name":"Akron Zips","price":-110,"point":4.5},
                        ],
                    }]},
                ],
            },),
            credits_remaining=400,credits_used=100,last_cost=1,
        )
        catalog=[
            {"school":"Akron","mascot":"Zips","abbreviation":"AKR","alternateNames":[]},
            {"school":"Central Michigan","mascot":"Chippewas","abbreviation":"CMU","alternateNames":[]},
        ]
        with tempfile.TemporaryDirectory() as td:
            quotes,_,health=_odds_quotes(
                identities={
                    "Akron @ Central Michigan":{
                        "game":"Akron @ Central Michigan","away":"Akron","home":"Central Michigan",
                        "kickoff":"2026-10-03T16:00:00+00:00",
                        "canonicalGameId":"cfbd:9",
                    }
                },
                known_teams={"Akron","Central Michigan"},
                team_catalog=catalog,
                raw_dir=Path(td)/"raw",
                bookmakers=("pinnacle","draftkings"),
                fetcher=lambda **_: pull,
            )
        self.assertEqual(len(quotes),2)
        self.assertEqual({q["book"] for q in quotes},{"pinnacle","draftkings"})
        self.assertEqual(health["matchedEvents"],1)
        self.assertEqual(health["unresolvedEvents"],[])
        self.assertEqual(health["kickoffMismatchEvents"],[])

    def test_catalog_mascot_uses_explicit_punctuation_alias(self):
        pull=Pull(
            sport="americanfootball_ncaaf",
            fetched_at="2026-09-29T12:00:10Z",
            markets=("spreads",),
            book_set=("pinnacle",),
            events=({
                "id":"x","commenceTime":"2026-10-04T03:59:00Z",
                "home":"Hawaii Rainbow Warriors","away":"San Jose State Spartans",
                "bookmakers":[{"key":"pinnacle","markets":[{
                    "key":"spreads","lastUpdate":"2026-09-29T12:00:00Z",
                    "outcomes":[
                        {"name":"Hawaii Rainbow Warriors","price":-110,"point":-3.5},
                        {"name":"San Jose State Spartans","price":-110,"point":3.5},
                    ],
                }]}],
            },),
            credits_remaining=199,credits_used=301,last_cost=1,
        )
        catalog=[
            {"school":"Hawai'i","mascot":"Rainbow Warriors","abbreviation":"HAW","alternateNames":[]},
            {"school":"San José State","mascot":"Spartans","abbreviation":"SJSU","alternateNames":[]},
        ]
        with tempfile.TemporaryDirectory() as td:
            quotes,_,health=_odds_quotes(
                identities={
                    "San José State @ Hawai'i":{
                        "game":"San José State @ Hawai'i","away":"San José State","home":"Hawai'i",
                        "kickoff":"2026-10-04T03:59:00+00:00",
                        "canonicalGameId":"cfbd:11",
                    }
                },
                known_teams={"San José State","Hawai'i"},
                team_catalog=catalog,
                raw_dir=Path(td)/"raw",
                bookmakers=("pinnacle",),
                fetcher=lambda **_: pull,
            )
        self.assertEqual(len(quotes),1)
        self.assertEqual(quotes[0]["canonicalGameId"],"cfbd:11")
        self.assertEqual(health["matchedEvents"],1)
        self.assertEqual(health["unresolvedEvents"],[])

    def test_odds_quotes_are_canonical_and_timestamped(self):
        pull=Pull(
            sport="americanfootball_ncaaf",
            fetched_at="2026-09-29T12:00:10Z",
            markets=("spreads",),
            book_set=("pinnacle",),
            events=({
                "id":"x","commenceTime":"2026-10-03T16:00:00Z",
                "home":"Hawaii","away":"Connecticut",
                "bookmakers":[{
                    "key":"pinnacle",
                    "markets":[{
                        "key":"spreads","lastUpdate":"2026-09-29T12:00:00Z",
                        "outcomes":[
                            {"name":"Hawaii","price":-110,"point":-2.5},
                            {"name":"Connecticut","price":-110,"point":2.5},
                        ],
                    }],
                }],
            },),
            credits_remaining=400,
            credits_used=100,
            last_cost=1,
        )
        with tempfile.TemporaryDirectory() as td:
            quotes,manifest,health=_odds_quotes(
                identities={
                    "UConn @ Hawai'i":{
                        "game":"UConn @ Hawai'i","away":"UConn","home":"Hawai'i",
                        "kickoff":"2026-10-03T16:00:00+00:00",
                        "canonicalGameId":"cfbd:7",
                    }
                },
                known_teams={"UConn","Hawai'i"},
                raw_dir=Path(td)/"raw",
                bookmakers=("pinnacle",),
                fetcher=lambda **_: pull,
            )
        self.assertEqual(len(quotes),1)
        q=quotes[0]
        self.assertEqual(q["canonicalGameId"],"cfbd:7")
        self.assertEqual(q["book"],"pinnacle")
        self.assertAlmostEqual(q["decimalPrice"],1+100/110)
        self.assertEqual(q["observedAt"],"2026-09-29T12:00:00Z")
        self.assertEqual(len(manifest[0]["sha256"]),64)
        self.assertEqual(health["lastCost"],1)


    def test_prospective_open_evidence_becomes_true_open_quote(self):
        identities={
            "Away @ Home":{
                "game":"Away @ Home","away":"Away","home":"Home",
                "kickoff":"2026-10-10T16:00:00+00:00",
                "canonicalGameId":"cfbd:42",
            }
        }
        status={
            "contract":"CFB_EDGE_PROSPECTIVE_OPEN_V1",
            "cohortId":"CFB_2026_PROVIDER_WEEK_6",
            "rows":[{
                "game":"Away @ Home",
                "kickoff":"2026-10-10T16:00:00Z",
                "state":"CAPTURED_TRUE_OPEN",
                "auditGrade":True,
                "historicalRecoveryUsed":False,
                "evidenceSha256":"a"*64,
                "evidencePath":"data/open-capture/x/evidence/a.json.gz",
                "eventTicker":"E",
                "marketTickers":["M1","M2"],
                "observedAt":"2026-10-04T12:01:00Z",
                "pollTime":"2026-10-04T12:01:02Z",
                "venueOpenTime":"2026-10-04T12:00:00Z",
                "openLagSeconds":60,
                "openingLine":-3.5,
            }],
        }
        quotes,rows,manifest=_prospective_open_evidence(
            status=status,identities=identities,
            expected_cohort_id="CFB_2026_PROVIDER_WEEK_6",
        )
        self.assertEqual(len(quotes),1)
        self.assertEqual(quotes[0]["canonicalGameId"],"cfbd:42")
        self.assertEqual(quotes[0]["provenance"],"true_open")
        self.assertEqual(quotes[0]["provenanceContract"],"CFB_EDGE_PROSPECTIVE_OPEN_V1")
        self.assertEqual(rows[0]["canonicalGameId"],"cfbd:42")
        self.assertEqual(manifest[0]["sha256"],"a"*64)

    def test_prospective_cohort_mismatch_fails_closed(self):
        identities={
            "Away @ Home":{
                "game":"Away @ Home","away":"Away","home":"Home",
                "kickoff":"2026-10-10T16:00:00+00:00",
                "canonicalGameId":"cfbd:42",
            }
        }
        status={
            "contract":"CFB_EDGE_PROSPECTIVE_OPEN_V1",
            "cohortId":"CFB_2026_PROVIDER_WEEK_5",
            "rows":[],
        }
        quotes,rows,manifest=_prospective_open_evidence(
            status=status,identities=identities,
            expected_cohort_id="CFB_2026_PROVIDER_WEEK_6",
        )
        self.assertEqual(quotes,[])
        self.assertEqual(manifest,[])
        self.assertEqual(rows[0]["reason"],"PROSPECTIVE_COHORT_MISMATCH")



if __name__=="__main__":
    unittest.main()
