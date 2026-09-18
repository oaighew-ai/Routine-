"""Appending to the ledger, and refusing everything that would corrupt it.

Three rules, all of them enforced here rather than by convention:

- A row is validated before it is written. An invalid row is never partially
  appended, because a truncated JSONL line poisons every later read of the file.
- A sample row is refused outright. `fixtures/sample.json` exists so the
  dashboard has something to render before there is real data, and the single
  worst outcome of that convenience is a fixture reaching the ledger and being
  counted as evidence.
- Rows are never rewritten. There is no update and no delete, deliberately.
  Parameter changes do not touch history (Law 1).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .schema import Schema, SchemaError, validate


class LedgerError(RuntimeError):
    """Raised when a write would corrupt the ledger rather than extend it."""


def _encode(row: Mapping[str, Any]) -> str:
    """One row, one line, deterministically.

    `sort_keys` is what makes acceptance check 11 meaningful: two runs of the
    same engine on the same inputs produce byte-identical lines, so a diff in
    the ledger is always a change in the data and never a change in dict
    ordering.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_sample(row: Mapping[str, Any]) -> None:
    if row.get("sample"):
        raise LedgerError(
            "refusing a row with sample=true. Fixture data belongs in "
            "fixtures/sample.json and must never reach the ledger, where it "
            "would be counted as evidence by every aggregate that reads rows."
        )


def append(path: Path | str, row: Mapping[str, Any], schema: Schema) -> int:
    """Append one validated row. Returns the file's new row count.

    Written with an explicit flush and fsync because the writer runs inside CI
    jobs that can be cancelled mid-step, and a half-written final line is the
    one failure mode that makes the whole file unreadable.
    """
    _reject_sample(row)
    validate(row, schema)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = _encode(row)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return count(p)


def append_many(
    path: Path | str, rows: Sequence[Mapping[str, Any]], schema: Schema
) -> int:
    """Append rows, all or none.

    Every row is validated before any is written, so a bad row late in a batch
    does not leave the earlier ones appended and the caller unsure how far it
    got.
    """
    for i, row in enumerate(rows):
        _reject_sample(row)
        try:
            validate(row, schema)
        except SchemaError as exc:
            raise LedgerError(f"row {i} of {len(rows)} rejected: {exc}") from None
    if not rows:
        return count(path)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_encode(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return count(p)


def load(path: Path | str, schema: Schema | None = None) -> list[dict]:
    """Read every row. A malformed line raises rather than being skipped.

    Skipping is the tempting behaviour and the wrong one: a ledger that quietly
    drops the rows it cannot parse reports a smaller, cleaner history than the
    one that exists, and the gate is computed on the difference.
    """
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{p}:{n} is not valid JSON: {exc}") from None
            if schema is not None:
                try:
                    validate(row, schema)
                except SchemaError as exc:
                    raise LedgerError(f"{p}:{n} {exc}") from None
            rows.append(row)
    return rows


def count(path: Path | str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    with open(p, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def write_json(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Write a whole-file JSON document atomically (run receipts, snapshots).

    Atomic because a watchdog reads these to decide whether automation is alive,
    and a truncated receipt would read as a corrupt run rather than a stale one.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def iter_rows(paths: Iterable[Path | str], schema: Schema | None = None):
    for path in paths:
        yield from load(path, schema)
