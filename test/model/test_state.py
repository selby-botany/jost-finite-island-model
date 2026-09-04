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


def test_validate_false_produces_byte_identical_frequencies_for_valid_input() -> None:
    """`validate=False` changes nothing about a valid state's own stored data.

    Direct, mechanism-level proof for the `step()`-internal fast path
    (`20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
    §6.3 item 3, `migrate`'s/`mutate`'s own `ModelState(..., validate=
    False)` calls): for input that already satisfies every check
    `validate=True` would run, the resulting `.frequencies` structure —
    same keys, same `AlleleId` types, same float values, same
    `MappingProxyType` wrapping — is exactly equal either way. Not an
    integration-level "does the whole pipeline still match" check (the
    existing `LinealBackend`/`GenerationalBackend` golden-parity and
    equilibrium suites already cover that, unchanged and still passing);
    this isolates the one function whose own contract this change
    actually touches.
    """
    frequencies = (
        (
            {AlleleId(0): 0.25, AlleleId(1): 0.75},
            {AlleleId(0): 1.0},
        ),
        (
            {AlleleId(0): 0.5, AlleleId(1): 0.5},
            {AlleleId(1): 1.0},
        ),
    )
    checked = ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 200)),
        frequencies=frequencies,
        generation=4,
    )
    unchecked = ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 200)),
        frequencies=frequencies,
        generation=4,
        validate=False,
    )
    assert checked == unchecked
    assert checked.frequencies == unchecked.frequencies
    for deme_index in range(checked.deme_count):
        for locus_index in range(checked.locus_count):
            checked_map = checked.frequency_map(deme_index, locus_index)
            unchecked_map = unchecked.frequency_map(deme_index, locus_index)
            assert dict(checked_map) == dict(unchecked_map)
            for allele_id, value in checked_map.items():
                # Exact float equality, not `pytest.approx` -- the whole
                # point is that skipping the checks cannot change a
                # single bit of a value that was already going to pass
                # them.
                assert unchecked_map[allele_id] == value


def test_validate_false_still_enforces_every_structural_check() -> None:
    """`validate=False` only ever reaches `_normalize_frequency_map`'s
    own per-allele checks -- `ModelState.__post_init__`'s own
    structural checks (empty loci, duplicate locus IDs, a negative
    generation, a deme/locus-count mismatch) run unconditionally either
    way, matching `__post_init__`'s own docstring.
    """
    with pytest.raises(ValueError, match="at least one locus"):
        ModelState(loci=(), frequencies=(), validate=False)
    with pytest.raises(ValueError, match="non-negative integer"):
        ModelState(
            loci=(LocusSpec(1, 100),),
            frequencies=(({AlleleId(0): 1.0},),),
            generation=-1,
            validate=False,
        )
    with pytest.raises(ValueError, match="loci; expected"):
        ModelState(
            loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
            frequencies=(({AlleleId(0): 1.0},),),  # only one locus map
            validate=False,
        )


def test_validate_false_skips_only_the_per_allele_checks() -> None:
    """The documented trust boundary, made explicit: `validate=False`
    really does skip the sum-to-1/finite/non-negative checks
    `_normalize_frequency_map` would otherwise run -- input that would
    be rejected under `validate=True` is accepted (and silently
    filtered/coerced exactly as the checked path would have shaped it,
    had it not raised first) under `validate=False`. This is the
    documented, deliberate cost of the fast path: it is only ever
    reached with data `step()`'s own `migrate`/`mutate` just produced,
    never with untrusted input.
    """
    frequencies = (({AlleleId(0): 0.4},),)  # sums to 0.4, not 1
    with pytest.raises(ValueError, match="sum to"):
        ModelState(loci=(LocusSpec(1, 100),), frequencies=frequencies)
    unchecked = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=frequencies,
        validate=False,
    )
    assert dict(unchecked.frequency_map(0, 0)) == {AlleleId(0): 0.4}


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
