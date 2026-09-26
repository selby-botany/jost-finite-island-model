"""The closed-form expected trajectory tracks the real engine, generation by generation.

`test/statistics/test_identity_recursion.py` proves the closed form equals
the identity recursion it solves. This proves the recursion is the engine's:
the mean of seeded engine runs must sit on the closed-form curve at every
checkpoint, starting each run's curve from that run's own first `H_S`/`H_T`
exactly as the Run card does.

The band is measured, not assumed: at each checkpoint the paired difference
(simulated minus closed form) is averaged over the replicates and must lie
within five standard errors of zero. Runs are seeded, so the outcome is a
pure function of the commit.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import numpy as np
import pytest
import yaml

from fim import cli
from fim.gui.trajectory_history import sampled_statistic_history
from fim.statistics import identities_from_heterozygosities, identity_recursion

pytestmark = [pytest.mark.slow, pytest.mark.statistical]

SIZE, MIGRATION, MUTATION, DEMES = 50, 0.02, 0.01, 4
GENERATIONS = 400
REPLICATES = 20
STANDARD_ERRORS = 5.0


def _run_history(tmp_path: Path, seed: int) -> tuple[list[int], dict[str, list[float]]]:
    """Run one seeded engine run and return its sampled statistic histories."""
    config = {
        "N": SIZE,
        "ploidy": "haploid",
        "d": DEMES,
        "m": MIGRATION,
        "mu": MUTATION,
        "seed": seed,
        "loci": [{"locus_id": index, "length": 100} for index in range(1, 9)],
        "convergence_window": GENERATIONS,
        "convergence_tolerance": 1e-9,
        "max_generations": GENERATIONS,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config_path = tmp_path / f"run-{seed}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output = tmp_path / f"out-{seed}"
    assert cli.main(["run", str(config_path), "-o", str(output), "--quiet"]) == 0
    history = sampled_statistic_history(output / "trajectory.jsonl")
    return history.generations, history.histories


@pytest.fixture(scope="module")
def engine_histories(
    tmp_path_factory: pytest.TempPathFactory,
) -> list[tuple[list[int], dict[str, list[float]]]]:
    """Run every seeded replicate once, shared by both statistics' tests."""
    root = tmp_path_factory.mktemp("identity-recursion")
    # `fim run` registers every run in the default Study; keep that out of
    # the developer's real results directory.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIM_RESULTS_DIRECTORY", str(root / "results"))
        return [_run_history(root, seed) for seed in range(1, REPLICATES + 1)]


@pytest.mark.parametrize("name", ["D", "G_ST"])
def test_engine_mean_sits_on_the_closed_form_curve(
    name: str, engine_histories: list[tuple[list[int], dict[str, list[float]]]]
) -> None:
    """Mean simulated `D`/`G_ST` matches the closed form at five checkpoints."""
    recursion = identity_recursion(SIZE, MIGRATION, MUTATION, DEMES)
    differences: list[list[float]] = []
    for generations, histories in engine_histories:
        within, between = identities_from_heterozygosities(
            histories["H_S"][0], histories["H_T"][0], DEMES
        )
        differences.append(
            [
                histories[name][index]
                - recursion.statistics_after(
                    generation - generations[0], within, between
                )[name]
                for index, generation in enumerate(generations)
            ]
        )

    generations = engine_histories[0][0]
    checkpoints = np.linspace(0, len(generations) - 1, 5, dtype=int)
    for checkpoint in checkpoints:
        column = [row[checkpoint] for row in differences]
        standard_error = statistics.stdev(column) / len(column) ** 0.5
        assert (
            abs(statistics.fmean(column)) <= STANDARD_ERRORS * standard_error + 1e-9
        ), (
            f"{name} at generation {generations[checkpoint]}: mean difference "
            f"{statistics.fmean(column):.4f}, standard error {standard_error:.4f}"
        )
