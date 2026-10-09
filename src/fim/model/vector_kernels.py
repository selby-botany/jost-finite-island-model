"""Compiled kernels for Backend V: one generation, all loci, bit-exact.

Every function here is a Numba `nopython` kernel. This module imports
`numba` at the top, so it must only be imported lazily, by code that is
about to run Backend V (`fim.model.vector_block` does that); importing
`fim` itself never needs Numba.

What a kernel generation reproduces, operation for operation, from the
dict-based operators in `fim.model.operators` (Backends L and G):

1. Stage-major order across loci, as `operators.step` runs it: migrate
   every locus, then drift every `(deme, locus)` pair deme-major and
   locus-minor, then draw mutation counts (and, under finite alleles,
   the K-allele targets) pair by pair in the same order.
2. Ascending allele-id order inside a pair, for the drift categories and
   the mutation counts. An absent allele consumes no random draw.
3. Migration arithmetic, as written in `operators`: the size-weighted
   mass is a sequential deme-ascending sum, each pool value is clamped
   at zero, and each destination row is divided by the exactly rounded
   sum (`math.fsum`) of its positive entries. A full migration matrix
   uses one `fsum` per cell, as `_migrate_matrix` does.
4. Drift normalization with NumPy's own pairwise `ndarray.sum()` over the
   present probabilities (`pairwise_sum`).
5. The same binomial primitive, `inversion_binomial`, a copy of
   `operators._inversion_binomial` with a scratch buffer in place of a
   Python list (identical floating-point operations in identical order).
6. Infinite alleles: new ids are handed out deme-major, locus-minor,
   consecutively inside a pair, from one counter, as `AlleleRegistry`
   does. Finite alleles: every mutant copy draws its target exactly as
   `FiniteAlleleSpace.mutate_target` does.

Nothing here uses `fastmath`: a fused multiply-add would change bits.
There is no self-recursion either (the cached-kernel recursion hazard
the design notes record): NumPy's recursive pairwise sum is evaluated
with explicit stacks.

The state layout (see `fim.model.vector_block.VectorBlock`) is one dense
`(loci, demes, width)` array for a whole replicate. Locus `l` keeps its
live alleles in columns `0 .. ncol[l] - 1`, in ascending allele-id order,
with the id of each column in `ids[l, column]`. Under infinite alleles
ids only grow, so appending every new mutant keeps the columns sorted,
and dropping extinct columns with an order-preserving compaction keeps
them sorted too. Under finite alleles the column is the allele id and
`ncol[l]` is the locus capacity.
"""

from __future__ import annotations

import math

import numba
import numpy as np

from fim.model.vector_block import (
    MIGRATION_MATRIX,
    MIGRATION_MATRIX_STOCHASTIC,
    MIGRATION_SCALAR,
    MIGRATION_SCALAR_STOCHASTIC,
)

# Migration is two phases, so continuous and stochastic sampling share a
# blend:
#
# 1. `_draw_migrant_fractions` fills one migrant fraction per destination
#    deme. Continuous sampling fills every fraction with the migration
#    rate and draws nothing. Stochastic sampling draws one
#    inversion-binomial count per destination, in ascending deme order,
#    before any locus is blended (`operators._migrant_fraction`), and
#    stores `count / size`. The count is shared by every locus.
# 2. `_blend_scalar` blends every locus with the per-deme fractions it was
#    given, drawing nothing. A stochastic matrix needs its own blend
#    (`_blend_matrix_stochastic`): its arithmetic differs from the
#    continuous matrix blend, because the pool excludes the destination's
#    own weight and is divided by the row's migrant weight.

STATISTIC_COLUMNS = 5
"""Columns of the per-locus statistics table: H_S, H_T, H_ST, G_ST, D."""

STATISTICS_TOLERANCE = 1e-12
"""Mirror of `fim.statistics.differentiation._TOLERANCE` (checked by a test)."""

_REFLECT_THRESHOLD = 0.5
_UNROLL = 8
_LEAF_LIMIT = 128


@numba.njit(cache=True, nogil=True)
def _mode_pmf(n, q):
    """Return the binomial PMF at its mode, in log space (never underflows).

    The first floating-point step of `operators._inversion_binomial`,
    unchanged: `exp` of a `lgamma`-based log of `C(n, mode) q^mode
    (1 - q)^(n - mode)`.

    Args:
        n: Number of trials.
        q: The success probability, reflected into `(0, 0.5]`.
    """
    mode = min(int((n + 1) * q), n)
    log_pmf_mode = (
        math.lgamma(n + 1.0)
        - math.lgamma(mode + 1.0)
        - math.lgamma(n - mode + 1.0)
        + mode * math.log(q)
        + (n - mode) * math.log1p(-q)
    )
    return math.exp(log_pmf_mode)


