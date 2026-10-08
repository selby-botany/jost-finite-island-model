"""Tests for Backend V's dense block: exactness, layout, growth, memory.

The first group steps a `VectorBlock` and `fim.model.operators.step` side
by side from the same generation zero and the same seed, and compares
the complete row stream of every generation (every allele id, every
frequency bit, in order), the final state, and the generator state (which
proves the two consumed exactly the same random draws). The rest check
the layout invariants the kernel relies on, column growth and compaction,
the memory ceiling, and the registry counter hand-back.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from vector_support import FINITE_CASES, INFINITE_CASES, loci, make_params

pytest.importorskip("numba")

from fim.engine import _build_finite_allele_spaces
from fim.model.allele import (
    MINTED_ID_START,
    AlleleRegistry,
    FiniteAlleleRegistry,
)
from fim.model.initial import generate_initial_state
from fim.model.operators import step
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.model.vector_block import (
    DEFAULT_MEMORY_CEILING_BYTES,
    MEMORY_CEILING_ENVIRONMENT_VARIABLE,
    MINIMUM_WIDTH,
    MODE_CACHE_LIMIT_ENTRIES,
    SHRINK_CHECK_INTERVAL,
    VectorBlock,
    VectorMemoryCeilingError,
    VectorMigration,
    _mode_cache_for,
    estimated_block_bytes,
    resolve_memory_ceiling,
)

PARITY_GENERATIONS = 60


def _generation_zero(params: SimulationParams) -> tuple[ModelState, AlleleRegistry]:
    """Return fim's own generation zero and the registry the engine builds."""
    rng = np.random.Generator(np.random.PCG64(params.seed))
    state = generate_initial_state(params, rng)
    highest = max(
        (int(a) for deme in state.frequencies for locus in deme for a in locus),
        default=MINTED_ID_START - 1,
    )
    return state, AlleleRegistry(start=max(MINTED_ID_START, highest + 1))


def _block_for(
    params: SimulationParams, state: ModelState, registry: AlleleRegistry, **kwargs: int
) -> VectorBlock:
    """Build a block for `params` holding `state`."""
    return VectorBlock.from_model_state(
        state,
        sizes=params.population_sizes,
        mutation_rates=params.mutation_rates,
        migration=VectorMigration.from_parameter(params.m, params.d),
        mutation_model=params.mutation_model,
        next_id=registry.next_value,
        **kwargs,
    )


def _assert_block_follows_operators(params: SimulationParams, generations: int) -> None:
    """Step the block and `operators.step` in lockstep; compare everything."""
    state, registry = _generation_zero(params)
    operator_rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, operator_rng)
    block_rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, block_rng)
    finite = (
        FiniteAlleleRegistry(_build_finite_allele_spaces(state, params))
        if params.mutation_model == "finite_alleles"
        else None
    )
    block = _block_for(params, state, registry)
    assert block.rows("run") == state.to_rows("run")
    for generation in range(1, generations + 1):
        state = step(state, params, registry, operator_rng, finite_alleles=finite)
        block.advance(block_rng)
        # Complete, ordered row streams: not a summary, not a set.
        assert block.rows("run") == state.to_rows("run"), generation
    assert block.to_model_state() == state
    assert block_rng.bit_generator.state == operator_rng.bit_generator.state
    block.sync_registry(registry)
    if params.mutation_model == "infinite_alleles":
        assert registry.next_value == block.next_id


@pytest.mark.parametrize("name", list(INFINITE_CASES))
def test_infinite_alleles_block_matches_operators_exactly(name: str) -> None:
    """Every infinite-alleles case reproduces `operators.step` bit for bit."""
    _assert_block_follows_operators(INFINITE_CASES[name](PARITY_GENERATIONS), 60)


@pytest.mark.parametrize("name", list(FINITE_CASES))
def test_finite_alleles_block_matches_operators_exactly(name: str) -> None:
    """Every finite-alleles case reproduces `operators.step` bit for bit.

    Includes the K-allele target draws, which interleave with the next
    pair's mutation counts, so the generator states agree only if the
    kernel visits pairs and draws in the operators' order.
    """
    _assert_block_follows_operators(FINITE_CASES[name](PARITY_GENERATIONS), 60)


def test_dear_nolan_low_shape_matches_operators_for_thousands_of_generations() -> None:
    """The dear-nolan-low shape, three loci, 3,000 generations, every row.

    Mutations are rare here (about one per 2,000 locus-generations), so a
    long run is what exercises minting, the column that appears, and the
    compaction that follows when a one-copy mutant is lost.
    """
    params = make_params(3000, loci=loci(3))
    _assert_block_follows_operators(params, 3000)


def test_block_matches_operators_after_compaction_and_growth_together() -> None:
    """High mutation and low drift: columns grow, die, and grow again."""
    params = make_params(150, loci=loci(2), gene_copies=60, mu=0.3, m=0.2, d=4)
    _assert_block_follows_operators(params, 150)


