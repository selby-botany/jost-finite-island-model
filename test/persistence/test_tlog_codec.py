"""Round-trip tests of the binary log's record codec.

The codec is lossless by construction (count coding is used only when it
reproduces the frequency bit for bit), so the central property is: any
frame, encoded and decoded, returns the same counts, ids and frequency
bits. The cases cover count-coded, raw and mixed pairs, unknown deme
sizes, empty pairs, large identifiers, widening beyond the initial state,
delta records against a long random walk, and the pure-Python versions
of every kernel against the compiled ones. All seeded; no timing.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, cast

import numpy as np
import pytest

from fim.persistence import tlog_codec as codec

PAIRS_DEMES = 3
PAIRS_LOCI = 4
SIZES = np.array([100, 64, 7], dtype=np.int64)


def _random_frame(
    rng: np.random.Generator,
    *,
    sizes: np.ndarray = SIZES,
    loci: int = PAIRS_LOCI,
    max_alleles: int = 5,
    raw_fraction: float = 0.2,
    id_base: int = 0,
    empty_fraction: float = 0.0,
    id_span: int = 10_000,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a random CSR frame; some pairs hold arbitrary floats, not counts."""
    nal: list[int] = []
    ids: list[int] = []
    fr: list[float] = []
    for deme in range(len(sizes)):
        size = int(sizes[deme])
        for _locus in range(loci):
            if empty_fraction and rng.random() < empty_fraction:
                nal.append(0)
                continue
            n = int(rng.integers(1, max_alleles + 1))
            n = min(n, max(size, 1))
            if rng.random() < raw_fraction or size <= 0:
                values = rng.random(n)
                values = values / values.sum()
            else:
                cuts = np.sort(rng.choice(np.arange(1, size), n - 1, replace=False))
                counts = np.diff(np.concatenate(([0], cuts, [size])))
                values = counts.astype(np.float64) / float(size)
            nal.append(n)
            ids.extend(
                (id_base + np.sort(rng.choice(id_span, n, replace=False))).tolist()
            )
            fr.extend(values.tolist())
    return (
        np.asarray(nal, dtype=np.int32),
        np.asarray(ids, dtype=np.int64),
        np.asarray(fr, dtype=np.float64),
    )


def _encode_full(
    nal: np.ndarray,
    ids: np.ndarray,
    fr: np.ndarray,
    sizes: np.ndarray = SIZES,
    loci: int = PAIRS_LOCI,
) -> bytes:
    """Encode one frame as a full record."""
    out = np.zeros(64 + 40 * len(ids) + 12 * len(nal), np.uint8)
    pos, rows = codec.enc_full(
        out, 0, 1, nal, ids, fr, len(sizes), loci, sizes, np.zeros(1)
    )
    assert rows == len(ids)
    return bytes(out[:pos])


def _decode(
    data: bytes,
    state: codec.DecodedState,
    sizes: np.ndarray = SIZES,
    loci: int = PAIRS_LOCI,
) -> tuple[int, int, int]:
    """Apply one record to `state` (widening it as needed); return its summary."""
    buf = np.frombuffer(data, dtype=np.uint8)
    while True:
        end, rows, kind, _gen = codec.apply_record(
            buf, 0, len(sizes), loci, sizes, state.pn, state.pid, state.pc, state.pf
        )
        if end >= 0:
            return int(end), int(rows), int(kind)
        state.grow()


def _same(
    state: codec.DecodedState, nal: np.ndarray, ids: np.ndarray, fr: np.ndarray
) -> None:
    """Require the decoded state to equal a frame, with exact frequency bits."""
    counts, got_ids, got_fr = state.frame_arrays()
    assert np.array_equal(counts, nal)
    assert np.array_equal(got_ids, ids)
    assert got_fr.tobytes() == fr.tobytes()


@pytest.mark.parametrize("seed", range(12))
def test_a_full_record_round_trips_counted_and_raw_pairs(seed: int) -> None:
    """Mixed count-coded and raw pairs come back with identical float bits."""
    rng = np.random.default_rng(seed)
    nal, ids, fr = _random_frame(rng)
    data = _encode_full(nal, ids, fr)
    state = codec.DecodedState(len(nal))
    end, rows, kind = _decode(data, state)
    assert (end, rows, kind) == (len(data), len(ids), codec.KIND_FULL)
    _same(state, nal, ids, fr)


