"""Tests for sparse model-state invariants and serialization."""

import pickle

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.state import ModelState


def _state() -> ModelState:
    """Return a multi-deme, multi-locus sparse state."""
    return ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 200)),
        frequencies=(
            (
                {AlleleId(0): 0.25, AlleleId(1): 0.75},
                {AlleleId(0): 1.0},
            ),
            (
                {AlleleId(0): 0.5, AlleleId(1): 0.5},
                {AlleleId(1): 1.0},
            ),
        ),
        generation=4,
    )


def test_total_frequency_reports_each_probability_vector() -> None:
    """Each deme/locus vector sums to one."""
    assert _state().total_frequency() == {
        (1, 1): 1.0,
        (1, 2): 1.0,
        (2, 1): 1.0,
        (2, 2): 1.0,
    }


def test_rows_round_trip_exactly() -> None:
    """Sparse long-form rows preserve value equality."""
    original = _state()

    restored = ModelState.from_rows(original.to_rows("run-test"), original.loci)

    assert restored == original


def test_state_survives_a_pickle_round_trip() -> None:
    """A `MappingProxyType`-backed state pickles like any other value.

    A `RunResult` nests a `ModelState` and crosses a process boundary
    under `fim.engine`'s opt-in parallel replicate execution
    (`max_workers`); this is the property that makes that possible.
    """
    original = _state()

    restored = pickle.loads(pickle.dumps(original))

    assert restored == original


def test_equality_is_independent_of_mapping_order() -> None:
    """Dictionary insertion order has no biological meaning."""
    first = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({AlleleId(0): 0.2, AlleleId(1): 0.8},),),
    )
    second = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({AlleleId(1): 0.8, AlleleId(0): 0.2},),),
    )

    assert first == second


def test_invalid_frequency_sum_is_rejected() -> None:
    """A malformed probability vector cannot enter the model."""
    try:
        ModelState(
            loci=(LocusSpec(1, 100),),
            frequencies=(({AlleleId(0): 0.2, AlleleId(1): 0.7},),),
        )
    except ValueError as error:
        assert "sum" in str(error)
    else:
        raise AssertionError("invalid state was accepted")


def test_support_cannot_exceed_population_size() -> None:
    """Support is bounded by the number of gene copies."""
    state = _state()

    try:
        state.validate_support((1, 2))
    except ValueError as error:
        assert "N is 1" in str(error)
    else:
        raise AssertionError("oversized support was accepted")
