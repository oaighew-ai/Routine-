"""Bounded GET retries. Authentication/schema failures are never retried or masked."""
import json
import time
import urllib.error
import urllib.request


def fetch_json(request, *, expected_type, opener=urllib.request.urlopen, sleep=time.sleep, attempts=3):
    for attempt in range(attempts):
        try:
            with opener(request, timeout=30) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, expected_type):
                raise ValueError('API response schema mismatch')
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            delay = exc.headers.get('Retry-After', '') if exc.headers else ''
            sleep(min(30, max(1, int(delay))) if delay.isdigit() else 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            sleep(2 ** attempt)
    raise ValueError('At least one attempt required')