@numba.njit(cache=True, nogil=True)
def _walk_pmf(u, n, q, reflect, pmf_mode, pmf):
    """Invert the binomial CDF at `u` by walking the PMF out from its mode.

    The rest of `operators._inversion_binomial`, unchanged: the lower half
    of the PMF is built from the mode downward into the caller's scratch
    array, scanned upward to the cumulative through the mode, and the walk
    then continues above the mode from that running total.

    Args:
        u: The uniform draw.
        n: Number of trials.
        q: The success probability, reflected into `(0, 0.5]`.
        reflect: Whether `q` is `1 - p` (the count is then `n - k`).
        pmf_mode: `_mode_pmf(n, q)`.
        pmf: Scratch `float64` array of length at least `n + 1`.
    """
    mode = min(int((n + 1) * q), n)
    pmf[mode] = pmf_mode
    current = pmf_mode
    for k in range(mode, 0, -1):
        current = current * k / (n - k + 1) * (1.0 - q) / q
        pmf[k - 1] = current
    cdf = 0.0
    for k in range(mode + 1):
        cdf += pmf[k]
        if cdf >= u:
            return n - k if reflect else k
    upper = pmf_mode
    k = mode
    while cdf < u and k < n:
        k += 1
        upper *= (n - k + 1) / k * q / (1.0 - q)
        cdf += upper
    return n - k if reflect else k


@numba.njit(cache=True, nogil=True)
def inversion_binomial(rng, n, p, pmf):
    """Draw one `Binomial(n, p)` count, exactly as `_inversion_binomial` does.

    A line-for-line copy of `fim.model.operators._inversion_binomial`,
    split into `_mode_pmf` and `_walk_pmf` (the operations and their order
    are unchanged). The only other difference is storage: the original
    builds a Python list of the lower half of the PMF, this one writes the
    same values into the caller's scratch array. The number of uniforms
    drawn is the same too (one for a real draw, none for the three
    short-circuits).

    Args:
        rng: The run's `numpy.random.Generator`.
        n: Number of trials (`n >= 0`).
        p: Success probability (`0.0 <= p <= 1.0`).
        pmf: Scratch `float64` array of length at least `n + 1`.

    Returns:
        A `Binomial(n, p)` count in `[0, n]`.
    """
    if n <= 0 or p <= 0.0:
        return 0
    if p >= 1.0:
        return n
    u = rng.random()
    reflect = p > _REFLECT_THRESHOLD
    q = 1.0 - p if reflect else p
    return _walk_pmf(u, n, q, reflect, _mode_pmf(n, q), pmf)


@numba.njit(cache=True, nogil=True)
def cached_binomial(rng, n, p, pmf, mode_cache):
    """`inversion_binomial` for a fixed `p`, reusing the mode PMF across calls.

    The mode PMF is a pure function of `(n, p)`, and a locus's mutation
    probability never changes, so it is computed once per `n` and kept in
    `mode_cache[n]` (`nan` marks an entry not yet computed). A cached value
    is the very float the uncached code computes, so the draw is bit for
    bit `inversion_binomial`'s.

    Args:
        rng: The run's `numpy.random.Generator`.
        n: Number of trials (`0 <= n < len(mode_cache)`).
        p: Success probability, the same for every call on this cache row.
        pmf: Scratch `float64` array of length at least `n + 1`.
        mode_cache: One row of the mode-PMF cache.

    Returns:
        A `Binomial(n, p)` count in `[0, n]`.
    """
    if n <= 0 or p <= 0.0:
        return 0
    if p >= 1.0:
        return n
    u = rng.random()
    reflect = p > _REFLECT_THRESHOLD
    q = 1.0 - p if reflect else p
    pmf_mode = mode_cache[n]
    if math.isnan(pmf_mode):
        pmf_mode = _mode_pmf(n, q)
        mode_cache[n] = pmf_mode
    return _walk_pmf(u, n, q, reflect, pmf_mode, pmf)


@numba.njit(cache=True, nogil=True)
def _pairwise_leaf(values, start, count):
    """Sum one NumPy pairwise-sum leaf block (`count <= 128`) in NumPy's order.

    Below 8 values the sum is sequential; from 8 values up, eight
    accumulators run in lockstep and are combined as a balanced tree,
    then the remainder is added sequentially.
    """
    if count < _UNROLL:
        result = 0.0
        for i in range(count):
            result += values[start + i]
        return result
    r0 = values[start]
    r1 = values[start + 1]
    r2 = values[start + 2]
    r3 = values[start + 3]
    r4 = values[start + 4]
    r5 = values[start + 5]
    r6 = values[start + 6]
    r7 = values[start + 7]
    i = 8
    stop = count - (count % 8)
    while i < stop:
        r0 += values[start + i]
        r1 += values[start + i + 1]
        r2 += values[start + i + 2]
        r3 += values[start + i + 3]
        r4 += values[start + i + 4]
        r5 += values[start + i + 5]
        r6 += values[start + i + 6]
        r7 += values[start + i + 7]
        i += 8
    result = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7))
    while i < count:
        result += values[start + i]
        i += 1
    return result


