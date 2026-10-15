"""How a run is carried out and what it keeps, apart from what it computes.

`Execution` and `Recording` never change a result: the same `RunSpec` gives
the same trajectory under any `Execution`, and `Recording` only chooses what
is written down. They are therefore passed beside the `RunSpec`, not inside
it, and take no part in a run's identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fim.config.defaults import (
    DEFAULT_AUTO_VECTOR_MAX_CAPACITY,
    DEFAULT_AUTO_VECTOR_MIN_D,
)
from fim.config.storage import TRAJECTORY_STRIDE
from fim.model.params import EngineBackend, Jit, TrajectoryRetentionChoice
from fim.spec.policy import Auto
from fim.spec.validation import check_choice, check_int, check_int_or_auto


@dataclass(frozen=True, slots=True, kw_only=True)
class Execution:
    """Which engine runs the simulation, and with how much parallelism.

    Args:
        backend: ``"lineal"`` (the reference), ``"generational"``,
            ``"generational-vector"``, or ``"auto"``, which chooses between
            the last two from the model. The default stays ``"lineal"``, the
            configuration file's own default; changing it is a separate
            decision.
        jit: ``"numba"`` compiles the generational backend's drift step.
        workers: Processes for the ``"lineal"`` backend's batches (`None`
            lets the front end choose).
        auto_vector_min_d: Smallest deme count for which ``"auto"`` chooses
            the vector backend.
        auto_vector_max_capacity: Largest per-locus allele capacity for
            which ``"auto"`` chooses the vector backend under finite alleles.
    """

    backend: EngineBackend = "lineal"
    jit: Jit = "off"
    workers: int | None = None
    auto_vector_min_d: int = DEFAULT_AUTO_VECTOR_MIN_D
    auto_vector_max_capacity: int = DEFAULT_AUTO_VECTOR_MAX_CAPACITY

    def __post_init__(self) -> None:
        """Check every value."""
        check_choice("execution.backend", self.backend, EngineBackend)
        check_choice("execution.jit", self.jit, Jit)
        if self.workers is not None:
            check_int("execution.workers", self.workers, minimum=1)
        check_int("execution.auto_vector_min_d", self.auto_vector_min_d, minimum=1)
        check_int(
            "execution.auto_vector_max_capacity",
            self.auto_vector_max_capacity,
            minimum=1,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Recording:
    """What a run writes down.

    Args:
        directory: Where to write the run (a path or its text), or `None` to
            keep it in memory.
        retention: ``"full"`` keeps every generation's frequencies;
            ``"thinned"`` keeps every generation early and then one in
            `stride`.
        stride: Keep one generation in this many once thinning starts.
        thinning_start: Generation at which thinning starts, or ``"auto"``.
    """

    directory: Path | str | None = None
    retention: TrajectoryRetentionChoice = "full"
    stride: int = TRAJECTORY_STRIDE
    thinning_start: int | Auto = "auto"

    def __post_init__(self) -> None:
        """Normalize the directory and check every value."""
        if self.directory is not None:
            object.__setattr__(self, "directory", Path(self.directory))
        check_choice("recording.retention", self.retention, TrajectoryRetentionChoice)
        check_int("recording.stride", self.stride, minimum=1)
        check_int_or_auto("recording.thinning_start", self.thinning_start, minimum=1)
