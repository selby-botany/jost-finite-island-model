"""Tests for sparse model-state invariants and serialization."""

import pickle

import pytest

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


@pytest.mark.parametrize(
    ("allele_id", "message"),
    [(1.9, "must be an integer"), (-3, "must be a non-negative integer")],
)
def test_direct_construction_rejects_malformed_allele_ids(
    allele_id: object,
    message: str,
) -> None:
    """`ModelState`'s own constructor validates allele identity the same
    way the config parser's `p_0` handling does.

    Regression test for S5: `_normalize_frequency_map` was a bare
    ``AlleleId(int(raw_allele_id))``, silently truncating a non-integral
    float (`1.9` to `1`) and accepting a negative allele ID — the
    config parser (`fim.model.params._parse_initial_frequencies`) had
    already been guarded against exactly this, but `ModelState`'s
    public constructor, reachable by a downstream embedder directly and
    not only through YAML config, retained the defect verbatim.
    """
    with pytest.raises(ValueError, match=message):
        ModelState(
            loci=(LocusSpec(1, 100),),
            frequencies=(({allele_id: 1.0},),),  # type: ignore[dict-item]
        )


@pytest.mark.parametrize(
    ("frequency", "message"),
    [
        (True, "must be in"),
        ("1", "must be in"),
        (0.0, "must be in"),
        (1.5, "must be in"),
        (float("nan"), "must be in"),
    ],
)
def test_from_rows_rejects_boolean_string_and_out_of_bounds_frequencies(
    frequency: object,
    message: str,
) -> None:
    """`ModelState.from_rows` validates a row's frequency field the same
    way `fim.persistence.store.normalize_row` does for the identical
    row schema.

    Regression test for S6: `_required_float` (now `_required_frequency`)
    was a bare ``float(row[key])``, which coerces `True` to `1.0` and
    the string `"1"` to `1.0` — both of which persistence's own
    `_frequency_field` already rejected for the same field, plus its
    `(0, 1]` bound, which `from_rows` did not enforce at all.
    """
    row = {
        "run_id": "run-a",
        "generation": 0,
        "deme": 1,
        "locus_id": 1,
        "allele_id": 0,
        "frequency": frequency,
    }
    with pytest.raises(ValueError, match=message):
        ModelState.from_rows([row], (LocusSpec(1, 100),))
