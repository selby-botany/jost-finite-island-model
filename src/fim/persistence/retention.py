"""Which generations of a trajectory are written to disk.

A long run's trajectory file is dominated by per-generation allele
frequencies (about 27 KB a generation for the largest examples). The app's
purpose is trajectories, so the default keeps every generation; a user can
thin a run instead. What is kept is a function of the generation number alone
(design 6.13): never of disk space or time, so a rerun keeps the same frames
and the replicates of a batch align.

Kept under thinning: generation 0, every generation before the thinning
start, every `stride`-th generation from the start on (counted from the
start), the last burn-in generation, and the final generation. The per-
generation statistics (`convergence.jsonl`) are never thinned: the monitor,
the trajectory graph and every number in the report come from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fim.model.params import SimulationParams


@dataclass(frozen=True, slots=True)
class TrajectoryRetention:
    """The rule for which generations a run writes.

    Attributes:
        thinned: Whether any generation is skipped.
        start: First generation that thinning may skip (at least 1).
        stride: From `start` on, one generation in this many is kept.
        burn_in: The last burn-in generation, always kept; 0 when the run has
            no fixed burn-in.
    """

    thinned: bool = False
    start: int = 1
    stride: int = 1
    burn_in: int = 0

    @classmethod
    def from_params(cls, params: SimulationParams) -> TrajectoryRetention:
        """Build the rule a configuration asks for.

        Args:
            params: The run's configuration; `trajectory_thinning_start` has
                already been resolved from `auto` by `SimulationParams`.

        Returns:
            A rule that keeps everything unless `trajectory_retention` is
            `"thinned"`.
        """
        if params.trajectory_retention != "thinned":
            return cls()
        return cls(
            thinned=True,
            start=params.trajectory_thinning_start,
            stride=params.trajectory_stride,
            burn_in=params.convergence_burn_in,
        )

    def keeps(self, generation: int) -> bool:
        """Return whether `generation` is written (the final one always is, too).

        Args:
            generation: A generation number.

        Returns:
            `True` unless thinning skips it. The caller also writes the stop
            generation, which this rule cannot know in advance.
        """
        if not self.thinned or generation < self.start or generation == self.burn_in:
            return True
        return (generation - self.start) % self.stride == 0

    def count_through(self, final: int) -> int:
        """Return how many generations a run that ended at `final` wrote.

        Args:
            final: The final generation, which is always written.

        Returns:
            The number of distinct kept generations among `0..final`.
        """
        if not self.thinned or final < self.start:
            return final + 1
        head = self.start
        strided = (final - self.start) // self.stride + 1
        extras = {
            generation
            for generation in (self.burn_in, final)
            if self.start <= generation <= final
            and (generation - self.start) % self.stride != 0
        }
        return head + strided + len(extras)
