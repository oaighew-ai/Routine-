"""The EDGE OS decision engine.

One implementation per formula, in Python, reusing the measured pieces this
repository already has rather than restating them: `staking.full_kelly` for the
push-aware Kelly solve, `market` for de-vig, `distribution` for the margin and
total PMFs, `bootstrap` for clustered intervals, `kalshi_fees` for the fee curve.

`spec/EDGE_OS_v2_DERIVED.md` is the specification this implements, and
`DECISIONS.md` D1 says what that document is and is not.
"""

from .version import ENGINE_VERSION, spec_hash

__all__ = ["ENGINE_VERSION", "spec_hash"]
