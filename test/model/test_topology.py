"""Tests for sparse stepping-stone migration-topology construction."""

import math

import pytest

from fim.model.topology import dense_matrix_from_neighbors, stepping_stone_neighbors


def test_ring_matrix_matches_hand_derived_values() -> None:
    """A small ring's dense matrix matches an independently computed value."""
    neighbors = stepping_stone_neighbors(4, topology="ring", rate=0.4)
    matrix = dense_matrix_from_neighbors(neighbors, 4)

    assert matrix == (
        (0.6, 0.2, 0.0, 0.2),
        (0.2, 0.6, 0.2, 0.0),
        (0.0, 0.2, 0.6, 0.2),
        (0.2, 0.0, 0.2, 0.6),
    )


def test_linear_matrix_matches_hand_derived_values() -> None:
    """A small bounded chain's dense matrix matches an independently computed value."""
    neighbors = stepping_stone_neighbors(4, topology="linear", rate=0.4)
    matrix = dense_matrix_from_neighbors(neighbors, 4)

    assert matrix == (
        (0.6, 0.4, 0.0, 0.0),
        (0.2, 0.6, 0.2, 0.0),
        (0.0, 0.2, 0.6, 0.2),
        (0.0, 0.0, 0.4, 0.6),
    )


def test_ring_wraps_but_linear_does_not() -> None:
    """A ring connects the two end demes; a linear chain leaves them at zero.

    This is the one property that actually distinguishes the two
    topologies — everything else about their construction is shared.
    """
    ring = dense_matrix_from_neighbors(
        stepping_stone_neighbors(5, topology="ring", rate=0.2), 5
    )
    linear = dense_matrix_from_neighbors(
        stepping_stone_neighbors(5, topology="linear", rate=0.2), 5
    )

    assert ring[0][4] == pytest.approx(0.1)
    assert ring[4][0] == pytest.approx(0.1)
    assert linear[0][4] == 0.0
    assert linear[4][0] == 0.0


@pytest.mark.parametrize("d", [2, 3, 4, 10, 25])
def test_linear_matrix_is_always_row_stochastic(d: int) -> None:
    """Every deme's row sums to exactly 1, regardless of neighbor count."""
    neighbors = stepping_stone_neighbors(d, topology="linear", rate=0.37)
    matrix = dense_matrix_from_neighbors(neighbors, d)

    for row in matrix:
        assert math.fsum(row) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("d", [3, 4, 10, 25])
def test_ring_matrix_is_always_row_stochastic(d: int) -> None:
    """Every deme's row sums to exactly 1 for every valid ring size."""
    neighbors = stepping_stone_neighbors(d, topology="ring", rate=0.37)
    matrix = dense_matrix_from_neighbors(neighbors, d)

    for row in matrix:
        assert math.fsum(row) == pytest.approx(1.0, abs=1e-12)


def test_linear_end_demes_send_their_whole_rate_to_one_neighbor() -> None:
    """An end deme has one neighbor, so that neighbor gets the full rate.

    Interior demes split ``rate`` between two neighbors instead.
    """
    neighbors = stepping_stone_neighbors(6, topology="linear", rate=0.3)

    assert neighbors[1] == {2: 0.3}
    assert neighbors[6] == {5: 0.3}
    assert neighbors[3] == {2: 0.15, 4: 0.15}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"d": 1, "topology": "linear", "rate": 0.1}, "at least 2"),
        ({"d": 2, "topology": "ring", "rate": 0.1}, "at least 3 demes"),
        ({"d": 4, "topology": "square", "rate": 0.1}, "must be one of"),
        ({"d": 4, "topology": "ring", "rate": 1.5}, "between 0 and 1"),
        ({"d": 4, "topology": "ring", "rate": float("nan")}, "between 0 and 1"),
        ({"d": 4, "topology": "ring", "rate": -0.1}, "between 0 and 1"),
    ],
)
def test_stepping_stone_neighbors_rejects_invalid_configuration(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Every documented validation rule rejects its invalid input."""
    with pytest.raises(ValueError, match=message):
        stepping_stone_neighbors(**kwargs)  # type: ignore[arg-type]


def test_dense_matrix_from_neighbors_leaves_absent_demes_at_identity() -> None:
    """A deme missing from the sparse map migrates with nobody."""
    matrix = dense_matrix_from_neighbors({}, 3)

    assert matrix == (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )


@pytest.mark.parametrize(
    ("neighbors", "d", "message"),
    [
        ({1: {2: 0.6, 3: 0.6}}, 3, "sum to more than 1"),
        ({1: {1: 0.1}}, 3, "cannot list itself"),
        ({1: {9: 0.1}}, 3, "neighbor 9 is outside"),
        ({9: {1: 0.1}}, 3, "outside 1..3"),
        ({0: {1: 0.1}}, 3, "outside 1..3"),
        ({1: {2: 1.5}}, 3, "must be between 0 and 1"),
    ],
)
def test_dense_matrix_from_neighbors_rejects_malformed_maps(
    neighbors: dict[int, dict[int, float]],
    d: int,
    message: str,
) -> None:
    """Every documented validation rule rejects its invalid sparse map."""
    with pytest.raises(ValueError, match=message):
        dense_matrix_from_neighbors(neighbors, d)
