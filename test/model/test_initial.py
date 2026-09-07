"""Tests for deterministic initial-condition strategies."""

from collections.abc import Callable

import numpy as np
import pytest

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
from fim.statistics import gd, gs, h_s


def _params(**changes: object) -> SimulationParams:
    """Construct standard parameters with focused overrides."""
    values: dict[str, object] = {
        "N": 20,
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
        N=20,
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
    params = _params(initial_frequencies=table, N=100)

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
    """Build a fast-converging condition: window 2, tolerance 1.0 (H_S is bounded
    in [0, 1), so any two values are within it) -- converges as soon as the
    trailing window fills, exercising at least one real mutate/drift step
    without a slow test."""
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
    params = _params(N=20, d=3, mu=0.01)

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
    params = _params(N=(15, 25), d=2, mu=0.05)

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
    params = _params(N=20, d=2, mu=0.02, seed=7)

    first_state, first_outcome = _equilibrium_condition().generate_with_outcome(
        params, rng(0)
    )
    second_state, second_outcome = _equilibrium_condition().generate_with_outcome(
        params, rng(999)
    )
    different_seed_state, _ = _equilibrium_condition().generate_with_outcome(
        _params(N=20, d=2, mu=0.02, seed=8), rng(0)
    )

    assert first_state == second_state
    assert first_outcome == second_outcome
    assert first_state != different_seed_state


def test_generate_matches_generate_with_outcome_state(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`generate` returns exactly `generate_with_outcome`'s own state."""
    params = _params(N=20, d=2, mu=0.02)
    condition = _equilibrium_condition()

    via_generate = condition.generate(params, rng(0))
    via_generate_with_outcome, _outcome = condition.generate_with_outcome(
        params, rng(0)
    )

    assert via_generate == via_generate_with_outcome


def test_equilibration_outcome_history_ends_at_the_final_heterozygosity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`history`'s last entry is `final_heterozygosity`, both valid `H_S` values."""
    params = _params(N=20, d=2, mu=0.02)

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
    params = _params(N=20, d=2, mu=0.02, mutation_model="finite_alleles")

    with pytest.raises(ValueError, match="infinite_alleles"):
        _equilibrium_condition().generate_with_outcome(params, rng(0))


def test_equilibrium_split_raises_when_it_never_converges(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Hitting the cap without stabilizing is fatal (design doc's own decision 4) --

    unlike the main run's own benign generation-cap outcome, a `d`-deme
    run must never be silently founded from a non-equilibrium ancestral
    population. `max_generations=1` can never satisfy a `window=2`
    criterion (it requires at least two recorded generations), so this
    is guaranteed to hit the cap without ever having a chance to converge.
    """
    params = _params(N=20, d=2, mu=0.02)
    condition = _equilibrium_condition(
        convergence_window=2, convergence_tolerance=0.0, max_generations=1
    )

    with pytest.raises(ValueError, match="did not reach equilibrium"):
        condition.generate_with_outcome(params, rng(0))


def test_equilibrium_split_condition_rejects_a_non_positive_max_generations() -> None:
    """`max_generations` is validated at construction time, not first use."""
    with pytest.raises(ValueError, match="max_generations"):
        EquilibriumSplitInitialCondition(
            convergence_window=2, convergence_tolerance=0.01, max_generations=0
        )