@numba.njit(cache=True, nogil=True)
def pairwise_sum(values, count):
    """Return `values[:count].sum()` exactly as NumPy computes it.

    NumPy recurses above 128 values (halves, the left half rounded down
    to a multiple of 8) and adds `sum(left) + sum(right)` at every
    internal node. The recursion is evaluated here post-order with
    explicit stacks, so the additions happen in exactly NumPy's order
    without a self-recursive function (which Numba's on-disk cache
    mishandles).

    Args:
        values: `float64` array.
        count: How many leading values to sum (`count >= 0`).

    Returns:
        The same `float64` NumPy's `ndarray.sum()` returns for that slice.
    """
    if count <= _LEAF_LIMIT:
        return _pairwise_leaf(values, 0, count)
    stack_start = np.empty(64, dtype=np.int64)
    stack_count = np.empty(64, dtype=np.int64)
    stack_state = np.empty(64, dtype=np.int64)
    partial = np.empty(64, dtype=np.float64)
    top = 0
    ptop = 0
    stack_start[0] = 0
    stack_count[0] = count
    stack_state[0] = 0
    while top >= 0:
        start = stack_start[top]
        size = stack_count[top]
        if size <= _LEAF_LIMIT:
            partial[ptop] = _pairwise_leaf(values, start, size)
            ptop += 1
            top -= 1
            continue
        if stack_state[top] == 0:
            half = size // 2
            half -= half % 8
            stack_state[top] = 1
            # Push the right half first so the left half is summed first.
            stack_start[top + 1] = start + half
            stack_count[top + 1] = size - half
            stack_state[top + 1] = 0
            stack_start[top + 2] = start
            stack_count[top + 2] = half
            stack_state[top + 2] = 0
            top += 2
        else:
            right = partial[ptop - 1]
            left = partial[ptop - 2]
            ptop -= 2
            partial[ptop] = left + right
            ptop += 1
            top -= 1
    return partial[0]


@numba.njit(cache=True, nogil=True)
def exact_sum(values, count, partials):
    """Return the exactly rounded sum of `values[:count]`, as `math.fsum`.

    A port of CPython's `math.fsum` (Shewchuk's algorithm with the final
    half-even correction) for finite inputs.

    Args:
        values: `float64` array of finite values.
        count: How many leading values to sum.
        partials: Scratch `float64` array of length at least `count + 1`.

    Returns:
        The correctly rounded sum.
    """
    used = 0
    for index in range(count):
        x = values[index]
        i = 0
        for j in range(used):
            y = partials[j]
            if abs(x) < abs(y):
                x, y = y, x
            hi = x + y
            lo = y - (hi - x)
            if lo != 0.0:
                partials[i] = lo
                i += 1
            x = hi
        partials[i] = x
        used = i + 1
    hi = 0.0
    if used > 0:
        k = used - 1
        hi = partials[k]
        lo = 0.0
        while k > 0:
            x = hi
            k -= 1
            y = partials[k]
            hi = x + y
            yr = hi - x
            lo = y - yr
            if lo != 0.0:
                break
        if k > 0 and (
            (lo < 0.0 and partials[k - 1] < 0.0) or (lo > 0.0 and partials[k - 1] > 0.0)
        ):
            y = lo * 2.0
            x = hi + y
            yr = x - hi
            if y == yr:
                hi = x
    return hi


@numba.njit(cache=True, nogil=True)
def _normalize_row(row, count, probs, partials):
    """Divide `row[:count]` by the `fsum` of its positive entries, in place.

    Mirrors `operators._normalize`: entries that are not strictly
    positive are dropped (they become exactly `0.0`), the rest are
    divided by the exactly rounded sum of the positive ones.
    """
    npos = 0
    for c in range(count):
        if row[c] > 0.0:
            probs[npos] = row[c]
            npos += 1
    norm = exact_sum(probs, npos, partials)
    for c in range(count):
        value = row[c]
        row[c] = value / norm if value > 0.0 else 0.0


