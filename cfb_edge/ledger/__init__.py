"""The ledger: append-only rows, and nothing that is not a fact.

Aggregates are never stored. Record, units, ROI, the equity curve and every CLV
statistic are recomputed from rows at load, every time (Law 2). A stored
aggregate is a number that can disagree with the rows it came from, and when it
does there is no way to tell which one is wrong.
"""

from .schema import (
    CANDIDATE, GRADE, PLACEMENT, RUN, SYSTEM, SchemaError, validate,
)
from .writer import LedgerError, append, append_many, load

__all__ = [
    "CANDIDATE", "GRADE", "PLACEMENT", "RUN", "SYSTEM",
    "SchemaError", "validate",
    "LedgerError", "append", "append_many", "load",
]
