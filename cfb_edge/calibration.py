"""Measure whether the model's projected margins are the size reality is.

`projection.RATING_SCALE` exists because the rating solver compresses. Priors
pull every team toward average and `margin_cap` clips blowouts, so the fitted
gap between two teams comes out smaller than the gap that actually shows up on
the field. Left uncorrected that is a one-directional bias: a compressed model
makes every underdog look undervalued, and a strategy built on disagreeing
with the market would then bet underdogs almost exclusively while reading its
own scale error as signal.

The correction was measured once, by hand, and written into the source as
1.40. Nothing in this package could check it again, which is the problem this
module exists to fix: the constant is a property of the pipeline, and the
pipeline has changed several times since.

The measurement is a walk-forward regression. For each week, ratings are
solved on earlier weeks only and used to project that week's games; the
realised margins are then regressed on the projections. A slope of one means
the projections are the right size. A slope above one means they are still
compressed and the scale is too low.

Nothing here reads a betting line. This asks whether the model predicts
football, which is a different and much weaker question than whether it beats
a market, and it is the only one a schedule feed can answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from .projection import Matchup, project
from .ratings import Game, solve_ratings

# Weeks 1 to 3 project off almost no games, so their ratings are mostly prior
# and their errors say more about the prior than about the scale.
DEFAULT_FROM_WEEK = 4

# Below this many prior games the solver has not seen enough of the league for
# a walk-forward projection to mean anything.
MIN_HISTORY = 80


@dataclass(frozen=True)
class Calibration:
    """A fitted answer to "are the projections the right size?"."""

    games: int
    slope: float
    intercept: float
    stderr: float
    residual_sd: float

    @property
    def t_against_one(self) -> float:
        """How many standard errors the slope sits from correctly scaled."""
        return (self.slope - 1.0) / self.stderr if self.stderr else 0.0

    @property
    def compressed(self) -> bool:
        """Whether the projections are too small at conventional significance."""
        return self.t_against_one > 1.96

    def implied_scale(self, current: float) -> float:
        """The `RATING_SCALE` these projections say they should have had."""
        return current * self.slope

    def summary(self, *, current_scale: float | None = None) -> str:
        lines = [
            f"{self.games} games walk-forward",
            f"actual = {self.intercept:+.3f} + {self.slope:.4f} x projected",
            f"slope {self.slope:.4f} +/- {self.stderr:.4f}, "
            f"t against 1.0 = {self.t_against_one:+.2f}",
            f"residual sd {self.residual_sd:.2f} points",
        ]
        if current_scale is not None:
            lines.append(
                f"implied RATING_SCALE {self.implied_scale(current_scale):.3f} "
                f"against {current_scale:.2f} in use"
            )
        verdict = ("projections are too small; the scale is too low"
                   if self.compressed else
                   "no significant miscalibration")
        lines.append(verdict)
        return "\n".join(lines)


def _rows_to_records(rows: Iterable[Mapping[str, str]]) -> list[tuple]:
    """(week, home, away, neutral, home_points, away_points) for FBS regular play.

    A row that cannot be parsed is dropped rather than defaulted. A game with a
    guessed score would enter the regression as though it were observed.
    """
    out = []
    for r in rows:
        if (r.get("season_type") or "").strip().lower() != "regular":
            continue
        if ((r.get("home_division") or "").strip().lower() != "fbs"
                or (r.get("away_division") or "").strip().lower() != "fbs"):
            continue
        try:
            week = int(float(r["week"]))
            home_points = int(float(r["home_points"]))
            away_points = int(float(r["away_points"]))
        except (KeyError, TypeError, ValueError):
            continue
        home, away = (r.get("home_team") or "").strip(), (r.get("away_team") or "").strip()
        if not home or not away:
            continue
        neutral = str(r.get("neutral_site", "")).strip().upper() in ("TRUE", "1")
        out.append((week, home, away, neutral, home_points, away_points))
    return out


def walk_forward_points(
    rows: Iterable[Mapping[str, str]], *, from_week: int = DEFAULT_FROM_WEEK,
    min_history: int = MIN_HISTORY, rating_scale: float | None = None,
) -> list[tuple[float, float]]:
    """(projected home margin, actual home margin) for each gradeable game.

    Ratings for week N are solved on weeks before N only. Using the full
    season would let a team's own result set the rating that predicts it, and
    the slope would come back at one no matter how wrong the scale was.
    """
    records = _rows_to_records(rows)
    weeks = sorted({w for w, *_ in records})
    points: list[tuple[float, float]] = []
    for week in weeks:
        if week < from_week:
            continue
        history = [Game(h, a, hp, ap, n)
                   for w, h, a, n, hp, ap in records if w < week]
        if len(history) < min_history:
            continue
        model = solve_ratings(history)
        for w, home, away, neutral, hp, ap in records:
            if w != week:
                continue
            if not (model.is_known(home) and model.is_known(away)):
                continue
            kwargs = {} if rating_scale is None else {"rating_scale": rating_scale}
            projected = project(
                model, Matchup(home=home, away=away, neutral=neutral), **kwargs
            ).home_margin
            points.append((projected, float(hp - ap)))
    return points


def fit(points: Sequence[tuple[float, float]]) -> Calibration:
    """Least squares of actual margin on projected margin.

    Raises on fewer than three points rather than returning a slope with no
    standard error behind it.
    """
    n = len(points)
    if n < 3:
        raise ValueError(f"need at least 3 games to fit a slope, got {n}")
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx <= 0.0:
        raise ValueError("every projection is identical, so no slope exists")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in points]
    variance = sum(r * r for r in residuals) / (n - 2)
    return Calibration(
        games=n, slope=slope, intercept=intercept,
        stderr=(variance / sxx) ** 0.5, residual_sd=variance ** 0.5,
    )


def pool(fits: Sequence[Calibration]) -> Calibration:
    """Inverse-variance weighted average of per-season slopes.

    Seasons are pooled rather than concatenated because each season's ratings
    restart from priors, so their errors are not one sample. A season with a
    tighter standard error counts for more, which is what inverse-variance
    weighting means.
    """
    usable = [f for f in fits if f.stderr > 0.0]
    if not usable:
        raise ValueError("no fits with a usable standard error")
    weights = [1.0 / f.stderr ** 2 for f in usable]
    total = sum(weights)
    slope = sum(f.slope * w for f, w in zip(usable, weights)) / total
    games = sum(f.games for f in usable)
    return Calibration(
        games=games, slope=slope,
        intercept=sum(f.intercept * w for f, w in zip(usable, weights)) / total,
        stderr=(1.0 / total) ** 0.5,
        residual_sd=sum(f.residual_sd * w for f, w in zip(usable, weights)) / total,
    )


def measure(
    seasons: Sequence[int], *,
    fetch: Callable[[int], list[Mapping[str, str]]] | None = None,
    from_week: int = DEFAULT_FROM_WEEK,
) -> Calibration:
    """Fit each season separately, then pool. `fetch` is injectable for tests."""
    if fetch is None:
        from .slate import fetch_season as fetch  # noqa: N813

    fits = []
    for season in seasons:
        points = walk_forward_points(fetch(season), from_week=from_week)
        if len(points) >= 3:
            fits.append(fit(points))
    if not fits:
        raise ValueError(f"no season in {list(seasons)} produced enough games")
    return pool(fits)
