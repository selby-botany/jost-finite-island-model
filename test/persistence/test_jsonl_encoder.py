"""Byte-identity tests for `fim.persistence.jsonl_store.encode_rows`.

`encode_rows` builds a trajectory row's JSON line by hand instead of
calling `json.dumps` (about six times faster). It is only acceptable if
the file is byte for byte what `json.dumps(row, sort_keys=True,
separators=(",", ":"), allow_nan=False)` always wrote. Three independent
bodies of evidence pin that, none of which depends on the clock or the
machine:

- a Hypothesis property (derandomized by this suite's profile) over rows
  with edge-case floats, integers, and run ids;
- a seeded bulk comparison of tens of thousands of rows, including every
  float bit pattern class (subnormals, huge, tiny, negative zero);
- the complete row streams of real runs (a single lineal run, an
  equilibrium-split run with its companion store, batches on two engines,
  and Backend V), compared against `json.dumps` of the very rows the
  engine handed to the store.

Rows that are not the known shape must fall back to `json.dumps`, so every
outcome, including its exception, matches; that is tested too.
"""

from __future__ import annotations

import enum
import json
import math
import random
import struct
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence.jsonl_store import (
    EQUILIBRIUM_TRAJECTORY_FILENAME,
    JSONLTrajectoryStore,
    encode_rows,
)
from fim.persistence.store import normalize_row


def _reference(rows: Iterable[Mapping[str, Any]]) -> str:
    """Return the lines the open-per-generation writer produced: `json.dumps`."""
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for row in rows
    )


def _row(
    *,
    run_id: str = "run-a",
    generation: int = 0,
    deme: int = 1,
    locus_id: int = 1,
    allele_id: int = 0,
    frequency: float = 0.5,
) -> dict[str, Any]:
    """Return one trajectory row, keys in the engine's own (unsorted) order."""
    return {
        "run_id": run_id,
        "generation": generation,
        "deme": deme,
        "locus_id": locus_id,
        "allele_id": allele_id,
        "frequency": frequency,
    }


EDGE_FLOATS = (
    5e-324,  # smallest subnormal
    2.2250738585072009e-308,  # largest subnormal
    2.2250738585072014e-308,  # smallest normal
    1e-300,
    1e-7,
    1e-5,
    0.0001,
    0.1 + 0.2,
    0.1,
    1 / 3,
    0.5,
    1.0,
    1e15,
    1e16,
    1e22,
    1e23,
    1.7976931348623157e308,
    -0.0,
    0.0,
    -1.0,
    123456789.123456789,
)
"""Floats whose `repr` is a known hazard: exponent form, rounding, signs."""

EDGE_RUN_IDS = (
    "run-a",
    "",
    'quote"inside',
    "back\\slash",
    "new\nline",
    "tab\tand\rreturn",
    "\x00\x01\x08\x0b\x0c\x1f",
    "del\x7fchar",
    "caf" + chr(0xE9),
    "snow" + chr(0x2603) + "man",
    "emoji " + chr(0x1F600) + " pair",
    chr(0x2028) + chr(0x2029),
    "lone " + chr(0xD800) + " surrogate",
    'mixed "\\\n' + chr(0xE9) + chr(0x1F600),
)
"""Run ids that exercise every escape `json` makes (it escapes non-ASCII)."""

_INT_STRATEGY = st.one_of(
    st.integers(min_value=0, max_value=2**63 - 1),
    st.integers(min_value=-(2**70), max_value=2**70),
    st.sampled_from([0, 1, 2**31 - 1, 2**31, 2**63 - 1, 2**63, 2**64]),
)
_FLOAT_STRATEGY = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False),
    st.sampled_from(EDGE_FLOATS),
)
_RUN_ID_STRATEGY = st.one_of(
    st.text(st.characters(exclude_categories=())),
    st.sampled_from(EDGE_RUN_IDS),
)


