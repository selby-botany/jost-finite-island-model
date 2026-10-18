"""What a sweep is expected to cost, before it runs.

A point's cost is not known until it runs, because nobody knows in advance
how soon the convergence monitor will settle. What is known is a band: a
replicate runs for at least its burn-in and at most its generation cap, and a
batch runs at least its minimum replicate count and at most `n_replicates`.
`PointCost` and `SweepCost` carry those bands, honestly wide, in the unit the
engine actually spends: replicate-generations. Seconds and bytes come from
measuring the engine (a later layer builds them on these types), and a sweep
that has started refines the band from the points that have finished.

Nothing here runs a simulation or touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fim.model.params import SimulationParams

if TYPE_CHECKING:
    from fim.sweep import SweepPlan, SweepSpec


@dataclass(frozen=True, slots=True)
class Band:
    """A range a quantity is expected to fall in.

    Attributes:
        low: The least it can be.
        high: The most it can be.
        calibrated: A better single estimate, once points have finished and
            their actual figures are known; `None` before that.
    """

    low: float
    high: float
    calibrated: float | None = None

    def __post_init__(self) -> None:
        """Refuse a band whose ends are the wrong way round."""
        if self.low > self.high:
            raise ValueError(f"a band's low {self.low} is above its high {self.high}")

    def __add__(self, other: Band) -> Band:
        """Add two bands end to end (the calibrated estimate is dropped)."""
        return Band(self.low + other.low, self.high + other.high)

    def __mul__(self, other: Band) -> Band:
        """Multiply two bands of non-negative quantities end to end."""
        return Band(self.low * other.low, self.high * other.high)

    def scaled(self, factor: float) -> Band:
        """Return the band with both ends multiplied by `factor >= 0`."""
        return Band(self.low * factor, self.high * factor)

    def to_dict(self) -> dict[str, float | None]:
        """Return the band as plain values, for JSON."""
        return {"low": self.low, "high": self.high, "calibrated": self.calibrated}


@dataclass(frozen=True, slots=True)
class PointCost:
    """What one point is expected to cost.

    Attributes:
        run_id: The point's id.
        generations: Generations per replicate: its burn-in up to its cap.
        replicates: Replicates the point runs: the batch minimum up to
            `n_replicates` (a single run is exactly one).
        work: Replicate-generations for the whole point, the product of the
            two bands above.
    """

    run_id: str
    generations: Band
    replicates: Band
    work: Band


@dataclass(frozen=True, slots=True)
class SweepCost:
    """What a sweep is expected to cost.

    Attributes:
        points: Each point's cost, in grid order.
        work: Replicate-generations for the whole sweep.
        points_at_once: How many points run at the same time, or `None` if
            it is not yet known.
    """

    points: tuple[PointCost, ...]
    work: Band
    points_at_once: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the cost as plain values, for the page and for JSON."""
        return {
            "points": len(self.points),
            "replicates": self.replicate_band().to_dict(),
            "generations": self.generation_band().to_dict(),
            "work": self.work.to_dict(),
            "pointsAtOnce": self.points_at_once,
        }

    def generation_band(self) -> Band:
        """Return the narrowest band holding every point's generations."""
        return Band(
            min((p.generations.low for p in self.points), default=0.0),
            max((p.generations.high for p in self.points), default=0.0),
        )

    def replicate_band(self) -> Band:
        """Return the narrowest band holding every point's replicates."""
        return Band(
            min((p.replicates.low for p in self.points), default=0.0),
            max((p.replicates.high for p in self.points), default=0.0),
        )


def point_cost(run_id: str, params: SimulationParams) -> PointCost:
    """Return the static cost band of one resolved point.

    Args:
        run_id: The point's id.
        params: The point's resolved configuration, whose automatic burn-in
            and generation cap are already derived from its own migration,
            mutation and sizes.

    Returns:
        A replicate lasts at least `convergence_burn_in` (or the cap, if the
        cap is smaller) and at most `max_generations` generations. A batch
        that may stop early runs at least `replicate_minimum` replicates
        (never more than `n_replicates`); otherwise, and for a single run, it
        runs
        `n_replicates` in full.
    """
    # A cap set below the burn-in ends the run at the cap, so the least a
    # replicate can run is the smaller of the two.
    cap = float(params.max_generations)
    generations = Band(min(float(params.convergence_burn_in), cap), cap)
    most = float(params.n_replicates)
    adaptive = params.stop_batch_early and params.n_replicates > 1
    least = (
        float(min(params.replicate_minimum, params.n_replicates)) if adaptive else most
    )
    replicates = Band(least, most)
    return PointCost(run_id, generations, replicates, generations * replicates)


def static_cost(
    spec: SweepSpec, plan: SweepPlan, *, points_at_once: int | None = None
) -> SweepCost:
    """Return the static cost bands of a plan's points.

    Args:
        spec: The sweep specification (kept for the layers that add seconds
            and bytes, which depend on its base configuration).
        plan: The enumerated plan; only its valid points cost anything.
        points_at_once: How many points will run at the same time (see
            `fim.sweep_run.resolve_concurrency`), if known.

    Returns:
        Each point's cost and the sweep's total work.
    """
    del spec
    costs = tuple(
        point_cost(point.run_id, SimulationParams.from_mapping(point.params))
        for point in plan.points
    )
    work = Band(0.0, 0.0)
    for cost in costs:
        work = work + cost.work
    return SweepCost(costs, work, points_at_once)
