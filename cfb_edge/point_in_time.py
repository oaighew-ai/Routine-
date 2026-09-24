"""Shared BR2 decision-time and immutable fixture identity contracts."""
from datetime import datetime, timezone
import hashlib
import json


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def known_at(value, decision, kickoff):
    source, decision, kickoff = instant(value), instant(decision), instant(kickoff)
    return bool(source and decision and kickoff and source <= decision < kickoff)


def source_time(manifest, kinds, decision, kickoff):
    """Every required source object must be timestamped and content addressed."""
    selected = [r for r in manifest if r.get('kind') in kinds]
    if set(kinds) - {r.get('kind') for r in selected}:
        return None
    for row in selected:
        sha = str(row.get('sha256', ''))
        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha) or not row.get('path'):
            return None
        if not known_at(row.get('retrievedAt'), decision, kickoff):
            return None
    return max(instant(r['retrievedAt']) for r in selected).isoformat() if selected else None


def norm(value):
    return ' '.join(str(value or '').lower().replace('&', 'and').split())


def resolve_game(slate_row, games):
    """Names bootstrap only a unique, kickoff-exact CFBD identity. No fuzzy match."""
    game = str(slate_row.get('game', ''))
    if '@' not in game or not instant(slate_row.get('kickoff')):
        return None
    away, home = (norm(x) for x in game.split('@', 1))
    candidates = [g for g in games
                  if norm(g.get('awayTeam')) == away and norm(g.get('homeTeam')) == home
                  and instant(g.get('startDate')) == instant(slate_row['kickoff'])]
    if len(candidates) != 1:
        return None
    row = candidates[0]
    game_id = row.get('id')
    if isinstance(game_id, bool) or not str(game_id or '').isdigit():
        return None
    canonical = 'cfbd:' + str(game_id)
    if slate_row.get('canonicalGameId') not in (None, '', canonical):
        return None
    return {'canonicalGameId': canonical, 'providerIds': {'cfbd': str(game_id)},
            'game': game, 'kickoff': instant(slate_row['kickoff']).isoformat(),
            'identityMethod': 'UNIQUE_CFBD_FIXTURE_AND_EXACT_KICKOFF'}


def register_provider_mapping(registry, canonical_id, provider, event_id, evidence):
    """Explicit crosswalk; no guessed cfbfastR, Action or exchange identifiers."""
    if not canonical_id.startswith('cfbd:') or not provider or not event_id or not evidence:
        raise ValueError('Verified mapping evidence required')
    key = f'{provider}:{event_id}'
    if key in registry and registry[key]['canonicalGameId'] != canonical_id:
        raise ValueError('Ambiguous provider identity')
    registry[key] = {'canonicalGameId': canonical_id, 'evidence': evidence}
    return registry


def unique_index(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return {k: group[0] for k, group in groups.items() if k and len(group) == 1}
