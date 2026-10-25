"""Tests of `fim.persistence.history`: the exact and compact statistic histories."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fim.persistence.history import (
    COMPACT_FILENAME,
    HISTORY_FILENAME,
    HistoryError,
    fractional_bits,
    history_path,
    iter_history_rows,
    read_compact_columns,
    read_history_columns,
    replicate_directories,
    sample_history,
    write_compact_history,
)


def _walk(count: int, *, seed: int) -> dict[str, list[float]]:
    """Three random-walk statistics in [0, 1], like a run's $D$, $H_S$ and $H_T$."""
    rng = np.random.default_rng(seed)
    out = {}
    for name in ("D", "H_S", "H_T"):
        steps = rng.normal(0.0, 0.004, count).cumsum() + 0.3
        out[name] = np.clip(steps, 0.0, 1.0).tolist()
    return out


def _write_exact(
    path: Path, generations: list[int], columns: dict[str, list[float]]
) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index, generation in enumerate(generations):
            row = {
                "generation": generation,
                **{n: v[index] for n, v in columns.items()},
            }
            handle.write(json.dumps(row) + "\n")


# --- the fixed-point step ----------------------------------------------------


@pytest.mark.parametrize(
    ("precision", "bits"), [(1.0, 8), (0.1, 11), (0.01, 15), (0.001, 18), (1e-4, 21)]
)
def test_the_step_is_about_one_percent_of_the_precision(
    precision: float, bits: int
) -> None:
    assert fractional_bits(precision) == bits
    assert 2.0**-bits <= precision / 100


@pytest.mark.parametrize("precision", [0.0, -1.0, math.nan, math.inf, 1e-30])
def test_a_degenerate_precision_gets_the_finest_step(precision: float) -> None:
    assert fractional_bits(precision) == 40


# --- round trips -------------------------------------------------------------


@pytest.mark.parametrize("bits", [4, 12, 15, 24])
def test_every_value_decodes_to_within_half_a_step(tmp_path: Path, bits: int) -> None:
    columns = _walk(500, seed=1)
    generations = list(range(500))
    path = tmp_path / COMPACT_FILENAME

    write_compact_history(path, generations, columns, bits=bits)
    decoded_generations, decoded = read_compact_columns(path)

    assert decoded_generations == generations
    for name, values in columns.items():
        error = np.abs(np.asarray(decoded[name]) - np.asarray(values)).max()
        assert error <= 2.0 ** -(bits + 1) + 1e-15
        assert all(v * 2**bits == round(v * 2**bits) for v in decoded[name])  # exact


def test_the_same_values_always_write_the_same_bytes(tmp_path: Path) -> None:
    columns = _walk(300, seed=2)
    first, second = tmp_path / "a.fhist", tmp_path / "b.fhist"

    write_compact_history(first, list(range(300)), columns, bits=15)
    write_compact_history(second, list(range(300)), columns, bits=15)

    assert first.read_bytes() == second.read_bytes()


def test_generations_that_are_not_consecutive_round_trip(tmp_path: Path) -> None:
    generations = [0, 1, 2, 10, 11, 5000, 5001, 1_000_000]
    columns = {"D": [0.1, 0.2, 0.25, 0.3, 0.31, 0.5, 0.49, 0.45]}
    path = tmp_path / COMPACT_FILENAME

    write_compact_history(path, generations, columns, bits=14)
    decoded_generations, decoded = read_compact_columns(path)

    assert decoded_generations == generations
    assert decoded["D"] == pytest.approx(columns["D"], abs=2.0**-15)


def test_a_run_that_starts_after_generation_zero_keeps_its_first_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / COMPACT_FILENAME
    write_compact_history(path, [7, 8, 9], {"D": [0.5, 0.5, 0.5]}, bits=8)

    assert read_compact_columns(path)[0] == [7, 8, 9]


def test_missing_values_come_back_missing(tmp_path: Path) -> None:
    path = tmp_path / COMPACT_FILENAME
    write_compact_history(
        path, [0, 1, 2, 3], {"G_ST": [0.2, None, math.nan, 0.4]}, bits=10
    )

    values = read_compact_columns(path)[1]["G_ST"]

    assert values[0] == pytest.approx(0.2, abs=2.0**-11)
    assert math.isnan(values[1]) and math.isnan(values[2])
    assert values[3] == pytest.approx(0.4, abs=2.0**-11)


def test_an_empty_history_round_trips(tmp_path: Path) -> None:
    path = tmp_path / COMPACT_FILENAME
    write_compact_history(path, [], {}, bits=10)

    assert read_compact_columns(path) == ([], {})


