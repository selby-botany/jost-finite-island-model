"""Tests for `VectorBlock.export_state` and `VectorBlock.from_state`.

A block exported mid-run and rebuilt from its export must continue exactly
as the original: the same rows every generation, the same widths, the same
growth and shrink history, and the same generator draws. Malformed exports
are refused before any work is done.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from vector_support import FINITE_CASES, INFINITE_CASES, loci, make_params

pytest.importorskip("numba")

from fim.model.allele import MINTED_ID_START
from fim.model.initial import generate_initial_state
from fim.model.params import SimulationParams
from fim.model.vector_block import SHRINK_CHECK_INTERVAL, VectorBlock, VectorMigration


def _migration(params: SimulationParams) -> VectorMigration:
    """Return the migration plan for `params`."""
    return VectorMigration.from_parameter(
        params.m, params.d, stochastic=params.migrant_sampling == "stochastic"
    )


def _started(params: SimulationParams) -> tuple[VectorBlock, np.random.Generator]:
    """Return generation zero as a block and the generator that follows it."""
    rng = np.random.Generator(np.random.PCG64(params.seed))
    state = generate_initial_state(params, rng)
    highest = max(
        (int(a) for deme in state.frequencies for locus in deme for a in locus),
        default=MINTED_ID_START - 1,
    )
    block = VectorBlock.from_model_state(
        state,
        sizes=params.population_sizes,
        mutation_rates=params.mutation_rates,
        migration=_migration(params),
        mutation_model=params.mutation_model,
        next_id=max(MINTED_ID_START, highest + 1),
    )
    return block, rng


def _rebuilt(
    params: SimulationParams,
    metadata: dict[str, Any],
    arrays: dict[str, np.ndarray],
) -> VectorBlock:
    """Rebuild a block from an export under `params`."""
    return VectorBlock.from_state(
        metadata,
        arrays,
        loci=params.loci,
        sizes=params.population_sizes,
        mutation_rates=params.mutation_rates,
        migration=_migration(params),
        mutation_model=params.mutation_model,
    )


def _assert_same_exports(
    left: tuple[dict[str, Any], dict[str, np.ndarray]],
    right: tuple[dict[str, Any], dict[str, np.ndarray]],
) -> None:
    """Assert two exports hold the same metadata and the same arrays."""
    assert left[0] == right[0]
    assert set(left[1]) == set(right[1])
    for name, array in left[1].items():
        assert array.dtype == right[1][name].dtype, name
        np.testing.assert_array_equal(array, right[1][name], err_msg=name)


def _assert_resumes_exactly(
    params: SimulationParams, split: int, generations: int
) -> tuple[VectorBlock, VectorBlock]:
    """Advance, export at `split`, rebuild, and step both in lockstep."""
    original, rng = _started(params)
    for _ in range(split):
        original.advance(rng)
    metadata, arrays = original.export_state()
    restored = _rebuilt(params, metadata, arrays)
    restored_rng = np.random.Generator(np.random.PCG64(params.seed))
    restored_rng.bit_generator.state = rng.bit_generator.state

    _assert_same_exports(restored.export_state(), (metadata, arrays))
    for _ in range(generations - split):
        original.advance(rng)
        restored.advance(restored_rng)
        assert restored.rows("r") == original.rows("r")
        assert restored.width == original.width
    assert restored.to_model_state() == original.to_model_state()
    assert restored.next_id == original.next_id
    assert restored_rng.bit_generator.state == rng.bit_generator.state
    _assert_same_exports(restored.export_state(), original.export_state())
    return original, restored


_CASES: dict[str, Callable[[int], SimulationParams]] = {
    "infinite, multi-locus": INFINITE_CASES["multi-locus with migration"],
    "infinite, matrix": INFINITE_CASES["migration matrix"],
    "infinite, unequal sizes": INFINITE_CASES["unequal deme sizes"],
    "finite, 16 states": FINITE_CASES["finite, 16 states"],
    "finite, mixed lengths": FINITE_CASES["finite, mixed lengths and rates"],
}


@pytest.mark.parametrize("name", list(_CASES))
@pytest.mark.parametrize("split", [0, 1, 17])
def test_a_rebuilt_block_continues_exactly(name: str, split: int) -> None:
    """Rows, widths, state, counter and draws all match the original."""
    _assert_resumes_exactly(_CASES[name](40), split, 40)


def test_growth_history_survives_the_round_trip() -> None:
    """A block that has grown keeps its width and growth count."""
    params = make_params(60, loci=loci(1), gene_copies=200, mu=0.3, m=0.2, d=3)
    original, restored = _assert_resumes_exactly(params, 30, 60)

    assert restored.grow_events == original.grow_events >= 1


def test_a_split_inside_the_shrink_window_shrinks_on_schedule() -> None:
    """The shrink-check counter is restored, so the halving is not delayed."""
    generations = SHRINK_CHECK_INTERVAL + 20
    params = make_params(
        generations,
        loci=loci(1),
        gene_copies=20,
        mu=0.0,
        m=0.5,
        d=2,
        initial_allele_count=16,
        initial_concentration=1.0,
    )
    original, restored = _assert_resumes_exactly(
        params, SHRINK_CHECK_INTERVAL // 2 + 3, generations
    )

    assert restored.shrink_events == original.shrink_events >= 1


def test_the_export_is_a_copy() -> None:
    """Changing the original after an export leaves the export unchanged."""
    params = INFINITE_CASES["one locus"](10)
    block, rng = _started(params)
    metadata, arrays = block.export_state()
    frozen = {name: array.copy() for name, array in arrays.items()}
    for _ in range(5):
        block.advance(rng)

    _assert_same_exports((metadata, arrays), (metadata, frozen))


def _export(
    case: str = "infinite, multi-locus",
) -> tuple[SimulationParams, dict[str, Any], dict[str, np.ndarray]]:
    """Return a configuration and an export taken a few generations in."""
    params = _CASES[case](10)
    block, rng = _started(params)
    for _ in range(3):
        block.advance(rng)
    metadata, arrays = block.export_state()
    return params, metadata, arrays


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda _m, a: a.pop("freq"), id="missing-array"),
        pytest.param(
            lambda _m, a: a.update(freq=a["freq"].astype(np.float32)), id="dtype"
        ),
        pytest.param(lambda _m, a: a.update(ids=a["ids"][:, :-1]), id="shape"),
        pytest.param(
            lambda _m, a: a.update(ncol=a["ncol"] + a["freq"].shape[2] + 1),
            id="ncol-range",
        ),
        pytest.param(lambda m, _a: m.update(generation=-1), id="generation"),
        pytest.param(
            lambda m, _a: m.update(steps_since_shrink_check=SHRINK_CHECK_INTERVAL),
            id="shrink-counter",
        ),
        pytest.param(lambda m, _a: m.update(next_id=12), id="next-id-type"),
    ],
)
def test_a_malformed_export_is_refused(
    spoil: Callable[[dict[str, Any], dict[str, np.ndarray]], object],
) -> None:
    """Every array and field is checked before the block is rebuilt."""
    params, metadata, arrays = _export()
    spoil(metadata, arrays)

    with pytest.raises(ValueError, match="block"):
        _rebuilt(params, metadata, arrays)


def test_finite_capacities_must_match_the_loci() -> None:
    """A finite export rebuilt under other loci is refused."""
    params, metadata, arrays = _export("finite, 16 states")
    arrays["capacities"] = arrays["capacities"] + 1

    with pytest.raises(ValueError, match="block"):
        _rebuilt(params, metadata, arrays)
