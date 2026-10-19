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

**Tapering** keeps the end of a run in full and thins the past further the
older it is. For a run that ends at generation `T` with `keep_last = K`, a
generation `g` is kept when `g = 0`, `g` is the last burn-in generation,
`T - g < K`, or, with `a = T - g` and `j = floor(log2(a / K))`, `g` is a
multiple of `2**j`. Multiples of a power of two are nested, so thinning to
stride `2**j` and later to `2**(j+1)` gives exactly what thinning once to
`2**(j+1)` gives: the rule is *path independent*, and a log tapered while a
run is going equals the one-shot taper of the full log. Burn-in frames (before
the last burn-in generation) are dropped unless `keep_burn_in` is set. The
rule needs the run's end, so unlike thinning it cannot decide a frame as it is
written; `fim.persistence.taper` applies it when the replicate finishes.
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
        tapered: Whether the run is tapered when it finishes (every frame is
            written first; see the module docstring).
        keep_last: With tapering, the last generations kept in full.
        keep_burn_in: With tapering, whether burn-in frames are kept.
    """

    thinned: bool = False
    start: int = 1
    stride: int = 1
    burn_in: int = 0
    tapered: bool = False
    keep_last: int = 1
    keep_burn_in: bool = False

    @classmethod
    def from_params(cls, params: SimulationParams) -> TrajectoryRetention:
        """Build the rule a configuration asks for.

        Args:
            params: The run's configuration; `trajectory_thinning_start` has
                already been resolved from `auto` by `SimulationParams`.

        Returns:
            A rule that keeps everything unless `trajectory_retention` is
            `"thinned"` or `"tapered"`.
        """
        if params.trajectory_retention == "tapered":
            return cls(
                tapered=True,
                burn_in=params.convergence_burn_in,
                keep_last=params.trajectory_keep_last,
                keep_burn_in=params.trajectory_keep_burn_in,
            )
        if params.trajectory_retention != "thinned":
            return cls()
        return cls(
            thinned=True,
            start=params.trajectory_thinning_start,
            stride=params.trajectory_stride,
            burn_in=params.convergence_burn_in,
        )

    @property
    def skips_generations(self) -> bool:
        """Return whether the finished log leaves out any generation."""
        return self.thinned or self.tapered

    def kept_generations(self, final: int) -> list[int]:
        """Return the generations a finished tapered log holds, in order.

        Args:
            final: The final generation of the run.

        Returns:
            Every generation `0..final` for a rule that does not taper, else
            the tapered set (`tapered_generations`).
        """
        if not self.tapered:
            return [g for g in range(final + 1) if self.keeps(g) or g == final]
        return tapered_generations(
            final, self.keep_last, self.burn_in, keep_burn_in=self.keep_burn_in
        )

    def keeps(self, generation: int) -> bool:
        """Return whether `generation` is written (the final one always is, too).

        Args:
            generation: A generation number.

        Returns:
            `True` unless thinning skips it. The caller also writes the stop
            generation, which this rule cannot know in advance.
        """
        if (
            self.tapered
            or not self.thinned
            or generation < self.start
            or generation == self.burn_in
        ):
            return True
        return (generation - self.start) % self.stride == 0

    def count_through(self, final: int) -> int:
        """Return how many generations a run that ended at `final` wrote.

        Args:
            final: The final generation, which is always written.

        Returns:
            The number of distinct kept generations among `0..final`.
        """
        if self.tapered:
            return len(self.kept_generations(final))
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


def tapered_generations(
    final: int, keep_last: int, burn_in: int = 0, *, keep_burn_in: bool = False
) -> list[int]:
    """Return the generations a tapered log of a run ending at `final` holds.

    The rule is in the module docstring. It is computed band by band
    (generations whose distance from the end lies in `[K * 2**j, K * 2**(j+1))`
    share the stride `2**j`), so the cost is the number of generations kept,
    not the length of the run.

    Args:
        final: The run's final generation.
        keep_last: How many of the last generations are kept in full (at
            least 1).
        burn_in: The last burn-in generation, or 0 if the run has none.
        keep_burn_in: Whether generations before `burn_in` are tapered like
            the rest instead of dropped.

    Returns:
        The kept generations, ascending. Generation 0, the last burn-in
        generation (if it is not after `final`) and `final` are always among
        them.

    Raises:
        ValueError: `final` is negative or `keep_last` is below 1.
    """
    if final < 0 or keep_last < 1:
        raise ValueError("a tapered log needs a final generation and keep_last >= 1")
    kept = set(range(max(0, final - keep_last + 1), final + 1))
    kept.update({0, final})
    if 0 < burn_in <= final:
        kept.add(burn_in)
    # Older bands: distance `a` from the end in [K * 2**j, K * 2**(j+1)).
    upper = final - keep_last
    band = 0
    while upper >= 0:
        lower = final - keep_last * 2 ** (band + 1)
        stride = 1 << band
        first = -(-max(lower + 1, 0) // stride) * stride
        kept.update(range(first, upper + 1, stride))
        upper = lower
        band += 1
    if not keep_burn_in:
        kept = {g for g in kept if g == 0 or g >= burn_in}
    return sorted(kept)
