"""Validate the derived convergence defaults against the analytic equilibrium.

`fim.convergence.defaults` derives `convergence_window` and `max_generations`
from the model's relaxation time. These tests run the engine with exactly
the shipped derivation (no overridden multiples) and check that runs stop
near the analytic equilibrium D from the identity recursion, and that none
end at the cap. The measurement that chose the multiples is versioned in
`test/validation/convergence-defaults-evidence.json`
(`dev/bin/calibrate-convergence-defaults`).

Acceptance is on the mean over replicates, not on each replicate: a single
stochastic run scatters around equilibrium by its own sampling noise, which
no stopping rule can remove. Seeds are fixed, so a given commit always gives
the same result.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import statistics
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fim.convergence.defaults import derive_convergence_defaults
from fim.engine import fim
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams

ROOT = Path(__file__).resolve().parents[2]
D_ACCEPT = 0.05
MAX_MEAN_STOP_D = 0.15
"""Design section 7 item 6: the source scenario never stops above this D."""


def _calibrate_module() -> ModuleType:
    """Import the calibration script, which holds the regime table."""
    path = ROOT / "dev" / "bin" / "calibrate-convergence-defaults"
    spec = importlib.util.spec_from_loader(
        "calibrate_convergence_defaults",
        loader=importlib.machinery.SourceFileLoader(
            "calibrate_convergence_defaults", str(path)
        ),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Discard:
    """Trajectory store that keeps nothing."""

    def write_generation(self, *args: object, **kwargs: object) -> None:
        """Drop a generation."""

    def read(self, run_id: str) -> Iterator[Any]:
        """Yield nothing."""
        del run_id
        return iter(())

    def discard(self, run_id: str) -> None:
        """Drop a run."""
        del run_id


def _run(
    regime: str, *, replicates: int, loci: int, seed: int
) -> tuple[float, list[Any]]:
    """Run one regime with the shipped derived defaults.

    Returns the analytic equilibrium D and every replicate's report.
    """
    module = _calibrate_module()
    d, n, migration, mu_for = module.REGIMES[regime]
    mus = mu_for(loci)
    derived = derive_convergence_defaults(
        deme_sizes=[n] * d,
        migration=migration,
        mutation_rates=mus,
        precision=module.TOLERANCE,
    )
    expected = module.analytic_d(module._load_oracle(), d, n, migration, mus)
    m = migration if isinstance(migration, float) else tuple(map(tuple, migration))
    params = SimulationParams(
        gene_copies=n,
        m=m,
        mu=tuple(mus) if len(set(mus)) > 1 else mus[0],
        d=d,
        seed=seed,
        loci=tuple(LocusSpec(index + 1, 200) for index in range(loci)),
        initial_allele_count=2,
        convergence_statistic="D",
        precision=module.TOLERANCE,
        max_generations=derived.max_generations,
        n_replicates=replicates,
        stop_batch_early=False,
    )
    results = fim(n, m, params.mu, d, params=params, store=_Discard())
    results = results if isinstance(results, tuple) else (results,)
    return expected, [result.report for result in results]


@pytest.mark.slow
@pytest.mark.statistical
def test_golden_part_vi_stops_at_its_analytic_equilibrium() -> None:
    """Fast regime: the mean stop is within 0.05 of the analytic D."""
    expected, reports = _run("golden-part-vi", replicates=30, loci=8, seed=910000)
    assert all(report["converged"] for report in reports)
    mean_d = statistics.fmean(report["D"] for report in reports)
    assert abs(mean_d - expected) <= D_ACCEPT


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_low_never_stops_at_the_transient() -> None:
    """The source scenario runs to equilibrium, not to drift fixation.

    Regression for the reported failure: the old fixed defaults stopped
    this scenario after about 100 generations with D near 0.4. The
    equilibrium is D near 0.04; the transient state is above 0.3.
    """
    expected, reports = _run("dear-nolan-low", replicates=3, loci=6, seed=910000)
    assert all(report["converged"] for report in reports)
    assert all(report["generation"] > 10_000 for report in reports)
    assert (
        statistics.fmean(report["D"] for report in reports) < expected + D_ACCEPT + 0.1
    )