@numba.njit(cache=True, nogil=True)
def _draw_migrant_fractions(rng, kind, rate, weights, sizes, fractions, pmf):
    """Fill the per-destination migrant fractions for this generation.

    The draw phase of migration. Continuous sampling uses the migration
    rate itself for every destination and consumes no random number, so
    the generator stream is untouched. Stochastic sampling draws
    `Binomial(size, p) / size` for each destination in ascending deme
    order, with `p` the scalar rate or, for a matrix, the row's migrant
    weight `1 - weights[i, i]`; `operators._migrant_fraction` does the
    same through `_inversion_binomial`, which takes one uniform for a real
    draw and none when `size <= 0`, `p <= 0` or `p >= 1`. A matrix row
    with no migrant weight (`p <= 0`) is not drawn for and its fraction
    stays `0.0`; the blend copies that row unchanged.

    Args:
        rng: The run's `numpy.random.Generator`.
        kind: A `MIGRATION_*` kind.
        rate: The scalar migration rate (scalar kinds).
        weights: The migration matrix (matrix kinds).
        sizes: Gene copies per deme.
        fractions: `(demes,)` output, the fraction of each destination
            deme's gene copies replaced by the migrant pool.
        pmf: Scratch of length at least `max(sizes) + 1`.
    """
    demes = fractions.shape[0]
    if kind == MIGRATION_SCALAR:
        for i in range(demes):
            fractions[i] = rate
    elif kind == MIGRATION_SCALAR_STOCHASTIC:
        for i in range(demes):
            fractions[i] = inversion_binomial(rng, sizes[i], rate, pmf) / sizes[i]
    elif kind == MIGRATION_MATRIX_STOCHASTIC:
        for i in range(demes):
            migrant_weight = 1.0 - weights[i, i]
            if migrant_weight > 0.0:
                count = inversion_binomial(rng, sizes[i], migrant_weight, pmf)
                fractions[i] = count / sizes[i]
            else:
                fractions[i] = 0.0


@numba.njit(cache=True, nogil=True)
def _blend_scalar(freq, ncol, sizes, fractions, probs, partials, mass, blended):
    """Blend every deme with its all-other-deme pool, every locus.

    `operators._migrate_symmetric`, operation for operation: the
    size-weighted global mass is a sequential deme-ascending sum, each
    destination's pool removes its own contribution and is clamped at
    zero, the blend is `(1 - f) * local + f * pool` with the destination's
    own fraction `f`, and the row is divided by the exactly rounded sum
    (`math.fsum`) of its positive entries.

    Args:
        freq: `(loci, demes, width)` frequencies, updated in place.
        ncol: Live columns per locus.
        sizes: Gene copies per deme.
        fractions: `(demes,)` migrant fraction per destination deme.
        probs: Scratch of length at least `width`.
        partials: Scratch of length at least `width + 1`.
        mass: Scratch of length at least `width`.
        blended: Scratch of length at least `width`.
    """
    loci, demes, _ = freq.shape
    total_size = 0
    for i in range(demes):
        total_size += sizes[i]
    total_size_f = float(total_size)
    for locus in range(loci):
        nc = ncol[locus]
        for c in range(nc):
            total = 0.0
            for i in range(demes):
                total += float(sizes[i]) * freq[locus, i, c]
            mass[c] = total
        for i in range(demes):
            size_f = float(sizes[i])
            other = total_size_f - size_f
            fraction = fractions[i]
            for c in range(nc):
                local = freq[locus, i, c]
                pool = (mass[c] - size_f * local) / other
                pool = max(0.0, pool)
                blended[c] = (1.0 - fraction) * local + fraction * pool
            _normalize_row(blended, nc, probs, partials)
            for c in range(nc):
                freq[locus, i, c] = blended[c]


@numba.njit(cache=True, nogil=True)
def _blend_matrix(freq, ncol, weights, probs, partials, mass, blended):
    """Blend every deme from a full continuous migration matrix, every locus.

    `operators._migrate_matrix` with no random generator: every cell is
    the exactly rounded sum (`math.fsum`) of `weights[i, j] * freq[j]`
    over all source demes `j`, and each destination row is divided by the
    exactly rounded sum of its positive entries.

    Args:
        freq: `(loci, demes, width)` frequencies, updated in place.
        ncol: Live columns per locus.
        weights: `(demes, demes)` row-stochastic source-weight matrix.
        probs: Scratch of length at least `width`.
        partials: Scratch of length at least `width + 1`.
        mass: Scratch of length at least `width`.
        blended: Scratch of length at least `demes * width`.
    """
    loci, demes, width = freq.shape
    terms = np.empty(demes, dtype=np.float64)
    term_partials = np.empty(demes + 1, dtype=np.float64)
    for locus in range(loci):
        nc = ncol[locus]
        # Every destination reads the pre-migration sources, so the
        # blended rows are collected first and written back after.
        for i in range(demes):
            for c in range(nc):
                for j in range(demes):
                    terms[j] = weights[i, j] * freq[locus, j, c]
                mass[c] = exact_sum(terms, demes, term_partials)
            _normalize_row(mass, nc, probs, partials)
            for c in range(nc):
                blended[i * width + c] = mass[c]
        for i in range(demes):
            for c in range(nc):
                freq[locus, i, c] = blended[i * width + c]


