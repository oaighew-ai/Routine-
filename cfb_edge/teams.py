"""Reconciling team names across providers, without guessing.

Every data source spells college football teams differently. cfbfastR says
`Miami` and `Miami (OH)`; an odds feed is as likely to send `Miami (FL)`,
`Miami Hurricanes` or `Miami Florida`. Get that one wrong and you have bet a
different team in a different state.

So the rule here is that a name resolves exactly, through normalisation, or
through an explicit alias, and otherwise it does not resolve at all. There is no
fuzzy fallback and no nearest-match. Every plausible fuzzy scheme in this sport
maps `Mississippi` to `Mississippi State` or `Miami` to `Miami (OH)` at some
edit distance, and an unmatched game costs one skipped bet while a mismatched
one costs a wrong bet. Those are not the same mistake.

`match_games` reports what did not resolve so the gap is visible rather than
silent, which matters because a name that stops matching after a provider
changes its spelling would otherwise look like a quiet week.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Explicit aliases, keyed by normalised foreign name, valued by the cfbfastR
# name. Only entries that normalisation cannot reach on its own belong here.
ALIASES: dict[str, str] = {
    # Initialisms that other feeds spell out.
    "brigham young": "BYU",
    "southern methodist": "SMU",
    "texas christian": "TCU",
    "central florida": "UCF",
    "connecticut": "UConn",
    "alabama birmingham": "UAB",
    "alabama at birmingham": "UAB",
    "nevada las vegas": "UNLV",
    "texas el paso": "UTEP",
    "texas san antonio": "UTSA",
    "louisiana state": "LSU",
    "california los angeles": "UCLA",
    "southern california": "USC",
    "miami florida": "Miami",
    "miami fl": "Miami",
    "miami hurricanes": "Miami",
    "miami ohio": "Miami (OH)",
    "miami oh": "Miami (OH)",
    "miami redhawks": "Miami (OH)",
    # The Louisiana family, which is the easiest place to bet the wrong team.
    "louisiana lafayette": "Louisiana",
    "louisiana monroe": "UL Monroe",
    "ul lafayette": "Louisiana",
    "ulm": "UL Monroe",
    "ull": "Louisiana",
    # Mississippi is not Mississippi State and is not spelled Ole Miss anywhere
    # but here.
    "mississippi": "Ole Miss",
    "ole miss rebels": "Ole Miss",
    "southern mississippi": "Southern Miss",
    # Diacritics and punctuation that survive some feeds and not others.
    "hawaii": "Hawai'i",
    "san jose state": "San José State",
    "texas a and m": "Texas A&M",
    "texas am": "Texas A&M",
    # Directional and abbreviated forms.
    "north carolina state": "NC State",
    "nc state wolfpack": "NC State",
    "pitt": "Pittsburgh",
    "app state": "App State",
    "appalachian state": "App State",
    "florida intl": "Florida International",
    "fiu": "Florida International",
    "fau": "Florida Atlantic",
    "usf": "South Florida",
    "utsa roadrunners": "UTSA",
    "sam houston state": "Sam Houston",
    "middle tennessee state": "Middle Tennessee",
    "western kentucky hilltoppers": "Western Kentucky",
}

# Suffixes an odds feed may append that carry no identifying information.
_MASCOT_NOISE = re.compile(
    r"\b(university|univ|college|state university)\b", re.IGNORECASE
)


def normalize(name: str) -> str:
    """Reduce a team name to a comparable key.

    Strips accents, punctuation and case, expands the abbreviations that are
    unambiguous, and collapses whitespace. Deliberately does not strip mascots:
    `Miami Hurricanes` and `Miami RedHawks` differ only by mascot and are
    different schools, so mascot removal would create exactly the ambiguity this
    module exists to avoid. Known mascot forms go in ALIASES instead.
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = _MASCOT_NOISE.sub(" ", text)
    text = re.sub(r"\bSt\.?\b", "State", text, flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def resolve(name: str, known: set[str]) -> str | None:
    """Map a foreign team name onto a known one, or return None.

    Returns None rather than a best guess. An unresolved name costs a skipped
    bet; a wrong one costs a wrong bet.
    """
    if name in known:
        return name
    key = normalize(name)
    by_key = {normalize(k): k for k in known}
    if key in by_key:
        return by_key[key]
    aliased = ALIASES.get(key)
    if aliased and aliased in known:
        return aliased
    # A normalised alias target, for feeds that also differ in punctuation.
    if aliased:
        return by_key.get(normalize(aliased))
    return None


def resolve_game(game: str, known: set[str]) -> str | None:
    """Map an `Away @ Home` string onto known names, preserving the order."""
    if "@" not in game:
        return None
    away_raw, home_raw = (part.strip() for part in game.split("@", 1))
    away, home = resolve(away_raw, known), resolve(home_raw, known)
    if not away or not home:
        return None
    return f"{away} @ {home}"


@dataclass
class MatchReport:
    """What reconciled and what did not."""

    matched: dict[str, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        total = len(self.matched) + len(self.unmatched)
        return len(self.matched) / total if total else 0.0

    def summary(self) -> str:
        lines = [
            f"{len(self.matched)} of "
            f"{len(self.matched) + len(self.unmatched)} games reconciled "
            f"({self.rate:.0%})"
        ]
        if self.unmatched:
            lines.append("unmatched, add these to ALIASES in cfb_edge/teams.py:")
            lines += [f"    {g}" for g in self.unmatched[:20]]
            if len(self.unmatched) > 20:
                lines.append(f"    ... and {len(self.unmatched) - 20} more")
        return "\n".join(lines)


def match_games(foreign: list[str], known_games: list[str]) -> MatchReport:
    """Reconcile a provider's game strings against the ones we know.

    Reports rather than raises, because one unrecognised school should not stop
    a card, but a silently halved board should never look like a quiet week.
    """
    known_teams: set[str] = set()
    for g in known_games:
        if "@" in g:
            a, h = (p.strip() for p in g.split("@", 1))
            known_teams.update((a, h))
    known_set = set(known_games)

    report = MatchReport()
    for g in foreign:
        resolved = resolve_game(g, known_teams)
        if resolved and resolved in known_set:
            report.matched[g] = resolved
        else:
            report.unmatched.append(g)
    return report