def _assert_layout(block: VectorBlock) -> None:
    """Check every invariant the kernel relies on, on the block as it stands."""
    infinite = block.mutation_model == "infinite_alleles"
    for locus in range(block.freq.shape[0]):
        live = int(block.ncol[locus])
        ids = block.ids[locus, :live]
        # Columns are in strictly ascending allele-id order.
        assert (np.diff(ids) > 0).all()
        # Nothing at or above `ncol` is left behind.
        assert not block.freq[locus, :, live:].any()
        if infinite:
            # Every live column is occupied in some deme.
            assert (block.freq[locus, :, :live] > 0).any(axis=0).all()
        # Each deme's frequencies are its gene-copy counts over its size.
        expected = block.counts[locus, :, :live] / block.sizes[:, None]
        assert np.array_equal(block.freq[locus, :, :live], expected)
        assert (block.counts[locus, :, :live].sum(axis=1) == block.sizes).all()


@pytest.mark.parametrize(
    "case",
    [
        INFINITE_CASES["multi-locus with migration"],
        INFINITE_CASES["more than 8 alleles per deme"],
        FINITE_CASES["finite, 16 states"],
    ],
)
def test_layout_invariants_hold_after_every_generation(
    case: Callable[[int], SimulationParams],
) -> None:
    """Ascending ids, no empty column, zero padding and exact frequencies."""
    params = case(80)
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    minted = 0
    for _ in range(80):
        block.advance(rng)
        _assert_layout(block)
        minted += int(block._mutants.sum())
    if params.mutation_model == "infinite_alleles":
        # The counter equals the number of identities handed out.
        assert block.next_id == registry.next_value + minted


def test_extinct_columns_are_dropped_and_survivors_keep_their_order() -> None:
    """A lost allele's column disappears; the others keep ascending order."""
    params = make_params(40, loci=loci(1), gene_copies=20, mu=0.2, m=0.1, d=3)
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    lost: set[int] = set()
    for _ in range(40):
        before = set(block.ids[0, : block.ncol[0]].tolist())
        block.advance(rng)
        current = block.ids[0, : block.ncol[0]].tolist()
        assert current == sorted(current)
        # An allele that has left is never back: infinite alleles do not recur.
        assert not lost & set(current)
        lost |= before - set(current)
    assert lost, "the run should have lost at least one allele"


def test_width_doubles_when_the_live_alleles_outgrow_it() -> None:
    """Growth happens by doubling, from `MINIMUM_WIDTH` upward."""
    params = make_params(60, loci=loci(1), gene_copies=200, mu=0.3, m=0.2, d=3)
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    start_width = block.width
    assert start_width >= MINIMUM_WIDTH
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    widths = {start_width}
    for _ in range(60):
        block.advance(rng)
        widths.add(block.width)
        assert block.width >= int(block.ncol.max())
    assert block.grow_events >= 1
    # Growth doubles (repeatedly, if one step needs more than one doubling),
    # so every width is the starting width times a power of two.
    for width in widths:
        ratio = width // start_width
        assert width == start_width * ratio
        assert ratio & (ratio - 1) == 0


def test_width_shrinks_after_a_long_stretch_of_low_use() -> None:
    """A table far wider than its live alleles is halved, bits unchanged.

    Starts with many alleles, then removes mutation so drift fixes the
    population down to a handful; after `SHRINK_CHECK_INTERVAL`
    generations the width follows the live count back down, and the
    result is still exactly the operators' (shrinking changes no bit).
    """
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
    _assert_block_follows_operators(params, generations)
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    grown = block.width
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    for _ in range(generations):
        block.advance(rng)
    assert block.shrink_events >= 1
    assert block.width < grown


def test_memory_estimate_counts_cells_ids_and_finite_bookkeeping() -> None:
    """The estimate is 16 bytes a cell, 8 an id, and 9 more a finite entry."""
    assert estimated_block_bytes(2, 3, 8) == 2 * 3 * 8 * 16 + 2 * 8 * 8
    assert estimated_block_bytes(2, 3, 8, finite=True) == (
        2 * 3 * 8 * 16 + 2 * 8 * 8 + 2 * 8 * 9
    )


def test_a_block_over_the_ceiling_is_refused_before_allocation() -> None:
    """The failure names the size, the ceiling, and every remedy."""
    params = make_params(10, loci=loci(2), mu=0.01, m=0.05)
    state, registry = _generation_zero(params)
    with pytest.raises(VectorMemoryCeilingError) as caught:
        _block_for(params, state, registry, memory_ceiling_bytes=100)
    message = str(caught.value)
    assert "GiB" in message
    assert "Reduce the number of loci or demes" in message
    assert "engine_backend 'generational'" in message
    assert MEMORY_CEILING_ENVIRONMENT_VARIABLE in message


