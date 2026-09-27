"""The expected-trajectory payload, checked against every shipped configuration.

The closed-form trajectory is exact for one model (infinite alleles,
expected migrant fractions, ratio-of-means loci) and
wrong for others, so it must be offered for exactly the configurations it
is right for. This walks every configuration the project ships or
documents (the fourteen worked examples, `doc/examples/`, and the
`fim init` starter) and pins, per configuration, whether the payload is
the two-variable form, the sampled matrix form, or absent. A new example,
or a change to the model options that decide this, fails here until the
table is updated on purpose.

The slow statistical half (`test_engine_agrees_with_the_payload_for_every_
example`) then runs each configuration that gets a curve against the real
engine, evaluating the payload the way the page does.
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from fim import cli
from fim.gui import app as app_module
from fim.gui.presets import list_presets
from fim.gui.trajectory_history import sampled_statistic_history
from fim.model.params import SimulationParams
from fim.statistics import (
    IDENTITY_STATISTIC_NAMES,
    IdentityRecursion,
    identities_from_heterozygosities,
)

ROOT = Path(__file__).resolve().parents[2]
WEBUI = ROOT / "src" / "fim" / "gui" / "webui"

ISLAND = "island"
SAMPLED = "sampled"
NONE = None

# What each shipped configuration gets, and why. Keys are worked-example
# ids, `doc/examples/<name>`, or `starter`.
EXPECTED: dict[str, str | None] = {
    # Unequal deme sizes and an explicit matrix: the full identity matrix.
    "unequal-island-sizes-with-a-migration-hub": SAMPLED,
    "stepping-stone-spatial-migration": SAMPLED,
    # No migration and no mutation: nothing to relax toward.
    "literature-distance-statistics-from-an-explicit-founder-split": NONE,
    # Equal sizes, scalar m: the two-variable form, from the run's own start.
    "equilibrium-split-founding": ISLAND,
    # migrant_sampling: stochastic
    "stochastic-migrant-counts": NONE,
    # mutation_model: finite_alleles
    "finite-length-alleles-the-k-allele-model": NONE,
    # Infinite alleles, equal sizes, scalar m: an ordinary island.
    "wright-takahata-finite-deme-correction": ISLAND,
    # A 20-deme ring: within the 24-deme matrix limit.
    "kimura-weiss-isolation-by-distance": SAMPLED,
    # Per-locus mutation rates.
    "per-base-mutation-rate-across-unequal-locus-lengths": NONE,
    "several-convergence-statistics": ISLAND,
    "within-run-sigma-band": ISLAND,
    "an-adaptive-replicate-batch-with-a-confidence-interval": ISLAND,
    # mutation_model: finite_alleles
    "a-large-d-batch-under-generational-vector": NONE,
    "a-long-locus-batch-under-the-generational-engine": NONE,
    "doc/examples/dear-nolan-low": ISLAND,
    "doc/examples/golden-part-vi": ISLAND,
    "starter": ISLAND,
}


def _configurations() -> dict[str, dict[str, Any]]:
    """Return every shipped configuration as a mapping, by id."""
    found: dict[str, dict[str, Any]] = {
        preset.preset_id: yaml.safe_load(preset.yaml_text)
        for preset in list_presets(WEBUI)
    }
    for path in sorted((ROOT / "doc" / "examples").glob("*/config.yaml")):
        found[f"doc/examples/{path.parent.name}"] = yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
    found["starter"] = yaml.safe_load(cli.STARTER_CONFIG)
    return found


CONFIGURATIONS = _configurations()


def _kind(payload: dict[str, Any] | None) -> str | None:
    """Name the payload's shape."""
    if payload is None:
        return None
    return SAMPLED if "statistics" in payload else ISLAND


def test_the_table_names_every_shipped_configuration() -> None:
    """A configuration missing from `EXPECTED` (or stale in it) fails here."""
    assert set(CONFIGURATIONS) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_payload_shape_matches_the_model_for_every_configuration(name: str) -> None:
    """Each shipped configuration gets exactly the payload the table says."""
    params = SimulationParams.from_mapping(CONFIGURATIONS[name])

    payload = app_module._closed_form_trajectory_payload(params)

    assert _kind(payload) == EXPECTED[name]


@pytest.mark.parametrize(
    "name", [name for name, kind in sorted(EXPECTED.items()) if kind is not NONE]
)
def test_payload_is_finite_bounded_and_settles_for_every_curve(name: str) -> None:
    """Every offered curve is finite, in range, and ends at the recursion's limit."""
    params = SimulationParams.from_mapping(CONFIGURATIONS[name])
    payload = app_module._closed_form_trajectory_payload(params)
    assert payload is not None

    if _kind(payload) == SAMPLED:
        grid = payload["generations"]
        assert grid[0] == 0
        assert grid[-1] == params.max_generations
        curves = payload["statistics"]
    else:
        # Evaluate the ingredients far past any relaxation time, starting
        # from an arbitrary valid state.
        recursion = _recursion_from_payload(payload)
        curves = {
            key: [value]
            for key, value in recursion.statistics_after(10**9, 0.9, 0.2).items()
        }
        assert curves["H_S"][0] == pytest.approx(1.0 - recursion.fixed_point[0])
    assert set(curves) == set(IDENTITY_STATISTIC_NAMES)
    for values in curves.values():
        assert all(math.isfinite(value) for value in values)
        # Within numerical noise of the unit interval.
        assert all(-1e-9 <= value <= 1.0 + 1e-9 for value in values)