def test_counted_pairs_are_much_smaller_than_raw_pairs() -> None:
    """Count coding is what makes the log small: about 2 bytes a row at N=100."""
    rng = np.random.default_rng(1)
    nal, ids, fr = _random_frame(rng, raw_fraction=0.0, max_alleles=4, id_span=100)
    counted = len(_encode_full(nal, ids, fr))
    raw = len(_encode_full(nal, ids, fr, sizes=np.zeros(3, dtype=np.int64)))
    assert counted < raw / 2
    assert counted < 3 * len(ids)


def test_an_unknown_deme_size_stores_every_frequency_raw_and_exactly() -> None:
    """Size 0 (unknown) disables count coding; the floats still round-trip."""
    rng = np.random.default_rng(5)
    unknown = np.zeros(PAIRS_DEMES, dtype=np.int64)
    nal, ids, fr = _random_frame(rng, sizes=SIZES, raw_fraction=0.0)
    data = _encode_full(nal, ids, fr, sizes=unknown)
    state = codec.DecodedState(len(nal))
    _decode(data, state, sizes=unknown)
    _same(state, nal, ids, fr)


@pytest.mark.parametrize("id_base", [0, 2**32, 2**40 - 20_000])
def test_large_allele_identifiers_round_trip(id_base: int) -> None:
    """Minted identifiers start at 2**32; the varint carries them exactly."""
    rng = np.random.default_rng(id_base % 97)
    nal, ids, fr = _random_frame(rng, id_base=id_base)
    state = codec.DecodedState(len(nal))
    _decode(_encode_full(nal, ids, fr), state)
    _same(state, nal, ids, fr)


def test_a_single_allele_at_frequency_one_costs_two_bytes_plus_the_id() -> None:
    """The fixed state of a deme is the common case and the cheapest."""
    nal = np.ones(12, dtype=np.int32)
    ids = np.arange(1, 13, dtype=np.int64)
    fr = np.ones(12)
    data = _encode_full(nal, ids, fr)
    assert len(data) == 10 + 12 * 2
    state = codec.DecodedState(12)
    _decode(data, state)
    _same(state, nal, ids, fr)