def test_growth_past_the_ceiling_fails_with_the_same_clear_message() -> None:
    """A table that outgrows the ceiling mid-run raises at the doubling."""
    params = make_params(80, loci=loci(1), gene_copies=200, mu=0.3, m=0.2, d=3)
    state, registry = _generation_zero(params)
    initial = estimated_block_bytes(1, 3, MINIMUM_WIDTH)
    block = _block_for(params, state, registry, memory_ceiling_bytes=initial * 4)
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    with pytest.raises(VectorMemoryCeilingError, match="columns are alive"):
        for _ in range(80):
            block.advance(rng)


def test_a_finite_alleles_locus_too_long_to_allocate_is_refused() -> None:
    """Capacity `4 ** 200` cannot fit; the error says to shorten the locus."""
    params = make_params(
        5, loci=loci(1, 200), mutation_model="finite_alleles", mu=0.01, m=0.05
    )
    state, registry = _generation_zero(params)
    with pytest.raises(VectorMemoryCeilingError, match="Shorten the locus"):
        _block_for(params, state, registry)


def test_the_ceiling_comes_from_the_argument_then_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit beats environment beats the 2 GiB default; junk is rejected."""
    monkeypatch.delenv(MEMORY_CEILING_ENVIRONMENT_VARIABLE, raising=False)
    assert resolve_memory_ceiling() == DEFAULT_MEMORY_CEILING_BYTES
    monkeypatch.setenv(MEMORY_CEILING_ENVIRONMENT_VARIABLE, "4096")
    assert resolve_memory_ceiling() == 4096
    assert resolve_memory_ceiling(10) == 10
    for junk in ("0", "-5", "lots"):
        monkeypatch.setenv(MEMORY_CEILING_ENVIRONMENT_VARIABLE, junk)
        with pytest.raises(ValueError, match=MEMORY_CEILING_ENVIRONMENT_VARIABLE):
            resolve_memory_ceiling()
    with pytest.raises(ValueError, match="memory_ceiling_bytes"):
        resolve_memory_ceiling(0)


def test_the_registry_counter_only_moves_forward() -> None:
    """`advance_to` hands ids back to the registry and refuses to go back."""
    registry = AlleleRegistry()
    assert registry.next_value == MINTED_ID_START
    registry.advance_to(MINTED_ID_START + 7)
    assert int(registry.next_id()) == MINTED_ID_START + 7
    with pytest.raises(ValueError, match="cannot move back"):
        registry.advance_to(MINTED_ID_START)


def test_rows_refuse_an_empty_run_id() -> None:
    """`rows` validates its run id as `ModelState.to_rows` does."""
    params = make_params(2, loci=loci(1))
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    with pytest.raises(ValueError, match="run_id"):
        block.rows("")


def test_frequency_maps_hold_present_alleles_in_ascending_order() -> None:
    """`frequency_maps` is `statistics_report`'s input, built from the arrays."""
    params = make_params(30, loci=loci(2), mu=0.05, m=0.1)
    state, registry = _generation_zero(params)
    block = _block_for(params, state, registry)
    rng = np.random.Generator(np.random.PCG64(params.seed))
    generate_initial_state(params, rng)
    for _ in range(30):
        block.advance(rng)
    model_state = block.to_model_state()
    for locus in range(2):
        maps = block.frequency_maps(locus)
        for deme, mapping in enumerate(maps):
            assert list(mapping) == sorted(mapping)
            assert mapping == dict(model_state.frequency_map(deme, locus))


def test_mode_cache_has_one_row_per_distinct_nonzero_rate() -> None:
    """Loci share a cache row when their mutation probability is the same."""
    rates = np.array([0.001, 0.0, 0.002, 0.001], dtype=np.float64)
    sizes = np.array([50, 120], dtype=np.int64)
    rate_class, cache = _mode_cache_for(rates, sizes)
    assert rate_class.tolist() == [0, -1, 1, 0]
    assert cache.shape == (2, 121)
    assert np.isnan(cache).all()


def test_mode_cache_is_disabled_when_it_would_be_too_large() -> None:
    """Past the entry limit every locus draws uncached, which is only slower."""
    rates = np.array([0.001, 0.002], dtype=np.float64)
    sizes = np.array([MODE_CACHE_LIMIT_ENTRIES], dtype=np.int64)
    rate_class, cache = _mode_cache_for(rates, sizes)
    assert rate_class.tolist() == [-1, -1]
    assert cache.size == 0


def test_a_run_without_the_mode_cache_is_identical_to_one_with_it() -> None:
    """Disabling the cache changes no row: it is a pure speedup."""
    params = make_params(60, loci=loci(2), mu=0.01, m=0.05)
    results = []
    for disable in (False, True):
        state, registry = _generation_zero(params)
        block = _block_for(params, state, registry)
        if disable:
            block._rate_class = np.full(2, -1, dtype=np.int64)
            block._mode_cache = np.zeros((0, 0), dtype=np.float64)
        rng = np.random.Generator(np.random.PCG64(params.seed))
        generate_initial_state(params, rng)
        rows = []
        for _ in range(60):
            block.advance(rng)
            rows.append(block.rows("run"))
        results.append((rows, rng.bit_generator.state))
    assert results[0] == results[1]