@st.composite
def _rows(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Draw one generation: rows sharing a run id, in any key order."""
    run_id = draw(_RUN_ID_STRATEGY)
    count = draw(st.integers(min_value=1, max_value=8))
    rows = []
    for _ in range(count):
        row = _row(
            run_id=run_id,
            generation=draw(_INT_STRATEGY),
            deme=draw(_INT_STRATEGY),
            locus_id=draw(_INT_STRATEGY),
            allele_id=draw(_INT_STRATEGY),
            frequency=draw(_FLOAT_STRATEGY),
        )
        # `json.dumps(sort_keys=True)` must not depend on insertion order.
        keys = draw(st.permutations(list(row)))
        rows.append({key: row[key] for key in keys})
    return rows


@settings(max_examples=600)
@given(_rows())
def test_property_encode_rows_is_byte_identical_to_json_dumps(
    rows: list[dict[str, Any]],
) -> None:
    """For any known-shape generation the bytes equal `json.dumps`'s."""
    assert encode_rows(rows) == _reference(rows)


def test_property_encode_rows_handles_alternating_run_ids() -> None:
    """The per-call run-id cache never leaks one row's id into the next."""
    rows = [
        _row(run_id=run_id, allele_id=index)
        for index, run_id in enumerate(EDGE_RUN_IDS * 2)
    ]

    assert encode_rows(rows) == _reference(rows)


BULK_ROW_COUNT = 60_000
"""Rows compared in `test_seeded_bulk_rows_are_byte_identical`."""

_BULK_SEED = 20261008


def _random_finite_float(generator: random.Random) -> float:
    """Return a random finite float drawn from several distinct populations."""
    kind = generator.randrange(6)
    if kind == 0:
        return generator.random()  # the ordinary case: a frequency
    if kind == 1:
        return generator.random() * 10.0 ** generator.randint(-320, 300)
    if kind == 2:
        return float(generator.randint(1, 10**6)) / generator.randint(1, 10**6)
    if kind == 3:
        return generator.choice(EDGE_FLOATS)
    if kind == 4:
        return -generator.random()
    while True:  # every 64-bit pattern: subnormals, huge, all rounding cases
        (value,) = struct.unpack("<d", generator.getrandbits(64).to_bytes(8, "little"))
        if math.isfinite(value):
            return float(value)


def _random_run_id(generator: random.Random) -> str:
    """Return a random run id: mostly tame, sometimes every escape class."""
    if generator.randrange(4) == 0:
        return generator.choice(EDGE_RUN_IDS)
    head = chr(generator.choice((0x20, 0x22, 0x5C, 0x7F, 0xE9, 0x1F600)))
    tail = "".join(chr(generator.randrange(0x20, 0x7F)) for _ in range(6))
    return head + tail


def test_seeded_bulk_rows_are_byte_identical() -> None:
    """Tens of thousands of seeded random rows encode exactly as `json.dumps`.

    Seeded with a literal, so the same commit always compares the same rows.
    """
    generator = random.Random(_BULK_SEED)
    compared = 0
    while compared < BULK_ROW_COUNT:
        run_id = _random_run_id(generator)
        rows = [
            _row(
                run_id=run_id,
                generation=generator.randrange(2**63),
                deme=generator.randint(1, 50),
                locus_id=generator.randint(1, 500),
                allele_id=generator.choice(
                    (generator.randrange(2**63), generator.randrange(0, 40))
                ),
                frequency=_random_finite_float(generator),
            )
            for _ in range(generator.randint(1, 40))
        ]

        assert encode_rows(rows) == _reference(rows)

        compared += len(rows)


def test_validated_rows_round_trip_through_normalize_row() -> None:
    """`validate=True` output equals `json.dumps` of the normalized rows."""
    rows = [_row(allele_id=index, frequency=index / 7 + 0.01) for index in range(1, 6)]
    normalized = [normalize_row(row, run_id="run-a", generation=0) for row in rows]

    assert encode_rows(normalized) == _reference(normalized)


def _outcome(function: Callable[[], str]) -> tuple[str, str]:
    """Return ("ok", text) or (exception type name, message) for `function`."""
    try:
        return ("ok", function())
    except (TypeError, ValueError) as error:
        return (type(error).__name__, str(error))


class _Color(enum.IntEnum):
    """An `int` subclass, as an allele id might (wrongly) be."""

    RED = 3


class _Name(str):
    """A `str` subclass."""

    __slots__ = ()


class _Weight(float):
    """A `float` subclass with its own `repr`, as numpy's floats have."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Differ from `float.__repr__`, which is what `json` uses."""
        return "Weight"


@pytest.mark.parametrize(
    "row",
    [
        _row(allele_id=True),
        _row(deme=False),
        _row(generation=_Color.RED),
        _row(allele_id=np.int64(5)),  # type: ignore[arg-type]
        _row(frequency=np.float64(0.25)),
        _row(frequency=_Weight(0.25)),
        _row(frequency=1),  # an int where a float belongs
        _row(frequency=float("nan")),
        _row(frequency=float("inf")),
        _row(frequency=float("-inf")),
        _row(run_id=_Name("named")),
        _row(run_id=7),  # type: ignore[arg-type]
        {**_row(), "extra": 1},
        {key: value for key, value in _row().items() if key != "deme"},
        {},
        {**_row(), "deme": None},
    ],
    ids=[
        "bool-int",
        "bool-int-false",
        "int-enum",
        "numpy-int",
        "numpy-float",
        "float-subclass",
        "int-frequency",
        "nan",
        "inf",
        "-inf",
        "str-subclass",
        "int-run-id",
        "extra-key",
        "missing-key",
        "empty-row",
        "none-value",
    ],
)
def test_unrecognized_rows_fall_back_with_json_dumps_own_outcome(
    row: dict[str, Any],
) -> None:
    """Anything off the known shape yields what `json.dumps` yields, errors too."""
    assert _outcome(lambda: encode_rows([row])) == _outcome(lambda: _reference([row]))


def test_non_finite_frequency_raises_the_same_error_as_before() -> None:
    """`allow_nan=False` still rejects a non-finite frequency, with its text."""
    with pytest.raises(ValueError, match="Out of range float values"):
        encode_rows([_row(), _row(frequency=float("nan"))])


def test_a_non_dict_mapping_is_not_encoded_as_a_row() -> None:
    """A `Mapping` that is not a `dict` is left to `json.dumps` (a `TypeError`)."""
    row = _row()

    class Wrapper(Mapping[str, Any]):
        """A read-only view with a row's items."""

        def __getitem__(self, key: str) -> Any:
            """Return the row's value for `key`."""
            return row[key]

        def __iter__(self) -> Any:
            """Iterate the row's keys."""
            return iter(row)

        def __len__(self) -> int:
            """Return the row's field count."""
            return len(row)

    assert _outcome(lambda: encode_rows([Wrapper()])) == _outcome(
        lambda: _reference([Wrapper()])
    )


def test_an_empty_iterable_encodes_to_nothing() -> None:
    """No rows, no bytes (the store rejects an empty generation itself)."""
    assert encode_rows([]) == ""


class _RecordingStore(JSONLTrajectoryStore):
    """A real store that also keeps a copy of every row it was handed.

    The rows are captured before the store encodes them, so they are
    exactly the dicts `encode_rows` receives from the engine.
    """

    def __init__(self, path: Path | str) -> None:
        """Bind to `path` with an empty capture list."""
        super().__init__(path)
        self.captured: list[dict[str, Any]] = []

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Capture the generation's rows, then write them as usual."""
        materialized = [dict(row) for row in rows]
        self.captured.extend(materialized)
        super().write_generation(run_id, generation, materialized, validate=validate)

    def equilibrium_store(self, run_id: str) -> JSONLTrajectoryStore:
        """Return a recording companion, so the ancestral stream is checked too."""
        del run_id
        with self._lock:
            if self._equilibrium is None:
                self._equilibrium = _RecordingStore(
                    self.path.with_name(EQUILIBRIUM_TRAJECTORY_FILENAME)
                )
            return self._equilibrium


def _assert_stream_identical(store: JSONLTrajectoryStore) -> int:
    """Assert `store`'s file is `json.dumps` of every captured row; return the count."""
    assert isinstance(store, _RecordingStore)
    store.close()
    assert store.captured
    assert store.path.read_bytes() == _reference(store.captured).encode("utf-8")
    return len(store.captured)


def _stream_config(**updates: object) -> SimulationParams:
    """Return a small configuration with several loci, demes and mutations."""
    config: dict[str, object] = {
        "N": 30,
        "ploidy": "haploid",
        "m": 0.1,
        "mu": 0.02,
        "d": 3,
        "seed": 20261008,
        "loci": [{"locus_id": 1, "length": 200}, {"locus_id": 2, "length": 200}],
        "initial_allele_count": 3,
        "convergence_window": 4,
        # Tight enough that the run goes to its cap instead of stopping
        # after four generations.
        "convergence_tolerance": 1e-12,
        "max_generations": 40,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    return SimulationParams.from_mapping(config)


def _run_streams(
    tmp_path: Path, params: SimulationParams
) -> list[JSONLTrajectoryStore]:
    """Run `params` with one recording store per replicate; return the stores."""
    stores: dict[str, _RecordingStore] = {}

    def factory(run_id: str) -> _RecordingStore:
        """Build (once) the recording store for one replicate."""
        return stores.setdefault(run_id, _RecordingStore(tmp_path / f"{run_id}.jsonl"))

    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=factory,
    )
    return list(stores.values())


def test_real_single_lineal_run_stream_is_byte_identical(tmp_path: Path) -> None:
    """A whole single run's trajectory file equals `json.dumps` of its rows."""
    (store,) = _run_streams(tmp_path, _stream_config())

    assert _assert_stream_identical(store) > 100


def test_real_equilibrium_split_streams_are_byte_identical(tmp_path: Path) -> None:
    """Both files of an equilibrium-split run, the companion's included."""
    params = _stream_config(
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )
    (store,) = _run_streams(tmp_path, params)

    main_rows = _assert_stream_identical(store)
    companion = store.equilibrium_store("any")
    ancestral_rows = _assert_stream_identical(companion)

    assert main_rows > 50
    assert ancestral_rows > 0
    assert companion.path.name == EQUILIBRIUM_TRAJECTORY_FILENAME


@pytest.mark.parametrize("backend", ["lineal", "generational"])
def test_real_batch_streams_are_byte_identical(tmp_path: Path, backend: str) -> None:
    """Every replicate's file in a batch, on both in-process engine paths."""
    params = _stream_config(n_replicates=3, engine_backend=backend, seed=7)

    stores = _run_streams(tmp_path, params)

    assert len(stores) == 3
    assert sum(_assert_stream_identical(store) for store in stores) > 300


def test_real_backend_v_stream_is_byte_identical(tmp_path: Path) -> None:
    """Backend V builds its rows another way; its file is identical too."""
    pytest.importorskip("numba")
    params = _stream_config(
        engine_backend="generational-vector",
        mutation_model="finite_alleles",
        loci=[{"locus_id": 1, "length": 2}],
        mu=0.1,
        N=40,
    )

    (store,) = _run_streams(tmp_path, params)

    assert _assert_stream_identical(store) > 20


def test_store_with_validation_writes_identical_bytes(tmp_path: Path) -> None:
    """The default `validate=True` path writes `json.dumps`'s bytes as well."""
    rows = [_row(allele_id=index, frequency=0.1 * index) for index in range(1, 5)]
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")

    store.write_generation("run-a", 0, rows)
    store.close()

    expected = _reference(normalize_row(row, run_id="run-a") for row in rows)
    assert (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8") == expected