@numba.njit(cache=True, nogil=True)
def _blend_matrix_stochastic(
    freq, ncol, weights, fractions, probs, partials, mass, blended
):
    """Blend every deme from a migration matrix with drawn migrant fractions.

    `operators._migrate_matrix` with a random generator, after the counts
    are drawn: for each destination `i` with migrant weight
    `1 - weights[i, i] > 0`, the pool of an allele is the exactly rounded
    sum (`math.fsum`) of `weights[i, j] * freq[j]` over the other demes
    `j != i`, divided by the migrant weight and not clamped; the row is
    `(1 - f) * local + f * pool` with the destination's drawn fraction
    `f`, divided by the exactly rounded sum of its positive entries. A
    row with no migrant weight is copied unchanged and not normalized.

    When `f == 0.0` the blend is exactly `local` (`1.0 * local + 0.0 *
    pool`), so the pool is not computed; the row is still normalized.

    Args:
        freq: `(loci, demes, width)` frequencies, updated in place.
        ncol: Live columns per locus.
        weights: `(demes, demes)` row-stochastic source-weight matrix.
        fractions: `(demes,)` migrant fraction per destination deme.
        probs: Scratch of length at least `width`.
        partials: Scratch of length at least `width + 1`.
        mass: Scratch of length at least `width`.
        blended: Scratch of length at least `demes * width`.
    """
    loci, demes, width = freq.shape
    terms = np.empty(demes, dtype=np.float64)
    term_partials = np.empty(demes + 1, dtype=np.float64)
    for locus in range(loci):
        nc = ncol[locus]
        # Every destination reads the pre-migration sources, so the
        # blended rows are collected first and written back after.
        for i in range(demes):
            migrant_weight = 1.0 - weights[i, i]
            fraction = fractions[i]
            base = i * width
            if migrant_weight <= 0.0:
                for c in range(nc):
                    blended[base + c] = freq[locus, i, c]
                continue
            for c in range(nc):
                local = freq[locus, i, c]
                if fraction == 0.0:
                    blended[base + c] = local
                    continue
                used = 0
                for j in range(demes):
                    if j != i:
                        terms[used] = weights[i, j] * freq[locus, j, c]
                        used += 1
                pool = exact_sum(terms, used, term_partials) / migrant_weight
                blended[base + c] = (1.0 - fraction) * local + fraction * pool
            for c in range(nc):
                mass[c] = blended[base + c]
            _normalize_row(mass, nc, probs, partials)
            for c in range(nc):
                blended[base + c] = mass[c]
        for i in range(demes):
            for c in range(nc):
                freq[locus, i, c] = blended[i * width + c]


@numba.njit(cache=True, nogil=True)
def _drift(freq, counts, ncol, sizes, rng, probs, cols, pmf):
    """Resample every `(deme, locus)` pair, deme-major then locus-minor.

    Each pair is the conditional-binomial decomposition of a multinomial
    draw over its present alleles in ascending allele-id order, with
    NumPy's pairwise sum normalizing the probabilities, exactly as
    `operators.drift` does. Results land in `counts`.
    """
    loci, demes, _ = freq.shape
    for i in range(demes):
        size = sizes[i]
        for locus in range(loci):
            nc = ncol[locus]
            npres = 0
            for c in range(nc):
                counts[locus, i, c] = 0
                if freq[locus, i, c] > 0.0:
                    probs[npres] = freq[locus, i, c]
                    cols[npres] = c
                    npres += 1
            total = pairwise_sum(probs, npres)
            for k in range(npres):
                probs[k] = probs[k] / total
            remaining_n = size
            remaining_p = 1.0
            for k in range(npres - 1):
                target = probs[k] / remaining_p if remaining_p > 0.0 else 0.0
                if target < 0.0:
                    target = 0.0
                elif target > 1.0:
                    target = 1.0
                drawn = inversion_binomial(rng, remaining_n, target, pmf)
                counts[locus, i, cols[k]] = drawn
                remaining_n -= drawn
                remaining_p -= probs[k]
            counts[locus, i, cols[npres - 1]] = remaining_n


@numba.njit(cache=True, nogil=True)
def _finite_target(
    rng, source, capacity, minted_mask, minted_list, minted_count, locus
):
    """Choose one K-allele mutation target, as `FiniteAlleleSpace` does.

    With probability `(minted - 1) / (capacity - 1)` the target is one
    already-minted state other than `source`, chosen uniformly; otherwise
    it is a uniformly drawn not-yet-minted state, which is then minted.
    The draws and their order match `FiniteAlleleSpace.mutate_target`
    (which `operators._mutate_targets_batched` already reproduces).

    Returns:
        The target state, or `-1` when every state is already minted and
        a fresh one is needed (a guard the caller turns into an error).
        The minted bookkeeping of `locus` is updated in place; the new
        minted count is stored in `minted_count[locus]`.
    """
    count = minted_count[locus]
    recurrence = (count - 1) / (capacity - 1)
    if recurrence > 0.0 and rng.random() < recurrence:
        current_index = 0
        for candidate in range(count):
            if minted_list[locus, candidate] == source:
                current_index = candidate
                break
        drawn = int(rng.integers(0, count - 1))
        if drawn >= current_index:
            drawn += 1
        return minted_list[locus, drawn]
    if count >= capacity:
        return -1
    target = int(rng.integers(0, capacity))
    while minted_mask[locus, target]:
        target = int(rng.integers(0, capacity))
    minted_list[locus, count] = target
    minted_mask[locus, target] = True
    minted_count[locus] = count + 1
    return target


