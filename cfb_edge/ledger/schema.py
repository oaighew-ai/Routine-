"""Row schemas for `edge-os-ledger`, from BUILD_PROMPT §5.

Deliberately a small hand-written validator rather than a dependency: this
package is standard library only and CI fails the build on any third-party
import. The validator checks presence, type and a handful of domain rules. It
does not coerce. A row that needs coercing is a row whose producer is wrong, and
fixing it here would hide that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

NUMBER = (int, float)


class SchemaError(ValueError):
    """Raised when a row does not match its schema. Never warns."""


@dataclass(frozen=True)
class Field:
    name: str
    types: tuple[type, ...]
    required: bool = True
    nullable: bool = False
    check: Callable[[Any], bool] | None = None
    note: str = ""


@dataclass(frozen=True)
class Schema:
    name: str
    fields: tuple[Field, ...]

    @property
    def names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)


def _is_decision(v: Any) -> bool:
    return v in {"BET", "PASS", "NO_BET", "STALE"}


def _is_price_source(v: Any) -> bool:
    return v in {"capture", "fill", "late", "unverified"}


SYSTEM = Schema("systems", (
    Field("id", (str,)),
    Field("name", (str,)),
    Field("sport", (str,)),
    Field("market", (str,)),
    Field("family", (str,)),
    Field("origin", (str,), check=lambda v: v in {"provider", "own"}),
    Field("providerRecord", (dict,), nullable=True,
          note="owner-supplied only; never invented"),
    Field("shadowStart", (str,), nullable=True),
))

CANDIDATE = Schema("candidates", (
    Field("id", (str,)),
    Field("loggedAt", (str,)),
    Field("timeSeen", (str,), nullable=True),
    Field("phase", (str,)),
    Field("sport", (str,)),
    Field("eventId", (str,)),
    Field("startsAt", (str,), nullable=True),
    Field("market", (str,)),
    Field("side", (str,)),
    Field("line", NUMBER, nullable=True),
    Field("price", NUMBER),
    Field("venue", (str,)),
    Field("bookSet", (list,)),
    Field("priceSource", (str,), check=_is_price_source, note="D6"),
    Field("priceKind", (str,),
          check=lambda x: x in {"american", "decimal", "exchange_cents"}),
    Field("inputs", (dict,)),
    Field("outputs", (dict,)),
    Field("decision", (str,), check=_is_decision),
    Field("reasonCodes", (list,)),
    Field("engineVersion", (str,)),
    Field("specHash", (str,)),
    Field("sample", (bool,), required=False),
))

GRADE = Schema("grades", (
    Field("candidateId", (str,)),
    Field("closeRef", (str,)),
    Field("closeLine", NUMBER, nullable=True),
    Field("closePrice", NUMBER, nullable=True),
    Field("closeFairProb", NUMBER),
    Field("clvPct", NUMBER),
    Field("clvLagPct", NUMBER, nullable=True),
    Field("clvMethod", (str,), check=lambda v: v in {"exact", "pmf"}),
    Field("rawMove", NUMBER, nullable=True),
    Field("result", (str,), nullable=True,
          check=lambda v: v in {"win", "loss", "push", "void", None}),
    Field("units", NUMBER),
    Field("gradedAt", (str,)),
    Field("sample", (bool,), required=False),
))

PLACEMENT = Schema("placements", (
    Field("candidateId", (str,)),
    Field("placedAt", (str,)),
    Field("venue", (str,)),
    Field("price", NUMBER),
    Field("stake", NUMBER),
    Field("sample", (bool,), required=False),
))

RUN = Schema("runs", (
    Field("runId", (str,)),
    Field("mode", (str,), check=lambda v: v in {"capture", "scan", "grade"}),
    Field("startedAt", (str,)),
    Field("finishedAt", (str,), nullable=True),
    Field("status", (str,), check=lambda v: v in {"ok", "failed", "partial"}),
    Field("steps", (list,)),
    Field("reasonCounts", (dict,)),
    Field("sourceAges", (dict,)),
    Field("creditsRemaining", NUMBER, nullable=True),
    Field("sample", (bool,), required=False),
))

SCHEMAS = {s.name: s for s in (SYSTEM, CANDIDATE, GRADE, PLACEMENT, RUN)}


def validate(row: Mapping[str, Any], schema: Schema, *, strict: bool = True) -> None:
    """Raise `SchemaError` unless `row` matches `schema`.

    `strict` also rejects unknown keys. On by default: a typo'd field name in a
    producer is otherwise a field that silently never arrives, and the row looks
    complete right up until something reads the missing value.
    """
    if not isinstance(row, Mapping):
        raise SchemaError(f"{schema.name}: row is {type(row).__name__}, not a mapping")

    for f in schema.fields:
        if f.name not in row:
            if f.required:
                raise SchemaError(f"{schema.name}: missing required field {f.name!r}")
            continue
        value = row[f.name]
        if value is None:
            if not f.nullable:
                raise SchemaError(
                    f"{schema.name}.{f.name} is null, which this schema does not "
                    f"allow. Missing data is a reason code, not a null."
                )
            continue
        if isinstance(value, bool) and bool not in f.types:
            raise SchemaError(f"{schema.name}.{f.name}: bool where {f.types} expected")
        if not isinstance(value, f.types):
            raise SchemaError(
                f"{schema.name}.{f.name}: {type(value).__name__} where "
                f"{'/'.join(t.__name__ for t in f.types)} expected"
            )
        if f.check is not None and not f.check(value):
            raise SchemaError(f"{schema.name}.{f.name}: {value!r} is not allowed")

    if strict:
        unknown = set(row) - schema.names
        if unknown:
            raise SchemaError(
                f"{schema.name}: unknown field(s) {sorted(unknown)}. Add them to "
                f"the schema deliberately or drop them; a field the schema does "
                f"not know about is a field nothing will ever read."
            )


def validate_many(rows: Sequence[Mapping[str, Any]], schema: Schema, **kw) -> None:
    for i, row in enumerate(rows):
        try:
            validate(row, schema, **kw)
        except SchemaError as exc:
            raise SchemaError(f"row {i}: {exc}") from None
