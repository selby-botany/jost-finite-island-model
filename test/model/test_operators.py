"""Tests for one-generation update operators."""

import math
from collections.abc import Callable

import numpy as np
import pytest

from fim.model import operators
from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.initial import generate_initial_state
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import (
    _attribute_finite_allele_targets,
    _build_migrate_symmetric_buffers,
    _inversion_binomial,
    _jit_migrate_symmetric_blend,
    _jit_multinomial_via_binomial,
    _jit_multinomial_via_inversion_binomial,
    _jit_mutate_event_counts_batched,
    _jit_mutate_targets_batched,
    _migrate_symmetric_blend_batched,
    _mint_infinite_allele_ids,
    _multinomial_via_binomial,
    _multinomial_via_inversion_binomial,
    _mutate_event_counts_batched,
    _mutate_targets_batched,
    _next_mutate_event_count,
    drift,
    migrate,
    mutate,
    step,
)
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.model.topology import dense_matrix_from_neighbors, stepping_stone_neighbors


def _state() -> ModelState:
    """Return a two-deme biallelic state."""
    return ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 0.8, AlleleId(1): 0.2},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )


def test_migrate_zero_is_identity() -> None:
    """No migration leaves all frequencies unchanged."""
    assert migrate(_state(), 0.0) == _state()


def test_migrate_single_deme_is_identity() -> None:
    """A single deme has no "other" pool to migrate from — a well-defined no-op.

    Before this guard existed, `_migrate_symmetric`'s own
    `other_weight = total_size - destination_size` was exactly `0.0`
    whenever `deme_count == 1`, raising `ZeroDivisionError` — one of
    three different failure modes this project's own multi-model engine
    review, 2026-09-04, found across the three backends for the same
    input (`FIM-02`/finding C-06/finding P2-2); `migrate_vectorized_
    symmetric` (`fim.model.vectorized`) has its own matching test.
    Unreachable via a validated `SimulationParams` (`d >= 2`), but
    `migrate`/`ModelState` are public.
    """
    single_deme_state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({AlleleId(0): 0.8, AlleleId(1): 0.2},),),
    )
    assert migrate(single_deme_state, 0.35) == single_deme_state


def test_migrate_one_replaces_each_deme_with_other_pool() -> None:
    """At m=1, two demes exchange their complete frequency vectors."""
    migrated = migrate(_state(), 1.0)

    expected_deme_zero = _state().frequency_map(1, 0)
    expected_deme_one = _state().frequency_map(0, 0)
    for allele_id, expected in expected_deme_zero.items():
        assert migrated.frequency_map(0, 0)[allele_id] == pytest.approx(expected)
    for allele_id, expected in expected_deme_one.items():
        assert migrated.frequency_map(1, 0)[allele_id] == pytest.approx(expected)


def test_migration_conserves_equal_deme_mean() -> None:
    """Symmetric all-to-all migration preserves metapopulation frequency."""
    migrated = migrate(_state(), 0.35)

    original_mean = (
        sum(_state().frequency_map(deme, 0).get(AlleleId(0), 0.0) for deme in range(2))
        / 2
    )
    migrated_mean = (
        sum(migrated.frequency_map(deme, 0).get(AlleleId(0), 0.0) for deme in range(2))
        / 2
    )
    assert migrated_mean == pytest.approx(original_mean)


def _extract_frequencies(state: ModelState) -> list[list[dict[int, float]]]:
    """Return normalized frequencies as plain ``int``-keyed dictionaries."""
    return [
        [
            {
                int(allele): freq
                for allele, freq in state.frequency_map(deme, locus).items()
            }
            for locus in range(state.locus_count)
        ]
        for deme in range(state.deme_count)
    ]


def _reference_symmetric_migration(
    frequencies: list[list[dict[int, float]]],
    sizes: tuple[int, ...],
    rate: float,
) -> list[list[dict[int, float]]]:
    """Compute scalar migration with the naive per-destination definition.

    This independent oracle re-sums every other deme for each destination
    (``O(d^2 * A)``) so that the optimized ``O(d * A)`` implementation can be
    validated against the exact definition it replaces.
    """
    deme_count = len(frequencies)
    locus_count = len(frequencies[0])
    result: list[list[dict[int, float]]] = []
    for destination in range(deme_count):
        others = [k for k in range(deme_count) if k != destination]
        other_weight = sum(sizes[k] for k in others)
        destination_maps: list[dict[int, float]] = []
        for locus in range(locus_count):
            local = frequencies[destination][locus]
            alleles: set[int] = set()
            for k in range(deme_count):
                alleles |= set(frequencies[k][locus])
            blended: dict[int, float] = {}
            for allele in alleles:
                pool = (
                    sum(
                        sizes[k] * frequencies[k][locus].get(allele, 0.0)
                        for k in others
                    )
                    / other_weight
                )
                blended[allele] = (1.0 - rate) * local.get(allele, 0.0) + rate * pool
            positive = {a: v for a, v in blended.items() if v > 0.0}
            total = sum(positive.values())
            destination_maps.append({a: v / total for a, v in positive.items()})
        result.append(destination_maps)
    return result


def _build_state(frequencies: list[list[dict[int, float]]]) -> ModelState:
    """Build a ``ModelState`` from raw ``int``-keyed frequency dictionaries."""
    locus_count = len(frequencies[0])
    return ModelState(
        loci=tuple(LocusSpec(index + 1, 100) for index in range(locus_count)),
        frequencies=tuple(
            tuple(
                {AlleleId(allele): freq for allele, freq in locus.items()}
                for locus in deme
            )
            for deme in frequencies
        ),
    )


_MIGRATION_EQUIVALENCE_CASES = {
    "equal_sizes_shared_biallelic": (
        [
            [{0: 0.7, 1: 0.3}],
            [{0: 0.2, 1: 0.8}],
            [{0: 0.5, 1: 0.5}],
        ],
        None,
        0.1,
    ),
    "uniform_size_multilocus": (
        [
            [{0: 0.6, 1: 0.4}, {2: 0.9, 3: 0.1}],
            [{0: 0.1, 1: 0.9}, {2: 0.3, 3: 0.7}],
            [{0: 0.5, 1: 0.5}, {2: 0.5, 3: 0.5}],
            [{0: 0.8, 1: 0.2}, {2: 0.2, 3: 0.8}],
        ],
        100,
        0.25,
    ),
    "unequal_sizes": (
        [
            [{0: 0.9, 1: 0.1}],
            [{0: 0.4, 1: 0.6}],
            [{0: 0.2, 1: 0.8}],
        ],
        (10, 250, 3000),
        0.3,
    ),
    "zero_rate_identity": (
        [
            [{0: 0.7, 1: 0.3}],
            [{0: 0.2, 1: 0.8}],
        ],
        (5, 9),
        0.0,
    ),
    "sparse_alleles": (
        [
            [{0: 1.0}],
            [{1: 0.5, 2: 0.5}],
            [{0: 0.25, 3: 0.75}],
            [{2: 0.4, 3: 0.6}],
        ],
        (100, 50, 200, 75),
        0.2,
    ),
    "private_alleles": (
        [
            [{0: 0.6, 10: 0.4}],
            [{0: 0.6, 11: 0.4}],
            [{0: 0.6, 12: 0.4}],
            [{0: 0.6, 13: 0.4}],
        ],
        (40, 60, 80, 20),
        0.15,
    ),
    "full_replacement_private": (
        [
            [{0: 0.5, 20: 0.5}],
            [{1: 0.5, 21: 0.5}],
            [{2: 0.5, 22: 0.5}],
        ],
        None,
        1.0,
    ),
}


@pytest.mark.parametrize(
    ("frequencies", "population_size", "rate"),
    list(_MIGRATION_EQUIVALENCE_CASES.values()),
    ids=list(_MIGRATION_EQUIVALENCE_CASES),
)
def test_symmetric_migration_matches_naive_reference(
    frequencies: list[list[dict[int, float]]],
    population_size: int | tuple[int, ...] | None,
    rate: float,
) -> None:
    """The O(d*A) migration reproduces the naive definition it replaces."""
    state = _build_state(frequencies)
    normalized = _extract_frequencies(state)
    deme_count = state.deme_count
    if population_size is None:
        sizes: tuple[int, ...] = (1,) * deme_count
    elif isinstance(population_size, int):
        sizes = (population_size,) * deme_count
    else:
        sizes = population_size

    migrated = migrate(state, rate, population_size)
    expected = _reference_symmetric_migration(normalized, sizes, rate)

    for deme in range(deme_count):
        for locus in range(state.locus_count):
            observed_map = migrated.frequency_map(deme, locus)
            expected_map = expected[deme][locus]
            assert {int(a) for a in observed_map} == set(expected_map)
            for allele, value in expected_map.items():
                assert observed_map[AlleleId(allele)] == pytest.approx(value, abs=1e-11)