@numba.njit(cache=True, nogil=True)
def _mutate(
    counts,
    ids,
    ncol,
    mus,
    mutants,
    rng,
    pmf,
    taken,
    rate_class,
    mode_cache,
    caps,
    minted_mask,
    minted_list,
    minted_count,
):
    """Mutate every gene copy independently, pair by pair.

    For each pair (deme-major, locus-minor) every present allele, in
    ascending id order, loses `Binomial(n, mu)` copies. Under infinite
    alleles the losses are only counted (`mutants`); new alleles are
    minted afterwards. Under finite alleles (`caps` non-empty) each lost
    copy immediately draws its target and the copy is added to it, before
    the next pair's counts, as `operators.mutate` interleaves them.

    A locus whose `rate_class` is not negative draws its counts with
    `cached_binomial` on row `rate_class` of `mode_cache` (the mutation
    probability is fixed per locus, so the mode PMF is reused); a negative
    class draws with `inversion_binomial`. The draws are identical.

    Returns:
        `0` on success, or `1 + locus` when a finite-alleles locus has no
        unminted state left to target.
    """
    loci, demes, _ = counts.shape
    finite = caps.shape[0] > 0
    for i in range(demes):
        for locus in range(loci):
            mu = mus[locus]
            nc = ncol[locus]
            total_mutants = 0
            if mu != 0.0:
                for c in range(nc):
                    n = counts[locus, i, c]
                    if finite:
                        taken[c] = 0
                    if n > 0:
                        if rate_class[locus] >= 0:
                            k = cached_binomial(
                                rng, n, mu, pmf, mode_cache[rate_class[locus]]
                            )
                        else:
                            k = inversion_binomial(rng, n, mu, pmf)
                        counts[locus, i, c] = n - k
                        total_mutants += k
                        if finite:
                            taken[c] = k
            mutants[locus, i] = total_mutants
            if finite and total_mutants > 0:
                for c in range(nc):
                    for _event in range(taken[c]):
                        target = _finite_target(
                            rng,
                            ids[locus, c],
                            caps[locus],
                            minted_mask,
                            minted_list,
                            minted_count,
                            locus,
                        )
                        if target < 0:
                            return 1 + locus
                        counts[locus, i, target] += 1
    return 0


@numba.njit(cache=True, nogil=True)
def compact_columns(counts, ids, ncol, mutants):
    """Drop extinct columns, keeping survivors in order; return the width needed.

    Under infinite alleles an allele absent from every deme can never
    return, so its column is free. The stable compaction keeps each
    locus's columns in ascending allele-id order.

    Args:
        counts: `(loci, demes, width)` kept gene-copy counts.
        ids: `(loci, width)` allele id of each column.
        ncol: Live columns per locus, updated in place.
        mutants: `(loci, demes)` mutant copies still to be minted.

    Returns:
        The largest per-locus column count after compaction plus the new
        mutant columns the locus is about to receive.
    """
    loci, demes, _ = counts.shape
    needed = 0
    for locus in range(loci):
        write = 0
        for c in range(ncol[locus]):
            alive = False
            for i in range(demes):
                if counts[locus, i, c] > 0:
                    alive = True
                    break
            if alive:
                if write != c:
                    for i in range(demes):
                        counts[locus, i, write] = counts[locus, i, c]
                    ids[locus, write] = ids[locus, c]
                write += 1
        for c in range(write, ncol[locus]):
            for i in range(demes):
                counts[locus, i, c] = 0
        ncol[locus] = write
        fresh = 0
        for i in range(demes):
            fresh += mutants[locus, i]
        needed = max(needed, write + fresh)
    return needed


