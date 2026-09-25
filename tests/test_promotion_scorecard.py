import copy
import json
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from statistics import NormalDist
import unittest
from cfb_edge.promotion_scorecard import evaluate, validate_rows, cluster_bounds, build_scorecards
from cfb_edge.point_in_time import digest

POLICY=json.loads((Path(__file__).parents[1]/'config/promotion_review_v1.json').read_text())


def evidence():
    start=datetime(2027,1,1,tzinfo=timezone.utc)
    reg={'modelSha256':'a'*64,'featureContractSha256':'b'*64,'trainingProtocolSha256':'c'*64,
         'executionPolicySha256':'d'*64,'registeredAt':'2026-12-01T00:00:00Z',
         'holdoutStart':start.isoformat(),'holdoutEnd':'2028-01-01T00:00:00Z',
         'holdoutWeeks':[f'2027-W{i}' for i in range(26)],'candidateAttempt':1,
         'policySha256':digest(POLICY),'independentWeekClustersReviewed':True}
    rows=[]
    for i in range(4800):
        date=start+timedelta(weeks=i%26,days=1)
        rows.append({'canonicalGameId':f'cfbd:{i+1}','weekId':reg['holdoutWeeks'][i%26],
                     'decisionTime':date.isoformat(),'archivedAt':date.isoformat(),
                     'kickoff':(date+timedelta(hours=2)).isoformat(),
                     'settledAt':(date+timedelta(hours=6)).isoformat(),
                     'modelSha256':'a'*64,'featureContractSha256':'b'*64,'executionPolicySha256':'d'*64,
                     'pitVerified':True,'executableQuoteVerified':True,'sourceSha256':'e'*64,'predictionSha256':'f'*64,
                     'probability':.6,'marketProbability':.5,'decimalOdds':2.1,'costPerUnit':.01,
                     'result':'WIN' if i%10<6 else 'LOSS',
                     'openProvenance':'true_open','openAuditVerified':True,'openLagSeconds':60})
    reviewed={'independentlyReviewed':True,'sha256':'9'*64,'policySha256':digest(POLICY),
              'modelSha256':'a'*64,'pointInTimeVerified':True,'allTrialsDisclosed':True,
              'passesRegisteredProtocol':True,'reviewedAt':'2026-12-20T00:00:00Z'}
    return {'registration':reg,'holdoutRows':rows,'historicalReplay':reviewed,'walkForward':reviewed,
            'universeAudit':{'independentlyReviewed':True,'sha256':'8'*64,
                             'allDecisionsAccountedFor':True,'eligibleExecutedGames':len(rows)}}


class PromotionTests(unittest.TestCase):
    def test_sample_plan_and_no_evidence_are_explicit(self):
        n=math.ceil(((NormalDist().inv_cdf(.975)+NormalDist().inv_cdf(.8))/.05)**2*1.5)
        self.assertEqual(POLICY['minimumHoldoutGames'],n)
        for card in build_scorecards(POLICY)['models']:
            self.assertEqual(card['status'],'BLOCKED')
            self.assertEqual(card['metrics']['games'],0)
            self.assertFalse(card['deliveryEligible'])

    def test_all_pass_only_allows_review_never_delivery(self):
        result=evaluate(POLICY,'S04_ES2',evidence(),now=datetime(2028,1,2,tzinfo=timezone.utc))
        self.assertEqual(result['status'],'REVIEW_ELIGIBLE')
        self.assertFalse(result['stakingEnabled'])
        self.assertFalse(result['deliveryEligible'])
        self.assertEqual(result['actualStakeUnits'],0)

    def test_no_peeking_at_performance_before_final_date(self):
        result=evaluate(POLICY,'S04_BR2',evidence(),now=datetime(2027,12,1,tzinfo=timezone.utc))
        self.assertEqual(result['status'],'BLOCKED')
        self.assertNotIn('meanNetReturn',result['metrics'])

    def test_trial_changes_and_policy_drift_block_registration(self):
        for key,value in [('candidateAttempt',2),('policySha256','0'*64),('registeredAt','2027-02-01T00:00:00Z')]:
            ev=evidence();ev['registration'][key]=value
            self.assertEqual(evaluate(POLICY,'S04_BR2',ev)['stages']['registration']['status'],'BLOCKED')

    def test_duplicate_late_and_first_seen_cannot_enter_holdout(self):
        for mutate in [lambda r:r.update(openProvenance='first_seen'),
                       lambda r:r.update(archivedAt='2029-01-01T00:00:00Z'),
                       lambda r:r.update(decimalOdds=float('nan'))]:
            ev=evidence();row=ev['holdoutRows'][0];mutate(row)
            accepted,errors=validate_rows([row],ev['registration'],'S04_ES2',datetime(2028,1,2,tzinfo=timezone.utc))
            self.assertTrue(errors);self.assertFalse(accepted)
        ev=evidence();row=ev['holdoutRows'][0]
        self.assertTrue(validate_rows([row,row],ev['registration'],'S04_ES2',datetime(2028,1,2,tzinfo=timezone.utc))[1])

    def test_small_sample_and_bad_economics_fail(self):
        ev=evidence();ev['holdoutRows']=ev['holdoutRows'][:100]
        ev['universeAudit']['eligibleExecutedGames']=100
        self.assertEqual(evaluate(POLICY,'S04_BR2',ev,now=datetime(2028,1,2,tzinfo=timezone.utc))['status'],'BLOCKED')
        ev=evidence()
        for row in ev['holdoutRows']:row['costPerUnit']=.5
        self.assertEqual(evaluate(POLICY,'S04_BR2',ev,now=datetime(2028,1,2,tzinfo=timezone.utc))['stages']['economics']['status'],'BLOCKED')

    def test_summary_boolean_alone_cannot_pass(self):
        result=evaluate(POLICY,'S04_ES2',{'passed':True,'roi':1,'games':100000})
        self.assertEqual(result['status'],'BLOCKED')


if __name__=='__main__':unittest.main()