def test_mutation_zero_is_identity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A zero mutation probability consumes no identities or randomness."""
    state = _state()

    assert mutate(state, 0.0, 100, AlleleRegistry(), rng(3)) is state


def _two_locus_state() -> ModelState:
    """Return a one-deme, two-locus state, each locus biallelic."""
    return ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
        frequencies=(
            (
                {AlleleId(0): 0.8, AlleleId(1): 0.2},
                {AlleleId(0): 0.8, AlleleId(1): 0.2},
            ),
        ),
    )


def test_mutation_per_locus_rate_only_mutates_the_configured_locus(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A per-locus mutation-rate tuple applies each rate to its own locus.

    Locus 1's rate is exactly 0; locus 2's is not. Only locus 2 may show a
    mutant-range identity afterward — proving the rate is picked up by
    locus, not shared or averaged across them.
    """
    mutated = mutate(
        _two_locus_state(), (0.0, 0.3), 100, AlleleRegistry(), rng(20260822)
    )

    assert mutated.frequency_map(0, 0) == _two_locus_state().frequency_map(0, 0)
    assert any(
        int(allele_id) >= MINTED_ID_START for allele_id in mutated.frequency_map(0, 1)
    )


def test_mutation_uniform_per_locus_tuple_matches_scalar_broadcast(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A per-locus tuple of equal rates behaves identically to the scalar.

    `SimulationParams` already collapses a uniform list to a scalar for
    storage, but `mutate()` itself must also treat the two forms as the
    same rate applied per locus, independent of that collapsing.
    """
    state = _two_locus_state()

    via_scalar = mutate(state, 0.3, 100, AlleleRegistry(), rng(11))
    via_tuple = mutate(state, (0.3, 0.3), 100, AlleleRegistry(), rng(11))

    assert via_scalar == via_tuple


def test_mutation_events_receive_fresh_ids(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every observed mutation has a unique mutant-range identity."""
    mutated = mutate(_state(), 0.1, 100, AlleleRegistry(), rng(17))
    mutant_ids = {
        int(allele_id)
        for deme in mutated.frequencies
        for locus in deme
        for allele_id in locus
        if int(allele_id) >= MINTED_ID_START
    }

    assert mutant_ids
    assert len(mutant_ids) == len(set(mutant_ids))


def test_mutation_scales_existing_mass_and_preserves_sub_grid_allele(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Mutation reduces existing mass proportionally, not by grid rounding.

    Regression guard for the mutation operator: existing allele frequencies
    must be scaled by a single retained-mass factor so their ratios are
    preserved and a rare sub-``1/N`` migrant survives. The earlier
    implementation rounded continuous frequencies onto the ``1/N`` grid before
    resampling, which deterministically dropped alleles below ``0.5/N`` (here
    the ``0.004`` allele) and undid migration, biasing runs toward spurious
    differentiation.
    """
    population_size = 100
    common = AlleleId(0)
    sub_grid_migrant = AlleleId(1)
    # 0.004 < 1 / 100: below the grid the old apportionment could represent.
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({common: 0.996, sub_grid_migrant: 0.004},),),
    )

    mutated = mutate(state, 0.05, population_size, AlleleRegistry(), rng(7))
    result = mutated.frequency_map(0, 0)

    # The rare migrant is retained, not rounded away.
    assert result.get(sub_grid_migrant, 0.0) > 0.0
    # Proportional scaling preserves the exact pre-existing frequency ratio.
    assert result[common] / result[sub_grid_migrant] == pytest.approx(0.996 / 0.004)
    # Events still mint fresh identities and total mass stays normalized.
    assert any(int(allele_id) >= MINTED_ID_START for allele_id in result)
    assert sum(result.values()) == pytest.approx(1.0)


def test_mutation_finite_alleles_respects_locus_specific_capacity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Each locus's mutation targets stay within that locus's own capacity.

    Two loci share one state but have different capacities (4 and 4**10);
    every observed target must respect its own locus's bound, not the
    other's — proving `mutate()` looks up the registry by `locus_id`
    rather than sharing one space across loci.
    """
    population_size = 200
    state = ModelState(
        loci=(LocusSpec(1, 1), LocusSpec(2, 10)),
        frequencies=(
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 1.0},
            ),
        ),
    )
    finite_alleles = FiniteAlleleRegistry(
        {
            1: FiniteAlleleSpace(4, [AlleleId(0)]),
            2: FiniteAlleleSpace(4**10, [AlleleId(0)]),
        }
    )

    mutated = mutate(
        state,
        0.3,
        population_size,
        AlleleRegistry(),
        rng(20260821),
        finite_alleles=finite_alleles,
    )

    assert all(int(a) < 4 for a in mutated.frequency_map(0, 0))
    assert all(int(a) < 4**10 for a in mutated.frequency_map(0, 1))
    assert any(int(a) >= 4 for a in mutated.frequency_map(0, 1))


def test_mutation_finite_alleles_accumulates_recurrent_targets() -> None:
    """Two events landing on the same target sum mass instead of overwriting.

    Capacity 2 leaves exactly one possible target for any mutating copy —
    deterministic in shape, so no statistical tolerance is needed: only
    alleles 0 and 1 can ever exist, and their mass must still sum to 1
    afterward. A flat assignment instead of accumulation would silently
    drop earlier events' contributions and this would fail.
    """
    population_size = 100
    state = ModelState(
        loci=(LocusSpec(1, 1),),
        frequencies=(({AlleleId(0): 0.5, AlleleId(1): 0.5},),),
    )
    finite_alleles = FiniteAlleleRegistry(
        {1: FiniteAlleleSpace(2, [AlleleId(0), AlleleId(1)])}
    )

    mutated = mutate(
        state,
        0.3,
        population_size,
        AlleleRegistry(),
        np.random.default_rng(5),
        finite_alleles=finite_alleles,
    )
    result = mutated.frequency_map(0, 0)

    assert set(result) == {AlleleId(0), AlleleId(1)}
    assert sum(result.values()) == pytest.approx(1.0)


