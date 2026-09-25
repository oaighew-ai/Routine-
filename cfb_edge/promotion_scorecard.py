"""Evidence-only promotion scorecard. A passing report never authorizes picks."""
import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from .point_in_time import instant, digest

CONTRACT = 'CFB_EDGE_PROMOTION_SCORECARD_V1'
POLICY_SHA256 = '1bb11b16fd03716418342d7cc63d82550043e1849b577b662c02cb113640cd77'


def sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stage(passed, reason):
    return {'status': 'PASS' if passed else 'BLOCKED', 'reason': reason}


def cluster_bounds(rows, policy):
    """Fixed-seed percentile bootstrap resampling entire weeks, not individual bets.

    This is an approximate fixed-look interval, not an anytime-valid guarantee.
    The registration must justify weeks as the dependence unit independently.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[row['weekId']].append(row)
    sums = [(len(g), sum(r['netReturn'] for r in g), sum(r['brierGain'] for r in g)) for g in groups.values()]
    rng = random.Random(policy['bootstrapSeed'])
    returns, gains = [], []
    for _ in range(policy['bootstrapReplicates']):
        sample = [sums[rng.randrange(len(sums))] for _ in sums]
        n = sum(g[0] for g in sample)
        returns.append(sum(g[1] for g in sample) / n)
        gains.append(sum(g[2] for g in sample) / n)
    index = max(0, math.floor(policy['perModelOneSidedAlpha'] * len(returns)) - 1)
    return {'netReturnLowerBound': sorted(returns)[index], 'brierGainLowerBound': sorted(gains)[index]}


def validate_rows(rows, registration, model_id, now):
    errors, accepted, seen = [], [], set()
    weeks = set(registration.get('holdoutWeeks') or [])
    for i, row in enumerate(rows):
        prefix = f'row {i}'
        if not isinstance(row, dict):
            errors.append(prefix + ': record must be an object')
            continue
        game = row.get('canonicalGameId')
        decision, kickoff, archived, settled = (instant(row.get(k)) for k in ('decisionTime','kickoff','archivedAt','settledAt'))
        start, end = instant(registration.get('holdoutStart')), instant(registration.get('holdoutEnd'))
        valid = (isinstance(game, str) and game.startswith('cfbd:') and game not in seen
                 and row.get('weekId') in weeks and decision and kickoff and archived and settled
                 and start and end and start <= decision < kickoff <= settled <= now
                 and archived <= decision and kickoff < end
                 and row.get('modelSha256') == registration.get('modelSha256')
                 and row.get('featureContractSha256') == registration.get('featureContractSha256')
                 and row.get('executionPolicySha256') == registration.get('executionPolicySha256')
                 and row.get('pitVerified') is True and row.get('executableQuoteVerified') is True
                 and sha(row.get('sourceSha256')) and sha(row.get('predictionSha256')))
        if model_id == 'S04_ES2':
            valid = valid and row.get('openProvenance') == 'true_open' and row.get('openAuditVerified') is True and number(row.get('openLagSeconds')) and 0 <= row['openLagSeconds'] <= 900
        p, market, odds, cost, result = (row.get(k) for k in ('probability','marketProbability','decimalOdds','costPerUnit','result'))
        valid = valid and all(number(v) for v in (p,market,odds,cost)) and 0 < p < 1 and 0 < market < 1 and odds > 1 and cost >= 0 and result in ('WIN','LOSS','PUSH')
        if not valid:
            errors.append(prefix + ': identity, PIT, execution, opening proof or settlement invalid')
            continue
        seen.add(game)
        # Probabilities are conditional on a non-push outcome. Pushes return stake.
        y = 1 if result == 'WIN' else 0
        gain = 0 if result == 'PUSH' else (market-y)**2 - (p-y)**2
        net = (odds-1 if result == 'WIN' else -1 if result == 'LOSS' else 0) - cost
        accepted.append({**row, 'netReturn':net, 'brierGain':gain})
    return accepted, errors


def evaluate(policy, model_id, evidence=None, *, now=None):
    if digest(policy) != POLICY_SHA256:
        raise ValueError("Frozen promotion policy changed; register a new protocol")
    now = now or datetime.now(timezone.utc)
    evidence = evidence or {}
    registration = evidence.get('registration') or {}
    stages = {}
    registration_errors = []
    if model_id not in policy['scope']:
        registration_errors.append('Unsupported model')
    for key in policy['requiredRegistration']:
        if key not in registration:
            registration_errors.append('Missing ' + key)
    for key in ('modelSha256','featureContractSha256','trainingProtocolSha256','executionPolicySha256'):
        if not sha(registration.get(key)):
            registration_errors.append('Invalid ' + key)
    registered, start, end = (instant(registration.get(k)) for k in ('registeredAt','holdoutStart','holdoutEnd'))
    if not (registered and start and end and instant(policy['frozenAt']) <= registered < start < end):
        registration_errors.append('Registration must precede the future holdout')
    if registration.get('policySha256') != digest(policy) or (type(registration.get('candidateAttempt')) is not int or registration.get('candidateAttempt') != 1) or registration.get('independentWeekClustersReviewed') is not True:
        registration_errors.append('Policy binding, attempt limit or dependence review missing')
    week_ids = registration.get('holdoutWeeks') or []
    if not isinstance(week_ids, list) or any(not isinstance(w, str) for w in week_ids) or len(set(week_ids)) != len(week_ids) or len(week_ids) < policy['minimumHoldoutWeeks']:
        registration_errors.append('Named future holdout weeks are incomplete')
    stages['registration'] = stage(not registration_errors, '; '.join(registration_errors) or 'Policy, artifact hashes and future evaluation window registered')

    # Pipeline artifacts alone cannot impersonate a preregistered evaluation ledger.
    rows = evidence.get('holdoutRows') or []
    accepted, row_errors = validate_rows(rows, registration, model_id, now) if not registration_errors else ([], [])
    coverage = evidence.get('universeAudit') or {}
    ledger_ok = (bool(accepted) and not row_errors and coverage.get('independentlyReviewed') is True
                 and sha(coverage.get('sha256')) and coverage.get('allDecisionsAccountedFor') is True
                 and coverage.get('eligibleExecutedGames') == len(accepted))
    stages['dataIntegrity'] = stage(ledger_ok, '; '.join(row_errors[:3]) or ('Full decision universe and all included games verified' if ledger_ok else 'Complete reviewed decision ledger and replayable evidence required; missing rows cannot be silently discarded'))

    for key in ('historicalReplay','walkForward'):
        report = evidence.get(key) or {}
        valid = (report.get('independentlyReviewed') is True and sha(report.get('sha256'))
                 and report.get('policySha256') == digest(policy)
                 and report.get('modelSha256') == registration.get('modelSha256')
                 and report.get('pointInTimeVerified') is True and report.get('allTrialsDisclosed') is True
                 and report.get('passesRegisteredProtocol') is True
                 and instant(report.get('reviewedAt')) and start and instant(report['reviewedAt']) < start)
        stages[key] = stage(bool(valid), 'Reviewed, policy-bound evidence required before the holdout begins' if not valid else 'Independent review recorded; historical evidence cannot replace prospective results')

    weeks = len({r['weekId'] for r in accepted})
    sample_ok = len(accepted) >= policy['minimumHoldoutGames'] and weeks >= policy['minimumHoldoutWeeks']
    final_look = bool(end and now >= end)
    stages['prospectiveHoldout'] = stage(bool(not registration_errors and ledger_ok and final_look and sample_ok),
        f'{len(accepted)}/{policy["minimumHoldoutGames"]} distinct games; {weeks}/{policy["minimumHoldoutWeeks"]} weeks. ' + ('Fixed evaluation date reached.' if final_look else 'Final evaluation date not reached or not registered.'))
    metrics = {'games':len(accepted),'weeks':weeks,'requiredGames':policy['minimumHoldoutGames'], 'requiredWeeks':policy['minimumHoldoutWeeks']}
    economic_ok = False
    if stages['prospectiveHoldout']['status'] == 'PASS':
        metrics.update(cluster_bounds(accepted, policy))
        metrics['meanNetReturn'] = sum(r['netReturn'] for r in accepted)/len(accepted)
        metrics['stressedNetReturnLowerBound'] = metrics['netReturnLowerBound'] - policy['additionalStressCostPerUnit']
        nonpush = [r for r in accepted if r['result'] != 'PUSH']
        bins = defaultdict(list)
        for r in nonpush:
            bins[min(9, int(r['probability']*10))].append(r)
        calibration = sum(abs(sum(r['probability'] - (r['result']=='WIN') for r in group)) for group in bins.values())/len(nonpush) if nonpush else 1
        metrics['calibrationError'] = calibration
        economic_ok = (metrics['stressedNetReturnLowerBound'] > 0
                       and metrics['brierGainLowerBound'] >= policy['minimumBrierImprovement']
                       and calibration <= policy['maximumCalibrationError'])
    stages['economics'] = stage(economic_ok, 'Positive stressed net-return lower bound, market forecast improvement and calibration required at the one final look')
    review_ready = all(s['status'] == 'PASS' for s in stages.values())
    return {'contract':CONTRACT,'generatedAt':now.isoformat(),'policyId':policy['policyId'],
            'policySha256':digest(policy),'modelId':model_id,'status':'REVIEW_ELIGIBLE' if review_ready else 'BLOCKED',
            'stages':stages,'metrics':metrics,'samplePlan':policy['samplePlan'],
            'deliveryEligible':False,'stakingEnabled':False,'actualStakeUnits':0,'promotionEffect':'NONE',
            'nextAction':'Independent promotion review; no automatic authority change' if review_ready else next(s['reason'] for s in stages.values() if s['status']=='BLOCKED')}


def build_scorecards(policy, evidence=None, *, now=None):
    return {'contract':CONTRACT,'generatedAt':(now or datetime.now(timezone.utc)).isoformat(),
            'models':[evaluate(policy, model, (evidence or {}).get(model), now=now) for model in policy['scope']],
            'decisionEffect':'NONE','stakingEnabled':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--policy',default='config/promotion_review_v1.json')
    p.add_argument('--evidence')
    p.add_argument('--out',required=True)
    args=p.parse_args(argv)
    policy=json.loads(Path(args.policy).read_text())
    evidence=json.loads(Path(args.evidence).read_text()) if args.evidence else {}
    report=build_scorecards(policy,evidence)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+'\n')
    print(' | '.join(r['modelId']+': '+r['status'] for r in report['models']))
    return 0


if __name__=='__main__':raise SystemExit(main())
