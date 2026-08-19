"""Tests for opaque allele identities."""

from collections.abc import Callable

import numpy as np
import pytest

from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
    founding_allele_ids,
)


@pytest.mark.parametrize(
    ("count", "message"),
    [(0, "at least 1"), (-1, "at least 1"), (MINTED_ID_START, "overlaps")],
)
def test_founding_allele_range_is_guarded(count: int, message: str) -> None:
    """Founding IDs cannot be empty or overlap globally minted IDs."""
    with pytest.raises(ValueError, match=message):
        founding_allele_ids(count)


def test_registry_rejects_founder_range_and_mints_in_order() -> None:
    """Mutant IDs are globally separated from founders and monotonically assigned."""
    with pytest.raises(ValueError, match="at or above"):
        AlleleRegistry(MINTED_ID_START - 1)
    registry = AlleleRegistry(MINTED_ID_START + 10)
    assert registry.next_id() == MINTED_ID_START + 10
    assert registry.next_id() == MINTED_ID_START + 11


def test_registry_returns_strictly_increasing_unique_ids() -> None:
    """Each mutation event receives a never-repeated identity."""
    registry = AlleleRegistry()

    observed = [registry.next_id() for _ in range(100)]

    assert len(set(observed)) == len(observed)
    assert [int(value) for value in observed] == list(
        range(MINTED_ID_START, MINTED_ID_START + 100)
    )


def test_founding_and_mutant_ranges_do_not_overlap() -> None:
    """Locus-relative founders cannot collide with mutants."""
    founders = founding_allele_ids(10)
    mutant = AlleleRegistry().next_id()

    assert all(founder != mutant for founder in founders)


def test_allele_identity_is_integer_equality() -> None:
    """The runtime identity contract is equality and no payload."""
    assert AlleleId(7) == AlleleId(7)
    assert AlleleId(7) != AlleleId(8)


@pytest.mark.parametrize(
    ("capacity", "initial_ids"),
    [
        (2, [AlleleId(0), AlleleId(1), AlleleId(2)]),
        (4, [AlleleId(3), AlleleId(4)]),
        (4, [AlleleId(-1)]),
    ],
)
def test_finite_allele_space_rejects_capacity_too_small_for_initial_ids(
    capacity: int,
    initial_ids: list[AlleleId],
) -> None:
    """Construction fails fast when initial IDs cannot fit the state space."""
    with pytest.raises(ValueError, match="too small"):
        FiniteAlleleSpace(capacity, initial_ids)


def test_finite_allele_space_never_targets_current_or_exceeds_capacity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A tiny space's targets stay bounded and never equal their own source.

    Capacity 4 with 100 successive mutation events (each generation's
    target becoming the next generation's source) leaves nowhere to go but
    recurrence eventually, exercising both branches deterministically.
    """
    space = FiniteAlleleSpace(4, [AlleleId(0), AlleleId(1)])
    generator = rng(20260821)
    current = AlleleId(0)
    seen = {0, 1}
    for _ in range(100):
        target = space.mutate_target(current, generator)
        assert target != current
        assert 0 <= int(target) < 4
        seen.add(int(target))
        current = target
    assert seen == {0, 1, 2, 3}


def test_finite_allele_space_fills_holes_left_by_noncontiguous_founders(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Non-contiguous founders never push minted IDs past ``capacity - 1``.

    Regression test for a defect where ``_next_unminted`` was seeded from
    ``max(initial_ids) + 1`` rather than the first actually-unused state:
    founders ``{0, 3}`` at capacity 4 left states 1 and 2 permanently
    unmintable and let later mutations target IDs 4 and 5, outside the
    declared ``K``-allele range.
    """
    space = FiniteAlleleSpace(4, [AlleleId(0), AlleleId(3)])
    generator = rng(20260819)
    current = AlleleId(0)
    seen = {0, 3}
    for _ in range(100):
        target = space.mutate_target(current, generator)
        assert target != current
        assert 0 <= int(target) < 4
        seen.add(int(target))
        current = target
    assert seen == {0, 1, 2, 3}


def test_finite_allele_space_rejects_capacity_below_two() -> None:
    """A single-state space has no "other" state for mutation to target."""
    with pytest.raises(ValueError, match="at least 2"):
        FiniteAlleleSpace(1, [AlleleId(0)])


def test_finite_allele_space_at_full_capacity_only_recurs(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Once every state is minted, every draw is a recurrence by construction."""
    space = FiniteAlleleSpace(3, [AlleleId(0), AlleleId(1), AlleleId(2)])
    generator = rng(1)

    targets = {space.mutate_target(AlleleId(0), generator) for _ in range(50)}

    assert targets <= {AlleleId(1), AlleleId(2)}


def test_finite_allele_space_underflows_to_zero_recurrence_at_huge_capacity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """An astronomically large capacity makes recurrence exactly impossible.

    Not merely rare: ``(minted - 1) / (capacity - 1)`` computed in plain
    Python floats underflows all the way to ``0.0`` well before ``minted``
    could ever grow enough to matter, so every draw is guaranteed fresh —
    the infinite-alleles model recovered exactly, not approximately, in
    this limit.
    """
    space = FiniteAlleleSpace(4**1000, [AlleleId(0), AlleleId(1)])
    generator = rng(2)

    targets = [space.mutate_target(AlleleId(0), generator) for _ in range(500)]

    assert len(set(targets)) == len(targets)
    assert all(int(target) >= 2 for target in targets)


@pytest.mark.statistical
def test_finite_allele_space_recurrence_rate_matches_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample recurrence rate falls in a pre-derived band.

    Capacity 100 with 50 states already minted gives a known recurrence
    probability of ``(50 - 1) / (100 - 1)``. Each trial reconstructs a
    fresh, identically seeded space so one trial's minting never changes
    the next trial's true probability.
    """
    capacity = 100
    minted_count = 50
    initial_ids = [AlleleId(identity) for identity in range(minted_count)]
    trials = 20_000
    expected_probability = (minted_count - 1) / (capacity - 1)
    generator = rng(20260821)

    outcomes = np.asarray(
        [
            int(
                FiniteAlleleSpace(capacity, initial_ids).mutate_target(
                    AlleleId(0), generator
                )
            )
            < minted_count
            for _ in range(trials)
        ]
    )
    observed_rate = outcomes.mean()
    standard_error = np.sqrt(
        expected_probability * (1.0 - expected_probability) / trials
    )

    assert observed_rate == pytest.approx(
        expected_probability, abs=5.0 * standard_error
    )


def test_finite_allele_registry_dispatches_by_locus_id(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Each locus keeps its own independent, differently sized state space."""
    registry = FiniteAlleleRegistry(
        {
            1: FiniteAlleleSpace(4, [AlleleId(0), AlleleId(1)]),
            2: FiniteAlleleSpace(4**10, [AlleleId(0)]),
        }
    )
    generator = rng(3)

    small_locus_targets = {
        int(registry.mutate_target(1, AlleleId(0), generator)) for _ in range(50)
    }
    large_locus_target = registry.mutate_target(2, AlleleId(0), generator)

    assert small_locus_targets <= {1, 2, 3}
    assert large_locus_target == AlleleId(1)