def test_mutation_finite_alleles_none_matches_infinite_alleles_default(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Omitting `finite_alleles` is identical to passing `None` explicitly.

    The opt-in contract: every call written before this feature existed
    (including every other test in this file) keeps meaning exactly what
    it always meant.
    """
    state = _state()

    omitted = mutate(state, 0.1, 100, AlleleRegistry(), rng(17))
    explicit_none = mutate(
        state, 0.1, 100, AlleleRegistry(), rng(17), finite_alleles=None
    )

    assert omitted == explicit_none


@pytest.mark.statistical
def test_mutation_finite_alleles_recurrence_rate_matches_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample recurrence rate through `mutate()` matches theory.

    End-to-end version of `FiniteAlleleSpace`'s own recurrence-rate test:
    drives the same probability through `mutate()`'s source-attribution and
    accumulation logic, not just the space's `mutate_target` in isolation.
    ``population_size=1, mu=1.0`` makes each call exactly one mutation
    event with a certain, deterministic source — the only randomness left
    is that one event's target — so each trial is one clean Bernoulli
    observation, exactly like the space-level test.
    """
    capacity = 100
    minted_count = 50
    trials = 20_000
    expected_probability = (minted_count - 1) / (capacity - 1)
    generator = rng(20260821)
    state = ModelState(
        loci=(LocusSpec(1, 1),),
        frequencies=(({AlleleId(0): 1.0},),),
    )

    def one_target() -> AlleleId:
        """Mutate the single gene copy once and return its new identity."""
        finite_alleles = FiniteAlleleRegistry(
            {
                1: FiniteAlleleSpace(
                    capacity,
                    [AlleleId(identity) for identity in range(minted_count)],
                )
            }
        )
        mutated = mutate(
            state, 1.0, 1, AlleleRegistry(), generator, finite_alleles=finite_alleles
        )
        (target,) = mutated.frequency_map(0, 0)
        return target

    outcomes = np.asarray(
        [int(one_target()) < minted_count for _trial in range(trials)]
    )
    observed_rate = outcomes.mean()
    standard_error = np.sqrt(
        expected_probability * (1.0 - expected_probability) / trials
    )

    assert observed_rate == pytest.approx(
        expected_probability, abs=5.0 * standard_error
    )


def test_jit_mutate_event_counts_batched_matches_plain_decomposition() -> None:
    """The Numba-JIT-compiled kernel is exactly the same function, compiled.

    Isolates the compilation layer from the "does batching the draw
    change anything" question the tests below cover — the same
    isolation `test_jit_multinomial_via_binomial_matches_plain_
    decomposition` already does for drift's own compiled primitive.
    """
    pytest.importorskip("numba")
    controller = np.random.default_rng(20260903)
    for _ in range(200):
        seed = int(controller.integers(0, 2**31))
        pair_count = int(controller.integers(1, 12))
        ns = controller.integers(1, 500, size=pair_count).astype(np.int64)
        ps = controller.random(pair_count)

        plain = _mutate_event_counts_batched(
            np.random.Generator(np.random.PCG64(seed)), ns, ps
        )
        jitted = _jit_mutate_event_counts_batched(
            np.random.Generator(np.random.PCG64(seed)), ns, ps
        )
        assert plain.tolist() == list(jitted)


def test_jit_multinomial_via_inversion_binomial_matches_plain_decomposition() -> None:
    """The Numba-JIT-compiled kernel matches the original, not just its own twin.

    Mirrors `test_jit_multinomial_via_binomial_matches_plain_
    decomposition`'s own structure, but compares against
    `_multinomial_via_inversion_binomial` — the function `mutate`'s
    own finite-alleles branch actually calls — rather than the older,
    `rng.binomial`-based `_multinomial_via_binomial`. The real risk
    this isolates: `_jit_multinomial_via_inversion_binomial`'s own
    inner `draw_one` closure duplicates `_inversion_binomial`'s
    algorithm rather than calling it (`nopython` mode cannot compile a
    call to a plain module-level function — see either function's own
    docstring), so this checks the duplication stayed faithful, not
    merely that compiling changes nothing.
    """
    pytest.importorskip("numba")
    controller = np.random.default_rng(20260903)
    for _ in range(200):
        seed = int(controller.integers(0, 2**31))
        category_count = int(controller.integers(2, 8))
        raw = controller.random(category_count)
        probabilities = raw / raw.sum()
        n = int(controller.integers(0, 500))

        plain = _multinomial_via_inversion_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        jitted = _jit_multinomial_via_inversion_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        assert plain.tolist() == list(jitted)


def test_jit_mutate_targets_batched_matches_plain_decomposition() -> None:
    """The Numba-JIT-compiled kernel is exactly the same function, compiled.

    Same isolation as the other low-level kernel tests above — this
    only asks whether `operators.py`'s own `_mutate_targets_batched`/
    `_jit_mutate_targets_batched` agree with each other, given
    identical, realistic minted-state arrays. Whether this module's own
    *copy* of the algorithm agrees with `FiniteAlleleSpace.mutate_
    target` is a separate question, covered by `test_mutate_targets_
    batched_matches_finite_allele_space_exactly`, below.
    """
    pytest.importorskip("numba")
    capacity = 20
    controller = np.random.default_rng(20260904)
    for _ in range(50):
        seed = int(controller.integers(0, 2**31))
        minted_count = int(controller.integers(2, capacity + 1))
        event_count = int(controller.integers(1, 10))
        sources = controller.integers(0, minted_count, size=event_count).astype(
            np.int64
        )
        minted_mask = np.zeros(capacity, dtype=np.bool_)
        minted_mask[:minted_count] = True
        minted_list = np.zeros(capacity, dtype=np.int64)
        minted_list[:minted_count] = np.arange(minted_count)

        plain = _mutate_targets_batched(
            np.random.Generator(np.random.PCG64(seed)),
            sources,
            capacity,
            minted_mask.copy(),
            minted_list.copy(),
            minted_count,
            minted_count,
        )
        jitted = _jit_mutate_targets_batched(
            np.random.Generator(np.random.PCG64(seed)),
            sources,
            capacity,
            minted_mask.copy(),
            minted_list.copy(),
            minted_count,
            minted_count,
        )
        plain_targets, _, _, plain_count, plain_next = plain
        jitted_targets, _, _, jitted_count, jitted_next = jitted
        assert plain_targets.tolist() == jitted_targets.tolist(), seed
        assert plain_count == jitted_count
        assert plain_next == jitted_next


def test_mutate_targets_batched_matches_finite_allele_space_exactly(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`operators.py`'s own duplicated kernel matches `FiniteAlleleSpace` exactly.

    Mirrors `test/model/test_vectorized.py`'s own `test_mutate_
    vectorized_recurrence_rate_matches_finite_allele_space` (the
    original this module's own `_mutate_targets_batched` is a
    deliberate duplicate of, per that function's own docstring) —
    re-proven directly here because a duplicate is only as trustworthy
    as its own, independent proof, not inherited from the original's.
    For the identical starting minted set and an identically seeded
    `rng`, the two must return the *exact same target*, every trial,
    not merely agree on the rate across many.
    """
    capacity = 20

    for minted_count in (2, 6, 12, capacity):
        minted = tuple(AlleleId(i) for i in range(minted_count))
        minted_mask = np.zeros(capacity, dtype=np.bool_)
        minted_mask[:minted_count] = True
        minted_list = np.zeros(capacity, dtype=np.int64)
        minted_list[:minted_count] = np.arange(minted_count)
        for source in range(minted_count):
            for seed in range(50):
                space = FiniteAlleleSpace(capacity, minted)
                expected = int(space.mutate_target(AlleleId(source), rng(seed)))

                targets, _, _, _, _ = _mutate_targets_batched(
                    rng(seed),
                    np.array([source], dtype=np.int64),
                    capacity,
                    minted_mask.copy(),
                    minted_list.copy(),
                    minted_count,
                    minted_count,
                )
                assert int(targets[0]) == expected, (minted_count, source, seed)


class _AlwaysMintRng:
    """A minimal stub forcing `_mutate_targets_batched`'s mint branch open.

    See `test/model/test_vectorized.py`'s own identical class for the
    full reasoning — `recurrence_probability` is exactly `1.0` whenever
    `minted_count == capacity`, so a real `np.random.Generator` (whose
    own `random()` never returns exactly `1.0`) can never reach the mint
    branch at that point; this fake's `random()` returning `1.0` itself
    is the only way to exercise it directly.
    """

    def random(self) -> float:
        return 1.0


def test_mutate_targets_batched_raises_when_capacity_is_exhausted() -> None:
    """`operators.py`'s own duplicate kernel gets the identical `FIM-16` guard.

    Mirrors `test/model/test_vectorized.py`'s own test of the same name
    for the original this module's own `_mutate_targets_batched` is a
    deliberate duplicate of — re-proven directly here since a duplicate
    is only as trustworthy as its own, independent proof.
    """
    capacity = 5
    minted_mask = np.ones(capacity, dtype=np.bool_)
    minted_list = np.arange(capacity, dtype=np.int64)

    with pytest.raises(RuntimeError, match="no unminted state left"):
        _mutate_targets_batched(
            _AlwaysMintRng(),  # type: ignore[arg-type]
            np.array([0], dtype=np.int64),
            capacity,
            minted_mask,
            minted_list,
            capacity,
            capacity,
        )


def test_next_mutate_event_count_reads_batched_array_or_draws_inline(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The small helper `mutate` delegates event-count selection to.

    Direct unit coverage for `_next_mutate_event_count`, split out of
    `mutate`'s own body purely to keep that function's branch count
    readable (stage 3's own commit message has the full reasoning) —
    covered indirectly by every `mutate`-level test above, but this
    project's own testing standard is one function, one direct test.
    """
    batched = np.array([3, 0, 7], dtype=np.int64)
    assert _next_mutate_event_count(rng(1), 100, 0.1, batched, 0) == 3
    assert _next_mutate_event_count(rng(1), 100, 0.1, batched, 2) == 7

    generator = rng(20260903)
    expected = _inversion_binomial(rng(20260903), 100, 0.1)
    assert _next_mutate_event_count(generator, 100, 0.1, None, 0) == expected


def test_mint_infinite_allele_ids_reads_reserved_slice_or_mints_inline() -> None:
    """The small helper `mutate` delegates infinite-alleles minting to.

    Direct unit coverage for `_mint_infinite_allele_ids`, split out for
    the same reason `_next_mutate_event_count`'s own test above is.
    """
    reserved = AlleleRegistry().next_k_ids(5)
    mutated: dict[AlleleId, float] = {}
    next_offset = _mint_infinite_allele_ids(
        mutated, AlleleRegistry(), 3, 0.5, reserved, 0
    )
    assert next_offset == 3
    assert mutated == {
        AlleleId(int(reserved[0])): 0.5,
        AlleleId(int(reserved[1])): 0.5,
        AlleleId(int(reserved[2])): 0.5,
    }

    # `minted_ids=None` mints one at a time via `registry.next_id()`,
    # exactly as every prior release did — a fresh registry's own first
    # two identities are known in advance (`MINTED_ID_START`, `+ 1`), so
    # this is checked directly against those values, not just a count.
    unbatched: dict[AlleleId, float] = {}
    unbatched_offset = _mint_infinite_allele_ids(
        unbatched, AlleleRegistry(), 2, 0.5, None, 7
    )
    assert unbatched_offset == 7  # unchanged -- no reservation to advance past
    assert unbatched == {
        AlleleId(MINTED_ID_START): 0.5,
        AlleleId(MINTED_ID_START + 1): 0.5,
    }


def test_attribute_finite_allele_targets_batches_when_eligible_or_falls_back(
    rng: Callable[[int], np.random.Generator],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct unit coverage for `_attribute_finite_allele_targets`.

    Covered indirectly by every finite-alleles `mutate`-level test
    above, but this project's own testing standard is one direct test
    per function — checks both of this function's own branches (the
    batched kernel, and the per-event fallback the capacity-bound
    monkeypatch below forces) produce the identical accumulated
    `mutated` dict for the same seed.
    """
    pytest.importorskip("numba")
    locus = LocusSpec(1, 2)  # capacity 16
    allele_ids = (AlleleId(0), AlleleId(1))
    source_counts = np.array([2, 1], dtype=np.int64)

    eligible_mutated: dict[AlleleId, float] = {}
    _attribute_finite_allele_targets(
        eligible_mutated,
        FiniteAlleleRegistry({1: FiniteAlleleSpace(16, allele_ids)}),
        locus,
        allele_ids,
        source_counts,
        0.1,
        rng(20260904),
        jit=True,
    )

    monkeypatch.setattr(operators, "_MAX_JIT_FINITE_ALLELE_CAPACITY", 0)
    fallback_mutated: dict[AlleleId, float] = {}
    _attribute_finite_allele_targets(
        fallback_mutated,
        FiniteAlleleRegistry({1: FiniteAlleleSpace(16, allele_ids)}),
        locus,
        allele_ids,
        source_counts,
        0.1,
        rng(20260904),
        jit=True,
    )

    assert eligible_mutated == fallback_mutated


def test_mutate_with_jit_matches_mutate_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` changes nothing about `mutate`'s own output, for the same seed.

    Under the default infinite-alleles model (`finite_alleles=None`),
    `jit=True` is real: `registry.next_id()` consumes no `rng` draw at
    all, so batching every pair's own event count up front never
    disturbs any other draw's own position in the stream.
    """
    pytest.importorskip("numba")
    unjitted = mutate(_state(), 0.1, 100, AlleleRegistry(), rng(20260903), jit=False)
    jitted = mutate(_state(), 0.1, 100, AlleleRegistry(), rng(20260903), jit=True)
    assert unjitted == jitted


def test_mutate_with_jit_under_finite_alleles_matches_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` under the finite-alleles model changes what runs, not the result.

    Stage 2 scoped `jit`'s event-count batching to the infinite-alleles
    model only — the finite-alleles model's own per-event source-
    attribution/target-selection draws interleave with the event-count
    draw in a way batching it up front would desync (`20260901-claude-
    sonnet-5-fim-engine-backend-factory-design.md` §10 item 10e, stage
    2's own docstring). Stages 3 and 3b together now give the finite-
    alleles model real `jit` benefit through the *entire* pipeline for
    a small-capacity locus like this one (`capacity=64`, well under
    `_MAX_JIT_FINITE_ALLELE_CAPACITY`): the event-count draw stays
    unbatched, deliberately, but source attribution is compiled
    (`_jit_multinomial_via_inversion_binomial`, stage 3) *and* target
    selection is now batched per pair too
    (`_jit_mutate_targets_batched`, stage 3b) — bit-identical output
    either way, checked here across the whole pipeline, not just the
    source-attribution slice stage 3 alone left checked. A fresh
    `FiniteAlleleSpace` per call, not a shared one — `mutate_target`
    mutates its own internal minted-state bookkeeping, so reusing one
    instance across two separate `mutate()` calls would make the
    second call's own result depend on the first call's own side
    effects, not on `jit` (the real bug an earlier version of this test
    itself had, per this stage's own commit history).
    """
    pytest.importorskip("numba")

    def _finite_alleles() -> FiniteAlleleRegistry:
        return FiniteAlleleRegistry(
            {1: FiniteAlleleSpace(64, [AlleleId(0), AlleleId(1)])}
        )

    without_jit = mutate(
        _state(),
        0.1,
        100,
        AlleleRegistry(),
        rng(6),
        finite_alleles=_finite_alleles(),
        jit=False,
    )
    with_jit_requested = mutate(
        _state(),
        0.1,
        100,
        AlleleRegistry(),
        rng(6),
        finite_alleles=_finite_alleles(),
        jit=True,
    )
    assert without_jit == with_jit_requested


def _finite_alleles_for(
    state: ModelState, params: SimulationParams
) -> FiniteAlleleRegistry:
    """Build one fresh `FiniteAlleleSpace` per locus, seeded from `state`.

    Mirrors `fim.engine._build_finite_allele_spaces` without importing
    `fim.engine` into this test module. Every call returns a brand new
    registry, never a shared one — `FiniteAlleleSpace` mutates its own
    internal state as a side effect, so two separately-compared
    `mutate()` calls always need their own independently-constructed
    registry (`test_mutate_with_jit_under_finite_alleles_matches_
    without_jit_bit_for_bit`'s own docstring has the real bug this
    guards against).
    """
    return FiniteAlleleRegistry(
        {
            locus.locus_id: FiniteAlleleSpace(
                finite_allele_capacity(locus.length),
                (
                    allele_id
                    for deme in state.frequencies
                    for allele_id in deme[locus_index]
                ),
            )
            for locus_index, locus in enumerate(params.loci)
        }
    )


def test_mutate_with_jit_matches_without_jit_under_multi_deme_finite_alleles(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Batched target-selection state carries correctly across demes.

    One `FiniteAlleleSpace` is shared across *every* deme at a given
    locus — a state minted while processing deme 0 is visible as a
    real recurrence candidate while processing deme 1 at the same
    locus, within the same generation
    (`_attribute_finite_allele_targets`'s own docstring). A single-pair
    test cannot exercise that cross-deme dependency at all; this uses
    several demes and two loci of different (small, both eligible)
    capacities, with a mutation rate high enough that real minting
    happens throughout.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=30,
        m=0.1,
        mu=0.2,
        d=6,
        seed=20260904,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 2), LocusSpec(2, 3)),  # capacities 16, 64
    )
    state = generate_initial_state(params, rng(20260904))

    without_jit = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(11),
        finite_alleles=_finite_alleles_for(state, params),
        jit=False,
    )
    with_jit = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(11),
        finite_alleles=_finite_alleles_for(state, params),
        jit=True,
    )
    assert without_jit == with_jit


