"""Exactness tests for the compiled primitives of Backend V.

Backend V reproduces Backends L and G bit for bit, so every numeric
primitive its kernel re-implements is checked here against the original
it copies: `math.fsum`, NumPy's pairwise `ndarray.sum()`, the inversion
binomial, and the five differentiation statistics. Every case is seeded;
none depends on timing or on the machine's load.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("numba")

from fim.model import vector_kernels as kernels
from fim.model.operators import _inversion_binomial, _migrant_fraction
from fim.model.vector_block import (
    MIGRATION_MATRIX,
    MIGRATION_MATRIX_STOCHASTIC,
    MIGRATION_NONE,
    MIGRATION_SCALAR,
    MIGRATION_SCALAR_STOCHASTIC,
)
from fim.statistics import differentiation

FUZZ_CASES = 10_000


def test_statistics_tolerance_mirrors_the_python_implementation() -> None:
    """The kernel clamps with the same tolerance `statistics_report` does."""
    assert kernels.STATISTICS_TOLERANCE == differentiation._TOLERANCE


def test_exact_sum_matches_math_fsum_on_seeded_fuzz() -> None:
    """The `fsum` port returns the same bits as CPython for any finite input."""
    rng = np.random.default_rng(20261008)
    for _ in range(FUZZ_CASES):
        count = int(rng.integers(1, 300))
        scale = 10.0 ** float(rng.integers(-12, 4))
        values = rng.random(count) * scale
        # Mixed signs and widely differing magnitudes exercise the carry
        # logic and the final half-even correction.
        if rng.random() < 0.3:
            values *= rng.choice([-1.0, 1.0], size=count)
        if rng.random() < 0.3:
            values[rng.integers(0, count)] *= 1e9
        partials = np.empty(count + 1, dtype=np.float64)
        assert kernels.exact_sum(values, count, partials) == math.fsum(values.tolist())


def test_exact_sum_of_nothing_is_zero() -> None:
    """An empty sum is exactly zero, as in `math.fsum`."""
    assert kernels.exact_sum(np.zeros(1), 0, np.zeros(2)) == 0.0


def test_pairwise_sum_matches_numpy_for_every_length_to_four_hundred() -> None:
    """Every regime of NumPy's pairwise sum is reproduced exactly.

    Below 8 values the sum is sequential, up to 128 it uses eight
    accumulators, above that it halves recursively; lengths 1 to 400
    cover all three, including each boundary.
    """
    rng = np.random.default_rng(7)
    for count in range(1, 401):
        for _ in range(5):
            values = rng.random(count)
            assert kernels.pairwise_sum(values, count) == values.sum()


def test_pairwise_sum_matches_numpy_on_seeded_fuzz() -> None:
    """Random lengths and magnitudes up to several thousand values agree."""
    rng = np.random.default_rng(20261009)
    for _ in range(FUZZ_CASES):
        count = int(rng.integers(1, 3000))
        scale = 10.0 ** float(rng.integers(-12, 4))
        values = rng.random(count) * scale
        assert kernels.pairwise_sum(values, count) == values[:count].sum()


def test_pairwise_sum_uses_only_the_leading_values() -> None:
    """Values past `count` are ignored, as the kernel's scratch arrays need."""
    values = np.arange(1.0, 50.0)
    assert kernels.pairwise_sum(values, 10) == values[:10].sum()


@pytest.mark.parametrize(
    ("n", "p"),
    [
        (0, 0.3),
        (5, 0.0),
        (5, 1.0),
        (1, 0.5),
        (1, 0.7),
        (10, 0.001),
        (100, 0.000001),
        (100, 0.5),
        (100, 0.9),
        (400, 0.4),
        (400, 0.6),
        (5000, 0.02),
        (5000, 0.98),
    ],
)
def test_inversion_binomial_matches_the_original_stream_and_values(
    n: int, p: float
) -> None:
    """Same values and the same generator state as `_inversion_binomial`.

    Equal final generator states prove the draw count (one uniform for a
    real draw, none for the short-circuits) is the same too.
    """
    seed = 20261008 + n
    original = np.random.Generator(np.random.PCG64(seed))
    compiled = np.random.Generator(np.random.PCG64(seed))
    scratch = np.empty(n + 1, dtype=np.float64)
    for _ in range(500):
        assert kernels.inversion_binomial(compiled, n, p, scratch) == (
            _inversion_binomial(original, n, p)
        )
    assert original.bit_generator.state == compiled.bit_generator.state


