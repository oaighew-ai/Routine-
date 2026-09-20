"""Loading `config/edge_os.json`, with the defaults written down once.

Every tunable the engine reads lives here. A constant that is not in this file
and not measured in `MODEL.md` is a constant somebody typed into a function, and
Law 6 says those have to be visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "edge_os.json"


@dataclass(frozen=True)
class Config:
    """The engine's parameters, frozen for the life of a run."""

    raw: Mapping[str, Any] = field(default_factory=dict)

    # -- blend -------------------------------------------------------------
    @property
    def w0(self) -> float:
        """Weight on the market baseline. PRIOR, calibrated to check 5."""
        return float(self._blend.get("w0", 0.5))

    @property
    def n_half(self) -> float:
        """Pooled n at which a signal carries half its flag weight. PRIOR."""
        return float(self._blend.get("n_half", 50.0))

    @property
    def near_even_band(self) -> tuple[float, float]:
        lo, hi = self._blend.get("near_even_band", [0.4, 0.6])
        return float(lo), float(hi)

    @property
    def own_n_for_variable_price(self) -> int:
        return int(self._blend.get("own_n_for_variable_price", 30))

    # -- sizing ------------------------------------------------------------
    @property
    def kelly_fraction(self) -> float:
        return float(self._sizing.get("kelly_fraction", 0.25))

    @property
    def rho(self) -> float:
        return float(self._sizing.get("rho", 0.3))

    @property
    def round_to_units(self) -> float:
        return float(self._sizing.get("round_to_units", 0.05))

    @property
    def ceiling_units(self) -> float:
        return float(self._sizing.get("ceiling_units", 2.0))

    # -- clv ---------------------------------------------------------------
    @property
    def lag_minutes(self) -> int:
        return int(self._clv.get("lag_minutes", 30))

    @property
    def kill_min_own_grades(self) -> int:
        return int(self._clv.get("kill_min_own_grades", 60))

    # -- gate 2 ------------------------------------------------------------
    @property
    def gate2_min_rows(self) -> int:
        return int(self._gate2.get("min_rows", 60))

    @property
    def gate2_min_clusters(self) -> int:
        return int(self._gate2.get("min_clusters", 12))

    @property
    def gate2_alpha(self) -> float:
        return float(self._gate2.get("alpha", 0.05))

    # -- price grid --------------------------------------------------------
    @property
    def price_grid(self) -> tuple[int, int, int]:
        g = self.raw.get("price_grid", {})
        return (
            int(g.get("low_american", -400)),
            int(g.get("high_american", 400)),
            int(g.get("step", 1)),
        )

    # -- integrity and freshness ------------------------------------------
    @property
    def implied_sum_band(self) -> tuple[float, float]:
        lo, hi = self.raw.get("integrity", {}).get("implied_sum_band", [1.0, 1.12])
        return float(lo), float(hi)

    @property
    def book_disagree_points(self) -> float:
        return float(self.raw.get("integrity", {}).get("book_disagree_points", 3.0))

    def max_age_seconds(self, source: str) -> float | None:
        ages = self.raw.get("max_source_age_seconds", {})
        value = ages.get(source)
        return None if value is None else float(value)

    # -- money -------------------------------------------------------------
    @property
    def bankroll(self) -> float:
        return float(self.raw.get("bankroll_usd", 0.0))

    @property
    def unit(self) -> float:
        return self.bankroll * float(self.raw.get("unit_fraction", 0.01))

    @property
    def phase(self) -> str:
        return str(self.raw.get("phase", "SHADOW"))

    def venue(self, name: str) -> Mapping[str, Any]:
        venues = self.raw.get("venues", {})
        return venues.get(name) or venues.get("book_default", {})

    # -- internals ---------------------------------------------------------
    @property
    def _blend(self) -> Mapping[str, Any]:
        return self.raw.get("blend", {})

    @property
    def _sizing(self) -> Mapping[str, Any]:
        return self.raw.get("sizing", {})

    @property
    def _clv(self) -> Mapping[str, Any]:
        return self.raw.get("clv", {})

    @property
    def _gate2(self) -> Mapping[str, Any]:
        return self.raw.get("gate2", {})


def load(path: Path | str | None = None) -> Config:
    p = Path(path) if path is not None else CONFIG_PATH
    return Config(raw=json.loads(p.read_text(encoding="utf-8")))


def default() -> Config:
    """The shipped config. Loaded fresh each call so a test can edit the file."""
    return load()