def test_mutate_with_jit_falls_back_above_the_capacity_bound(
    rng: Callable[[int], np.random.Generator],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locus above `_MAX_JIT_FINITE_ALLELE_CAPACITY` still works, unjitted.

    The real default bound (`2**20`) is deliberately too large to
    exercise cheaply in a unit test (building a real ineligible case
    would mean an actual multi-million-entry `FiniteAlleleSpace`) — the
    threshold itself is patched down instead, the standard way to
    exercise a capacity-bound branch without paying the capacity's own
    cost, and a real, modest capacity (1000) is used so this test still
    proves something about real `FiniteAlleleSpace` behavior, not just
    the comparison operator.
    """
    pytest.importorskip("numba")
    monkeypatch.setattr(operators, "_MAX_JIT_FINITE_ALLELE_CAPACITY", 50)
    params = SimulationParams(
        N=30,
        m=0.1,
        mu=0.2,
        d=3,
        seed=20260904,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 5),),  # capacity 1024, above the patched bound
    )
    state = generate_initial_state(params, rng(20260904))

    without_jit = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(12),
        finite_alleles=_finite_alleles_for(state, params),
        jit=False,
    )
    with_jit_requested = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(12),
        finite_alleles=_finite_alleles_for(state, params),
        jit=True,
    )
    assert without_jit == with_jit_requested


def test_mutate_with_jit_handles_mixed_eligible_and_ineligible_loci(
    rng: Callable[[int], np.random.Generator],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eligibility is decided per locus, not once for the whole run.

    `FiniteAlleleRegistry` already supports different capacities per
    locus in the same run (`test_finite_allele_registry_dispatches_by_
    locus_id`) — this checks `mutate`'s own per-pair eligibility check
    respects that: one locus routes through the batched kernel, the
    other falls back, within the very same `mutate()` call, and the
    combined result still matches the fully unjitted path exactly.
    """
    pytest.importorskip("numba")
    monkeypatch.setattr(operators, "_MAX_JIT_FINITE_ALLELE_CAPACITY", 50)
    params = SimulationParams(
        N=30,
        m=0.1,
        mu=0.2,
        d=4,
        seed=20260904,
        mutation_model="finite_alleles",
        loci=(
            LocusSpec(1, 2),  # capacity 16 -- eligible
            LocusSpec(2, 5),  # capacity 1024 -- above the patched bound
        ),
    )
    state = generate_initial_state(params, rng(20260904))

    without_jit = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(13),
        finite_alleles=_finite_alleles_for(state, params),
        jit=False,
    )
    with_jit = mutate(
        state,
        params.mu,
        params.N,
        AlleleRegistry(),
        rng(13),
        finite_alleles=_finite_alleles_for(state, params),
        jit=True,
    )
    assert without_jit == with_jit


