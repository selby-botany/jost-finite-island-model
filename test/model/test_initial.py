"""Tests for deterministic initial-condition strategies."""

import math
from collections.abc import Callable

import numpy as np
import pytest

from fim.convergence.defaults import panmictic_equilibration
from fim.model.allele import AlleleId
from fim.model.initial import (
    EquilibrationOutcome,
    EquilibriumSplitInitialCondition,
    ExplicitInitialCondition,
    founding_condition_for_heterozygosity,
    generate_initial_state,
)
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.statistics import gd, gs, h_s


def _params(**changes: object) -> SimulationParams:
    """Construct standard parameters with focused overrides."""
    values: dict[str, object] = {
        "gene_copies": 20,
        "m": 0.1,
        "mu": 0.0,
        "d": 2,
        "seed": 42,
        "loci": (LocusSpec(1, 100),),
    }
    values.update(changes)
    return SimulationParams(**values)  # type: ignore[arg-type]


def test_same_seed_produces_identical_dirichlet_state(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Random starts are exact functions of the seed."""
    params = _params()

    assert generate_initial_state(params, rng(42)) == generate_initial_state(
        params,
        rng(42),
    )


def test_generate_initial_state_dispatches_to_equilibrium_split_when_configured(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The three `equilibrium_*` fields' own presence selects the new strategy.

    Mirrors `initial_frequencies`'s own existing "presence selects the
    strategy" dispatch, per the design doc's own decision 6 -- no
    separate mode field to check.
    """
    params = _params(
        gene_copies=20,
        d=2,
        mu=0.02,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=1.0,
        equilibrium_max_generations=50,
    )

    state = generate_initial_state(params, rng(0))

    assert state.deme_count == 2
    assert state.generation == 0


def test_initial_concentration_changes_evenness(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """High concentration yields a more even fixed-seed draw."""
    low = generate_initial_state(
        _params(initial_concentration=0.01),
        rng(19),
    )
    high = generate_initial_state(
        _params(initial_concentration=100.0),
        rng(19),
    )

    low_spread = max(low.frequency_map(0, 0).values()) - min(
        low.frequency_map(0, 0).values()
    )
    high_spread = max(high.frequency_map(0, 0).values()) - min(
        high.frequency_map(0, 0).values()
    )
    assert low_spread > high_spread


def test_explicit_p0_is_used_verbatim() -> None:
    """Published or surveyed starting frequencies bypass random generation."""
    params = _params(
        initial_frequencies=(
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
            ({AlleleId(0): 0.9, AlleleId(1): 0.1},),
        )
    )

    state = generate_initial_state(params)

    assert dict(state.frequency_map(0, 0)) == {
        AlleleId(0): 0.25,
        AlleleId(1): 0.75,
    }


@pytest.mark.parametrize(
    ("allele_id", "message"),
    [(1.9, "must be an integer"), (-3, "must be a non-negative integer")],
)
def test_direct_construction_rejects_malformed_p0_allele_ids(
    allele_id: object,
    message: str,
) -> None:
    """Constructing `SimulationParams` directly (bypassing `from_mapping`)
    still validates `p_0` allele identities: this path reaches
    `_normalize_initial_frequencies` without ever going through
    `_parse_initial_frequencies`, so it needs its own guard against a
    truncated float or a negative ID sneaking through as a bare Python
    key.
    """
    with pytest.raises(ValueError, match=message):
        _params(
            initial_frequencies=(
                ({allele_id: 1.0},),
                ({AlleleId(0): 1.0},),
            )
        )


def test_explicit_strategy_requires_p0() -> None:
    """The explicit strategy refuses a configuration without frequencies."""
    params = _params()
    with np.testing.assert_raises_regex(ValueError, "require p_0"):
        ExplicitInitialCondition().generate(params, np.random.default_rng(1))


def test_generate_initial_state_uses_seed_when_rng_is_omitted() -> None:
    """The convenience API creates the same PCG64 stream as the engine."""
    params = _params()
    assert generate_initial_state(params) == generate_initial_state(params)


@pytest.mark.parametrize(
    "heterozygosity", [0.0, 0.1, 0.3, 0.5, 0.6667, 0.8, 0.9, 0.95, 0.99]
)
@pytest.mark.parametrize("deme_count", [2, 3, 5])
def test_founding_condition_gs_equals_gd_equals_one_minus_h_s_0(
    heterozygosity: float, deme_count: int
) -> None:
    """`R7`'s own exit condition: `Gs(0) = Gd(0) = 1 - H_S(0)`, exactly.

    `dev/doc/apps/selby/jost-finite-island-model/20260903-claude-opus-5-
    gene-identity-recursion-fim-implications.md` §9, `R7` — the whole
    point of this helper is that every deme is an identical ancestral
    copy, so the within- and between-deme gene identities coincide
    exactly with the requested heterozygosity's own complement, for any
    deme count and at any achievable heterozygosity, not merely
    approximately.
    """
    table = founding_condition_for_heterozygosity(heterozygosity, deme_count=deme_count)
    locus_table = [deme[0] for deme in table]

    assert h_s(locus_table) == pytest.approx(heterozygosity, abs=1e-12)
    assert gs(locus_table) == pytest.approx(1.0 - heterozygosity, abs=1e-12)
    assert gd(locus_table) == pytest.approx(1.0 - heterozygosity, abs=1e-12)
    assert gs(locus_table) == pytest.approx(gd(locus_table), abs=1e-12)


def test_founding_condition_builds_identical_demes_and_independent_loci() -> None:
    """Every deme is byte-for-byte identical; every locus is its own copy."""
    table = founding_condition_for_heterozygosity(0.6, deme_count=4, locus_count=3)

    assert len(table) == 4
    assert all(len(deme) == 3 for deme in table)
    first_deme = table[0]
    assert all(deme == first_deme for deme in table[1:])
    # Independent per-locus dicts, not the same object reused — mutating
    # one deme's own copy must never be observable from another's.
    assert table[0][0] is not table[1][0]


def test_founding_condition_realizes_the_state_through_explicit_initial_condition() -> (
    None
):
    """The built table is a genuine, usable `p_0` — not just internally consistent."""
    table = founding_condition_for_heterozygosity(0.5, deme_count=2, locus_count=1)
    params = _params(initial_frequencies=table, gene_copies=100)

    state = ExplicitInitialCondition().generate(params, np.random.default_rng(1))

    assert state.frequency_map(0, 0) == state.frequency_map(1, 0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"heterozygosity": -0.1, "deme_count": 2}, r"heterozygosity"),
        ({"heterozygosity": 1.0, "deme_count": 2}, r"heterozygosity"),
        ({"heterozygosity": True, "deme_count": 2}, r"heterozygosity"),
        ({"heterozygosity": float("nan"), "deme_count": 2}, r"heterozygosity"),
        ({"heterozygosity": 0.5, "deme_count": 0}, r"deme_count"),
        ({"heterozygosity": 0.5, "deme_count": 2, "locus_count": 0}, r"locus_count"),
    ],
)
def test_founding_condition_rejects_invalid_inputs(
    kwargs: dict[str, object], message: str
) -> None:
    """Every argument is validated, not passed straight into the arithmetic."""
    with pytest.raises(ValueError, match=message):
        founding_condition_for_heterozygosity(**kwargs)  # type: ignore[arg-type]


