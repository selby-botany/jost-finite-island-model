"""Malformed state and trajectory-row validation tests."""

from __future__ import annotations

import math

import pytest

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.state import ModelState

LOCUS = (LocusSpec(1, 100),)


def _row(**updates: object) -> dict[str, object]:
    """Return one valid serialized trajectory row."""
    row: dict[str, object] = {
        "run_id": "run-a",
        "generation": 3,
        "deme": 1,
        "locus_id": 1,
        "allele_id": 0,
        "frequency": 1.0,
    }
    row.update(updates)
    return row


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"loci": (), "frequencies": ((),)}, "at least one locus"),
        (
            {
                "loci": (LocusSpec(1, 10), LocusSpec(1, 20)),
                "frequencies": (({AlleleId(0): 1.0}, {AlleleId(0): 1.0}),),
            },
            "locus IDs",
        ),
        (
            {
                "loci": LOCUS,
                "frequencies": (({AlleleId(0): 1.0},),),
                "generation": -1,
            },
            "generation",
        ),
        (
            {"loci": LOCUS, "frequencies": ()},
            "at least one deme",
        ),
        (
            {"loci": (LocusSpec(1, 10), LocusSpec(2, 10)), "frequencies": (({},),)},
            "expected 2",
        ),
    ],
)
def test_state_shape_and_identity_invariants_are_enforced(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """State construction rejects malformed dimensions and metadata."""
    with pytest.raises(ValueError, match=message):
        ModelState(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("frequency_map", "message"),
    [
        ({AlleleId(0): 0.0}, "at least one allele"),
        ({AlleleId(0): -0.1, AlleleId(1): 1.1}, "non-negative"),
        ({AlleleId(0): math.inf}, "finite"),
        ({AlleleId(0): 0.2, AlleleId(1): 0.7}, "sum to"),
    ],
)
def test_frequency_maps_require_positive_finite_probability_vectors(
    frequency_map: dict[AlleleId, float],
    message: str,
) -> None:
    """Sparse state maps preserve the probability-vector invariant."""
    with pytest.raises(ValueError, match=message):
        ModelState(loci=LOCUS, frequencies=((frequency_map,),))


def test_state_rows_reject_empty_and_incomplete_or_inconsistent_inputs() -> None:
    """Trajectory reconstruction validates all row-level grouping invariants."""
    cases: tuple[tuple[list[dict[str, object]], str], ...] = (
        ([], "no rows"),
        ([_row(generation=2), _row(generation=3)], "one generation"),
        ([_row(run_id="a"), _row(run_id="b")], "one run"),
        ([_row(deme=2)], "start at 1"),
        ([_row(deme=1), _row(deme=3)], "contiguous"),
        ([_row(locus_id=9)], "unknown locus"),
        ([_row(), _row(allele_id=0)], "duplicate"),
    )
    for rows, message in cases:
        with pytest.raises(ValueError, match=message):
            ModelState.from_rows(rows, LOCUS)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"generation": True}, "generation.*integer"),
        ({"generation": "3"}, "generation.*integer"),
        ({"run_id": ""}, "run_id.*string"),
        ({"run_id": 1}, "run_id.*string"),
        ({"frequency": math.inf}, r"frequency.*must be in \(0, 1\]"),
        ({"frequency": -math.inf}, r"frequency.*must be in \(0, 1\]"),
    ],
)
def test_state_rows_reject_wrong_required_field_types(
    updates: dict[str, object],
    message: str,
) -> None:
    """Required row fields retain their strict integer/string/float contracts."""
    with pytest.raises(ValueError, match=message):
        ModelState.from_rows([_row(**updates)], LOCUS)


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("generation", "missing 'generation'"),
        ("run_id", "missing 'run_id'"),
        ("deme", "missing 'deme'"),
        ("locus_id", "missing 'locus_id'"),
        ("allele_id", "missing 'allele_id'"),
        ("frequency", "missing 'frequency'"),
    ],
)
def test_state_rows_report_missing_fields(
    missing: str,
    message: str,
) -> None:
    """Missing serialized fields produce stable error contracts."""
    row = _row()
    del row[missing]
    with pytest.raises(ValueError, match=message):
        ModelState.from_rows([row], LOCUS)


def test_state_support_shape_and_empty_run_id_are_validated() -> None:
    """Public serialization helpers validate their external identifiers."""
    state = ModelState(loci=LOCUS, frequencies=(({AlleleId(0): 1.0},),))
    with pytest.raises(ValueError, match="run_id"):
        state.to_rows("")
    with pytest.raises(ValueError, match="every deme"):
        state.validate_support((1, 2))
