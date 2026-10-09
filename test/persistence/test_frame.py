"""Tests for `fim.persistence.frame`: frames, layouts and row conversion.

A frame is the flat-array spelling of one generation's rows, so the
central property is that converting rows to a frame and back returns the
same rows. The rest pin the pair numbering, the canonical order, and
every refusal. Every case is seeded or fixed; none depends on timing.
"""

from __future__ import annotations

import numpy as np
import pytest

from fim.engine import fim
from fim.model.initial import generate_initial_state
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.persistence.frame import (
    UNKNOWN_DEME_SIZE,
    FrameLayout,
    TrajectoryFrame,
    frame_to_rows,
    layout_for_sizes,
    layout_for_state,
    rows_to_frame,
    state_to_frame,
    validate_frame,
)
from fim.persistence.store import InMemoryTrajectoryStore

RUN_ID = "run-frame"


def _row(
    deme: int, locus_id: int, allele_id: int, frequency: float, generation: int = 4
) -> dict[str, object]:
    """Build one trajectory row with the engine's field order."""
    return {
        "run_id": RUN_ID,
        "generation": generation,
        "deme": deme,
        "locus_id": locus_id,
        "allele_id": allele_id,
        "frequency": frequency,
    }


def _layout() -> FrameLayout:
    """Two demes of 100 and 50 gene copies, loci 7 and 3 (in that order)."""
    return layout_for_sizes([7, 3], [100, 50])


def _rows() -> list[dict[str, object]]:
    """A generation in pair order: demes, then loci, then ascending alleles."""
    return [
        _row(1, 7, 1, 0.25),
        _row(1, 7, 2, 0.75),
        _row(1, 3, 5, 1.0),
        _row(2, 7, 2, 1.0),
        _row(2, 3, 5, 0.5),
        _row(2, 3, 9, 0.5),
    ]


def test_layout_numbers_pairs_deme_major_then_locus_position() -> None:
    """Pair 0 is deme 1 locus 7; the locus order is the layout's, not sorted."""
    layout = _layout()
    assert (layout.demes, layout.loci, layout.pairs) == (2, 2, 4)
    assert layout.pair_index(1, 7) == 0
    assert layout.pair_index(1, 3) == 1
    assert layout.pair_index(2, 7) == 2
    assert layout.pair_index(2, 3) == 3


@pytest.mark.parametrize(
    ("deme", "locus_id"),
    [(0, 7), (3, 7), (1, 99)],
)
def test_layout_refuses_a_pair_it_does_not_have(deme: int, locus_id: int) -> None:
    """A deme outside `1..demes` or an unknown locus is an error."""
    with pytest.raises(ValueError, match=r"outside|not in the layout"):
        _layout().pair_index(deme, locus_id)


@pytest.mark.parametrize(
    ("loci", "sizes", "message"),
    [
        ([], [10], "at least one locus"),
        ([1], [], "at least one deme"),
        ([1, 1], [10], "unique"),
        ([1], [-1], "negative"),
    ],
)
def test_layout_refuses_a_malformed_shape(
    loci: list[int], sizes: list[int], message: str
) -> None:
    """Empty, repeated or negative layout parts are refused at construction."""
    with pytest.raises(ValueError, match=message):
        layout_for_sizes(loci, sizes)


def test_rows_to_frame_and_back_returns_the_same_rows() -> None:
    """Rows in pair order survive the round trip exactly, field order included."""
    layout = _layout()
    rows = _rows()
    frame = rows_to_frame(rows, layout, run_id=RUN_ID, generation=4)
    assert frame.generation == 4
    assert frame.counts.tolist() == [2, 1, 1, 2]
    assert frame.allele_ids.tolist() == [1, 2, 5, 2, 5, 9]
    assert frame.frequencies.tolist() == [0.25, 0.75, 1.0, 1.0, 0.5, 0.5]
    assert frame.rows == 6
    back = frame_to_rows(frame, layout, RUN_ID)
    assert back == rows
    assert [list(row) for row in back] == [list(row) for row in rows]


def test_rows_in_another_order_are_put_in_pair_order() -> None:
    """The frame is canonical: pairs ascending, each pair keeps its given order."""
    layout = _layout()
    shuffled = [_rows()[i] for i in (4, 0, 5, 3, 2, 1)]
    frame = rows_to_frame(shuffled, layout)
    assert frame_to_rows(frame, layout, RUN_ID) == [
        _rows()[i] for i in (0, 1, 2, 3, 4, 5)
    ]


def test_rows_to_frame_infers_the_generation_from_the_rows() -> None:
    """Without a `generation` argument the rows' own generation is used."""
    frame = rows_to_frame(_rows(), _layout())
    assert frame.generation == 4


def test_rows_of_two_generations_are_refused() -> None:
    """One frame is one generation."""
    rows = _rows()
    rows[-1] = _row(2, 3, 9, 0.5, generation=5)
    with pytest.raises(ValueError, match="share a generation"):
        rows_to_frame(rows, _layout())


def test_rows_to_frame_refuses_an_empty_generation() -> None:
    """A generation must hold at least one row."""
    with pytest.raises(ValueError, match="at least one row"):
        rows_to_frame([], _layout())


def test_rows_to_frame_checks_run_and_generation_when_asked() -> None:
    """A row of another run or generation fails when the caller names them."""
    with pytest.raises(ValueError, match="run_id"):
        rows_to_frame(_rows(), _layout(), run_id="another")
    with pytest.raises(ValueError, match="generation"):
        rows_to_frame(_rows(), _layout(), generation=9)