def _recursion_from_payload(payload: dict[str, Any]) -> IdentityRecursion:
    """Rebuild `IdentityRecursion` from the ingredients the page receives."""
    vectors = payload["eigenvectors"]
    inverse = payload["inverse"]
    return IdentityRecursion(
        deme_count=payload["demes"],
        fixed_point=tuple(payload["fixedPoint"]),
        eigenvalues=tuple(payload["eigenvalues"]),
        eigenvectors=((vectors[0][0], vectors[0][1]), (vectors[1][0], vectors[1][1])),
        inverse=((inverse[0][0], inverse[0][1]), (inverse[1][0], inverse[1][1])),
    )


def _defined(value: float | None) -> float:
    """Narrow one of `TrajectoryHistory.histories`' own optional entries.

    Every statistic read this way here is one of `D`/`G_ST`/`H_S`/`H_T` at a
    healthy, multi-locus configuration -- always defined in practice (only
    `G_ST`, at a monomorphic locus, can ever be `None`).
    """
    assert value is not None, "expected a defined statistic value"
    return value


def _evaluate_like_the_page(
    payload: dict[str, Any],
    name: str,
    generations: list[int],
    histories: dict[str, list[float | None]],
) -> list[float]:
    """Evaluate `payload` for statistic `name` the way `closedFormTrajectories` does."""
    if _kind(payload) == SAMPLED:
        return list(
            np.interp(generations, payload["generations"], payload["statistics"][name])
        )
    recursion = _recursion_from_payload(payload)
    within, between = identities_from_heterozygosities(
        _defined(histories["H_S"][0]), _defined(histories["H_T"][0]), payload["demes"]
    )
    return [
        recursion.statistics_after(generation - generations[0], within, between)[name]
        for generation in generations
    ]


REPLICATES = 8
# The curve is the model's expectation. `D` and `G_ST` are ratios, and with a
# single locus the mean of the ratio sits measurably below the ratio of the
# expected identities (up to ~0.06 here), which is the estimator, not a
# modelling error. Enough loci make the ratio track its expectation, so
# every example is compared on the same footing, with its own other options.
LOCI = 12
STANDARD_ERRORS = 5.0
MINIMUM_HORIZON = 60


@pytest.mark.slow
@pytest.mark.statistical
@pytest.mark.parametrize(
    "name", [name for name, kind in sorted(EXPECTED.items()) if kind is not NONE]
)
def test_engine_agrees_with_the_payload_for_every_example(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mean simulated `D` and `G_ST` sit on the offered curve, per example.

    Each example runs `REPLICATES` seeds as one scalar run, held for three
    relaxation times with no early stop (the curve does not depend on where
    a run stops). At five checkpoints the mean paired difference (simulated
    minus curve) must lie within five standard errors of zero. Every other
    option of the example is kept; only the locus count is raised to `LOCI`
    (see there), so this catches a wrong model, not the single-locus bias.
    """
    monkeypatch.setenv("FIM_RESULTS_DIRECTORY", str(tmp_path / "results"))
    base = CONFIGURATIONS[name]
    reference = SimulationParams.from_mapping(base)
    horizon = max(MINIMUM_HORIZON, math.ceil(3.0 * (reference.relaxation_time or 0.0)))
    horizon = min(horizon, 1500)

    runs: list[tuple[dict[str, Any], list[int], dict[str, list[float | None]]]] = []
    for offset in range(REPLICATES):
        config = {
            **base,
            "seed": base["seed"] + offset,
            "n_replicates": 1,
            "convergence_window": horizon,
            "convergence_tolerance": 1e-12,
            "max_generations": horizon,
            "loci": [
                {"locus_id": index, "length": base["loci"][0]["length"]}
                for index in range(1, LOCI + 1)
            ],
        }
        path = tmp_path / f"{name.replace('/', '-')}-{offset}.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        output = tmp_path / f"out-{name.replace('/', '-')}-{offset}"
        assert cli.main(["run", str(path), "-o", str(output), "--quiet"]) == 0
        history = sampled_statistic_history(output / "trajectory.jsonl")
        payload = app_module._closed_form_trajectory_payload(
            SimulationParams.from_mapping(config)
        )
        assert payload is not None
        runs.append((payload, history.generations, history.histories))

    for statistic in ("D", "G_ST"):
        differences = [
            [
                _defined(simulated) - expected
                for simulated, expected in zip(
                    histories[statistic],
                    _evaluate_like_the_page(payload, statistic, generations, histories),
                    strict=True,
                )
            ]
            for payload, generations, histories in runs
        ]
        length = len(differences[0])
        for checkpoint in np.linspace(0, length - 1, 5, dtype=int):
            column = [row[checkpoint] for row in differences]
            standard_error = statistics.stdev(column) / len(column) ** 0.5
            assert (
                abs(statistics.fmean(column)) <= STANDARD_ERRORS * standard_error + 1e-9
            ), (
                f"{name} {statistic} at generation {runs[0][1][checkpoint]}: "
                f"mean difference {statistics.fmean(column):.4f}, "
                f"standard error {standard_error:.4f}"
            )
