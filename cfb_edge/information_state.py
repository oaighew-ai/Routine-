"""PIT market information state, shadow only. Never consumes closing labels."""
import math
import statistics
from .point_in_time import instant, known_at, digest

FIELDS = ('openToDecisionSpread', 'unchangedSpreadPriceMove', 'crossBookDispersion',
          'freshBookCount', 'secondsSinceLastMove', 'verifiedNewsSinceOpen', 'secondsToKickoff')


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def build_state(*, canonical_game_id, decision_time, kickoff, quotes=(), news=(), max_age_seconds=900):
    decision, start = instant(decision_time), instant(kickoff)
    values = dict.fromkeys(FIELDS)
    exclusions = []
    if not canonical_game_id or not canonical_game_id.startswith('cfbd:') or not decision or not start or decision >= start:
        exclusions.append('DECISION_OR_IDENTITY_INVALID')
    else:
        values['secondsToKickoff'] = (start - decision).total_seconds()
        eligible = []
        for q in quotes:
            if (q.get('canonicalGameId') == canonical_game_id and q.get('market') == 'spread'
                    and q.get('side') == 'home' and q.get('period') == 'full_game'
                    and q.get('book') and q.get('sourceSha256') and q.get('role') != 'closing'
                    and known_at(q.get('observedAt'), decision_time, kickoff)
                    and known_at(q.get('retrievedAt'), decision_time, kickoff)
                    and instant(q['observedAt']) <= instant(q['retrievedAt'])
                    and number(q.get('spread')) and number(q.get('decimalPrice')) and q['decimalPrice'] > 1):
                eligible.append(q)
        # Conflicting quotes at the same book/time cannot be resolved by input order.
        groups = {}
        for q in eligible:
            groups.setdefault((q['book'], instant(q['observedAt'])), []).append(q)
        conflicts = {k[0] for k, rows in groups.items() if len({(r['spread'], r['decimalPrice']) for r in rows}) > 1}
        eligible = [q for q in eligible if q['book'] not in conflicts]
        latest = {}
        for q in sorted(eligible, key=lambda q: instant(q['observedAt'])):
            latest[q['book']] = q
        fresh = {book: q for book, q in latest.items() if (decision - instant(q['observedAt'])).total_seconds() <= max_age_seconds}
        if fresh:
            values['freshBookCount'] = len(fresh)
            if len(fresh) > 1:
                values['crossBookDispersion'] = statistics.pstdev(q['spread'] for q in fresh.values())
        else:
            exclusions.append('FRESH_MARKET_EVIDENCE_MISSING')
        movements, prices, move_times, opens = [], [], [], []
        for book, current in fresh.items():
            history = sorted((q for q in eligible if q['book'] == book), key=lambda q: instant(q['observedAt']))
            # TRUE_OPEN means independently validated provenance, not first seen.
            opening = [q for q in history if q.get('provenance') == 'true_open' and q.get('openAuditGrade') is True]
            if len(opening) == 1:
                first = opening[0]; opens.append(instant(first['observedAt']))
                movements.append(current['spread'] - first['spread'])
                if current['spread'] == first['spread']:
                    prices.append(1/current['decimalPrice'] - 1/first['decimalPrice'])
                segment = [q for q in history if instant(q['observedAt']) >= instant(first['observedAt'])]
                for previous, following in zip(segment, segment[1:]):
                    if (previous['spread'], previous['decimalPrice']) != (following['spread'], following['decimalPrice']):
                        move_times.append(instant(following['observedAt']))
        if movements:
            values['openToDecisionSpread'] = statistics.median(movements)
        else:
            exclusions.append('AUDIT_GRADE_OPEN_MISSING')
        if prices:
            values['unchangedSpreadPriceMove'] = statistics.median(prices)
        if move_times:
            values['secondsSinceLastMove'] = (decision - max(move_times)).total_seconds()
        # No news packet means unknown, never zero by absence.
        valid_news = [n for n in news if n.get('canonicalGameId') == canonical_game_id
                      and n.get('verified') is True and n.get('official') is True
                      and n.get('sourceSha256') and n.get('kind') in ('qb', 'injury')
                      and known_at(n.get('publishedAt'), decision_time, kickoff)
                      and known_at(n.get('retrievedAt'), decision_time, kickoff)]
        if opens and valid_news:
            values['verifiedNewsSinceOpen'] = len({n['sourceSha256'] for n in valid_news if instant(n['publishedAt']) >= min(opens)})
        if conflicts:
            exclusions.append('CONFLICTING_BOOK_QUOTES_EXCLUDED')
    row = {'contract': 'CFB_EDGE_INFORMATION_STATE_V1', 'canonicalGameId': canonical_game_id,
           'decisionTime': decision_time, 'kickoff': kickoff, 'features': values,
           'exclusions': exclusions, 'decisionEffect': 'NONE', 'stakingEnabled': False}
    row['rowSha256'] = digest(row)
    return row