@pytest.mark.parametrize("frequency", [0.0, -0.5, 1.5, float("nan"), float("inf")])
def test_rows_to_frame_refuses_a_frequency_outside_the_unit_interval(
    frequency: float,
) -> None:
    """Validation keeps `(0, 1]` and finite, as every store does."""
    rows = _rows()
    rows[0] = _row(1, 7, 1, frequency)
    with pytest.raises(ValueError, match="frequency"):
        rows_to_frame(rows, _layout())


def test_validate_false_trusts_the_rows() -> None:
    """The engine's own rows skip the per-row checks (and still convert)."""
    frame = rows_to_frame(_rows(), _layout(), validate=False)
    assert frame.counts.tolist() == [2, 1, 1, 2]


def test_layout_infer_reads_demes_and_loci_from_rows() -> None:
    """Without a layout the demes are `1..max` and loci keep first appearance."""
    layout = FrameLayout.infer(_rows())
    assert layout.locus_ids == (7, 3)
    assert layout.deme_sizes == (UNKNOWN_DEME_SIZE, UNKNOWN_DEME_SIZE)
    with pytest.raises(ValueError, match="at least one row"):
        FrameLayout.infer([])


def _frame(**changes: object) -> TrajectoryFrame:
    """Return the good frame of `_rows`, with some parts replaced."""
    parts = {
        "generation": 4,
        "counts": np.array([2, 1, 1, 2], dtype=np.int32),
        "allele_ids": np.array([1, 2, 5, 2, 5, 9], dtype=np.int64),
        "frequencies": np.array([0.25, 0.75, 1.0, 1.0, 0.5, 0.5]),
    }
    parts.update(changes)
    return TrajectoryFrame(**parts)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"generation": -1}, "generation"),
        ({"counts": np.array([2, 1, 1], dtype=np.int32)}, "counts"),
        ({"counts": np.array([2, 1, 1, 3], dtype=np.int32)}, "sum"),
        ({"counts": np.array([-1, 1, 1, 5], dtype=np.int32)}, "non-negative"),
        ({"allele_ids": np.array([1, 2, 5, 2, 5], dtype=np.int64)}, "same length"),
        ({"allele_ids": np.array([-1, 2, 5, 2, 5, 9], dtype=np.int64)}, "negative"),
        ({"frequencies": np.array([0.25, 0.75, 1.0, 1.0, 0.5, 0.0])}, r"\(0, 1\]"),
        (
            {
                "counts": np.zeros(4, dtype=np.int32),
                "allele_ids": np.zeros(0, dtype=np.int64),
                "frequencies": np.zeros(0),
            },
            "at least one row",
        ),
    ],
)
def test_validate_frame_refuses_an_inconsistent_frame(
    changes: dict[str, object], message: str
) -> None:
    """Every way a frame can disagree with itself or its layout is reported."""
    validate_frame(_frame(), _layout())
    with pytest.raises(ValueError, match=message):
        validate_frame(_frame(**changes), _layout())


def test_frame_to_rows_refuses_an_empty_run_id() -> None:
    """Rows need a run identity."""
    with pytest.raises(ValueError, match="run_id"):
        frame_to_rows(_frame(), _layout(), "")


def test_a_real_initial_state_survives_the_round_trip() -> None:
    """Frames carry a generated generation zero exactly, ids and float bits."""
    params = SimulationParams(
        gene_copies=(30, 40, 50),
        m=0.1,
        mu=0.01,
        d=3,
        seed=7,
        loci=tuple(LocusSpec(i + 1, 50) for i in range(4)),
        initial_allele_count=3,
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=5,
        n_replicates=1,
        replicate_tolerance=None,
    )
    state = generate_initial_state(params, np.random.Generator(np.random.PCG64(7)))
    layout = layout_for_sizes([locus.locus_id for locus in state.loci], [30, 40, 50])
    rows = state.to_rows(RUN_ID)
    frame = rows_to_frame(rows, layout, run_id=RUN_ID, generation=0)
    assert frame_to_rows(frame, layout, RUN_ID) == rows


def test_state_to_frame_equals_to_rows_for_every_generation_of_a_run() -> None:
    """Backends L and G hand over `ModelState`; its frame must equal its rows."""
    params = SimulationParams(
        gene_copies=(30, 40),
        m=0.2,
        mu=0.05,
        d=2,
        seed=11,
        loci=tuple(LocusSpec(i + 1, 50) for i in range(3)),
        initial_allele_count=2,
        convergence_window=4,
        convergence_tolerance=1e-12,
        max_generations=20,
        n_replicates=1,
        replicate_tolerance=None,
    )
    store = InMemoryTrajectoryStore()
    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        run_id=RUN_ID,
    )
    assert not isinstance(result, tuple)
    state = result.final_state
    layout = layout_for_state(state, (30, 40))
    assert layout.deme_sizes == (30, 40)
    frame = state_to_frame(state)
    assert frame.generation == state.generation
    assert frame_to_rows(frame, layout, RUN_ID) == state.to_rows(RUN_ID)
    with pytest.raises(ValueError, match="one entry per deme"):
        layout_for_state(state, (30,))
    assert layout_for_state(state).deme_sizes == (UNKNOWN_DEME_SIZE,) * 2
