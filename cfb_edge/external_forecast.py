"""Validate and import frozen S02 forecasts from the private authority.

This is the safe path when the forecast implementation remains external to this
repository. The importer checks identity, timestamps, hashes and the immutable
forecast schema, then appends rows without changing model authority.

It never computes a forecast and never turns an imported forecast into a pick.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .ledger import FORECAST, LedgerError, append_many, load, validate

CONTRACT = "CFB_EDGE_S02_FORECASTS_V1"
SCHEMA_VERSION = 1


class ForecastImportError(ValueError):
    pass


def _time(value: Any) -> datetime:
    if not value:
        raise ForecastImportError("missing timestamp")
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ForecastImportError(f"invalid timestamp {value!r}") from None
    if result.tzinfo is None:
        raise ForecastImportError(f"timestamp lacks timezone: {value!r}")
    return result.astimezone(timezone.utc)


def validate_envelope(
    envelope: Mapping[str, Any], authority: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if envelope.get("schemaVersion") != SCHEMA_VERSION:
        raise ForecastImportError("unsupported forecast export schemaVersion")
    if envelope.get("contract") != CONTRACT:
        raise ForecastImportError("forecast export contract mismatch")

    generated_at = _time(envelope.get("generatedAt"))
    model = envelope.get("model") or {}
    expected = {
        "modelId": authority.get("modelId"),
        "modelVersion": authority.get("modelVersion"),
        "modelSha256": authority.get("modelSha256"),
        "protocol": authority.get("protocol"),
    }
    for key, value in expected.items():
        if not value or model.get(key) != value:
            raise ForecastImportError(
                f"envelope {key} mismatch: expected {value!r}, got {model.get(key)!r}"
            )

    rows = list(envelope.get("forecasts") or [])
    seen_ids: set[str] = set()
    seen_markets: set[tuple[str, str, str | None]] = set()
    for i, row in enumerate(rows):
        try:
            validate(row, FORECAST)
        except Exception as exc:
            raise ForecastImportError(f"forecast {i}: {exc}") from None

        for key, value in expected.items():
            if row.get(key) != value:
                raise ForecastImportError(f"forecast {i}: {key} mismatch")

        if row.get("market") != "spreads":
            raise ForecastImportError(
                f"forecast {i}: S02 export only accepts market='spreads'"
            )
        if row.get("fairLine") is None and row.get("fairProbability") is None:
            raise ForecastImportError(
                f"forecast {i}: fairLine or fairProbability is required"
            )
        p = row.get("fairProbability")
        if p is not None and not 0.0 <= float(p) <= 1.0:
            raise ForecastImportError(
                f"forecast {i}: fairProbability must be in [0,1]"
            )
        if not str(row.get("featureSnapshotHash") or "").strip():
            raise ForecastImportError(f"forecast {i}: empty featureSnapshotHash")

        row_time = _time(row.get("generatedAt"))
        if row_time > generated_at:
            raise ForecastImportError(
                f"forecast {i}: row generated after its export envelope"
            )
        starts_at = row.get("startsAt")
        if starts_at and row_time >= _time(starts_at):
            raise ForecastImportError(
                f"forecast {i}: forecast was generated at or after kickoff"
            )

        row_id = str(row.get("id"))
        if row_id in seen_ids:
            raise ForecastImportError(f"duplicate forecast id {row_id!r}")
        seen_ids.add(row_id)

        key = (str(row.get("gameId")), str(row.get("market")), row.get("side"))
        if key in seen_markets:
            raise ForecastImportError(
                f"duplicate game/market/side forecast {key!r}"
            )
        seen_markets.add(key)

    return rows


def import_envelope(
    envelope: Mapping[str, Any],
    authority: Mapping[str, Any],
    *,
    out: Path | str,
    dry_run: bool = False,
) -> int:
    rows = validate_envelope(envelope, authority)
    existing = load(out, FORECAST)
    existing_ids = {str(row.get("id")) for row in existing}
    duplicates = sorted(str(row["id"]) for row in rows if str(row["id"]) in existing_ids)
    if duplicates:
        raise ForecastImportError(
            f"refusing already-imported forecast id(s): {duplicates}"
        )
    if dry_run:
        return len(rows)
    try:
        append_many(out, rows, FORECAST)
    except LedgerError as exc:
        raise ForecastImportError(str(exc)) from None
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfb_edge.external_forecast", description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--authority", default="config/delivery_authority.json")
    p.add_argument("--out", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    envelope = json.loads(Path(args.input).read_text(encoding="utf-8"))
    authority = json.loads(Path(args.authority).read_text(encoding="utf-8"))
    count = import_envelope(
        envelope, authority, out=args.out, dry_run=args.dry_run
    )
    mode = "validated" if args.dry_run else "imported"
    print(f"{mode} {count} frozen S02 forecast row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
