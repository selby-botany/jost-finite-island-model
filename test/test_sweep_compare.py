"""Tests for `fim.sweep_compare`: joining sweeps and the Welch difference.

Most tests build the sweeps' tables by hand and serve them in place of
`ResultTable.read`, so no simulation runs and every number is checkable on
paper; a last test compares two real, tiny sweeps end to end.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_compare import (
    SweepRef,
    compare_sweeps,
    welch_difference,
)
from fim.sweep_run import LocalPointRunner, create_sweep_study, run_sweep
from fim.sweep_table import (
    PointHealth,
    PointRow,
    ResultTable,
    StatisticCell,
    TablePoint,
)

_BASE: dict[str, Any] = {
    "N": 100,
    "ploidy": "haploid",
    "d": 4,
    "m": 0.01,
    "mu": 0.001,
    "seed": 7,
    "n_replicates": 5,
    "max_generations": 1000,
    "name": "A sweep",
}


def _cell(mean: float, std: float | None = 0.04, count: int = 5) -> StatisticCell:
    """A statistic cell with a 95% interval filled in plausibly."""
    return StatisticCell(mean, mean - 0.1, mean + 0.1, 0.1, std, count, 0.95)


def _table(
    study_id: str,
    name: str,
    axes: tuple[Any, ...],
    cells: dict[tuple[Any, ...], StatisticCell | None],
    *,
    base: dict[str, Any] | None = None,
    seeds: dict[tuple[Any, ...], int] | None = None,
    seed_policy: str = "spaced",
    health: dict[tuple[Any, ...], PointHealth] | None = None,
) -> ResultTable:
    """A table with one point per coordinate tuple; `None` means unfinished."""
    spec = SweepSpec(
        base={**_BASE, **(base or {})},
        axes=axes,
        seed_policy=seed_policy,  # type: ignore[arg-type]
    )
    keys = [axis.key for axis in axes]
    points = []
    for index, (coordinate, cell) in enumerate(cells.items()):
        coordinates = dict(zip(keys, coordinate, strict=True))
        row = None
        if cell is not None:
            row = PointRow(
                index=index,
                coordinates=coordinates,
                run_id=f"{study_id}-{index}",
                state="done",
                directory=Path("/nowhere"),
                n_replicates=cell.sample_count,
                seed=(seeds or {}).get(coordinate, 1000 + index),
                software_version="1.2.0",
                engine_backend="lineal",
                started_at=None,
                ended_at=None,
                statistics={"D": cell},
                health=(health or {}).get(coordinate),
                parameters={},
            )
        points.append(
            TablePoint(
                index,
                coordinates,
                f"{study_id}-{index}",
                "done" if cell else "waiting",
                row,
            )
        )
    return ResultTable(study_id, name, spec, points)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Serve prebuilt tables in place of reading Studies."""
    served: dict[str, ResultTable] = {}

    def read(cls: Any, study_id: str, *, results: Path | None = None) -> ResultTable:
        del cls, results
        return served[study_id]

    monkeypatch.setattr(ResultTable, "read", classmethod(read))

    def add(*tables: ResultTable) -> None:
        for table in tables:
            served[table.study_id] = table

    return add


_M = expand_axis("m", [0.01, 0.1])
_D = expand_axis("d", [2, 3])


def test_the_welch_interval_matches_a_hand_computed_case() -> None:
    # se^2 = 0.04^2/5 + 0.06^2/8 = 0.00077; df = 10.87 -> 10; t(10, 95%) = 2.228
    difference = welch_difference(_cell(0.30, 0.04, 5), _cell(0.20, 0.06, 8))

    assert difference.difference == pytest.approx(0.10)
    assert difference.degrees_of_freedom == 10
    assert difference.confidence == 0.95
    assert difference.half_width == pytest.approx(0.0618245, abs=1e-6)
    assert difference.low == pytest.approx(0.0381755, abs=1e-6)
    assert difference.high == pytest.approx(0.1618245, abs=1e-6)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (StatisticCell(0.3), _cell(0.2)),  # a single run has no spread
        (_cell(0.3, count=1), _cell(0.2)),  # one replicate
        (_cell(0.3, std=None), _cell(0.2)),
    ],
)
def test_without_two_replicates_and_a_spread_there_is_a_difference_but_no_interval(
    first: StatisticCell, second: StatisticCell
) -> None:
    difference = welch_difference(first, second)

    assert difference.difference == pytest.approx(0.1)
    assert (difference.low, difference.high, difference.half_width) == (
        None,
        None,
        None,
    )
    assert difference.degrees_of_freedom is None


