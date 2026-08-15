"""Tests for opaque allele identities."""

import pytest

from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
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
