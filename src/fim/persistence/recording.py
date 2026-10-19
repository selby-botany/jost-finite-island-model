"""What a run kept, and whether it kept enough for what is asked of it.

A run can write down more or less of itself: every generation's allele
frequencies or only some (the trajectory log), the statistic history at full
precision or rounded, or only the statistics. The settings that choose this
are outside a run's identity, so two runs with one id can differ in what they
hold. This module reads what a run promised from its recorded parameters and
answers two questions without touching the files:

- `satisfies`: does an existing run hold at least what a new request asks for?
  (A sweep reuses a point only if it does.)
- `comparable_artifacts`: which artifacts of two runs must agree bit for bit?
  (Two runs of one seed agree on everything both kept the same way.)

The ladder, from most to least kept: `full`, then `thinned` and `tapered`
(different frames, neither a superset of the other unless equal), then
`statistics` (no trajectory log at all). `full` satisfies every level.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fim.config.storage import TRAJECTORY_KEEP_LAST, TRAJECTORY_STRIDE

LEVELS: Final = ("full", "thinned", "tapered", "statistics")
"""The trajectory retention levels, most kept first."""


@dataclass(frozen=True, slots=True)
class Recording:
    """What a run keeps of itself.

    Attributes:
        level: `full`, `thinned`, `tapered` or `statistics`.
        stride: With thinning, one generation in this many.
        thinning_start: With thinning, the first generation that may be
            skipped (0 when not recorded).
        keep_last: With tapering, the last generations kept in full.
        keep_burn_in: With tapering, whether burn-in frames are kept.
        history: `exact` or `compact`.
    """

    level: str = "full"
    stride: int = TRAJECTORY_STRIDE
    thinning_start: int = 0
    keep_last: int = TRAJECTORY_KEEP_LAST
    keep_burn_in: bool = False
    history: str = "exact"

    @classmethod
    def from_parameters(cls, parameters: Mapping[str, Any]) -> Recording:
        """Read a recording from a run's parameters (a manifest's or a point's).

        A setting that is absent has its default, which is how a run records
        that it kept everything.

        Args:
            parameters: A configuration mapping or a manifest's `parameters`.
        """
        start = parameters.get("trajectory_thinning_start", 0)
        return cls(
            level=str(parameters.get("trajectory_retention", "full")),
            stride=int(parameters.get("trajectory_stride", TRAJECTORY_STRIDE)),
            thinning_start=start if isinstance(start, int) else 0,
            keep_last=int(parameters.get("trajectory_keep_last", TRAJECTORY_KEEP_LAST)),
            keep_burn_in=bool(parameters.get("trajectory_keep_burn_in", False)),
            history=str(parameters.get("history_format", "exact")),
        )

    @property
    def keeps_trajectory(self) -> bool:
        """Whether the run has a trajectory log at all."""
        return self.level != "statistics"

    @property
    def absent_artifacts(self) -> frozenset[str]:
        """The artifact keys this run deliberately does not have."""
        if self.keeps_trajectory:
            return frozenset()
        return frozenset({"trajectory", "equilibrium_trajectory", "convergence"})

    def satisfies(self, wanted: Recording) -> bool:
        """Return whether a run with this recording serves a request for `wanted`.

        Args:
            wanted: What a new run would be asked to keep.

        Returns:
            `True` when everything `wanted` asks for is held here: the
            statistic history is at least as exact, and the frames kept are
            at least those `wanted` keeps.
        """
        if self.history == "compact" and wanted.history == "exact":
            return False
        if wanted.level == "statistics" or self.level == "full":
            return True
        if wanted.level == "full" or self.level != wanted.level:
            return False
        if self.level == "thinned":
            return (
                self.thinning_start == wanted.thinning_start
                and wanted.stride % self.stride == 0
            )
        return self.keep_last >= wanted.keep_last and (
            self.keep_burn_in or not wanted.keep_burn_in
        )

    def same_frames_as(self, other: Recording) -> bool:
        """Return whether two runs keep exactly the same trajectory frames."""
        if self.level != other.level:
            return False
        if self.level == "thinned":
            return (self.stride, self.thinning_start) == (
                other.stride,
                other.thinning_start,
            )
        if self.level == "tapered":
            return (self.keep_last, self.keep_burn_in) == (
                other.keep_last,
                other.keep_burn_in,
            )
        return True


def comparable_artifacts(older: Recording, newer: Recording) -> frozenset[str]:
    """Return the artifact keys two runs of one seed must hold identically.

    The report, the pairwise statistics and (for a batch) the summary do not
    depend on what was kept. The trajectory log does, so it is compared only
    when both kept the same frames; the statistic history only when both
    wrote it in the same format.

    Args:
        older: The existing run's recording.
        newer: The recomputed run's recording.
    """
    keys = {"report", "pairwise", "summary"}
    if older.same_frames_as(newer) and older.keeps_trajectory:
        keys.add("trajectory")
    if (
        older.history == newer.history
        and older.keeps_trajectory == newer.keeps_trajectory
    ):
        keys.add("convergence")
    return frozenset(keys)


def recording_of_run(directory: Path) -> Recording:
    """Read what a finished run kept from its manifest.

    Works for a single run and for a batch (whose manifest records the
    configuration too). A run whose manifest cannot be read is taken to have
    kept everything, which is what a run that predates these settings did.

    Args:
        directory: The run directory.
    """
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Recording()
    parameters = manifest.get("parameters") if isinstance(manifest, dict) else None
    return Recording.from_parameters(parameters if isinstance(parameters, dict) else {})


def run_satisfies(directory: Path, wanted: Mapping[str, Any]) -> bool:
    """Return whether a finished run holds what a configuration asks it to keep.

    Args:
        directory: A finished run.
        wanted: The configuration (a sweep point's mapping) that would run now.
    """
    return recording_of_run(directory).satisfies(Recording.from_parameters(wanted))
