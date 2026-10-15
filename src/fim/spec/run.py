"""The complete scientific specification of one run."""

from __future__ import annotations

from dataclasses import dataclass, field

from fim.config.expert import ExpertSettings
from fim.errors import ConfigurationError
from fim.spec.model import Model
from fim.spec.policy import Analysis, Replication, Stopping
from fim.spec.validation import check_int


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSpec:
    """Everything that determines a run's numbers.

    Two runs with equal specifications produce the same trajectory on the
    same platform, whatever `Execution` or `Recording` they use. The parts
    are grouped by concern so each can be read, documented, and extended on
    its own.

    Args:
        model: What is simulated.
        seed: Non-negative random seed. Replicate `i` derives its own seed
            from it.
        stopping: When one replicate counts as converged.
        replication: How many replicates to run.
        analysis: How the reported statistics are computed.
        expert: Tuning constants of the convergence and statistics
            machinery. The storage group of these (log block sizes and
            checkpoint cadence) does not affect results and takes no part in
            a run's identity.
    """

    model: Model
    seed: int
    stopping: Stopping = field(default_factory=Stopping)
    replication: Replication = field(default_factory=Replication)
    analysis: Analysis = field(default_factory=Analysis)
    expert: ExpertSettings = field(default_factory=ExpertSettings)

    def __post_init__(self) -> None:
        """Check the seed and the type of each group."""
        check_int("seed", self.seed, minimum=0)
        for path, value, kind in (
            ("model", self.model, Model),
            ("stopping", self.stopping, Stopping),
            ("replication", self.replication, Replication),
            ("analysis", self.analysis, Analysis),
            ("expert", self.expert, ExpertSettings),
        ):
            if not isinstance(value, kind):
                raise ConfigurationError(f"{path} must be a {kind.__name__}")
