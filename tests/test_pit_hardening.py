import copy
import json
from pathlib import Path
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from cfb_edge.point_in_time import instant, known_at, resolve_game, register_provider_mapping, digest
from cfb_edge.information_state import build_state
from cfb_edge.br2_context import build_context
from cfb_edge.feature_warehouse import build_snapshot
from cfb_edge.http_json import fetch_json
from cfb_edge.weather_replay import replay
from cfb_edge.br2_weather import capture
import hashlib
import tempfile

D = '2026-09-23T12:00:00+00:00'
K = '2026-09-26T20:15:00Z'
ROOT = Path(__file__).parent / 'fixtures/br2_real'


class PitTests(unittest.TestCase):
    def test_timezone_and_exact_boundary(self):
        self.assertIsNone(instant('2026-09-23T12:00:00'))
        self.assertTrue(known_at(D, D, K))
        self.assertFalse(known_at(K, K, K))
        self.assertFalse(known_at('2026-09-24T12:00:00Z', D, K))

    def test_identity_rejects_duplicates_reversals_and_reschedules(self):
        games = json.loads((ROOT/'week_games.json').read_text())
        slate = {'game': 'Vanderbilt @ Auburn', 'kickoff': K}
        self.assertEqual(resolve_game(slate, games)['canonicalGameId'], 'cfbd:401856698')
        self.assertIsNone(resolve_game(slate, games + games))
        self.assertIsNone(resolve_game({**slate, 'game': 'Auburn @ Vanderbilt'}, games))
        self.assertIsNone(resolve_game({**slate, 'kickoff': '2026-09-27T20:15:00Z'}, games))
        registry = register_provider_mapping({}, 'cfbd:1', 'action', '99', 'reviewed fixture')
        with self.assertRaises(ValueError):
            register_provider_mapping(registry, 'cfbd:2', 'action', '99', 'conflicting fixture')

    def real_context(self):
        load = lambda key: json.loads((ROOT/(key+'.json')).read_text())
        return build_context(slate=[{'game': 'Vanderbilt @ Auburn', 'kickoff': K}],
                             week_games=load('week_games'), teams=load('teams'), venues=load('venues'),
                             advanced=load('advanced_stats'), wepa=[], weather=None, qb_evidence=None,
                             source_manifest=load('provenance')['sources'], as_of=instant(D))

    def test_real_cfbd_semantics_and_decision_contract(self):
        row = self.real_context()['rows'][0]
        advanced = {r['team']: r for r in json.loads((ROOT/'advanced_stats.json').read_text())}
        net = lambda team: advanced[team]['offense']['lineYards'] - advanced[team]['defense']['lineYards']
        self.assertAlmostEqual(row['features']['linePlayDiff'], net('Auburn') - net('Vanderbilt'))
        self.assertLess(row['features']['travelMilesDiff'], -200)
        self.assertEqual(row['evidence']['travel']['homeMiles'], 0)
        self.assertFalse(row['evidence']['travel']['venue']['dome'])
        self.assertEqual(row['canonicalGameId'], 'cfbd:401856698')
        self.assertEqual(row['decisionTime'], D)

    def test_context_time_and_hash_tampering_fail_closed(self):
        for mutation in ('time', 'hash', 'kickoff'):
            context = self.real_context()
            row = context['rows'][0]
            if mutation == 'time': row['decisionTime'] = '2026-09-24T12:00:00Z'
            if mutation == 'hash': row['features']['linePlayDiff'] = 100
            if mutation == 'kickoff': row['kickoff'] = '2026-09-27T20:15:00Z'
            output = build_snapshot(slate=[{'game': 'Vanderbilt @ Auburn', 'kickoff': K}], plays=[],
                                    prior_games=[], as_of=instant(D), context=context)
            self.assertTrue(all(v is None for v in output['rows'][0]['features'].values()))

    def test_only_completed_prior_games_supply_plays(self):
        context = self.real_context()
        games = [{'id': 1, 'startDate': '2026-09-20T12:00:00Z', 'completed': True},
                 {'id': 2, 'startDate': '2026-09-24T12:00:00Z', 'completed': True},
                 {'id': 3, 'startDate': '2026-09-20T12:00:00Z', 'completed': False}]
        plays = [{'gameId': i, 'offense': t, 'ppa': v} for i,t,v in
                 [(1,'Auburn',.4),(1,'Vanderbilt',.1),(2,'Auburn',999),(3,'Vanderbilt',999)]]
        manifest = [{'kind': kind, 'path': 'raw.json', 'sha256': 'a'*64, 'retrievedAt': D} for kind in ('plays','games')]
        row = build_snapshot(slate=[{'game':'Vanderbilt @ Auburn','kickoff':K}], plays=plays,
                             prior_games=games, as_of=instant(D), context=context, source_manifest=manifest)['rows'][0]
        self.assertAlmostEqual(row['features']['ppaDiff'], .3)

    def test_information_state_no_close_leakage_or_fake_opens(self):
        q = {'canonicalGameId':'cfbd:1','book':'a','market':'spread','period':'full_game','side':'home',
             'sourceSha256':'a'*64,'observedAt':'2026-09-23T11:55:00Z','retrievedAt':'2026-09-23T11:55:01Z',
             'spread':-3,'decimalPrice':1.91,'provenance':'first_seen'}
        build = lambda quotes: build_state(canonical_game_id='cfbd:1',decision_time=D,kickoff=K,quotes=quotes)
        first = build([q])
        self.assertIsNone(first['features']['openToDecisionSpread'])
        self.assertEqual(first['features']['freshBookCount'],1)
        future = {**q,'spread':-20,'observedAt':K,'retrievedAt':K,'role':'closing'}
        self.assertEqual(build([q,future])['features'],first['features'])
        true_open={**q,'provenance':'true_open','openAuditGrade':True,'observedAt':'2026-09-23T11:00:00Z','retrievedAt':'2026-09-23T11:00:01Z','spread':-2}
        self.assertEqual(build([true_open,q])['features']['openToDecisionSpread'],-1)
        self.assertIsNone(build([true_open,q])['features']['verifiedNewsSinceOpen'])
        conflict={**q,'spread':-9}
        self.assertIsNone(build([q,conflict])['features']['freshBookCount'])

    def test_api_retries_transient_only_and_rejects_schema(self):
        calls=[]
        def forbidden(*a, **k):
            calls.append(1);raise HTTPError('https://example.test',401,'unauthorized',{},None)
        with self.assertRaises(HTTPError):fetch_json('x',expected_type=list,opener=forbidden,sleep=lambda _:None)
        self.assertEqual(len(calls),1)
        calls.clear()
        def transient(*a, **k):
            calls.append(1);raise HTTPError('https://example.test',503,'unavailable',{},None)
        with self.assertRaises(HTTPError):fetch_json('x',expected_type=list,opener=transient,sleep=lambda _:None)
        self.assertEqual(len(calls),3)

    def test_weather_replay_never_uses_realized_or_later_runs(self):
        raw=json.dumps({'hourly_units':{'wind_speed_10m':'mp/h'},
                        'hourly':{'time':['2026-09-26T20:00:00Z'],'wind_speed_10m':[12]}}).encode()
        manifest={'url':'https://single-runs-api.open-meteo.com/v1/forecast?run=2026-09-23T00:00:00Z',
                  'runInitializedAt':'2026-09-23T00:00:00Z','runAvailableAt':'2026-09-23T06:00:00Z',
                  'sha256':hashlib.sha256(raw).hexdigest(),'availabilityEvidenceSha256':hashlib.sha256(b'archived availability receipt').hexdigest()}
        self.assertEqual(replay(raw,manifest,decision_time=D,kickoff=K,availability_evidence=b'archived availability receipt')['windMph'],12)
        for change in ({'url':'https://archive-api.open-meteo.com/v1/archive'},
                       {'runAvailableAt':'2026-09-24T00:00:00Z'}, {'sha256':'bad'},
                       {'availabilityEvidenceSha256':None}):
            self.assertFalse(replay(raw,{**manifest,**change},decision_time=D,kickoff=K,availability_evidence=b'archived availability receipt')['auditGrade'])

    def test_postgame_dome_cannot_become_pregame_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            result=capture(slate=[{'game':'A @ H','kickoff':'2026-09-22T12:00:00Z'}],
                           games=[{'awayTeam':'A','homeTeam':'H','venueId':1}],
                           venues=[{'id':1,'latitude':40,'longitude':-80,'dome':True}],
                           out_raw_dir=root,retrieved_at=instant(D),
                           opener=lambda *a,**k: self.fail('Postgame weather must not be fetched'))
            self.assertFalse(result['rows'][0]['auditGrade'])
            self.assertIsNone(result['rows'][0]['windMph'])


if __name__ == '__main__': unittest.main()
