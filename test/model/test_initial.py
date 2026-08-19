"""Tests for deterministic initial-condition strategies."""

from collections.abc import Callable

import numpy as np
import pytest

from fim.model.allele import AlleleId
from fim.model.initial import ExplicitInitialCondition, generate_initial_state
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams


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