def test_two_sweeps_join_on_their_shared_axis_and_get_a_difference(serve: Any) -> None:
    serve(
        _table("a", "Island", (_M,), {(0.01,): _cell(0.30), (0.1,): _cell(0.50)}),
        _table(
            "b",
            "Ring",
            (_M,),
            {(0.01,): _cell(0.20), (0.1,): _cell(0.45)},
            base={"d": 6},
        ),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert result.on == ("m",)
    assert result.labels == ("Island", "Ring")
    assert [dict(p.coordinates) for p in result.points] == [{"m": 0.01}, {"m": 0.1}]
    first = result.points[0]
    assert (first.cells["Island"].mean, first.cells["Ring"].mean) == (0.30, 0.20)  # type: ignore[union-attr]
    assert first.difference is not None
    assert first.difference.difference == pytest.approx(0.10)
    assert result.base_differences == {"d": {"Island": 4, "Ring": 6}}


def test_labels_default_to_the_study_names_and_can_be_given(serve: Any) -> None:
    serve(
        _table("a", "Island", (_M,), {(0.01,): _cell(0.3), (0.1,): _cell(0.5)}),
        _table("b", "Island", (_M,), {(0.01,): _cell(0.2), (0.1,): _cell(0.4)}),
    )

    with pytest.raises(ValueError, match="different labels"):
        compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")
    result = compare_sweeps(
        [SweepRef("a", "this year"), SweepRef("b", "last year")], statistic="D"
    )

    assert result.labels == ("this year", "last year")


def test_a_value_only_one_sweep_has_is_left_out_of_the_join(serve: Any) -> None:
    wide = expand_axis("m", [0.01, 0.1, 0.5])
    serve(
        _table(
            "a",
            "A",
            (wide,),
            {(0.01,): _cell(0.3), (0.1,): _cell(0.4), (0.5,): _cell(0.5)},
        ),
        _table("b", "B", (_M,), {(0.01,): _cell(0.2), (0.1,): _cell(0.3)}),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert [p.coordinates["m"] for p in result.points] == [0.01, 0.1]


def test_a_flagged_point_stays_flagged_in_a_comparison(serve: Any) -> None:
    serve(
        _table(
            "a",
            "A",
            (_M,),
            {(0.01,): _cell(0.3), (0.1,): _cell(0.4)},
            health={(0.01,): PointHealth(5, 3, 2, 0, {}, 0.01)},
        ),
        _table("b", "B", (_M,), {(0.01,): _cell(0.2), (0.1,): None}),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert result.points[0].health == {"A": "degraded", "B": None}
    assert result.points[1].health == {"A": None, "B": None}


def test_a_deleted_or_unfinished_point_leaves_a_none_cell_and_no_difference(
    serve: Any,
) -> None:
    serve(
        _table("a", "A", (_M,), {(0.01,): _cell(0.3), (0.1,): _cell(0.4)}),
        _table("b", "B", (_M,), {(0.01,): _cell(0.2), (0.1,): None}),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    missing = result.points[1]
    assert missing.cells["B"] is None
    assert missing.seeds["B"] is None
    assert missing.difference is None


def test_more_than_two_sweeps_have_cells_but_no_difference(serve: Any) -> None:
    serve(
        _table("a", "A", (_M,), {(0.01,): _cell(0.3), (0.1,): _cell(0.4)}),
        _table("b", "B", (_M,), {(0.01,): _cell(0.2), (0.1,): _cell(0.3)}),
        _table("c", "C", (_M,), {(0.01,): _cell(0.1), (0.1,): _cell(0.2)}),
    )

    result = compare_sweeps([SweepRef(i) for i in "abc"], statistic="D")

    assert all(p.difference is None for p in result.points)
    assert [p.cells["C"].mean for p in result.points] == [0.1, 0.2]  # type: ignore[union-attr]


def test_joining_on_one_of_two_shared_keys(serve: Any) -> None:
    both: dict[tuple[Any, ...], StatisticCell | None] = {
        (m, d): _cell(0.1 * d + m) for m in (0.01, 0.1) for d in (2, 3)
    }
    serve(_table("a", "A", (_M, _D), both), _table("b", "B", (_M, _D), both))

    full = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert full.on == ("m", "d")
    assert len(full.points) == 4
    with pytest.raises(ValueError, match="also varies 'd'"):
        compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D", on=["m"])


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("one", "at least two sweeps"),
        ("no shared key", "share no axis key"),
        ("on not shared", "cannot join on 'nope'"),
        ("no common value", "has no value in common"),
        ("extra axis", "also varies 'd'"),
        ("ploidy", "different ploidy"),
        ("statistic", "has statistic 'G_ST'"),
    ],
)
def test_a_comparison_that_cannot_be_made_is_refused_with_the_reason(
    serve: Any, scenario: str, message: str
) -> None:
    a = _table("a", "A", (_M,), {(0.01,): _cell(0.3), (0.1,): _cell(0.4)})
    b = _table("b", "B", (_M,), {(0.01,): _cell(0.2), (0.1,): _cell(0.3)})
    refs = [SweepRef("a"), SweepRef("b")]
    statistic, on = "D", None
    if scenario == "one":
        refs = refs[:1]
    elif scenario == "no shared key":
        b = _table("b", "B", (_D,), {(2,): _cell(0.2), (3,): _cell(0.3)})
    elif scenario == "on not shared":
        on = ["nope"]
    elif scenario == "no common value":
        b = _table(
            "b",
            "B",
            (expand_axis("m", [0.5, 0.9]),),
            {(0.5,): _cell(0.2), (0.9,): _cell(0.3)},
        )
    elif scenario == "extra axis":
        b = _table(
            "b",
            "B",
            (_M, _D),
            {(m, d): _cell(0.2) for m in (0.01, 0.1) for d in (2, 3)},
        )
        a, b = b, a
        refs = [SweepRef("a"), SweepRef("b")]
    elif scenario == "ploidy":
        b = _table(
            "b",
            "B",
            (_M,),
            {(0.01,): _cell(0.2), (0.1,): _cell(0.3)},
            base={"ploidy": "diploid"},
        )
    elif scenario == "statistic":
        statistic = "G_ST"
    serve(a, b)

    with pytest.raises(ValueError, match=message):
        compare_sweeps(refs, statistic=statistic, on=on)


def test_different_base_seeds_are_reported_as_independent(serve: Any) -> None:
    serve(
        _table(
            "a",
            "A",
            (_M,),
            {(0.01,): _cell(0.3), (0.1,): _cell(0.4)},
            seeds={(0.01,): 1, (0.1,): 2},
        ),
        _table(
            "b",
            "B",
            (_M,),
            {(0.01,): _cell(0.2), (0.1,): _cell(0.3)},
            seeds={(0.01,): 11, (0.1,): 12},
        ),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert result.pairing == "independent"
    assert result.seed_policies == {"A": "spaced", "B": "spaced"}


def test_the_same_seeds_are_reported_as_shared_and_the_method_is_not_switched(
    serve: Any,
) -> None:
    seeds = {(0.01,): 1, (0.1,): 2}
    serve(
        _table(
            "a",
            "A",
            (_M,),
            {(0.01,): _cell(0.3), (0.1,): _cell(0.4)},
            seeds=seeds,
            seed_policy="same",
        ),
        _table(
            "b",
            "B",
            (_M,),
            {(0.01,): _cell(0.2), (0.1,): _cell(0.3)},
            seeds=seeds,
            seed_policy="same",
        ),
    )

    result = compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D")

    assert result.pairing == "shared-seeds"
    assert result.seed_policies == {"A": "same", "B": "same"}
    # The interval is still the Welch one; it is reported, not replaced.
    assert result.points[0].difference is not None
    assert result.points[0].difference.degrees_of_freedom is not None


def test_some_shared_seeds_are_reported_as_partly_shared(serve: Any) -> None:
    serve(
        _table(
            "a",
            "A",
            (_M,),
            {(0.01,): _cell(0.3), (0.1,): _cell(0.4)},
            seeds={(0.01,): 1, (0.1,): 2},
        ),
        _table(
            "b",
            "B",
            (_M,),
            {(0.01,): _cell(0.2), (0.1,): _cell(0.3)},
            seeds={(0.01,): 1, (0.1,): 99},
        ),
    )

    assert (
        compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D").pairing
        == "partly-shared-seeds"
    )


def test_with_no_coordinate_in_both_the_pairing_is_unknown(serve: Any) -> None:
    serve(
        _table("a", "A", (_M,), {(0.01,): _cell(0.3), (0.1,): None}),
        _table("b", "B", (_M,), {(0.01,): None, (0.1,): _cell(0.3)}),
    )

    assert (
        compare_sweeps([SweepRef("a"), SweepRef("b")], statistic="D").pairing
        == "unknown"
    )


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Point the results directory at a temporary one for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


def test_two_real_sweeps_compare_end_to_end(results: Path) -> None:
    del results
    ids = []
    for population, name in ((16, "Small"), (32, "Large")):
        spec = SweepSpec(
            base={
                "N": population,
                "ploidy": "haploid",
                "d": 2,
                "m": 0.1,
                "mu": 0.01,
                "seed": 7,
                "n_replicates": 3,
                "max_generations": 10,
            },
            axes=(expand_axis("m", [0.05, 0.1]),),
        )
        study = create_sweep_study(spec, enumerate_points(spec), name)
        run_sweep(
            study.study_id,
            LocalPointRunner(),
            lambda _e: None,
            threading.Event(),
            points_at_once=1,
        )
        ids.append(study.study_id)

    result = compare_sweeps([SweepRef(i) for i in ids], statistic="D")

    assert result.labels == ("Small", "Large")
    assert result.base_differences == {"N": {"Small": 16, "Large": 32}}
    assert [p.coordinates["m"] for p in result.points] == [0.05, 0.1]
    assert all(p.difference is not None for p in result.points)
    assert result.pairing == "shared-seeds"  # same base seed and grid: same seeds