@pytest.mark.parametrize("jump", [10, 3_000, 100_000, 5_000_000])
def test_large_steps_choose_a_wider_integer(tmp_path: Path, jump: int) -> None:
    path = tmp_path / COMPACT_FILENAME
    columns = {"X": [0.0, float(jump), 0.0, float(jump)]}

    write_compact_history(path, [0, 1, 2, 3], columns, bits=8)

    assert read_compact_columns(path)[1]["X"] == columns["X"]


def test_a_value_too_large_for_the_step_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="too large"):
        write_compact_history(
            tmp_path / COMPACT_FILENAME, [0], {"X": [1.0e30]}, bits=30
        )


def test_columns_of_the_wrong_length_are_refused(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="has 2 values, not 3"):
        write_compact_history(
            tmp_path / COMPACT_FILENAME, [0, 1, 2], {"D": [0.1, 0.2]}, bits=8
        )


@settings(max_examples=40, deadline=None)
@given(
    values=st.lists(
        st.floats(min_value=-4.0, max_value=4.0, allow_nan=False),
        min_size=1,
        max_size=60,
    ),
    bits=st.integers(min_value=1, max_value=30),
)
def test_any_values_decode_within_half_a_step(
    tmp_path_factory: pytest.TempPathFactory, values: list[float], bits: int
) -> None:
    path = tmp_path_factory.mktemp("h") / COMPACT_FILENAME
    write_compact_history(path, list(range(len(values))), {"X": values}, bits=bits)

    decoded = read_compact_columns(path)[1]["X"]

    assert np.abs(np.asarray(decoded) - np.asarray(values)).max() <= 2.0 ** -(bits + 1)


# --- damage ------------------------------------------------------------------


def test_a_file_that_is_not_a_history_is_refused(tmp_path: Path) -> None:
    path = tmp_path / COMPACT_FILENAME
    path.write_bytes(b"not a history at all")

    with pytest.raises(HistoryError, match="not a compact history"):
        read_compact_columns(path)


def test_a_truncated_or_corrupt_history_is_refused(tmp_path: Path) -> None:
    path = tmp_path / COMPACT_FILENAME
    write_compact_history(path, list(range(200)), _walk(200, seed=3), bits=14)
    whole = path.read_bytes()

    path.write_bytes(whole[:-7])
    with pytest.raises(HistoryError):
        read_compact_columns(path)
    path.write_bytes(whole[:30])
    with pytest.raises(HistoryError):
        read_compact_columns(path)
    damaged = bytearray(whole)
    damaged[-20] ^= 0xFF
    path.write_bytes(bytes(damaged))
    with pytest.raises(HistoryError):
        read_compact_columns(path)


def test_a_header_that_lies_about_the_rows_is_refused(tmp_path: Path) -> None:
    path = tmp_path / COMPACT_FILENAME
    write_compact_history(path, list(range(50)), {"D": [0.5] * 50}, bits=8)
    data = path.read_bytes()
    (length,) = struct.unpack_from("<I", data, 8)
    header = json.loads(data[12 : 12 + length])
    header["rows"] = 49
    lie = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(data[:8] + struct.pack("<I", len(lie)) + lie + data[12 + length :])

    with pytest.raises(HistoryError, match="wrong length"):
        read_compact_columns(path)


# --- both formats through one reader -----------------------------------------


def test_both_formats_read_through_the_same_functions(tmp_path: Path) -> None:
    columns = _walk(400, seed=4)
    generations = list(range(400))
    exact = tmp_path / HISTORY_FILENAME
    compact = tmp_path / COMPACT_FILENAME
    _write_exact(exact, generations, columns)
    write_compact_history(compact, generations, columns, bits=16)

    exact_generations, exact_columns = read_history_columns(exact)
    compact_generations, compact_columns = read_history_columns(compact)

    assert exact_generations == compact_generations == generations
    for name in columns:
        assert exact_columns[name] == columns[name]
        assert compact_columns[name] == pytest.approx(columns[name], abs=2.0**-17)
    assert [g for g, _ in iter_history_rows(compact)] == generations
    first = next(iter_history_rows(compact))[1]
    assert set(first) == set(columns)


