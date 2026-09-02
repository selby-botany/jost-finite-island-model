"""Tests for the bounded-K (finite-alleles) array-native operators.

Statistical, not bit-identical, parity with `fim.model.operators` was
this module's own original correctness bar — see `fim.model.vectorized`'s
own module docstring for why, and for which functions now clear a
materially higher bar as of Stage F8 (exact numerical agreement, not
merely statistical): `migrate_vectorized`, `drift_vectorized`, and, as
of this module's own `test_mutate_vectorized_matches_dict_based_mutate_
exactly`, `mutate_vectorized` too — every operator `fim.engine`'s run
loop actually calls now agrees with its dict-based counterpart exactly,
not just statistically. Exact/invariant checks (round-tripping,
frequency sums, target != source) need no tolerance at all and are kept
separate from the remaining genuinely statistical ones (still present
for the properties that were never meant to be exact, like distributional
variance), which use a normal-approximation band matching this project's
own existing precedent (`test_drift_variance_matches_binomial_theory`).
"""

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest

from fim.model.allele import (
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import drift as drift_dict
from fim.model.operators import migrate as migrate_dict
from fim.model.operators import mutate as mutate_dict
from fim.model.state import ModelState
from fim.model.vectorized import (
    VectorizedLocusState,
    _mutate_targets_batched,
    build_vectorized_state,
    drift_vectorized,
    migrate_vectorized,
    mutate_vectorized,
    step_vectorized,
    symmetric_migration_weights,
    vectorized_state_to_model_state,
    vectorized_state_to_rows,
)


def _finite_alleles_state(deme_count: int = 4, capacity_length: int = 1) -> ModelState:
    """A multi-deme, single-locus finite-alleles state, evenly spread.

    Fills every capacity slot — deliberately the *saturated* case. A
    cross-backend probe run over only this shape once passed 20 seeds
    clean (`test_drift_vectorized_matches_dict_based_drift_exactly`'s
    own earlier form) while a real ULP-level normalization bug sat
    underneath it undetected, because "the last present allele" and
    "the last capacity slot" always coincide when nothing is unminted.
    Use `_partial_finite_alleles_state`, not this one, for any new test
    meant to catch that class of bug — see its own docstring.
    """
    capacity = finite_allele_capacity(capacity_length)
    return ModelState(
        loci=(LocusSpec(1, capacity_length),),
        frequencies=tuple(
            ({AlleleId(a): 1.0 / capacity for a in range(capacity)},)
            for _ in range(deme_count)
        ),
    )


def _partial_finite_alleles_state(
    deme_count: int, capacity_length: int, minted_count: int
) -> ModelState:
    """A multi-deme, single-locus finite-alleles state with capacity to spare.

    Unlike `_finite_alleles_state` above, only the first `minted_count`
    of `finite_allele_capacity(capacity_length)` states are ever
    minted — the realistic case for any locus whose mutation rate or
    generation count hasn't yet filled its whole state space, and the
    specific shape that exposed a real cross-backend normalization bug
    (`_multinomial_rows_batched`'s own inline comment in
    `fim.model.vectorized`): the dict-based backend normalizes a
    probability array over exactly the present alleles, while the
    array-native backend's dense row is `capacity`-wide with the unused
    slots at exactly `0.0` — summing over the wider, zero-padded row is
    not bit-identical to summing over the present-only one, even though
    the extra terms are mathematically inert.
    """
    capacity = finite_allele_capacity(capacity_length)
    assert 0 < minted_count <= capacity
    return ModelState(
        loci=(LocusSpec(1, capacity_length),),
        frequencies=tuple(
            ({AlleleId(a): 1.0 / minted_count for a in range(minted_count)},)
            for _ in range(deme_count)
        ),
    )


def test_build_and_convert_round_trips() -> None:
    """`build_vectorized_state` then converting back reproduces the original exactly."""
    state = _finite_alleles_state()

    vectorized = build_vectorized_state(state)
    restored = vectorized_state_to_model_state(vectorized)

    assert restored == state


def test_vectorized_state_to_rows_matches_model_state_to_rows() -> None:
    """The array-native row serializer matches `ModelState.to_rows` exactly."""
    state = _finite_alleles_state()
    vectorized = build_vectorized_state(state)

    assert vectorized_state_to_rows(vectorized, "run-a") == state.to_rows("run-a")


def test_build_vectorized_state_rejects_an_out_of_range_allele_id() -> None:
    """An out-of-range allele id is a configuration error, not silently kept."""
    state = ModelState(
        loci=(LocusSpec(1, 1),),  # capacity 4
        frequencies=(({AlleleId(99): 1.0},),),
    )
    with pytest.raises(ValueError, match=r"outside 0\.\.3"):
        build_vectorized_state(state)


def test_migrate_vectorized_matches_dict_based_migrate() -> None:
    """The dense matmul reproduces `fim.model.operators.migrate`'s own blend."""
    state = _finite_alleles_state(deme_count=5)
    sizes = np.array([10, 20, 30, 40, 50], dtype=np.int64)
    rate = 0.3

    expected = migrate_dict(state, rate, tuple(int(s) for s in sizes))

    vectorized = build_vectorized_state(state)
    weights = symmetric_migration_weights(rate, sizes)
    migrated = migrate_vectorized(vectorized.locus_states[0], weights)
    vectorized_result = vectorized_state_to_model_state(
        replace(vectorized, locus_states=(migrated,))
    )

    for deme in range(5):
        expected_map = expected.frequency_map(deme, 0)
        observed_map = vectorized_result.frequency_map(deme, 0)
        assert set(expected_map) == set(observed_map)
        for allele_id, value in expected_map.items():
            assert observed_map[allele_id] == pytest.approx(value, abs=1e-9)


def test_drift_vectorized_matches_dict_based_drift_exactly(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Stage F8's own deliverable: not statistical parity — exact agreement.

    `fim.model.operators.drift` and `drift_vectorized` both draw via
    the identical mode-anchored inversion-binomial algorithm now
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4), in the identical ascending-allele-id order — `drift`'s own
    dict-based path via `sorted(frequency_map)`, this module's own
    dense array natively. Checked directly, across many seeds and a
    deme count large enough that some demes draw more real (non-
    short-circuited) categories than others: given the identical
    starting state and an identically seeded `rng`, the two backends'
    own resulting frequencies match *exactly*, not merely within a
    statistical band — the counts underneath are literally the same
    integers, not just close. This is the actual, concrete proof the
    whole unified-RNG effort exists to deliver, not yet attempted
    before this test.
    """
    for seed in range(20):
        state = _finite_alleles_state(deme_count=6, capacity_length=1)
        sizes = np.array([12, 30, 7, 100, 1, 55], dtype=np.int64)

        expected = drift_dict(state, tuple(int(s) for s in sizes), rng(seed))

        vectorized = build_vectorized_state(state)
        observed_vectorized = drift_vectorized(
            vectorized.locus_states[0], sizes, rng(seed)
        )
        observed = vectorized_state_to_model_state(
            replace(vectorized, locus_states=(observed_vectorized,))
        )

        for deme in range(6):
            expected_map = expected.frequency_map(deme, 0)
            observed_map = observed.frequency_map(deme, 0)
            assert set(expected_map) == set(observed_map), (seed, deme)
            for allele_id, value in expected_map.items():
                assert observed_map[allele_id] == value, (seed, deme, allele_id)


def test_drift_vectorized_matches_dict_based_drift_exactly_with_partial_capacity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The same exact-agreement proof, but with capacity to spare.

    `test_drift_vectorized_matches_dict_based_drift_exactly` above only
    ever exercises a *saturated* capacity (`_finite_alleles_state` mints
    every state-space slot) — a real gap, because a normalization bug
    in `_multinomial_rows_batched` (`fim.model.vectorized`'s own inline
    comment) stayed invisible under that shape specifically: "the last
    present allele" and "the last capacity slot" are the same column
    when nothing is unminted, so the bug's own precondition (a present
    allele short of `capacity - 1`, with `remaining_n` still positive
    when the array-native decomposition reaches it) never arose. This
    test uses `_partial_finite_alleles_state` instead — 6 of 16 states
    minted per deme — which does exercise that precondition, and did
    catch real cross-backend mismatches before the fix landed (found
    via a direct scratch probe, not assumed).
    """
    for seed in range(20):
        state = _partial_finite_alleles_state(
            deme_count=5, capacity_length=2, minted_count=6
        )
        sizes = np.array([20, 45, 8, 100, 3], dtype=np.int64)

        expected = drift_dict(state, tuple(int(s) for s in sizes), rng(seed))

        vectorized = build_vectorized_state(state)
        observed_vectorized = drift_vectorized(
            vectorized.locus_states[0], sizes, rng(seed)
        )
        observed = vectorized_state_to_model_state(
            replace(vectorized, locus_states=(observed_vectorized,))
        )

        for deme in range(5):
            expected_map = expected.frequency_map(deme, 0)
            observed_map = observed.frequency_map(deme, 0)
            assert set(expected_map) == set(observed_map), (seed, deme)
            for allele_id, value in expected_map.items():
                assert observed_map[allele_id] == value, (seed, deme, allele_id)


def test_mutate_vectorized_matches_dict_based_mutate_exactly(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`mutate`'s own version of the `drift` exact-agreement proof.

    `fim.model.operators.mutate` and `mutate_vectorized` now draw via
    the identical event-count, source-attribution, *and* target-
    selection mechanism, in the identical per-deme order (`mutate_
    vectorized`'s own module/function docstrings) —
    `20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4's "one RNG scheme for every backend" reaching `mutate`, not
    just `drift`. Three separate divergence sources had to be found and
    fixed to get here, none of them visible from a single-deme test
    alone: a per-step-batched vs. per-deme-interleaved draw order once
    more than one deme was involved, a rejection-sampling vs. fixed-
    draw mismatch in the recurrence branch, and this same partial-
    capacity normalization bug `drift_vectorized`'s own analogous test
    above exists to catch. Uses `_partial_finite_alleles_state` for the
    same reason that one does — a saturated capacity cannot exercise
    the normalization bug at all.
    """
    capacity_length = 2
    capacity = finite_allele_capacity(capacity_length)
    initial_minted = tuple(AlleleId(i) for i in range(6))
    deme_count = 5
    sizes = np.array([20, 45, 8, 100, 3], dtype=np.int64)
    mu = 0.1

    for seed in range(30):
        state = _partial_finite_alleles_state(
            deme_count=deme_count, capacity_length=capacity_length, minted_count=6
        )
        finite_alleles = FiniteAlleleRegistry(
            {1: FiniteAlleleSpace(capacity, initial_minted)}
        )
        expected = mutate_dict(
            state,
            mu,
            tuple(int(s) for s in sizes),
            AlleleRegistry(),
            rng(seed),
            finite_alleles=finite_alleles,
        )

        vectorized = build_vectorized_state(state)
        observed_vectorized = mutate_vectorized(
            vectorized.locus_states[0], sizes, mu, rng(seed)
        )
        observed = vectorized_state_to_model_state(
            replace(vectorized, locus_states=(observed_vectorized,))
        )

        for deme in range(deme_count):
            expected_map = expected.frequency_map(deme, 0)
            observed_map = observed.frequency_map(deme, 0)
            assert set(expected_map) == set(observed_map), (seed, deme)
            for allele_id, value in expected_map.items():
                assert observed_map[allele_id] == value, (seed, deme, allele_id)


def test_symmetric_migration_weights_rows_are_stochastic() -> None:
    """Every row of the derived weight matrix sums to exactly 1."""
    sizes = np.array([7, 13, 20, 3], dtype=np.int64)
    weights = symmetric_migration_weights(0.4, sizes)

    assert weights.sum(axis=1) == pytest.approx(np.ones(4))


def test_drift_vectorized_variance_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Across many replicate draws, empirical variance matches Binomial(N, p) theory.

    Mirrors `test/model/test_operators.py`'s own
    `test_drift_variance_matches_binomial_theory` — same statistical
    bar, applied to the array-native path instead.
    """
    size = 200
    probability = 0.3
    replicates = 4000
    locus_state = VectorizedLocusState(
        frequencies=np.array([[probability, 1.0 - probability]]),
        capacity=2,
        minted_mask=np.array([True, True]),
        minted_list=np.array([0, 1]),
        minted_count=2,
        next_unminted=2,
    )
    sizes = np.array([size], dtype=np.int64)

    observed = np.array(
        [
            drift_vectorized(locus_state, sizes, rng(seed)).frequencies[0, 0]
            for seed in range(replicates)
        ]
    )
    expected_variance = probability * (1.0 - probability) / size
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))
    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance, abs=5.0 * variance_standard_error
    )


def test_mutate_targets_batched_never_selects_its_own_source(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every event's own target differs from its own source, across many trials.

    Exercises both branches (recurrence and fresh-mint) by ramping
    `minted_count` from 2 up to `capacity`, and repeats every source id
    across many seeds — the actual, checked invariant the prior version
    of this test claimed in its own name but never inspected.
    """
    capacity = 12
    for minted_count in range(2, capacity + 1):
        minted_mask = np.zeros(capacity, dtype=np.bool_)
        minted_mask[:minted_count] = True
        minted_list = np.zeros(capacity, dtype=np.int64)
        minted_list[:minted_count] = np.arange(minted_count)
        for source in range(minted_count):
            for seed in range(50):
                targets, *_ = _mutate_targets_batched(
                    rng(seed + minted_count * 1000 + source * 10_000),
                    np.array([source], dtype=np.int64),
                    capacity,
                    minted_mask.copy(),
                    minted_list.copy(),
                    minted_count,
                    minted_count,
                )
                assert targets[0] != source


def test_mutate_vectorized_preserves_frequency_invariants(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Repeated mutation keeps every deme's own frequencies a valid distribution."""
    capacity_length = 2  # capacity 16
    state = _finite_alleles_state(deme_count=10, capacity_length=capacity_length)
    vectorized = build_vectorized_state(state)
    sizes = np.full(10, 500, dtype=np.int64)

    locus_state = vectorized.locus_states[0]
    generator = rng(1)
    for _ in range(50):
        locus_state = mutate_vectorized(locus_state, sizes, 0.05, generator)

    assert np.all(locus_state.frequencies >= 0.0)
    assert locus_state.frequencies.sum(axis=1) == pytest.approx(np.ones(10))


def test_mutate_vectorized_recurrence_rate_matches_finite_allele_space(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The vectorized recurrence-vs-mint decision matches `FiniteAlleleSpace` directly.

    Builds both a real `FiniteAlleleSpace` and this module's own array
    mirror from the identical initial minted set, then compares the
    *empirical* recurrence rate each produces over many independent
    single-event trials at a fixed, known `minted_count` — the same
    normal-approximation banding this project's own statistical tests
    already use, not just an isolated assertion that the formula looks
    right.
    """
    capacity = 20
    initial_minted = tuple(AlleleId(i) for i in range(6))  # minted_count = 6
    trials = 8000

    reference_recurrences = 0
    for seed in range(trials):
        space = FiniteAlleleSpace(capacity, initial_minted)
        target = space.mutate_target(AlleleId(0), rng(seed))
        # A recurrence always lands among the already-minted ids (0..5,
        # excluding the source, 0); a fresh mint always lands on the
        # next unminted id — deterministically 6, since nothing has been
        # minted beyond the initial set on this, the very first call.
        if int(target) < 6:
            reference_recurrences += 1

    vectorized_recurrences = 0
    for seed in range(trials):
        minted_mask = np.zeros(capacity, dtype=np.bool_)
        minted_mask[:6] = True
        minted_list = np.zeros(capacity, dtype=np.int64)
        minted_list[:6] = np.arange(6)
        locus_state = VectorizedLocusState(
            frequencies=np.zeros((1, capacity)),
            capacity=capacity,
            minted_mask=minted_mask,
            minted_list=minted_list,
            minted_count=6,
            next_unminted=6,
        )
        targets, _, _, _, _ = _mutate_targets_batched(
            rng(seed),
            np.array([0], dtype=np.int64),
            capacity,
            locus_state.minted_mask.copy(),
            locus_state.minted_list.copy(),
            locus_state.minted_count,
            locus_state.next_unminted,
        )
        if targets[0] < 6:
            vectorized_recurrences += 1

    expected_probability = 5 / 19  # (minted_count - 1) / (capacity - 1)
    standard_error = np.sqrt(expected_probability * (1 - expected_probability) / trials)
    assert reference_recurrences / trials == pytest.approx(
        expected_probability, abs=5.0 * standard_error
    )
    assert vectorized_recurrences / trials == pytest.approx(
        expected_probability, abs=5.0 * standard_error
    )
    assert reference_recurrences / trials == pytest.approx(
        vectorized_recurrences / trials, abs=10.0 * standard_error
    )


def test_step_vectorized_preserves_frequency_invariants(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every deme's own frequencies sum to 1 after a full fused generation."""
    state = _finite_alleles_state(deme_count=6, capacity_length=1)
    vectorized = build_vectorized_state(state)
    sizes = np.full(6, 200, dtype=np.int64)
    weights = symmetric_migration_weights(0.1, sizes)
    generator = rng(20260901)

    for _ in range(20):
        vectorized = step_vectorized(vectorized, (weights,), (0.02,), sizes, generator)

    assert vectorized.generation == 20
    for locus_state in vectorized.locus_states:
        assert locus_state.frequencies.sum(axis=1) == pytest.approx(np.ones(6))
        assert np.all(locus_state.frequencies >= 0.0)
    restored = vectorized_state_to_model_state(vectorized)
    restored.validate_support(tuple(int(s) for s in sizes))