def test_inversion_binomial_matches_on_seeded_fuzz() -> None:
    """Random `(n, p)` pairs, one stream, stay in step to the last bit."""
    shape = np.random.default_rng(99)
    original = np.random.Generator(np.random.PCG64(5))
    compiled = np.random.Generator(np.random.PCG64(5))
    scratch = np.empty(2001, dtype=np.float64)
    for _ in range(FUZZ_CASES):
        n = int(shape.integers(0, 2001))
        p = float(shape.random() ** int(shape.integers(1, 6)))
        assert kernels.inversion_binomial(compiled, n, p, scratch) == (
            _inversion_binomial(original, n, p)
        )
    assert original.bit_generator.state == compiled.bit_generator.state


def _random_table(
    rng: np.random.Generator, demes: int, alleles: int, size: int
) -> list[dict[int, float]]:
    """Return one locus's per-deme frequency maps on the `1 / size` grid.

    Alleles are drawn from a pool of `alleles` ids, so demes share some
    and not others; every deme has at least one allele.
    """
    table: list[dict[int, float]] = []
    for _ in range(demes):
        chosen = rng.choice(
            alleles, size=int(rng.integers(1, alleles + 1)), replace=False
        )
        counts = rng.multinomial(size, np.full(len(chosen), 1.0 / len(chosen)))
        table.append(
            {
                int(allele): float(count) / size
                for allele, count in zip(chosen, counts, strict=True)
                if count > 0
            }
        )
    return table


