"""Published-value validation from the ordinary Dear-Nolan founder population."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim.engine import simulate_params
from fim.model.params import SimulationParams
from fim.persistence.store import TrajectoryRow

CONFIG = (
    Path(__file__).resolve().parents[2] / "doc/examples/dear-nolan-high/config.yaml"
)

# The example is an adaptive batch; this test is a fixed experiment on its
# population instead: one seeded run that evolves for 4500 generations and
# then averages the final 500, so the outcome is repeatable and a premature
# stop cannot pass.
FIXED_HORIZON = {
    "n_replicates": 1,
    "convergence_burn_in": 4500,
    "replicate_averaging_window": 500,
    "max_generations": 5000,
}


class _FounderStore:
    """Discard the trajectory except for generation zero's founder evidence."""

    def __init__(self) -> None:
        """Initialize the retained founder rows and completed generation."""
        self.founders: list[Mapping[str, Any]] = []
        self.last_generation = -1

    def discard(self, run_id: str) -> None:
        """Accept cleanup; no persisted run artifacts exist."""
        del run_id

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield no persisted trajectory rows."""
        del run_id
        return iter(())

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Retain founder evidence without accumulating the long trajectory."""
        del run_id, validate
        self.last_generation = generation
        if generation == 0:
            self.founders = list(rows)


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_high_converges_from_founder_population() -> None:
    """Founders approach both published high-migration differentiation values.

    This functional test uses the example's ordinary two-allele founder
    initialization, not the internal identity recursion or a prepared
    equilibrium. The predetermined published-value bounds are inherited
    from the earlier validation: |G_ST - 0.02| <= 0.005 and
    0.89 <= D <= 0.92. They are not the stationarity calibration bands.

    A fixed seed and 5000-generation horizon make the outcome repeatable.
    Both final values and the last 500 generations' means must satisfy
    the bounds, so a matching G_ST alone or a premature stop cannot pass.
    Runtime is characterized, never asserted against a wall-clock budget.
    """
    pytest.importorskip("numba")
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    params = SimulationParams.from_mapping({**config, **FIXED_HORIZON})
    assert params.initial_frequencies is None
    assert params.initial_allele_count == 2
    store = _FounderStore()
    result = simulate_params(params, store=store)
    assert not isinstance(result, tuple)
    assert result.manifest.engine_backend == "generational-vector"
    assert store.last_generation == result.final_state.generation == 5000
    assert len(store.founders) == 200
    assert len({row["allele_id"] for row in store.founders}) == 2
    assert len({row["deme"] for row in store.founders}) == 100

    report = result.report
    assert report["G_ST"] == pytest.approx(0.02, abs=0.005)
    assert 0.89 <= report["D"] <= 0.92
    windows = report["window_statistics"]
    for statistic in ("D", "G_ST"):
        assert windows[statistic]["window_start"] == 4500
        assert windows[statistic]["window"] == 501
    assert windows["G_ST"]["mean"] == pytest.approx(0.02, abs=0.005)
    assert 0.89 <= windows["D"]["mean"] <= 0.92
