"""Tests for deterministic initial-condition strategies."""

from collections.abc import Callable

import numpy as np
import pytest

from fim.model.allele import AlleleId
from fim.model.initial import (
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
