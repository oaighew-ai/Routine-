"""Replay a single archived forecast run, never reanalysis or stitched weather."""
import hashlib
import json
from urllib.parse import urlparse, parse_qs
from .point_in_time import instant, known_at
from .br2_weather import _nearest_hour


def replay(raw, manifest, *, decision_time, kickoff, availability_evidence=None):
    exclusions = []
    parsed = urlparse(manifest.get('url', ''))
    query = parse_qs(parsed.query)
    run = instant(manifest.get('runInitializedAt'))
    available = instant(manifest.get('runAvailableAt'))
    if parsed.scheme != 'https' or parsed.hostname != 'single-runs-api.open-meteo.com' or parsed.path != '/v1/forecast':
        exclusions.append('NOT_SINGLE_FORECAST_RUN')
    requested = instant((query.get('run') or [None])[0])
    if not run or requested != run or not available or available < run:
        exclusions.append('RUN_IDENTITY_OR_AVAILABILITY_INVALID')
    if not availability_evidence or hashlib.sha256(availability_evidence).hexdigest() != manifest.get('availabilityEvidenceSha256') or not known_at(manifest.get('runAvailableAt'), decision_time, kickoff):
        exclusions.append('RUN_NOT_PROVEN_AVAILABLE_AT_DECISION')
    if hashlib.sha256(raw).hexdigest() != manifest.get('sha256'):
        exclusions.append('RAW_HASH_MISMATCH')
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('hourly_units', {}).get('wind_speed_10m') not in ('mp/h', 'mph'):
        exclusions.append('WIND_UNIT_INVALID')
    point = _nearest_hour(payload, instant(kickoff)) if not exclusions else None
    wind = point.get('windMph') if point else None
    if wind is None or wind < 0:
        exclusions.append('FORECAST_POINT_MISSING')
    return {'contract': 'CFB_EDGE_WEATHER_REPLAY_V1', 'decisionTime': decision_time,
            'featureAsOf': manifest.get('runAvailableAt'), 'kickoff': kickoff,
            'windMph': wind if not exclusions else None, 'auditGrade': not exclusions,
            'exclusions': exclusions, 'decisionEffect': 'NONE'}
