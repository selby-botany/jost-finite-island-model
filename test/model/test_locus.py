"""Tests for locus metadata."""

from dataclasses import FrozenInstanceError

import pytest

from fim.model.locus import LocusSpec


def test_locus_is_immutable_and_hashable() -> None:
    """Locus descriptions remain safe value objects."""
    locus = LocusSpec(1, 200)

    assert hash(locus)
    with pytest.raises(FrozenInstanceError):
        locus.length = 300  # type: ignore[misc]


@pytest.mark.parametrize(
    ("locus_id", "length"),
    [(0, 100), (1, 0), (-1, 100), (1, -1)],
)
def test_locus_rejects_nonpositive_values(locus_id: int, length: int) -> None:
    """Both locus identity and length are positive."""
    with pytest.raises(ValueError):
        LocusSpec(locus_id, length)
