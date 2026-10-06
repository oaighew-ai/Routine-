"""Eastern time and portable time formatting, with no tz database required.

Two host-portability problems kept biting this package, both invisible on CI
because the runners are Ubuntu and both fatal on a Windows box:

**No tz database.** `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError`
on a host with no system tz data, which is every Windows machine without the
`tzdata` package. `ops_health` built it at module scope, so importing the module
failed outright. `card_render` guarded the lookup but then fell back to a
hardcoded `-4` hours, which is right in EDT and an hour wrong in EST, and bowl
and playoff numbers post in December and January.

Installing `tzdata` is not available: `tests.yml` fails the build on any
third-party import under `cfb_edge/`, and that check is not to be weakened.

**No-pad strftime directives.** `%-d` and `%-I` are a glibc extension. On Windows
`strftime` raises `ValueError: Invalid format string`, so `card_render.render`
could not produce a page at all.

The DST rule here is a transcription of US law since 2007, which Congress can
change and this file cannot know about. It is a fallback, not a replacement:
when the tz database is present it is used, because it tracks such changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - depends on the host's tz database
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    EASTERN = None

# Standard and daylight offsets for US Eastern.
_EST = timezone(timedelta(hours=-5))
_EDT = timezone(timedelta(hours=-4))


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    """The nth Sunday of a month, midnight UTC. Monday is 0 to `weekday()`."""
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    first += timedelta(days=(6 - first.weekday()) % 7)
    return first + timedelta(days=7 * (n - 1))


def eastern_for(utc: datetime):
    """The Eastern zone to convert one UTC instant into.

    Returns the tz-database zone when the host has one, so every caller gets the
    authoritative answer where it exists. Otherwise returns a fixed offset chosen
    by the post-2007 rule: DST from the second Sunday in March at 02:00 standard
    (07:00 UTC) to the first Sunday in November at 02:00 daylight (06:00 UTC).

    The instant decides the offset, so a caller must pass the moment it is
    converting rather than reusing one zone across a season.
    """
    if EASTERN is not None:
        return EASTERN
    if utc.tzinfo is None:
        raise ValueError("eastern_for needs an aware datetime")
    utc = utc.astimezone(timezone.utc)
    starts = _nth_sunday(utc.year, 3, 2) + timedelta(hours=7)
    ends = _nth_sunday(utc.year, 11, 1) + timedelta(hours=6)
    return _EDT if starts <= utc < ends else _EST


# The no-pad directives this package actually uses, plus the two neighbours most
# likely to be reached for next. Each maps to a function of the datetime.
_NO_PAD = {
    "%-d": lambda d: str(d.day),
    "%-I": lambda d: str((d.hour % 12) or 12),
    "%-H": lambda d: str(d.hour),
    "%-m": lambda d: str(d.month),
}


def strf(when: datetime, fmt: str) -> str:
    """`strftime` that accepts the no-pad directives Windows rejects.

    Each `%-X` is replaced with its already-formatted value before the format
    string reaches `strftime`. The substitutions are decimal digits, so they
    cannot introduce a new directive, and the rest of the string is left for
    `strftime` to handle as usual.
    """
    for token, render in _NO_PAD.items():
        if token in fmt:
            fmt = fmt.replace(token, render(when))
    return when.strftime(fmt)