def test_a_frequency_that_is_not_a_count_falls_back_to_raw_for_its_pair() -> None:
    """One non-count frequency makes the pair raw; neighbours stay counted."""
    nal = np.array([2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=np.int32)
    ids = np.array([1, 2, 1, 2] + [5] * 10, dtype=np.int64)
    fr = np.array([0.3, 0.7, 0.25, 0.75] + [1.0] * 10)
    state = codec.DecodedState(12)
    _decode(_encode_full(nal, ids, fr), state)
    _same(state, nal, ids, fr)


def test_pairs_with_no_allele_are_kept_empty() -> None:
    """A pair with no entries round-trips as empty (partial frames exist)."""
    rng = np.random.default_rng(3)
    nal, ids, fr = _random_frame(rng, empty_fraction=0.4)
    assert (nal == 0).any()
    state = codec.DecodedState(len(nal))
    _decode(_encode_full(nal, ids, fr), state)
    _same(state, nal, ids, fr)


def test_the_state_widens_when_a_pair_has_more_alleles_than_it_holds() -> None:
    """More than the initial 16 alleles in a pair widens the state and retries."""
    sizes = np.array([200], dtype=np.int64)
    nal = np.array([40], dtype=np.int32)
    ids = np.arange(40, dtype=np.int64) * 3 + 1
    fr = np.full(40, 5.0 / 200.0)
    data = _encode_full(nal, ids, fr, sizes=sizes, loci=1)
    state = codec.DecodedState(1, width=4)
    _decode(data, state, sizes=sizes, loci=1)
    assert state.width >= 40
    _same(state, nal, ids, fr)


@pytest.mark.parametrize("gen_delta", [0, 1, 2, 127, 128, 10_000, 2**33])
def test_the_generation_delta_of_a_record_round_trips(gen_delta: int) -> None:
    """A record carries how many generations it follows (more than 1 when thinned)."""
    rng = np.random.default_rng(gen_delta % 31)
    nal, ids, fr = _random_frame(rng)
    out = np.zeros(64 + 40 * len(ids) + 12 * len(nal), np.uint8)
    pos, _rows = codec.enc_full(
        out, 0, gen_delta, nal, ids, fr, PAIRS_DEMES, PAIRS_LOCI, SIZES, np.zeros(1)
    )
    state = codec.DecodedState(len(nal))
    end, _rows2, _kind, got = codec.apply_record(
        out, 0, PAIRS_DEMES, PAIRS_LOCI, SIZES, state.pn, state.pid, state.pc, state.pf
    )
    assert (int(end), int(got)) == (int(pos), gen_delta)
    _same(state, nal, ids, fr)


@pytest.mark.parametrize(
    "value", [0, 1, 127, 128, 16_383, 16_384, 2**32, 2**40 + 17, 2**62]
)
def test_varints_round_trip(value: int) -> None:
    """Base-128 varints carry any non-negative 63-bit value."""
    out = np.zeros(16, np.uint8)
    end = codec.put_varint(out, 0, value)
    got, pos = codec.get_varint(out, 0)
    assert (int(got), int(pos)) == (value, int(end))


def test_a_padded_length_has_four_bytes_whatever_its_value() -> None:
    """Lengths are written before they are known, so they are fixed-width."""
    out = np.zeros(8, np.uint8)
    for value in (0, 1, 200, 2**21 + 5, 2**28 - 1):
        assert codec.put_padded4(out, 0, value) == 4
        got, pos = codec.get_varint(out, 0)
        assert (int(got), pos) == (value, 4)


def test_a_long_random_walk_of_delta_records_reproduces_every_frame() -> None:
    """Deltas against the previous frame decode to the full frame every step."""
    rng = np.random.default_rng(99)
    pairs = PAIRS_DEMES * PAIRS_LOCI
    nal, ids, fr = _random_frame(rng, max_alleles=3, raw_fraction=0.1)
    width = 16
    pn = np.zeros(pairs, np.int32)
    pid = np.zeros((pairs, width), np.int64)
    pf = np.zeros((pairs, width), np.float64)
    codec.load_state_from_csr(nal, ids, fr, pn, pid, pf)
    decoder = codec.DecodedState(pairs)
    _decode(_encode_full(nal, ids, fr), decoder)
    for _step in range(200):
        # Change a few pairs: new content, or unchanged most of the time.
        fresh = _random_frame(rng, max_alleles=3, raw_fraction=0.1)
        keep = rng.random(pairs) < 0.8
        starts = np.concatenate(([0], np.cumsum(nal)))
        fresh_starts = np.concatenate(([0], np.cumsum(fresh[0])))
        new_nal: list[int] = []
        new_ids: list[np.ndarray] = []
        new_fr: list[np.ndarray] = []
        for p in range(pairs):
            if keep[p]:
                new_nal.append(int(nal[p]))
                new_ids.append(ids[starts[p] : starts[p + 1]])
                new_fr.append(fr[starts[p] : starts[p + 1]])
            else:
                new_nal.append(int(fresh[0][p]))
                new_ids.append(fresh[1][fresh_starts[p] : fresh_starts[p + 1]])
                new_fr.append(fresh[2][fresh_starts[p] : fresh_starts[p + 1]])
        nal = np.asarray(new_nal, dtype=np.int32)
        ids = np.concatenate(new_ids)
        fr = np.concatenate(new_fr)
        out = np.zeros(64 + 40 * len(ids) + 12 * pairs, np.uint8)
        pos, rows, changed = codec.enc_delta(
            out,
            0,
            1,
            nal,
            ids,
            fr,
            PAIRS_DEMES,
            PAIRS_LOCI,
            SIZES,
            pn,
            pid,
            pf,
            np.zeros(1),
        )
        assert rows == len(ids)
        assert changed <= pairs
        end, got_rows, kind = _decode(bytes(out[:pos]), decoder)
        assert (end, got_rows, kind) == (pos, len(ids), codec.KIND_DELTA)
        _same(decoder, nal, ids, fr)


def test_an_unchanged_generation_is_a_short_delta() -> None:
    """The common case in a quiet run: nothing changed, a dozen bytes."""
    rng = np.random.default_rng(8)
    nal, ids, fr = _random_frame(rng)
    pairs = len(nal)
    pn = np.zeros(pairs, np.int32)
    pid = np.zeros((pairs, 16), np.int64)
    pf = np.zeros((pairs, 16), np.float64)
    codec.load_state_from_csr(nal, ids, fr, pn, pid, pf)
    out = np.zeros(4096, np.uint8)
    pos, rows, changed = codec.enc_delta(
        out,
        0,
        1,
        nal,
        ids,
        fr,
        PAIRS_DEMES,
        PAIRS_LOCI,
        SIZES,
        pn,
        pid,
        pf,
        np.zeros(1),
    )
    assert changed == 0
    assert rows == len(ids)
    assert pos == 8 + 1 + 1 + 3


@pytest.mark.skipif(not codec.HAVE_NUMBA, reason="needs numba to compare against")
def test_the_pure_python_kernels_produce_the_compiled_bytes() -> None:
    """Without numba the same code runs; its output must be identical."""
    rng = np.random.default_rng(21)
    nal, ids, fr = _random_frame(rng, max_alleles=4)
    size = len(ids) * 40 + 200
    compiled = np.zeros(size, np.uint8)
    plain = np.zeros(size, np.uint8)
    a = codec.enc_full(compiled, 0, 1, nal, ids, fr, 3, 4, SIZES, np.zeros(1))
    b = cast(Any, codec.enc_full).py_func(
        plain, 0, 1, nal, ids, fr, 3, 4, SIZES, np.zeros(1)
    )
    assert a == b
    assert compiled.tobytes() == plain.tobytes()
    state = codec.DecodedState(len(nal))
    buf = compiled
    end, rows, kind, gen_delta = cast(Any, codec.apply_record).py_func(
        buf, 0, 3, 4, SIZES, state.pn, state.pid, state.pc, state.pf
    )
    assert (int(end), int(rows), int(kind), int(gen_delta)) == (
        a[0],
        len(ids),
        codec.KIND_FULL,
        1,
    )
    _same(state, nal, ids, fr)


def test_the_codec_runs_without_numba() -> None:
    """With numba absent the same code runs as plain Python and round-trips.

    A fresh interpreter hides numba (`sys.modules["numba"] = None` makes
    its import fail), so this proves the optional dependency really is
    optional for the module every default store uses.
    """
    program = (
        "import sys\n"
        "sys.modules['numba'] = None\n"
        "import numpy as np\n"
        "from fim.persistence import tlog_codec as c\n"
        "assert not c.HAVE_NUMBA\n"
        "sizes = np.array([100, 50], dtype=np.int64)\n"
        "nal = np.array([2, 1, 1, 3], dtype=np.int32)\n"
        "ids = np.array([1, 2, 7, 4, 5, 6, 9], dtype=np.int64)\n"
        "fr = np.array([0.25, 0.75, 1.0, 0.1, 0.2, 0.7, 1.0 / 3.0])\n"
        "out = np.zeros(512, np.uint8)\n"
        "pos, rows = c.enc_full(out, 0, 5, nal, ids, fr, 2, 2, sizes, np.zeros(1))\n"
        "assert rows == 7\n"
        "state = c.DecodedState(4)\n"
        "end, rows, kind, gen = c.apply_record(\n"
        "    out, 0, 2, 2, sizes, state.pn, state.pid, state.pc, state.pf)\n"
        "assert end == pos and rows == 7 and kind == 0 and gen == 5\n"
        "counts, got_ids, got_fr = state.frame_arrays()\n"
        "assert counts.tolist() == nal.tolist()\n"
        "assert got_ids.tolist() == ids.tolist()\n"
        "assert got_fr.tobytes() == fr.tobytes()\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