def _dense(
    table: list[dict[int, float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pack frequency maps as the kernel's `(1, demes, width)` layout."""
    ids = sorted({allele for deme in table for allele in deme})
    freq = np.zeros((1, len(table), len(ids) + 3), dtype=np.float64)
    for deme_index, deme in enumerate(table):
        for column, allele in enumerate(ids):
            freq[0, deme_index, column] = deme.get(allele, 0.0)
    return freq, np.array([len(ids)], dtype=np.int64), np.array(ids, dtype=np.int64)


@pytest.mark.parametrize("demes", [2, 3, 5, 20])
@pytest.mark.parametrize("alleles", [1, 2, 7, 30, 200])
def test_locus_statistics_match_statistics_report_bit_for_bit(
    demes: int, alleles: int
) -> None:
    """`H_S`, `H_T`, `H_ST`, `G_ST` and `D` equal `statistics_report`'s bits.

    Seeded states with 1 to 200 alleles, shared and private across demes.
    """
    rng = np.random.default_rng(1000 * demes + alleles)
    for _ in range(40):
        table = _random_table(rng, demes, alleles, 400)
        freq, ncol, _ids = _dense(table)
        out = np.zeros((1, kernels.STATISTIC_COLUMNS), dtype=np.float64)
        status = np.zeros(1, dtype=np.int64)
        kernels.locus_statistics(freq, ncol, out, status)
        report = differentiation.statistics_report(
            table, None, validate=False, statistics=()
        )
        assert status[0] == 0
        assert out[0, 0] == report["H_S"]
        assert out[0, 1] == report["H_T"]
        assert out[0, 2] == report["H_ST"]
        assert out[0, 4] == report["D"]
        if report["G_ST"] is None:
            assert math.isnan(out[0, 3])
        else:
            assert out[0, 3] == report["G_ST"]


def test_locus_statistics_report_g_st_undefined_when_every_deme_is_fixed_alike() -> (
    None
):
    """A monomorphic locus has no `G_ST`: the kernel returns `nan`, not an error."""
    table = [{4: 1.0}, {4: 1.0}, {4: 1.0}]
    freq, ncol, _ids = _dense(table)
    out = np.zeros((1, kernels.STATISTIC_COLUMNS), dtype=np.float64)
    status = np.zeros(1, dtype=np.int64)
    kernels.locus_statistics(freq, ncol, out, status)
    assert status[0] == 0
    assert math.isnan(out[0, 3])
    assert (
        differentiation.statistics_report(table, None, validate=False)["G_ST"] is None
    )


def test_locus_statistics_flag_a_frequency_row_that_is_not_normalized() -> None:
    """A value outside the unit interval is flagged, not silently clamped.

    `statistics_report` raises `ArithmeticError` there, so the kernel
    sets the locus's status and lets the caller recompute with Python.
    """
    freq = np.zeros((1, 2, 4), dtype=np.float64)
    freq[0, 0, 0] = 3.0
    freq[0, 1, 1] = 3.0
    out = np.zeros((1, kernels.STATISTIC_COLUMNS), dtype=np.float64)
    status = np.zeros(1, dtype=np.int64)
    kernels.locus_statistics(freq, np.array([2], dtype=np.int64), out, status)
    assert status[0] == 1


def _draw(
    rng: np.random.Generator,
    kind: int,
    rate: float,
    weights: np.ndarray,
    sizes: list[int],
) -> np.ndarray:
    """Run the kernel's draw phase and return the fractions it filled."""
    fractions = np.zeros(len(sizes), dtype=np.float64)
    scratch = np.empty(max(sizes) + 1, dtype=np.float64)
    kernels._draw_migrant_fractions(
        rng,
        kind,
        rate,
        weights,
        np.asarray(sizes, dtype=np.int64),
        fractions,
        scratch,
    )
    return fractions


NO_WEIGHTS = np.zeros((0, 0), dtype=np.float64)


def test_continuous_migration_fills_every_fraction_and_draws_nothing() -> None:
    """Continuous sampling fills each fraction with the rate, drawing nothing.

    The generator stream is exactly what the dictionary-based operators
    leave, so a continuous run is unaffected by the stochastic option.
    """
    rng = np.random.Generator(np.random.PCG64(11))
    before = rng.bit_generator.state
    sizes = [100] * 5
    assert _draw(rng, MIGRATION_SCALAR, 0.05, NO_WEIGHTS, sizes).tolist() == [0.05] * 5
    for kind in (MIGRATION_NONE, MIGRATION_MATRIX):
        untouched = _draw(rng, kind, 0.05, NO_WEIGHTS, sizes)
        assert untouched.tolist() == [0.0] * 5
    assert rng.bit_generator.state == before


@pytest.mark.parametrize("rate", [0.0001, 0.05, 0.3, 0.75, 1.0])
def test_stochastic_scalar_fractions_equal_the_operators_draws(rate: float) -> None:
    """Each destination draws `Binomial(size, rate) / size`, in deme order.

    Compared against `operators._migrant_fraction` on an identical stream,
    with unequal sizes, and the generator state afterward must agree (one
    uniform per real draw, none when `rate >= 1`).
    """
    sizes = [50, 80, 100, 120, 150, 1]
    kernel_rng = np.random.Generator(np.random.PCG64(5))
    operator_rng = np.random.Generator(np.random.PCG64(5))
    for _ in range(200):
        got = _draw(kernel_rng, MIGRATION_SCALAR_STOCHASTIC, rate, NO_WEIGHTS, sizes)
        want = [_migrant_fraction(operator_rng, size, rate) for size in sizes]
        assert got.tolist() == want
    assert kernel_rng.bit_generator.state == operator_rng.bit_generator.state


def test_stochastic_matrix_fractions_use_each_rows_migrant_weight() -> None:
    """Matrix draws use `1 - weights[i, i]`; a row with none is not drawn.

    Row 0 keeps everything (no migrant weight): no uniform, fraction 0.
    Row 2 has no self-weight (migrant weight 1): no uniform, fraction 1.
    """
    weights = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.1, 0.6, 0.2, 0.1],
            [0.0, 0.5, 0.0, 0.5],
            [0.2, 0.2, 0.2, 0.4],
        ]
    )
    sizes = [60, 90, 120, 150]
    kernel_rng = np.random.Generator(np.random.PCG64(9))
    operator_rng = np.random.Generator(np.random.PCG64(9))
    for _ in range(200):
        got = _draw(kernel_rng, MIGRATION_MATRIX_STOCHASTIC, 0.0, weights, sizes)
        want = [0.0, 0.0, 1.0, 0.0]
        want[1] = _migrant_fraction(operator_rng, sizes[1], 1.0 - 0.6)
        want[3] = _migrant_fraction(operator_rng, sizes[3], 1.0 - 0.4)
        assert got.tolist() == want
    assert kernel_rng.bit_generator.state == operator_rng.bit_generator.state


@pytest.mark.parametrize("p", [1e-6, 0.002, 0.3, 0.5, 0.75, 0.999])
def test_cached_binomial_equals_the_uncached_draw_bit_for_bit(p: float) -> None:
    """Reusing the mode probability changes no draw and no stream position.

    The cache holds the exact float the uncached code computes for that
    count, so values and the final generator state agree, over counts that
    repeat (cache hits) and counts seen once (cache fills).
    """
    shape = np.random.default_rng(31)
    plain = np.random.Generator(np.random.PCG64(8))
    cached = np.random.Generator(np.random.PCG64(8))
    scratch = np.empty(301, dtype=np.float64)
    cache = np.full(301, np.nan, dtype=np.float64)
    for _ in range(5000):
        n = int(shape.integers(0, 301))
        assert kernels.cached_binomial(cached, n, p, scratch, cache) == (
            kernels.inversion_binomial(plain, n, p, scratch)
        )
    assert plain.bit_generator.state == cached.bit_generator.state
    filled = ~np.isnan(cache)
    assert filled[0] == (p >= 1.0)  # n = 0 never reaches the cache
    assert filled[1:].any()