@numba.njit(cache=True, nogil=True)
def mint_columns(freq, counts, ids, ncol, sizes, mutants, next_id):
    """Append one column per mutant copy, then write every frequency.

    Ids are handed out as `AlleleRegistry` would: pairs deme-major,
    locus-minor, consecutive inside a pair. Inside one locus that order
    is deme-ascending and every new id exceeds every older one, so the
    appended columns keep the locus in ascending id order.

    Args:
        freq: `(loci, demes, width)` frequencies, rewritten as
            `counts / sizes`.
        counts: Kept counts after compaction; new columns are appended.
        ids: Allele id of each column, extended for the new columns.
        ncol: Live columns per locus, updated in place.
        sizes: Gene copies per deme.
        mutants: `(loci, demes)` mutant copies to mint.
        next_id: One-element `int64` array holding the next unused id,
            advanced in place.
    """
    loci, demes, width = freq.shape
    cursor = next_id[0]
    starts = np.empty((loci, demes), dtype=np.int64)
    for i in range(demes):
        for locus in range(loci):
            starts[locus, i] = cursor
            cursor += mutants[locus, i]
    for locus in range(loci):
        nc = ncol[locus]
        for i in range(demes):
            for k in range(mutants[locus, i]):
                for j in range(demes):
                    counts[locus, j, nc] = 0
                counts[locus, i, nc] = 1
                ids[locus, nc] = starts[locus, i] + k
                nc += 1
        ncol[locus] = nc
        for i in range(demes):
            for c in range(nc):
                freq[locus, i, c] = counts[locus, i, c] / sizes[i]
            for c in range(nc, width):
                freq[locus, i, c] = 0.0
    next_id[0] = cursor


@numba.njit(cache=True, nogil=True)
def run_generation(
    freq,
    counts,
    ids,
    ncol,
    sizes,
    mus,
    mutants,
    rng,
    kind,
    rate,
    weights,
    rate_class,
    mode_cache,
    caps,
    minted_mask,
    minted_list,
    minted_count,
    next_id,
):
    """Advance every locus one generation: migrate, drift, mutate.

    For infinite alleles (`caps` empty) the generation ends with
    compaction and minting when the columns still fit. If they do not,
    the call returns early with the width needed, leaving the arrays
    after compaction and before minting, so the caller can grow them and
    call `mint_columns`.

    Args:
        freq: `(loci, demes, width)` frequencies, rewritten in place.
        counts: `(loci, demes, width)` int64 scratch and kept counts.
        ids: `(loci, width)` allele id of each column.
        ncol: Live columns per locus.
        sizes: Gene copies per deme.
        mus: Per-copy mutation probability per locus.
        mutants: `(loci, demes)` output, mutant copies per pair.
        rng: The run's `numpy.random.Generator`.
        kind: A `MIGRATION_*` kind (see `fim.model.vector_block`).
        rate: Scalar migration rate.
        weights: Migration matrix (a `(0, 0)` array when unused).
        rate_class: `(loci,)` int64, the row of `mode_cache` a locus's
            mutation draws use, or `-1` for uncached draws.
        mode_cache: `(classes, max_size + 1)` float64 mode-PMF cache
            (`nan` = not yet computed); persists across generations.
        caps: Finite-alleles capacity per locus (empty for infinite).
        minted_mask: `(loci, width)` bool, finite alleles only.
        minted_list: `(loci, width)` int64, finite alleles only.
        minted_count: `(loci,)` int64, finite alleles only.
        next_id: One-element array holding the next unused allele id.

    Returns:
        `0` when the generation is complete; a positive width when the
        infinite-alleles columns must grow to that width before
        `mint_columns`; a negative `-(1 + locus)` when a finite-alleles
        locus has no unminted state left to target.
    """
    loci, demes, width = freq.shape
    max_size = 0
    for i in range(demes):
        max_size = max(max_size, sizes[i])
    pmf = np.empty(max_size + 1, dtype=np.float64)
    probs = np.empty(width, dtype=np.float64)
    cols = np.empty(width, dtype=np.int64)
    partials = np.empty(width + 1, dtype=np.float64)
    mass = np.empty(width, dtype=np.float64)
    blended = np.empty(demes * width, dtype=np.float64)
    taken = np.empty(width, dtype=np.int64)

    # Migration runs for every locus before drift, as `operators.step`
    # does; under continuous sampling it draws no random number, and
    # under stochastic sampling its draws form one block ahead of drift.
    fractions = np.zeros(demes, dtype=np.float64)
    _draw_migrant_fractions(rng, kind, rate, weights, sizes, fractions, pmf)
    if kind in (MIGRATION_SCALAR, MIGRATION_SCALAR_STOCHASTIC):
        _blend_scalar(freq, ncol, sizes, fractions, probs, partials, mass, blended)
    elif kind == MIGRATION_MATRIX:
        _blend_matrix(freq, ncol, weights, probs, partials, mass, blended)
    elif kind == MIGRATION_MATRIX_STOCHASTIC:
        _blend_matrix_stochastic(
            freq, ncol, weights, fractions, probs, partials, mass, blended
        )
    _drift(freq, counts, ncol, sizes, rng, probs, cols, pmf)
    failed = _mutate(
        counts,
        ids,
        ncol,
        mus,
        mutants,
        rng,
        pmf,
        taken,
        rate_class,
        mode_cache,
        caps,
        minted_mask,
        minted_list,
        minted_count,
    )
    if failed != 0:
        return -failed
    if caps.shape[0] > 0:
        for locus in range(loci):
            for i in range(demes):
                for c in range(ncol[locus]):
                    freq[locus, i, c] = counts[locus, i, c] / sizes[i]
        return 0
    needed = compact_columns(counts, ids, ncol, mutants)
    if needed > width:
        return needed
    mint_columns(freq, counts, ids, ncol, sizes, mutants, next_id)
    return 0