def test_sampling_a_compact_history_matches_sampling_the_exact_one(
    tmp_path: Path,
) -> None:
    columns = _walk(1000, seed=5)
    generations = list(range(1000))
    exact = tmp_path / HISTORY_FILENAME
    compact = tmp_path / COMPACT_FILENAME
    _write_exact(exact, generations, columns)
    write_compact_history(compact, generations, columns, bits=18)
    grid = [0, 1, 17, 250, 999, 1000]  # 1000 is beyond the history

    from_exact = sample_history(exact, grid)
    from_compact = sample_history(compact, grid)

    for name in columns:
        assert from_exact[name][-1] is None and from_compact[name][-1] is None
        assert from_compact[name][:-1] == pytest.approx(
            from_exact[name][:-1], abs=2.0**-19
        )


def test_the_compact_file_is_far_smaller_than_the_json(tmp_path: Path) -> None:
    columns = _walk(20_000, seed=6)
    generations = list(range(20_000))
    exact = tmp_path / HISTORY_FILENAME
    compact = tmp_path / COMPACT_FILENAME
    _write_exact(exact, generations, columns)

    write_compact_history(compact, generations, columns, bits=fractional_bits(0.01))

    assert compact.stat().st_size * 20 < exact.stat().st_size


def test_the_exact_path_is_named_when_a_folder_holds_no_history(tmp_path: Path) -> None:
    assert history_path(tmp_path) == tmp_path / HISTORY_FILENAME


def test_replicate_folders_are_those_with_a_manifest(tmp_path: Path) -> None:
    assert replicate_directories(tmp_path) == [tmp_path]
    for name, has_manifest in (("replicate-001", True), ("replicate-002", False)):
        (tmp_path / name).mkdir()
        if has_manifest:
            (tmp_path / name / "manifest.json").write_text("{}")

    assert replicate_directories(tmp_path) == [tmp_path / "replicate-001"]


# --- the decimated history --------------------------------------------------


def test_a_short_history_is_decimated_to_every_generation(tmp_path: Path) -> None:
    from fim.persistence.history import (  # noqa: PLC0415
        DECIMATED_FILENAME,
        read_decimated_columns,
        write_decimated_history,
    )

    columns = _walk(100, seed=7)
    path = tmp_path / DECIMATED_FILENAME

    write_decimated_history(path, list(range(100)), columns)

    generations, decoded = read_decimated_columns(path)
    assert generations == list(range(100))
    assert decoded["D"] == columns["D"]


def test_a_long_history_is_decimated_to_the_cap_and_keeps_both_ends(
    tmp_path: Path,
) -> None:
    from fim.persistence.history import (  # noqa: PLC0415
        DECIMATED_FILENAME,
        read_decimated_columns,
        write_decimated_history,
    )

    rows = 50_000
    columns = {"D": [i / rows for i in range(rows)]}
    path = tmp_path / DECIMATED_FILENAME

    write_decimated_history(path, list(range(rows)), columns, maximum=256)

    generations, decoded = read_decimated_columns(path)
    assert len(generations) <= 256
    assert generations[0] == 0 and generations[-1] == rows - 1
    assert decoded["D"] == [g / rows for g in generations]
    assert path.stat().st_size < 20_000


def test_the_nearest_recorded_generation_answers_a_grid_sample(tmp_path: Path) -> None:
    from fim.persistence.history import DECIMATED_FILENAME  # noqa: PLC0415

    path = tmp_path / DECIMATED_FILENAME
    path.write_text(
        json.dumps(
            {
                "generations": [0, 10, 20, 100],
                "statistics": {"D": [0.0, 0.1, 0.2, None]},
            }
        )
    )

    sampled = sample_history(path, [0, 4, 6, 18, 70, 100, 101])

    assert sampled["D"] == [0.0, 0.0, 0.1, 0.2, None, None, None]


def test_a_file_that_is_not_a_decimated_history_is_refused(tmp_path: Path) -> None:
    from fim.persistence.history import (  # noqa: PLC0415
        DECIMATED_FILENAME,
        read_decimated_columns,
    )

    path = tmp_path / DECIMATED_FILENAME
    path.write_text("[1, 2, 3]")

    with pytest.raises(HistoryError, match="not a decimated history"):
        read_decimated_columns(path)


def test_the_history_path_prefers_exact_then_compact_then_decimated(
    tmp_path: Path,
) -> None:
    from fim.persistence.history import DECIMATED_FILENAME  # noqa: PLC0415

    (tmp_path / DECIMATED_FILENAME).write_text("{}")
    assert history_path(tmp_path).name == DECIMATED_FILENAME
    (tmp_path / COMPACT_FILENAME).write_bytes(b"x")
    assert history_path(tmp_path).name == COMPACT_FILENAME
    (tmp_path / HISTORY_FILENAME).write_text("")
    assert history_path(tmp_path).name == HISTORY_FILENAME
