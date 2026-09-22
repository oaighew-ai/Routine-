"""Runtime lock for the preregistered Week 5 S04_ES2 rule."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


class FreezeViolation(RuntimeError):
    pass


def _diff(actual: Mapping[str, Any], expected: Mapping[str, Any], prefix: str) -> list[str]:
    out: list[str] = []
    for key, wanted in expected.items():
        path = f"{prefix}.{key}"
        if key not in actual:
            out.append(f"{path}: missing")
            continue
        got = actual[key]
        if got != wanted:
            out.append(f"{path}: expected {wanted!r}, got {got!r}")
    return out


def assert_frozen(
    *,
    s04_config: Mapping[str, Any],
    market_quality_config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    violations = []
    violations.extend(_diff(s04_config, manifest["s04_es2"], "s04_es2"))
    violations.extend(_diff(market_quality_config, manifest["s03_m1"], "s03_m1"))
    if violations:
        detail = "\n".join(f"- {x}" for x in violations)
        raise FreezeViolation(
            "Week 5 frozen rule drifted. Refusing to run S04_ES2 until the "
            "configuration matches config/week5_freeze.json:\n" + detail
        )


def assert_frozen_files(
    s04_path: str | Path,
    market_quality_path: str | Path,
    manifest_path: str | Path = "config/week5_freeze.json",
) -> None:
    s04 = json.loads(Path(s04_path).read_text(encoding="utf-8"))
    quality = json.loads(Path(market_quality_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert_frozen(
        s04_config=s04,
        market_quality_config=quality,
        manifest=manifest,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--challenger", default="config/s04_es2.json")
    p.add_argument("--market-quality", default="config/s03_m1.json")
    p.add_argument("--manifest", default="config/week5_freeze.json")
    args = p.parse_args(argv)
    assert_frozen_files(args.challenger, args.market_quality, args.manifest)
    print("Week 5 freeze intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