@numba.njit(cache=True, nogil=True)
def _bounded(value, status, locus):
    """Clamp a unit-interval statistic, flagging a value outside tolerance."""
    if -STATISTICS_TOLERANCE <= value <= 1.0 + STATISTICS_TOLERANCE:
        return min(1.0, max(0.0, value))
    status[locus] = 1
    return value


@numba.njit(cache=True, nogil=True)
def locus_statistics(freq, ncol, out, status):
    """Compute `H_S`, `H_T`, `H_ST`, `G_ST` and `D` for every locus.

    Equal deme weights, as `statistics_report` uses for these five
    fields. Each value reproduces `fim.statistics.differentiation`
    operation for operation: exact sums (`math.fsum`) of squared
    frequencies, pooled frequencies accumulated deme-ascending, the same
    clamp to `[0, 1]` within a tolerance, the same formulas.

    Args:
        freq: `(loci, demes, width)` frequencies.
        ncol: Live columns per locus.
        out: `(loci, 5)` output: `H_S`, `H_T`, `H_ST`, `G_ST`, `D`.
            `G_ST` is `nan` where it is undefined (`H_T` is zero).
        status: `(loci,)` int64 output, `0` when every value is in range.
            A non-zero entry means the Python implementation would have
            raised; the caller recomputes that generation with it.
    """
    loci, demes, width = freq.shape
    weight = 1.0 / demes
    squares = np.empty(width, dtype=np.float64)
    terms = np.empty(demes, dtype=np.float64)
    partials = np.empty(width + demes + 1, dtype=np.float64)
    for locus in range(loci):
        nc = ncol[locus]
        status[locus] = 0
        for i in range(demes):
            for c in range(nc):
                squares[c] = freq[locus, i, c] * freq[locus, i, c]
            homozygosity = exact_sum(squares, nc, partials)
            terms[i] = weight * (1.0 - homozygosity)
        within = _bounded(exact_sum(terms, demes, partials), status, locus)
        for c in range(nc):
            pooled = 0.0
            for i in range(demes):
                pooled += weight * freq[locus, i, c]
            squares[c] = pooled * pooled
        total = _bounded(1.0 - exact_sum(squares, nc, partials), status, locus)
        denominator = 1.0 - within
        if denominator == 0.0:
            status[locus] = 1
            continue
        out[locus, 0] = within
        out[locus, 1] = total
        out[locus, 2] = _bounded((total - within) / denominator, status, locus)
        if total == 0.0:
            out[locus, 3] = np.nan
        else:
            out[locus, 3] = _bounded((total - within) / total, status, locus)
        scaled = ((total - within) / denominator) * float(demes) / float(demes - 1)
        out[locus, 4] = _bounded(scaled, status, locus)


@numba.njit(cache=True, nogil=True)
def present_entries(freq, ids, ncol, locus_ids):
    """List every present allele frequency in trajectory-row order.

    The order is deme-major, then locus, then ascending allele id, the
    order `ModelState.to_rows` writes.

    Args:
        freq: `(loci, demes, width)` frequencies.
        ids: `(loci, width)` allele id of each column.
        ncol: Live columns per locus.
        locus_ids: `(loci,)` the `locus_id` of each locus.

    Returns:
        Four equal-length arrays: one-based deme, `locus_id`, allele id
        and frequency.
    """
    loci, demes, _ = freq.shape
    total = 0
    for i in range(demes):
        for locus in range(loci):
            for c in range(ncol[locus]):
                if freq[locus, i, c] > 0.0:
                    total += 1
    deme_out = np.empty(total, dtype=np.int64)
    locus_out = np.empty(total, dtype=np.int64)
    allele_out = np.empty(total, dtype=np.int64)
    freq_out = np.empty(total, dtype=np.float64)
    at = 0
    for i in range(demes):
        for locus in range(loci):
            for c in range(ncol[locus]):
                value = freq[locus, i, c]
                if value > 0.0:
                    deme_out[at] = i + 1
                    locus_out[at] = locus_ids[locus]
                    allele_out[at] = ids[locus, c]
                    freq_out[at] = value
                    at += 1
    return deme_out, locus_out, allele_out, freq_out
