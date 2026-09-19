"""Every reason code the engine can emit.

A reason code is the answer to "why is there no bet here", and it is the only
thing standing between an empty execution queue and an empty execution queue
that nobody can explain. `spec/EDGE_OS_v2_DERIVED.md` §8 is the table.
"""

from __future__ import annotations

# Stage A
NO_EVIDENCE = "NO_EVIDENCE"
AGGREGATE_ONLY = "AGGREGATE_ONLY"
CLV_KILL = "CLV_KILL"
LUCK_RISK = "LUCK_RISK"

# Stage B
FAMILY_DUP = "FAMILY_DUP"
OPPOSED = "OPPOSED"
NEG_EV = "NEG_EV"
DEVIG_SENSITIVE = "DEVIG_SENSITIVE"
DOMINATED = "DOMINATED"

# Sizing
SUB_MIN = "SUB_MIN"
CEILING_HIT = "CEILING_HIT"

# Integrity and freshness
STALE = "STALE"
UNMAPPED = "UNMAPPED"
DEVIG_IMPLAUSIBLE = "DEVIG_IMPLAUSIBLE"
BOOK_DISAGREE = "BOOK_DISAGREE"
LOOKAHEAD = "LOOKAHEAD"
UNGRADEABLE_PRICE = "UNGRADEABLE_PRICE"

# Shopping
# The venue is quoting a different number from the consensus, so its price is
# the price of a different bet. Comparing them needs a margin model to bridge
# the half-points, and importing a model into a market-relative measurement is
# how a model error comes back looking like an edge. The row is logged and does
# not bet.
LINE_MISMATCH = "LINE_MISMATCH"

ALL = frozenset({
    NO_EVIDENCE, AGGREGATE_ONLY, CLV_KILL, LUCK_RISK,
    FAMILY_DUP, OPPOSED, NEG_EV, DEVIG_SENSITIVE, DOMINATED,
    SUB_MIN, CEILING_HIT,
    STALE, UNMAPPED, DEVIG_IMPLAUSIBLE, BOOK_DISAGREE, LOOKAHEAD,
    UNGRADEABLE_PRICE, LINE_MISMATCH,
})

# Decisions
BET = "BET"
PASS = "PASS"
NO_BET = "NO_BET"
STALE_DECISION = "STALE"

DECISIONS = frozenset({BET, PASS, NO_BET, STALE_DECISION})