def test_step_with_finite_alleles_jit_matches_without_jit_across_many_generations(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Target-selection state carries correctly across generations too.

    `finite_alleles` is built once by the caller and threaded through
    every `step()` call for the whole run (`mutate`'s own `finite_
    alleles` docstring) — a single-generation test cannot exercise
    whether a batched call's own write-back
    (`FiniteAlleleSpace.restore_from_arrays`) is visible to the *next*
    generation's own processing. This runs several real generations
    (migrate, mutate, drift, in order) through two fully independent,
    self-consistent pipelines, mirroring `test_step_with_mutate_jit_
    matches_without_jit_across_many_generations`'s own proven-safe
    shape.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.15,
        mu=0.15,
        d=5,
        seed=20260904,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 2),),  # capacity 16
    )
    state = generate_initial_state(params, rng(20260904))
    unjitted_finite_alleles = _finite_alleles_for(state, params)
    jitted_finite_alleles = _finite_alleles_for(state, params)

    unjitted_rng = rng(19)
    jitted_rng = rng(19)
    for _ in range(20):
        unjitted_state = step(
            state,
            params,
            AlleleRegistry(),
            unjitted_rng,
            finite_alleles=unjitted_finite_alleles,
            jit=False,
        )
        jitted_state = step(
            state,
            params,
            AlleleRegistry(),
            jitted_rng,
            finite_alleles=jitted_finite_alleles,
            jit=True,
        )
        assert unjitted_state == jitted_state
        state = unjitted_state


def test_mutate_with_jit_matches_without_jit_across_many_demes_and_loci(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The flat, per-pair event-count and minting batching stay bit-identical at scale.

    `_state()`'s own fixture (2 demes, 1 locus) barely exercises the
    `(deme, locus)` flat layout `_mutate_event_counts_batched` depends
    on visiting in deme-major, locus-minor order, or `_mint_infinite_
    allele_ids`'s own running `minted_offset` across many pairs — this
    uses many demes and several loci of different mutation rates
    (including one exact `0.0` rate, `_inversion_binomial`'s own
    zero-draw short-circuit) against a freshly generated, already-ragged
    initial state, mirroring
    stage 1's own analogous `migrate` test
    (`test_migrate_with_jit_matches_without_jit_across_many_demes_and_
    loci`) in shape: one realistic-scale call, not a chained multi-
    generation run — `test_step_with_mutate_jit_matches_without_jit_
    across_many_generations`, below, is what covers naturally,
    generation-by-generation reshaped allele sets, through two fully
    independent, self-consistent pipelines (this test's own single-call
    shape cannot safely be extended to "chain the result forward" without
    also driving `drift` identically on both sides, since `mutate`'s own
    event-count draw shares one running `rng` stream with everything
    else called on it afterward).
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.15,
        mu=(0.03, 0.0, 0.01),
        d=10,
        seed=20260903,
        loci=(LocusSpec(1, 40), LocusSpec(2, 20), LocusSpec(3, 60)),
    )
    state = generate_initial_state(params, rng(20260903))

    unjitted = mutate(state, params.mu, params.N, AlleleRegistry(), rng(13), jit=False)
    jitted = mutate(state, params.mu, params.N, AlleleRegistry(), rng(13), jit=True)

    assert unjitted == jitted


def test_step_with_mutate_jit_matches_without_jit_across_many_generations(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`step`'s own mutate stage stays bit-identical under `jit=True` too.

    Companion to `test_step_with_migrate_jit_matches_without_jit_
    across_many_generations` (stage 1) — same shape, now exercising
    stage 2's own event-count batching inside a full `step` pipeline
    (migrate, mutate, drift, in order, `jit` shared by all three).
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.2,
        mu=0.02,
        d=10,
        seed=20260903,
        loci=(LocusSpec(1, 40), LocusSpec(2, 60)),
    )
    state = generate_initial_state(params, rng(20260903))

    unjitted_rng = rng(17)
    jitted_rng = rng(17)
    for _ in range(20):
        unjitted_state = step(state, params, AlleleRegistry(), unjitted_rng, jit=False)
        jitted_state = step(state, params, AlleleRegistry(), jitted_rng, jit=True)
        assert unjitted_state == jitted_state
        state = unjitted_state


def test_drift_preserves_invariants_and_is_seeded(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Multinomial drift returns reproducible N-bounded frequency maps."""
    first = drift(_state(), 20, rng(99))
    second = drift(_state(), 20, rng(99))

    assert first == second
    assert all(
        total == pytest.approx(1.0) for total in first.total_frequency().values()
    )
    assert all(support <= 20 for deme in first.support_sizes() for support in deme)


def test_step_matches_explicit_operator_order(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The public pipeline is drift(mutate(migrate(state)))."""
    params = SimulationParams(
        N=20,
        m=0.2,
        mu=0.05,
        d=2,
        seed=6,
        loci=_state().loci,
    )
    expected_rng = rng(6)
    expected = drift(
        mutate(
            migrate(_state(), params.m, params.N),
            params.mu,
            params.N,
            AlleleRegistry(),
            expected_rng,
        ),
        params.N,
        expected_rng,
    )

    assert step(_state(), params, AlleleRegistry(), rng(6)) == expected


def test_matrix_migration_applies_source_weights() -> None:
    """A complete migration matrix blends source demes row by row."""
    migrated = migrate(
        _state(),
        ((0.75, 0.25), (0.25, 0.75)),
    )
    assert migrated.frequency_map(0, 0)[AlleleId(0)] == pytest.approx(0.65)
    assert migrated.frequency_map(1, 0)[AlleleId(0)] == pytest.approx(0.35)


def _three_deme_state() -> ModelState:
    """Return a three-deme biallelic state with distinct frequencies."""
    return ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 0.8, AlleleId(1): 0.2},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
        ),
    )


def test_asymmetric_migration_matrix_applies_per_row_directional_weights() -> None:
    """Rows need not agree: each deme's inflow mix is independently configurable.

    Deme 0 retains almost all of its own frequency and takes a small pull from
    deme 2; deme 1 blends evenly with deme 0; deme 2 does not migrate at all.
    A symmetric matrix (equal off-diagonal entries, as in the row-by-row test
    above) could never produce this — the whole point of a general matrix
    (design §9's "asymmetric migration" row) is a source mix that differs by
    destination.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))

    migrated = migrate(_three_deme_state(), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(0)] == pytest.approx(0.785)
    assert migrated.frequency_map(1, 0)[AlleleId(0)] == pytest.approx(0.5)
    assert migrated.frequency_map(2, 0)[AlleleId(0)] == pytest.approx(0.5)


def test_matrix_migration_ignores_population_size() -> None:
    """A full matrix's rows are the authoritative weights, not N-derived ones.

    Unlike the scalar path, which auto-computes a size-weighted migrant pool
    from ``population_size``, a full matrix already states each destination's
    exact source weights — ``population_size`` has nothing left to contribute
    and must not silently change the result.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))
    state = _three_deme_state()

    without_sizes = migrate(state, matrix)
    with_wildly_unequal_sizes = migrate(state, matrix, (5, 5_000, 20))

    assert without_sizes == with_wildly_unequal_sizes


def test_symmetric_scalar_migration_is_the_matrix_special_case() -> None:
    """Scalar migration for equal-size demes equals its explicit matrix form.

    Confirms design §9's claim directly: at equal deme size, the scalar
    all-other-demes formula and a fully written-out symmetric matrix
    (``1 - m`` on the diagonal, ``m / (d - 1)`` off it) are the same
    operator, not merely similar ones.
    """
    state = _three_deme_state()
    rate = 0.3
    equal_sizes = (10, 10, 10)
    equivalent_matrix = tuple(
        tuple(1.0 - rate if row == column else rate / 2.0 for column in range(3))
        for row in range(3)
    )

    via_scalar = migrate(state, rate, equal_sizes)
    via_matrix = migrate(state, equivalent_matrix)

    for deme in range(3):
        scalar_map = via_scalar.frequency_map(deme, 0)
        matrix_map = via_matrix.frequency_map(deme, 0)
        assert set(scalar_map) == set(matrix_map)
        for allele_id, value in scalar_map.items():
            assert matrix_map[allele_id] == pytest.approx(value)


def _single_private_allele_state(d: int) -> ModelState:
    """Return a state where deme 0 alone is fixed for a private allele."""
    frequencies: list[tuple[dict[AlleleId, float]]] = [({AlleleId(99): 1.0},)]
    frequencies.extend(({AlleleId(0): 1.0},) for _ in range(d - 1))
    return ModelState(loci=(LocusSpec(1, 100),), frequencies=tuple(frequencies))


def test_ring_stepping_stone_migration_reaches_only_direct_neighbors() -> None:
    """A ring topology is genuinely spatial: one hop reaches only neighbors.

    Proves the actual claim behind "stepping-stone" — not merely that the
    generated matrix looks sparse, but that migrating through it leaves an
    allele private to deme 0 completely absent from every non-neighboring
    deme after a single generation, while both ring neighbors (including
    the wraparound one) pick up exactly the expected trace of it.
    """
    d = 6
    matrix = dense_matrix_from_neighbors(
        stepping_stone_neighbors(d, topology="ring", rate=0.3), d
    )

    migrated = migrate(_single_private_allele_state(d), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(99)] == pytest.approx(0.7)
    assert migrated.frequency_map(1, 0)[AlleleId(99)] == pytest.approx(0.15)
    assert migrated.frequency_map(5, 0)[AlleleId(99)] == pytest.approx(0.15)
    for deme in (2, 3, 4):
        assert AlleleId(99) not in migrated.frequency_map(deme, 0)


def test_linear_stepping_stone_migration_does_not_wrap_around() -> None:
    """A linear chain is the ring's non-wrapping special case, not a variant math.

    Identical setup and rate to the ring test above; the only difference is
    that deme 5 — the ring's wraparound neighbor of deme 0 — picks up
    nothing at all here, because a bounded chain has no edge connecting the
    two ends.
    """
    d = 6
    matrix = dense_matrix_from_neighbors(
        stepping_stone_neighbors(d, topology="linear", rate=0.3), d
    )

    migrated = migrate(_single_private_allele_state(d), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(99)] == pytest.approx(0.7)
    assert migrated.frequency_map(1, 0)[AlleleId(99)] == pytest.approx(0.15)
    for deme in (2, 3, 4, 5):
        assert AlleleId(99) not in migrated.frequency_map(deme, 0)


def test_symmetric_migration_uses_population_size_weights() -> None:
    """Unequal demes contribute migrants in proportion to their copy counts."""
    state = ModelState(
        loci=_state().loci,
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    migrated = migrate(state, 1.0, (10, 30))
    assert migrated.frequency_map(0, 0) == {AlleleId(1): 1.0}
    assert migrated.frequency_map(1, 0) == {AlleleId(0): 1.0}


def test_jit_migrate_symmetric_blend_matches_plain_batched_computation() -> None:
    """The Numba-JIT-compiled kernel is exactly the same function, compiled.

    Isolates the compilation layer from the array-vs-dict question the
    tests below cover: this only asks whether `_jit_migrate_symmetric_
    blend` (compiled) and `_migrate_symmetric_blend_batched` (plain)
    agree with each other, given the identical, realistic ragged buffers
    `_build_migrate_symmetric_buffers` produces for a real multi-locus
    state — the same isolation `test_jit_multinomial_via_binomial_
    matches_plain_decomposition` already does for drift's own compiled
    primitive.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=30,
        m=0.2,
        mu=0.02,
        d=10,
        seed=20260903,
        loci=(LocusSpec(1, 50), LocusSpec(2, 30), LocusSpec(3, 80)),
    )
    state = generate_initial_state(params, np.random.default_rng(20260903))
    sizes_array = np.full(state.deme_count, 30.0, dtype=np.float64)
    other_weights = np.full(
        state.deme_count, 30.0 * (state.deme_count - 1), dtype=np.float64
    )
    _, widths, offsets, frequencies_flat = _build_migrate_symmetric_buffers(state)

    plain = _migrate_symmetric_blend_batched(
        0.2, sizes_array, other_weights, widths, offsets, frequencies_flat
    )
    jitted = _jit_migrate_symmetric_blend(
        0.2, sizes_array, other_weights, widths, offsets, frequencies_flat
    )

    assert plain.tolist() == jitted.tolist()


def test_migrate_with_jit_matches_migrate_without_jit_bit_for_bit() -> None:
    """`jit=True` changes nothing about `migrate`'s own output, for any rate.

    Unlike `drift`'s own jit argument, `migrate`'s deterministic path
    consumes no RNG at all, so there is no stream-consumption-order
    question to test here — only whether the array-native kernel's own
    floating-point arithmetic reproduces the dict-based loop's own
    arithmetic exactly (see `_migrate_symmetric_blend_batched`'s own
    docstring for why it does, by construction).
    """
    pytest.importorskip("numba")
    for rate in (0.0, 0.1, 0.35, 1.0):
        unjitted = migrate(_state(), rate, jit=False)
        jitted = migrate(_state(), rate, jit=True)
        assert unjitted == jitted, rate


def test_migrate_with_jit_matches_without_jit_across_many_demes_and_loci() -> None:
    """The ragged, per-locus flat-buffer path stays bit-identical.

    `_state()`'s own fixture (2 demes, 1 locus, both alleles present
    everywhere) barely exercises the ragged, varying-per-locus-width
    layout `_build_migrate_symmetric_buffers` builds — this test uses
    many demes and several loci of different capacities (and so,
    already at generation zero via `generate_initial_state`'s own
    per-locus allele space, different segregating-allele counts),
    exactly the shape most likely to expose an indexing bug in the
    flat-buffer-plus-offsets packing/unpacking if one existed.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=40,
        m=0.25,
        mu=0.02,
        d=12,
        seed=20260903,
        loci=(LocusSpec(1, 50), LocusSpec(2, 30), LocusSpec(3, 80)),
    )
    state = generate_initial_state(params, np.random.default_rng(20260903))

    unjitted = migrate(state, params.m, params.N, jit=False)
    jitted = migrate(state, params.m, params.N, jit=True)

    assert unjitted == jitted


def test_migrate_jit_is_silently_ignored_for_a_full_matrix() -> None:
    """`jit=True` with a full weight matrix falls back, not an error.

    `migrate`'s own `jit` support is scoped to the scalar-rate,
    deterministic case only (`20260901-claude-sonnet-5-fim-engine-
    backend-factory-design.md` §10 item 10e, stage 1) — a matrix `m`
    must keep working exactly as before, silently, not raise.
    """
    pytest.importorskip("numba")
    matrix = ((0.75, 0.25), (0.25, 0.75))

    without_jit = migrate(_state(), matrix)
    with_jit_requested = migrate(_state(), matrix, jit=True)

    assert without_jit == with_jit_requested


def test_migrate_jit_is_silently_ignored_for_stochastic_sampling(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` with an `rng` (stochastic migrant sampling) falls back too.

    Same scope boundary as the matrix case above, checked against the
    other axis `migrate`'s own `jit` argument does not cover: the
    opt-in stochastic-migrant-count model still consumes `rng` calls
    identically either way, so both calls, given the same seed, must
    produce the same draws.
    """
    pytest.importorskip("numba")
    without_jit = migrate(_state(), 0.3, (20, 20), rng=rng(20260903), jit=False)
    with_jit_requested = migrate(_state(), 0.3, (20, 20), rng=rng(20260903), jit=True)

    assert without_jit == with_jit_requested


def test_step_with_migrate_jit_matches_without_jit_across_many_generations(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`step`'s own migrate stage stays bit-identical under `jit=True` too.

    Runs several real generations (migrate, mutate, drift, in order) so
    `migrate`'s own jit path sees the naturally shrinking, unequal
    per-locus allele sets drift/mutate actually produce over time, not
    only generation zero's own freshly generated state.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.2,
        mu=0.02,
        d=10,
        seed=20260903,
        loci=(LocusSpec(1, 40), LocusSpec(2, 60)),
    )
    state = generate_initial_state(params, rng(20260903))

    unjitted_rng = rng(11)
    jitted_rng = rng(11)
    for _ in range(20):
        unjitted_state = step(state, params, AlleleRegistry(), unjitted_rng, jit=False)
        jitted_state = step(state, params, AlleleRegistry(), jitted_rng, jit=True)
        assert unjitted_state == jitted_state
        state = unjitted_state


def test_migrate_stochastic_requires_population_size(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """rng without population_size is rejected rather than silently ignored."""
    with pytest.raises(ValueError, match="population_size"):
        migrate(_state(), 0.3, rng=rng(1))


@pytest.mark.statistical
def test_migrate_stochastic_scalar_migrant_count_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample mean and variance fall in pre-derived bands.

    Deme 0 starts fixed for a private allele and deme 1 fixed for another;
    with only two demes, deme 0's entire migrant pool is deme 1, so the
    post-migration frequency of deme 1's allele in deme 0 *is* the random
    migrant fraction ``K / size`` directly, with ``K ~ Binomial(size, rate)``
    by construction.
    """
    size = 100
    rate = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    generator = rng(20260818)
    observed = np.asarray(
        [
            migrate(state, rate, size, rng=generator)
            .frequency_map(0, 0)
            .get(AlleleId(1), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_mean = rate
    expected_variance = rate * (1.0 - rate) / size
    mean_standard_error = np.sqrt(expected_variance / replicates)
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.mean(observed) == pytest.approx(
        expected_mean, abs=5.0 * mean_standard_error
    )
    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance, abs=5.0 * variance_standard_error
    )


def test_migrate_stochastic_preserves_pool_composition(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Randomizing the migrant count never changes migrant composition.

    Deme 0 carries only allele 0; the migrant pool (demes 1 and 2, equal
    size) carries only alleles 1 and 2, in an exact 0.5/0.5 split. Because
    allele 0 never appears in the pool and alleles 1/2 never appear
    locally, whatever count a stochastic draw lands on, alleles 1 and 2
    must still arrive in that same 0.5/0.5 ratio — only the *total*
    migrant mass is random here, never its internal mixture (`migrate`'s
    docstring, `_blend`).
    """
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 0.6, AlleleId(2): 0.4},),
            ({AlleleId(1): 0.4, AlleleId(2): 0.6},),
        ),
    )
    rate = 0.4
    sizes = (100, 100, 100)

    migrated = migrate(state, rate, sizes, rng=rng(7))

    migrant_one = migrated.frequency_map(0, 0).get(AlleleId(1), 0.0)
    migrant_two = migrated.frequency_map(0, 0).get(AlleleId(2), 0.0)
    assert migrant_one + migrant_two > 0.0
    assert migrant_one / (migrant_one + migrant_two) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.statistical
def test_migrate_stochastic_matrix_migrant_count_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The matrix path's stochastic branch obeys the same binomial theory.

    Mirrors the scalar-path statistical test above, but through
    ``_migrate_matrix``'s independent stochastic branch: deme 0's row gives
    a 0.3 non-self weight entirely to deme 1, so the same direct
    migrant-fraction argument applies.
    """
    size = 100
    matrix = ((0.7, 0.3), (0.3, 0.7))
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    generator = rng(20260818)
    observed = np.asarray(
        [
            migrate(state, matrix, size, rng=generator)
            .frequency_map(0, 0)
            .get(AlleleId(1), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_mean = 0.3
    expected_variance = 0.3 * 0.7 / size
    mean_standard_error = np.sqrt(expected_variance / replicates)
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.mean(observed) == pytest.approx(
        expected_mean, abs=5.0 * mean_standard_error
    )
    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance, abs=5.0 * variance_standard_error
    )


def test_migrate_stochastic_matrix_self_weight_one_matches_continuous(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A row with no outgoing weight never samples and never drifts from it.

    Deme 2's row is ``(0.0, 0.0, 1.0)`` — full self-retention. The
    stochastic and continuous paths must agree exactly for that deme,
    since there is nothing random left to draw once the migrant weight
    is zero.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))
    state = _three_deme_state()

    continuous = migrate(state, matrix)
    stochastic = migrate(state, matrix, (100, 100, 100), rng=rng(3))

    assert stochastic.frequency_map(2, 0) == continuous.frequency_map(2, 0)


class _NoBinomialRng:
    """Wraps a real generator, raising if `.binomial()` is ever called.

    Proves a stochastic-migration code path draws its migrant count via
    `_inversion_binomial` (which only ever calls `.random()` — see that
    function's own docstring), never `rng.binomial` directly — the exact
    regression `FIM-11` fixes. Duck-typed rather than a `np.random.
    Generator` subclass: nothing in `fim.model.operators` ever checks
    `isinstance(rng, np.random.Generator)`, so forwarding every other
    attribute straight through to a real generator is sufficient.
    """

    def __init__(self, inner: np.random.Generator) -> None:
        self._inner = inner

    def binomial(self, *args: object, **kwargs: object) -> int:
        del args, kwargs  # unused; the point is that this is never reached
        raise AssertionError("rng.binomial() should not be called directly")

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_migrate_stochastic_draws_migrant_count_via_inversion_binomial(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The stochastic migrant count comes from `_inversion_binomial`, not `.binomial`.

    Regression test for FIM-11: `_blend` used to call `rng.binomial`
    directly — the one draw in this module not migrated to the fixed-
    draw-count `_inversion_binomial` the rest of it was rebuilt around,
    reintroducing variable, `n`/`p`-dependent stream consumption that
    breaks cross-backend bit-matching. Exercises both the scalar and
    matrix stochastic paths, since each has its own call site.
    """
    wrapped = _NoBinomialRng(rng(20260818))

    scalar_result = migrate(_state(), 0.3, 100, rng=wrapped)  # type: ignore[arg-type]
    matrix_result = migrate(
        _three_deme_state(),
        ((0.7, 0.2, 0.1), (0.2, 0.7, 0.1), (0.1, 0.1, 0.8)),
        (100, 100, 100),
        rng=wrapped,  # type: ignore[arg-type]
    )

    assert isinstance(scalar_result, ModelState)
    assert isinstance(matrix_result, ModelState)


def test_migrate_stochastic_shares_one_migrant_count_across_every_locus(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every locus in a deme uses the same drawn migrant count, not one each.

    Regression test for FIM-13: `_blend` used to draw its own migrant
    count internally, once per call, and every caller called it once per
    (deme, locus) — modeling loci as independent gametic pools rather
    than individuals carrying every locus together. Two identical loci
    must now produce identical post-migration frequencies (one shared
    migrant fraction applied to the same inputs at both), not two
    independently drawn counts landing on two different answers.
    """
    deme0_locus = {AlleleId(0): 0.8, AlleleId(1): 0.2}
    deme1_locus = {AlleleId(0): 0.2, AlleleId(1): 0.8}
    state = ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
        frequencies=((deme0_locus, deme0_locus), (deme1_locus, deme1_locus)),
    )

    migrated = migrate(state, 0.4, (100, 100), rng=rng(20260818))

    assert migrated.frequency_map(0, 0) == migrated.frequency_map(0, 1)
    assert migrated.frequency_map(1, 0) == migrated.frequency_map(1, 1)


def test_symmetric_pool_mass_clamps_tiny_negative_cancellation_to_zero() -> None:
    """Catastrophic cancellation can never produce a negative migrant-pool mass.

    Regression test for FIM-14: ``total - destination_size *
    local_frequency`` is exact in real arithmetic but can round a
    genuinely non-negative result to a tiny negative `float64` for a
    heavily skewed allele. The raw formula (asserted first, to confirm
    this input genuinely triggers the cancellation rather than testing a
    clamp that never engages) goes negative for `total=0.0` with any
    positive `local_frequency`; `_symmetric_pool_mass` must clamp that to
    exactly `0.0`, and must leave an ordinary positive result untouched.
    """
    raw = (0.0 - 1 * 1e-300) / 1.0
    assert raw < 0.0

    assert operators._symmetric_pool_mass(0.0, 1, 1e-300, 1.0) == 0.0
    assert operators._symmetric_pool_mass(10.0, 2, 0.5, 5.0) == pytest.approx(1.8)


@pytest.mark.parametrize(
    ("m", "message"),
    [
        (True, "between 0 and 1"),
        (-0.1, "between 0 and 1"),
        (1.1, "between 0 and 1"),
        (float("nan"), "between 0 and 1"),
        (((1.0, 0.0),), "one matrix row per deme"),
        (((0.5, 0.5), (0.5, 0.5), (0.5, 0.5)), "one matrix row per deme"),
        (((0.5, 0.5, 0.0), (0.5, 0.5)), "must have 2 entries"),
    ],
)
def test_migrate_rejects_invalid_m_shapes(m: object, message: str) -> None:
    """Public `migrate` validates scalar range and matrix shape directly.

    Regression test for FIM-23: an invalid `m` used to be silently
    absorbed into a plausible-looking wrong distribution (a `bool`
    coerced to `0`/`1`, a wrong row/column count just producing a
    wrong-shaped result) rather than raising. Unreachable through
    `SimulationParams.from_mapping` (already validated there — see
    `test_params.py`), but `migrate` is public API on its own.
    """
    with pytest.raises(ValueError, match=message):
        migrate(_state(), m)  # type: ignore[arg-type]


def test_mutation_can_have_no_events_without_changing_maps(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A seeded zero-event mutation pass preserves the existing maps."""
    state = _state()
    mutated = mutate(state, 1e-12, 100, AlleleRegistry(), rng(1))
    assert mutated == state


@pytest.mark.parametrize(
    ("operation", "population_size", "message"),
    [
        (drift, 0, "positive"),
        (drift, (20,), "one gene-copy"),
        (mutate, (20,), "one gene-copy"),
    ],
)
def test_operators_validate_population_size_shape(
    operation: object,
    population_size: object,
    message: str,
) -> None:
    """Operator entry points reject invalid shared and per-deme sizes."""
    if operation is drift:
        with pytest.raises(ValueError, match=message):
            drift(_state(), population_size, np.random.default_rng(1))  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError, match=message):
            mutate(
                _state(),
                0.1,
                population_size,  # type: ignore[arg-type]
                AlleleRegistry(),
                np.random.default_rng(1),
            )


@pytest.mark.statistical
def test_drift_variance_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample variance falls in a pre-derived five-sigma band."""
    population_size = 100
    probability = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),),
    )
    generator = rng(20260814)
    observed = np.asarray(
        [
            drift(state, population_size, generator)
            .frequency_map(0, 0)
            .get(AlleleId(0), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_variance = probability * (1.0 - probability) / population_size
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance,
        abs=5.0 * variance_standard_error,
    )


@pytest.mark.statistical
def test_drift_variance_matches_binomial_theory_per_deme_when_n_is_unequal(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Each deme resamples at its own N, not a shared or averaged value."""
    sizes = (50, 400)
    probability = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),
            ({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),
        ),
    )
    generator = rng(20260814)
    observed = np.asarray(
        [
            [
                drift(state, sizes, generator)
                .frequency_map(deme, 0)
                .get(
                    AlleleId(0),
                    0.0,
                )
                for deme in range(len(sizes))
            ]
            for _ in range(replicates)
        ]
    )
    for deme, size in enumerate(sizes):
        expected_variance = probability * (1.0 - probability) / size
        variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))
        assert np.var(observed[:, deme], ddof=1) == pytest.approx(
            expected_variance,
            abs=5.0 * variance_standard_error,
        )


# `_inversion_binomial` (Stage F8, `20260901-claude-sonnet-5-fim-engine-
# backend-factory-design.md` §5.4): the one genuinely new sampling
# primitive this whole unified-RNG effort needs — a `Binomial(n, p)`
# draw that always consumes exactly one `rng.random()` uniform, no
# retry loop, unlike `rng.binomial` itself (whose own internal
# algorithm choice, and thus how much of the bit stream one call
# consumes, is opaque and `n`/`p`-dependent). These tests check the
# distribution (moment-matched against theory, banded the same way
# `test_drift_variance_matches_binomial_theory` already is), the
# "exactly one draw" property directly (not merely assumed), and the
# specific numerical regression a first, `k=0`-anchored version of this
# function had — silently, deterministically wrong for realistic deme
# population sizes — found by exactly this kind of test before it ever
# reached real simulation code.


def test_inversion_binomial_matches_theoretical_mean_and_variance() -> None:
    """Empirical mean/variance land within a Student's-t band of theory.

    Swept across `n`/`p` combinations spanning several orders of
    magnitude in `n`, including the exact range
    (`n` in the thousands, `p` away from the extremes) a first,
    rejected version of this function returned deterministically wrong
    output for — see `_inversion_binomial`'s own docstring.
    """
    replicates = 3000
    for n in (1, 5, 50, 200, 1000, 5000, 20000):
        for p in (0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99):
            rng = np.random.Generator(np.random.PCG64(hash((n, p)) % 2**31))
            samples = np.array(
                [_inversion_binomial(rng, n, p) for _ in range(replicates)]
            )
            expected_mean = n * p
            expected_variance = n * p * (1.0 - p)
            mean_standard_error = math.sqrt(expected_variance / replicates)
            assert samples.mean() == pytest.approx(
                expected_mean, abs=max(6.0 * mean_standard_error, 1e-6)
            ), f"n={n} p={p}"
            # The usual normal-approximation formula for the variance of
            # a *sample variance* itself needs enough effective spread
            # to hold at all — verified directly, not assumed: even
            # `numpy.random.Generator.binomial` (unquestionably correct)
            # fails this same check at n=1, p=0.99, so a failure here at
            # small `expected_variance` reflects the formula's own
            # limits, not a real algorithm defect. `>= 5` mirrors the
            # standard normal-approximation-to-binomial rule of thumb.
            if expected_variance >= 5.0:
                variance_standard_error = expected_variance * math.sqrt(
                    2.0 / (replicates - 1)
                )
                assert samples.var(ddof=1) == pytest.approx(
                    expected_variance, abs=6.0 * variance_standard_error
                ), f"n={n} p={p}"


def test_inversion_binomial_handles_edge_cases() -> None:
    """`n=0`, `p=0`, and `p=1` are legal, deterministic, no-draw-needed cases."""
    rng = np.random.Generator(np.random.PCG64(1))
    assert _inversion_binomial(rng, 0, 0.5) == 0
    assert _inversion_binomial(rng, 10, 0.0) == 0
    assert _inversion_binomial(rng, 10, 1.0) == 10


def test_inversion_binomial_consumes_exactly_one_uniform_for_a_real_draw() -> None:
    """The whole unification depends on this: one draw in, one count out.

    Checked directly, not assumed: seed two identical generators, spend
    one on `_inversion_binomial`, spend the other on one explicit
    `rng.random()` call (thrown away, standing in for whatever
    `_inversion_binomial` itself consumed) — if the two generators are
    still in lockstep afterward, exactly one uniform was consumed either
    way, confirmed by the *next* value each one produces being
    identical. Only for `n > 0` and `0 < p < 1` — the genuine-draw case;
    see the companion test below for the `n=0`/`p=0`/`p=1` short-circuits,
    which consume zero draws, not one.
    """
    for n, p in ((1, 0.5), (37, 0.3), (5000, 0.5), (1, 0.001), (1, 0.999)):
        seed = 20260901
        via_inversion = np.random.Generator(np.random.PCG64(seed))
        _inversion_binomial(via_inversion, n, p)

        via_explicit_draw = np.random.Generator(np.random.PCG64(seed))
        via_explicit_draw.random()

        assert via_inversion.random() == via_explicit_draw.random(), (n, p)


def test_inversion_binomial_short_circuits_consume_zero_draws() -> None:
    """`n=0`/`p=0`/`p=1` are known in advance from the arguments alone.

    Still a "fixed, known-in-advance" draw count in the sense §5.4
    actually needs (nothing about *how many* uniforms get consumed
    depends on a draw's own outcome) — just zero rather than one, since
    whether `n=0`/`p=0`/`p=1` holds is knowable before any random
    number is ever needed at all.
    """
    for n, p in ((0, 0.5), (10, 0.0), (10, 1.0)):
        seed = 20260901
        via_inversion = np.random.Generator(np.random.PCG64(seed))
        _inversion_binomial(via_inversion, n, p)

        via_untouched = np.random.Generator(np.random.PCG64(seed))

        assert via_inversion.random() == via_untouched.random(), (n, p)


def test_inversion_binomial_avoids_the_mode_zero_underflow_regression() -> None:
    """The exact `n`/`p` combinations a `k=0`-anchored version got 100% wrong.

    Before the mode-anchored fix, every one of these combinations
    returned `n` (or `0`) unconditionally, for every seed — this test
    exists specifically to keep that regression from coming back.
    """
    regressed_before_fix = ((5000, 0.3), (5000, 0.5), (5000, 0.7), (20000, 0.5))
    for n, p in regressed_before_fix:
        rng = np.random.Generator(np.random.PCG64(1))
        samples = np.array([_inversion_binomial(rng, n, p) for _ in range(500)])
        expected_mean = n * p
        # A loose band is enough here: the point is "not stuck at the
        # boundary," not a tight distributional match (already checked
        # above) — anything within 20% of the true mean rules out the
        # specific all-n/all-0 failure mode directly.
        assert samples.mean() == pytest.approx(expected_mean, rel=0.2), (n, p)
        assert not (samples == n).all(), (n, p)
        assert not (samples == 0).all(), (n, p)


# `_multinomial_via_binomial`/`_jit_multinomial_via_binomial` (JIT
# feasibility, `20260901-claude-sonnet-5-fim-engine-backend-factory-
# design.md` §5.3): the whole point of this decomposition is that it
# reproduces `numpy.random.Generator.multinomial`'s own output
# bit-for-bit, not merely the same distribution — these tests check
# exact equality, across many randomized parameter combinations with a
# fixed master seed (reproducible per commit, not left to real entropy),
# deliberately including edge cases a hand-picked example could miss:
# `n == 0`, a single category, and a zero-probability category.


def test_multinomial_via_binomial_matches_generator_multinomial_exactly() -> None:
    """The plain (unjitted) decomposition matches `rng.multinomial` bit-for-bit."""
    controller = np.random.default_rng(20260901)
    mismatches = 0
    for trial in range(1000):
        seed = int(controller.integers(0, 2**31))
        category_count = int(controller.integers(2, 12))
        raw = controller.random(category_count)
        if trial % 7 == 0:
            raw[0] = 0.0  # exercise a zero-probability category
        probabilities = raw / raw.sum()
        n = int(controller.integers(0, 2000))

        direct = np.random.Generator(np.random.PCG64(seed)).multinomial(
            n, probabilities
        )
        decomposed = _multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        if not (direct == decomposed).all():
            mismatches += 1

    assert mismatches == 0


def test_multinomial_via_binomial_handles_a_single_category() -> None:
    """A single category has no conditional draw to make — `n` goes there whole."""
    result = _multinomial_via_binomial(
        np.random.Generator(np.random.PCG64(1)), 42, np.array([1.0])
    )
    assert result.tolist() == [42]


def test_multinomial_via_binomial_handles_n_zero() -> None:
    """Zero individuals to distribute is a legal, all-zero draw."""
    result = _multinomial_via_binomial(
        np.random.Generator(np.random.PCG64(1)), 0, np.array([0.3, 0.7])
    )
    assert result.tolist() == [0, 0]


def test_jit_multinomial_via_binomial_matches_plain_decomposition() -> None:
    """The Numba-JIT-compiled path is exactly the same function, compiled."""
    pytest.importorskip("numba")
    controller = np.random.default_rng(20260902)
    for _ in range(200):
        seed = int(controller.integers(0, 2**31))
        category_count = int(controller.integers(2, 8))
        raw = controller.random(category_count)
        probabilities = raw / raw.sum()
        n = int(controller.integers(0, 500))

        plain = _multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        jitted = _jit_multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        assert plain.tolist() == list(jitted)


def test_drift_with_jit_matches_drift_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` changes nothing about `drift`'s own output, for the same seed."""
    pytest.importorskip("numba")
    unjitted = drift(_state(), 20, rng(20260901), jit=False)
    jitted = drift(_state(), 20, rng(20260901), jit=True)

    assert unjitted == jitted


def test_step_with_jit_matches_step_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` changes nothing about `step`'s own output either, end to end."""
    pytest.importorskip("numba")
    params = SimulationParams(N=20, m=0.2, mu=0.05, d=2, seed=6, loci=_state().loci)

    unjitted = step(_state(), params, AlleleRegistry(), rng(6), jit=False)
    jitted = step(_state(), params, AlleleRegistry(), rng(6), jit=True)

    assert unjitted == jitted


def test_drift_with_jit_matches_without_jit_across_many_generations_and_demes(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The batched, ragged (deme, locus) flat-buffer path stays bit-identical.

    `_state()`'s own fixture (2 demes, 1 locus) barely exercises
    `_build_flat_drift_buffers`'s own ragged, varying-category-count
    layout — this test uses many demes and loci, run across enough
    generations that some (deme, locus) pairs lose alleles to drift
    entirely (shrinking their own category count generation to
    generation, changing every later `offsets` slice's own width), which
    is exactly the shape most likely to expose an indexing bug in the
    flat-buffer-plus-offsets packing/unpacking if one existed.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.15,
        mu=0.02,
        d=12,
        seed=20260901,
        loci=(LocusSpec(1, 50), LocusSpec(2, 30), LocusSpec(3, 80)),
    )
    state = generate_initial_state(params, rng(20260901))

    unjitted_rng = rng(7)
    jitted_rng = rng(7)
    for _ in range(40):
        unjitted_state = drift(state, params.N, unjitted_rng, jit=False)
        jitted_state = drift(state, params.N, jitted_rng, jit=True)
        assert unjitted_state == jitted_state
        state = unjitted_state