def _equilibrium_condition(
    **changes: object,
) -> EquilibriumSplitInitialCondition:
    """Build a fast condition: tolerance 1.0 needs no derived burn-in (any
    heterozygosity is within 1 of equilibrium), so the ancestral phase runs
    exactly its minimum, `convergence_window` = 2 generations -- real
    mutate/drift steps without a slow test."""
    values: dict[str, object] = {
        "convergence_window": 2,
        "convergence_tolerance": 1.0,
        "max_generations": 50,
    }
    values.update(changes)
    return EquilibriumSplitInitialCondition(**values)  # type: ignore[arg-type]


def test_equilibrium_split_produces_a_valid_d_deme_state(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The split state has the right shape and generation, for every deme."""
    params = _params(gene_copies=20, d=3, mu=0.01)

    state, outcome = _equilibrium_condition().generate_with_outcome(params, rng(0))

    assert state.deme_count == 3
    assert state.generation == 0
    assert isinstance(outcome, EquilibrationOutcome)


def test_equilibrium_split_conserves_the_ancestral_gene_count_per_locus(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every locus's own gene copies are conserved exactly across the split.

    The whole point of a finite-pool partition (P1 item 5's own design
    doc, decision 1): no gene copy is created or lost at the moment of
    founding, only reassigned to one of the `d` new demes.
    """
    params = _params(gene_copies=(15, 25), d=2, mu=0.05)

    state, _outcome = _equilibrium_condition().generate_with_outcome(params, rng(0))

    for locus_index in range(state.locus_count):
        totals: dict[AlleleId, float] = {}
        for deme_index, size in enumerate(params.population_sizes):
            for allele_id, frequency in state.frequency_map(
                deme_index, locus_index
            ).items():
                totals[allele_id] = totals.get(allele_id, 0.0) + frequency * size
        assert sum(round(count) for count in totals.values()) == sum(
            params.population_sizes
        )


def test_equilibrium_split_is_a_function_of_the_seed(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The same seed reproduces the identical split; a different seed does not."""
    params = _params(gene_copies=20, d=2, mu=0.02, seed=7)

    first_state, first_outcome = _equilibrium_condition().generate_with_outcome(
        params, rng(0)
    )
    second_state, second_outcome = _equilibrium_condition().generate_with_outcome(
        params, rng(999)
    )
    different_seed_state, _ = _equilibrium_condition().generate_with_outcome(
        _params(gene_copies=20, d=2, mu=0.02, seed=8), rng(0)
    )

    assert first_state == second_state
    assert first_outcome == second_outcome
    assert first_state != different_seed_state


def test_generate_matches_generate_with_outcome_state(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`generate` returns exactly `generate_with_outcome`'s own state."""
    params = _params(gene_copies=20, d=2, mu=0.02)
    condition = _equilibrium_condition()

    via_generate = condition.generate(params, rng(0))
    via_generate_with_outcome, _outcome = condition.generate_with_outcome(
        params, rng(0)
    )

    assert via_generate == via_generate_with_outcome


def test_on_generation_observes_every_ancestral_generation_without_changing_anything(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The observer sees generations `0..generation_count`, one deme each.

    Its states are the ones the outcome summarizes (`history`), and
    observing changes neither the split state nor the outcome — the
    contract `fim.engine` relies on to stream `equilibrium_trajectory.
    jsonl` without perturbing the run.
    """
    params = _params(gene_copies=20, d=2, mu=0.02)
    condition = _equilibrium_condition(convergence_window=5)
    observed: list[ModelState] = []

    observed_state, observed_outcome = condition.generate_with_outcome(
        params, rng(0), on_generation=observed.append
    )
    plain_state, plain_outcome = condition.generate_with_outcome(params, rng(0))

    assert (observed_state, observed_outcome) == (plain_state, plain_outcome)
    assert [state.generation for state in observed] == list(
        range(observed_outcome.generation_count + 1)
    )
    assert {state.deme_count for state in observed} == {1}
    assert [
        math.fsum(
            h_s([state.frequency_map(0, locus)]) for locus in range(state.locus_count)
        )
        / state.locus_count
        for state in observed
    ] == list(observed_outcome.history)


def test_equilibration_outcome_history_ends_at_the_final_heterozygosity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`history`'s last entry is `final_heterozygosity`, both valid `H_S` values."""
    params = _params(gene_copies=20, d=2, mu=0.02)

    _state, outcome = _equilibrium_condition().generate_with_outcome(params, rng(0))

    assert outcome.history[-1] == outcome.final_heterozygosity
    assert 0.0 <= outcome.final_heterozygosity < 1.0
    assert len(outcome.history) == outcome.generation_count + 1
    assert outcome.generation_count >= 1


def test_equilibrium_split_rejects_finite_alleles_mutation_model(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Finite-alleles support needs a shared `_build_finite_allele_spaces`,
    not yet extracted from `fim.engine` (design doc's own noted scope limit)."""
    params = _params(gene_copies=20, d=2, mu=0.02, mutation_model="finite_alleles")

    with pytest.raises(ValueError, match="infinite_alleles"):
        _equilibrium_condition().generate_with_outcome(params, rng(0))


def test_equilibrium_split_raises_when_the_burn_in_exceeds_the_cap(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A burn-in longer than the cap is fatal (design doc's own decision 4).

    Unlike the main run's own benign generation-cap outcome, a `d`-deme
    run must never be silently founded from a non-equilibrium ancestral
    population. The burn-in is known before the phase starts, so the
    message says how many generations it needs.
    """
    params = _params(gene_copies=20, d=2, mu=0.02)
    needed = panmictic_equilibration(
        total_size=40, mutation_rates=[0.02], tolerance=0.01
    ).generations
    condition = _equilibrium_condition(
        convergence_tolerance=0.01, max_generations=needed - 1
    )

    with pytest.raises(ValueError, match=f"at least {needed:,}"):
        condition.generate_with_outcome(params, rng(0))


def test_equilibrium_split_runs_the_derived_burn_in_or_its_minimum(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The ancestral phase runs the model's burn-in, never fewer than its window.

    40 gene copies with `mu` 0.02 relax in about 16 generations, so a
    tolerance of 0.01 needs `ceil(ln 0.01 / ln rho)` generations,
    `rho = (1 - 1/40)((1 - 0.02)² + 0.02 (1 - 0.02)/40)`; a larger
    window is a floor.
    """
    params = _params(gene_copies=20, d=2, mu=0.02)
    rho = (1.0 - 1.0 / 40) * ((1.0 - 0.02) ** 2 + 0.02 * (1.0 - 0.02) / 40)
    derived = math.ceil(math.log(0.01) / math.log(rho))

    _state, outcome = _equilibrium_condition(
        convergence_tolerance=0.01, max_generations=derived
    ).generate_with_outcome(params, rng(0))
    _state, floored = _equilibrium_condition(
        convergence_window=derived + 30,
        convergence_tolerance=0.01,
        max_generations=derived + 30,
    ).generate_with_outcome(params, rng(0))

    assert outcome.generation_count == derived
    assert len(outcome.history) == derived + 1
    assert floored.generation_count == derived + 30


def test_equilibrium_split_uses_the_slowest_locus_for_its_burn_in(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """With per-locus rates, the locus with the smallest `mu` sets the burn-in."""
    params = _params(
        gene_copies=20,
        d=2,
        mu=(0.05, 0.005),
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
    )
    slowest = panmictic_equilibration(
        total_size=40, mutation_rates=[0.005], tolerance=0.01
    ).generations

    _state, outcome = _equilibrium_condition(
        convergence_tolerance=0.01, max_generations=slowest
    ).generate_with_outcome(params, rng(0))

    assert outcome.generation_count == slowest


def test_equilibrium_split_ends_near_the_expected_equilibrium_heterozygosity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Averaged over many loci, the founded population sits at equilibrium.

    A single locus wanders around its expected heterozygosity by drift,
    but 200 independent loci average that out: the mean across loci ends
    within 0.05 (about four standard errors) of `1 - F*`, starting from
    a one-allele draw (heterozygosity 0) far from it. Seeded, so the
    outcome is fixed by the commit.
    """
    loci = tuple(LocusSpec(index, 100) for index in range(1, 201))
    params = _params(gene_copies=25, d=2, mu=0.01, loci=loci, initial_allele_count=1)
    expected = panmictic_equilibration(
        total_size=50, mutation_rates=[0.01], tolerance=0.01
    ).expected_heterozygosity

    _state, outcome = _equilibrium_condition(
        convergence_tolerance=0.01, max_generations=1_000
    ).generate_with_outcome(params, rng(0))

    assert outcome.history[0] == pytest.approx(0.0, abs=1e-12)
    assert abs(outcome.final_heterozygosity - expected) < 0.05


def test_equilibrium_split_condition_rejects_a_zero_tolerance() -> None:
    """A zero tolerance would need an endless burn-in, so it is refused."""
    with pytest.raises(ValueError, match="greater than 0"):
        EquilibriumSplitInitialCondition(
            convergence_window=2, convergence_tolerance=0.0, max_generations=10
        )


def test_equilibrium_split_condition_rejects_a_non_positive_max_generations() -> None:
    """`max_generations` is validated at construction time, not first use."""
    with pytest.raises(ValueError, match="max_generations"):
        EquilibriumSplitInitialCondition(
            convergence_window=2, convergence_tolerance=0.01, max_generations=0
        )
