"""Engine version and spec hash, stamped on every candidate row.

A row that cannot be traced to the rules that produced it is a row you cannot
re-examine after changing the rules, which is most of the value of keeping it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ENGINE_VERSION = "derived-1"

SPEC_PATH = Path(__file__).resolve().parents[2] / "spec" / "EDGE_OS_v2_DERIVED.md"


def spec_hash(path: Path | str | None = None) -> str:
    """SHA-256 of the spec, or `"missing:<name>"` when it is not there.

    Deliberately does not raise. A missing spec is a fact worth recording on the
    row rather than a reason to lose the row.
    """
    p = Path(path) if path is not None else SPEC_PATH
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return f"missing:{p.name}"
