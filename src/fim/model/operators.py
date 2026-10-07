"""Pure migration, mutation, drift, and generation-pipeline operators.

This module implements the three biological processes every generation
of the simulation actually goes through, plus `step`, which chains all
three together in the standard order:

- `migrate` — a fraction of each deme's gene copies are replaced by a
  weighted average of every other deme's own allele frequencies (the
  "migrant pool"), modeling individuals moving between sub-populations.
- `drift` — the full population of `N` gene copies is re-sampled from
  the current frequencies, the same way flipping a weighted coin `N`
  times only approximately reproduces the coin's own true weighting;
  this is what makes a finite population's allele frequencies wander
  randomly from one generation to the next, purely from chance, even
  with no migration or mutation happening at all.
- `mutate` — each of those newly drawn gene copies independently
  switches, with probability `mu`, to a different, new-or-existing
  allele, modeling a real mutation event.

Every function here is "pure" in the sense that none of them mutate
their `ModelState` argument in place — each one returns a brand-new
state representing the *result* of applying that one process, leaving
the state it was given untouched (see `fim.model.state.ModelState`'s
own docstring for why that immutability matters). `step`, at the
bottom of this file, is what `fim.engine`'s run loop actually calls
once per generation: it runs migration, then drift, then mutation, in
that fixed order — the textbook Wright-Fisher island model, in which
the new generation's gene copies are drawn from the post-migration
parental pool and each copy then mutates on its own.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from fim.model.allele import (
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.locus import LocusSpec
from fim.model.params import (
    Migration,
    MutationRate,
    PopulationSize,
    SimulationParams,
)
from fim.model.state import FrequencyMap, ModelState

_JIT_MULTINOMIAL_VIA_BINOMIAL: (
    Callable[[np.random.Generator, int, np.ndarray], np.ndarray] | None
) = None

# `_inversion_binomial`'s own reflection cutover: reflecting p -> 1 - p
# whenever p exceeds this keeps q = min(p, 1 - p) always <= 0.5, which
# is what keeps the mode -- and so the scan's own worst-case length --
# bounded by roughly n / 2 rather than n.
_REFLECT_THRESHOLD = 0.5

# `mutate`'s own batched, JIT-compiled finite-alleles target-selection
# path (`_jit_mutate_targets_batched`) needs a `capacity`-sized dense
# array per locus per pair (`FiniteAlleleSpace.to_arrays`) -- safe and
# cheap at the capacities this model's own docstring calls "moderate,"
# genuinely unrepresentable at the astronomical ones it also names as a
# real, intended case (this project's own tests use capacity 4**1000).
# A locus above this bound falls back to the unjitted, O(1)-per-draw
# `FiniteAlleleSpace.mutate_target` path instead -- silently, not an
# error, matching `migrate`'s/`mutate`'s own established "performance
# hint, not a mode switch" contract. A reasonable, deliberately
# conservative first default, not a benchmarked one -- see
# `20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
# §10 item 10e's own "target selection" follow-on entry.
_MAX_JIT_FINITE_ALLELE_CAPACITY = 2**20

# `mutate` acts on whole gene copies: a frequency times its deme's `N`
# farther than this from a whole number is not a post-drift state.
_GRID_TOLERANCE = 1e-6


def _inversion_binomial(rng: np.random.Generator, n: int, p: float) -> int:
    """Draw one `Binomial(n, p)` count via inverse-CDF ("chop-down") sampling.

    The one genuinely new primitive Stage F8
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4) needs: `numpy.random.Generator.binomial` is *not* a fixed,
    known-in-advance number of underlying random draws — NumPy picks
    among several internal algorithms depending on `n`/`p` (inversion
    for small `n * min(p, 1 - p)`, a rejection method (BTPE) for large
    values), and a rejection method's own retry loop can consume a
    different number of bits from the same seed's own bit stream
    depending on which candidates get rejected — invisible from the
    caller's own seed/argument values alone. That variability is
    exactly what stands between today's two backends: Backend L's own
    dict-based `drift`/`mutate` and Backend V's own array-native
    versions already draw the same *distributions*, in the same
    canonical order, but not detectably the same *underlying bits*,
    because `Generator.binomial`'s own algorithm choice is opaque and
    can differ in how much of the stream one call actually consumes.

    For any genuine draw (`n > 0`, `0 < p < 1`), this function always
    does exactly the same thing: draw one `rng.random()` uniform, then
    walk the `Binomial(n, p)` PMF outward from its own mode until the
    cumulative probability first reaches that uniform — the textbook
    inverse-CDF construction. Exactly one `rng.random()` call, always,
    for that case — no retry loop, no data-dependent stream consumption
    — which is the actual property this whole unification depends on,
    not merely "produces the same distribution" (already true of
    `rng.binomial` itself). The three short-circuits below (`n <= 0`,
    `p <= 0.0`, `p >= 1.0`) consume *zero* draws instead — still a
    "fixed, known-in-advance" count in the sense that matters here
    (which branch applies is knowable from `n`/`p` alone, before any
    random number is needed at all), just not the same fixed count as
    the general case.

    **Anchored at the mode, not at `k=0`** — a first version of this
    function anchored the scan at `pmf(0) = (1 - q)^n` instead, tested,
    and found genuinely wrong, not just imprecise: for `n` in the
    thousands (this project's own deme population sizes, `N`, are
    exactly this shape of number) and `q = min(p, 1 - p)` not tiny,
    `(1 - q)^n` underflows to an exact `0.0` in `float64` — after that,
    the forward multiplicative recurrence can only ever multiply zero by
    finite ratios, so the scan silently returns `n` (or `0`, after
    reflection) unconditionally, for every `u`, 100% of the time
    (confirmed directly by a moment-match check before this fix, not
    merely reasoned about — `test_inversion_binomial_...` covers the
    exact `n`/`p` combinations that failed). Anchoring at the
    distribution's own mode instead — where the true probability mass
    actually is, computed via `math.lgamma`-based log-space binomial
    coefficients (never underflows for any `n` a real gene-copy count
    could plausibly be) — keeps every step of the walk outward a
    well-conditioned ratio of adjacent, comparable-magnitude PMF values,
    never a product decaying from a literal zero.

    Args:
        rng: The run's explicitly threaded random generator.
        n: Number of trials (`n >= 0`).
        p: Success probability (`0.0 <= p <= 1.0`).

    Returns:
        A `Binomial(n, p)`-distributed count in `[0, n]`.
    """
    if n <= 0 or p <= 0.0:
        return 0
    if p >= 1.0:
        return n
    u = rng.random()
    reflect = p > _REFLECT_THRESHOLD
    q = 1.0 - p if reflect else p
    mode = min(int((n + 1) * q), n)
    log_pmf_mode = (
        math.lgamma(n + 1.0)
        - math.lgamma(mode + 1.0)
        - math.lgamma(n - mode + 1.0)
        + mode * math.log(q)
        + (n - mode) * math.log1p(-q)
    )
    pmf_mode = math.exp(log_pmf_mode)

    # Walk mode -> 0 first (short, stable chain from the mode — never a
    # product decaying from an underflowed value, unlike the rejected
    # k=0-anchored version above), collecting every pmf value, then scan
    # those values 0 -> mode in ascending order to get the *true*
    # cumulative through the mode (not `pmf_mode` alone — an earlier
    # version of this function conflated the two, a second, distinct
    # bug caught the same way: a moment-match test that actually failed
    # rather than one skipped or hand-waved past).
    low_pmf = [pmf_mode]
    current = pmf_mode
    for k in range(mode, 0, -1):
        current = current * k / (n - k + 1) * (1.0 - q) / q
        low_pmf.append(current)
    low_pmf.reverse()
    cdf = 0.0
    for k in range(mode + 1):
        cdf += low_pmf[k]
        if cdf >= u:
            return n - k if reflect else k

    # `u` exceeds the true cumulative through the mode — continue
    # outward, upward, from that same (now-correct) running total.
    pmf = pmf_mode
    k = mode
    while cdf < u and k < n:
        k += 1
        pmf *= (n - k + 1) / k * q / (1.0 - q)
        cdf += pmf
    return n - k if reflect else k


def _multinomial_via_binomial(
    rng: np.random.Generator, n: int, probabilities: np.ndarray
) -> np.ndarray:
    """Draw one multinomial sample via sequential conditional-binomial draws.

    The standard identity: a multinomial draw of size `n` over
    categories `p_1 .. p_k` decomposes into `count_1 ~ Binomial(n,
    p_1)`, then `count_2 ~ Binomial(n - count_1, p_2 / (1 - p_1))`, and
    so on, the final category absorbing whatever remains. This is not
    merely *statistically equivalent* to `numpy.random.Generator.
    multinomial` — confirmed directly, across several thousand
    randomized seed/parameter combinations (`test/model/
    test_operators.py`), it reproduces `Generator.multinomial`'s own
    output bit-for-bit, because NumPy's own internal implementation
    already uses this identical decomposition. That bit-identity is
    exactly what makes `_jit_multinomial_via_binomial`, below, a safe
    drop-in replacement for `rng.multinomial(n, probabilities)`: the
    only reason this function exists at all is that `Generator.
    multinomial` itself is something Numba's `@jit` cannot compile,
    where `Generator.binomial` with scalar arguments is — not because
    the sampling algorithm itself needed to change.

    Args:
        rng: The run's explicitly threaded random generator.
        n: Total count to distribute across categories.
        probabilities: Row-stochastic (summing to 1) category weights.

    Returns:
        Integer counts, one per category, summing to `n`.
    """
    category_count = probabilities.shape[0]
    counts = np.empty(category_count, dtype=np.int64)
    remaining_n = n
    remaining_p = 1.0
    for index in range(category_count - 1):
        target_p = probabilities[index] / remaining_p if remaining_p > 0.0 else 0.0
        if target_p < 0.0:
            target_p = 0.0
        elif target_p > 1.0:
            target_p = 1.0
        drawn = rng.binomial(remaining_n, target_p)
        counts[index] = drawn
        remaining_n -= drawn
        remaining_p -= probabilities[index]
    counts[category_count - 1] = remaining_n
    return counts


def _jit_multinomial_via_binomial(
    rng: np.random.Generator, n: int, probabilities: np.ndarray
) -> np.ndarray:
    """`_multinomial_via_binomial`, JIT-compiled with `nogil=True`.

    Not used by `drift` itself (see `_drift_counts_batched`'s own
    docstring for why a per-call version of this regresses wall-clock
    time rather than improving it) — kept as the direct, one-call-at-a-
    time building block `_drift_counts_batched` below is proven against,
    and as the smallest possible reproduction of the underlying
    bit-identity claim for anything that only needs a single draw.

    `numba` is an optional dependency (``pip install fim[jit]``),
    imported here and nowhere else in this module — importing `fim.
    model.operators` never requires it, and only a caller that
    explicitly requests JIT-compiled drift ever pays its import/
    compilation cost or needs it installed at all. Compiled once, on
    first call, and cached at module level — every call after the first
    reuses the already-compiled function rather than recompiling.

    `cache=True` additionally persists the compiled machine code to an
    on-disk cache file, reused across separate processes too, not just
    separate calls within one -- see `fim.model.vectorized._jit_
    multinomial_rows_batched`'s own docstring for the measured benefit
    and the confirmed-safe read-only-install fallback.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MULTINOMIAL_VIA_BINOMIAL  # noqa: PLW0603
    if _JIT_MULTINOMIAL_VIA_BINOMIAL is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MULTINOMIAL_VIA_BINOMIAL = numba.jit(nogil=True, cache=True)(
            _multinomial_via_binomial
        )
    return _JIT_MULTINOMIAL_VIA_BINOMIAL(rng, n, probabilities)


def _multinomial_via_inversion_binomial(
    rng: np.random.Generator, n: int, probabilities: np.ndarray
) -> np.ndarray:
    """Draw one multinomial sample via `_inversion_binomial`, category by category.

    The same sequential conditional-binomial decomposition
    `_multinomial_via_binomial` (above) uses, with one deliberate
    difference: each category's own count comes from `_inversion_
    binomial`, not `rng.binomial`. That is the actual mechanism Stage
    F8 (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4, "one RNG scheme for every backend") needs — a fixed,
    known-in-advance number of uniforms per draw, which `rng.binomial`
    itself does not guarantee (its own internal algorithm choice is
    opaque and `n`/`p`-dependent; `_inversion_binomial`'s own docstring
    has the full argument). Deliberately **not** bit-identical to
    `rng.multinomial`/`_multinomial_via_binomial` — a different
    underlying binomial algorithm necessarily produces a different
    specific sample for the same seed, even though both target the
    identical distribution (`_inversion_binomial`'s own docstring; the
    accepted cost of Stage F8, `20260901-...-design.md` §5.4's own "the
    real cost, named plainly").

    `drift`'s own `jit=False` path calls this directly, in place of
    `rng.multinomial`; `_drift_counts_batched`'s own inner loop performs
    the equivalent computation for the `jit=True` path, batched across
    every (deme, locus) pair rather than calling this once per pair —
    the two paths use the identical underlying primitive and decompose
    the identical way, which is what makes them bit-identical to each
    other (see `drift`'s own docstring), not merely close.

    Args:
        rng: The run's explicitly threaded random generator.
        n: Total count to distribute across categories.
        probabilities: Row-stochastic (summing to 1) category weights,
            already in whatever order the caller has fixed as
            canonical — this function draws in exactly that order and
            has no opinion of its own about what the right order is
            (see `drift`'s own docstring for what canonical means
            there).

    Returns:
        Integer counts, one per category, summing to `n`.
    """
    category_count = probabilities.shape[0]
    counts = np.empty(category_count, dtype=np.int64)
    remaining_n = n
    remaining_p = 1.0
    for index in range(category_count - 1):
        target_p = probabilities[index] / remaining_p if remaining_p > 0.0 else 0.0
        if target_p < 0.0:
            target_p = 0.0
        elif target_p > 1.0:
            target_p = 1.0
        drawn = _inversion_binomial(rng, remaining_n, target_p)
        counts[index] = drawn
        remaining_n -= drawn
        remaining_p -= probabilities[index]
    counts[category_count - 1] = remaining_n
    return counts


_JIT_MUTATE_TARGETS_BATCHED: (
    Callable[
        [np.random.Generator, np.ndarray, int, np.ndarray, np.ndarray, int, int],
        tuple[np.ndarray, np.ndarray, np.ndarray, int, int],
    ]
    | None
) = None


def _mutate_targets_batched(
    rng: np.random.Generator,
    sources: np.ndarray,
    capacity: int,
    minted_mask: np.ndarray,
    minted_list: np.ndarray,
    minted_count: int,
    next_unminted: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Choose one mutation target per event, in order, updating minted state as it goes.

    **A deliberate duplicate of `fim.model.vectorized._mutate_targets_
    batched`, not an import of it** — `fim.model.vectorized` already
    imports from this module (`_inversion_binomial`), so the reverse
    import would be circular. Kept identical to the original on
    purpose (`20260901-claude-sonnet-5-fim-engine-backend-factory-
    design.md` §10 item 10e, stage 3's own "target selection" follow-
    on): a direct array/loop translation of `FiniteAlleleSpace.
    mutate_target`, called once per event in `sources` rather than once
    per event from `mutate`'s own dict-based loop, proven exactly
    matching that method by the original's own `test_mutate_targets_
    batched_matches_finite_allele_space_exactly` (`test/model/
    test_vectorized.py`) — this module's own `test_mutate_targets_
    batched_matches_finite_allele_space_exactly` (`test/model/
    test_operators.py`) re-proves it directly for *this* copy, not
    inherited from the original's own test. Needs no nested-closure
    duplication of any other function the way `_drift_counts_batched`/
    `_mutate_event_counts_batched`/`_multinomial_via_inversion_
    binomial_compiled` above do — this function calls nothing from
    `fim.model.operators` at all, only `rng.random()`/`rng.integers()`,
    both natively supported in `nopython` mode.

    Args:
        rng: The run's explicitly threaded random generator.
        sources: `(events,)` int64 — each mutating copy's own source
            allele id, in visiting order.
        capacity: This locus's own fixed state-space size, `K`.
        minted_mask: `(capacity,)` bool, mutated in place — pass a copy
            if the caller's own array must stay unchanged.
        minted_list: `(capacity,)` int64, mutated in place — same caveat.
        minted_count: How many of `minted_list`'s own entries are valid.
        next_unminted: Inert since `FIM-46`'s fix, below — carried
            through this signature and the returned tuple unchanged,
            never read for a mint decision (`FiniteAlleleSpace.to_
            arrays`'s own docstring, `fim.model.allele`, has the full
            reasoning for why the argument/return shape did not change
            along with the fix).

    Returns:
        `(targets, minted_mask, minted_list, minted_count, next_unminted)`
        — `targets` is `(events,)` int64, one target per event;
        `minted_mask`/`minted_list`/`minted_count` are as they stand
        after every event; `next_unminted` is `next_unminted`, verbatim.

    Raises:
        RuntimeError: If a mint event is needed but every state in
            `0 .. capacity - 1` is already minted — mirrors
            `FiniteAlleleSpace.mutate_target`'s own guard exactly (see
            its own docstring for why this is unreachable in practice).
            Restores a guard `_mutate_targets_batched` itself never had
            (this project's own multi-model engine review, 2026-09-04,
            `FIM-16`): the dict-based original already raised here, but
            this batched copy indexed `minted_mask[next_unminted]` with
            no bound check, `IndexError`-ing on a `NumPy` internal
            instead of failing with a message naming the actual problem.
    """
    event_count = sources.shape[0]
    targets = np.empty(event_count, dtype=np.int64)
    for event_index in range(event_count):
        current = sources[event_index]
        recurrence_probability = (minted_count - 1) / (capacity - 1)
        if recurrence_probability > 0.0 and rng.random() < recurrence_probability:
            # See `fim.model.vectorized._mutate_targets_batched`'s own
            # inline comment for the full "exclude one element, shift
            # the drawn index" argument this branch relies on.
            current_index = 0
            for candidate_index in range(minted_count):
                if minted_list[candidate_index] == current:
                    current_index = candidate_index
                    break
            drawn_index = int(rng.integers(0, minted_count - 1))
            if drawn_index >= current_index:
                drawn_index += 1
            targets[event_index] = minted_list[drawn_index]
        else:
            # Uniform rejection sampling over every not-yet-minted
            # state, matching `FiniteAlleleSpace.mutate_target`'s own
            # fix exactly (`FIM-46`) — not `next_unminted`, which always
            # returned the smallest not-yet-minted state deterministically,
            # never a genuine uniform draw among every one of them.
            if minted_count >= capacity:
                raise RuntimeError(
                    "finite allele space has no unminted state left to target"
                )
            target = int(rng.integers(0, capacity))
            while minted_mask[target]:
                target = int(rng.integers(0, capacity))
            minted_list[minted_count] = target
            minted_mask[target] = True
            minted_count += 1
            targets[event_index] = target
    return targets, minted_mask, minted_list, minted_count, next_unminted


def _jit_mutate_targets_batched(
    rng: np.random.Generator,
    sources: np.ndarray,
    capacity: int,
    minted_mask: np.ndarray,
    minted_list: np.ndarray,
    minted_count: int,
    next_unminted: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """`_mutate_targets_batched`, JIT-compiled with `nogil=True`.

    `numba` is an optional dependency (`pip install fim[jit]`), imported
    here and nowhere else in this function — only a caller that
    explicitly requests `mutate(..., jit=True)` on an eligible (bounded-
    capacity) finite-alleles run ever pays its import/compilation cost
    or needs it installed at all. Compiled once, on first call, and
    cached at module level, independently of `fim.model.vectorized`'s
    own identically-named cache — two separate compiled artifacts of
    the same source, one per module, exactly as the duplication above
    already implies (`cache=True` below persists each to its own
    on-disk file for the same reason — one per module, keyed by that
    module's own name — not one shared file the two could collide
    over).

    `cache=True` additionally persists the compiled machine code across
    separate processes, not just separate calls within one -- see
    `fim.model.vectorized._jit_multinomial_rows_batched`'s own
    docstring for the measured benefit and the confirmed-safe
    read-only-install fallback.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MUTATE_TARGETS_BATCHED  # noqa: PLW0603
    if _JIT_MUTATE_TARGETS_BATCHED is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MUTATE_TARGETS_BATCHED = numba.jit(nogil=True, cache=True)(
            _mutate_targets_batched
        )
    return _JIT_MUTATE_TARGETS_BATCHED(
        rng, sources, capacity, minted_mask, minted_list, minted_count, next_unminted
    )


_JIT_DRIFT_COUNTS_BATCHED: (
    Callable[[np.random.Generator, np.ndarray, np.ndarray, np.ndarray], np.ndarray]
    | None
) = None


def _drift_counts_batched(
    rng: np.random.Generator,
    ns: np.ndarray,
    offsets: np.ndarray,
    probabilities_flat: np.ndarray,
) -> np.ndarray:
    """Draw every (deme, locus) pair's own multinomial sample in one pass.

    A single per-(deme, locus) call to `_jit_multinomial_via_binomial`
    (what an earlier version of `drift`'s own `jit=True` path did,
    tagged `spike/jit-per-deme-call-overhead`) is a measured wall-clock
    *regression*, not a win: at this project's own reference scale, the
    fixed cost of crossing the Python/Numba call boundary once per pair
    — hundreds to tens of thousands of times per run — dominates the
    tiny amount of actual compute (at most a handful of binomial draws)
    each individual call does. This function removes that specific
    regression by paying the call-boundary crossing exactly once per
    *generation* instead of once per pair — every pair's own probability
    vector is packed into one flat `float64` buffer, with `offsets`
    marking where each pair's own slice starts and ends (a ragged-array-
    as-flat-buffer-plus-offsets layout, since different (deme, locus)
    pairs can have different segregating-allele counts), and the loop
    over pairs, and the conditional-binomial loop within each pair, both
    happen *inside* one JIT-compiled call.

    Measured honestly, this is not a clean win for `drift` as a whole,
    though — only a fix for the specific regression above. Once the
    one-time JIT compilation cost is correctly excluded (paid once per
    process, not once per generation), the compiled call itself is fast,
    but `drift`'s own overall wall-clock time across a realistic range of
    allele diversity (`K` from 2 to 256, `d=100` demes) lands within
    roughly 0.9x-1.1x of the unjitted path either way — a wash, not a
    win. The reason: building `probabilities_flat`/`offsets` before this
    call and unpacking `counts_flat` back into per-deme frequency maps
    after it are both still ordinary Python-level work over `ModelState`'s
    own sparse dict-of-dicts representation, done as two separate full
    passes over every pair instead of the unjitted path's one interleaved
    pass — and at this project's reference scale, that Python-level
    marshaling cost, not the random draw itself, is what actually
    dominates `drift`'s own wall-clock time. JIT-compiling the draw
    cannot fix a bottleneck that was never in the draw. See
    `20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §5.3
    for the full measurement history (including the initial, misleadingly
    optimistic benchmark that did not account for this) and what it
    implies for whether `jit="numba"` alone, without a wider array-native
    pipeline, is worth using at all.

    Bit-identity to `drift`'s own unjitted path depends on visiting
    pairs, and categories within a pair, in the identical order that
    path does (`_build_flat_drift_buffers`, below, builds `ns`/
    `offsets`/`probabilities_flat` in exactly that deme-major,
    locus-minor, ascending-allele-id order) and on drawing each
    category's own count via `_inversion_binomial` rather than
    `rng.binomial` — the same primitive `drift`'s own unjitted path
    uses via `_multinomial_via_inversion_binomial`, inlined here into
    one JIT-compiled call instead of invoked once per pair. This is
    Stage F8's own canonical, cross-backend-unified draw
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4) — no longer bit-identical to `rng.multinomial` itself
    (`_multinomial_via_binomial`, above, still is, and is kept for that
    reason, but is no longer what `drift` actually calls).

    Args:
        rng: The run's explicitly threaded random generator.
        ns: One total count per pair, in visiting order.
        offsets: Length ``len(ns) + 1``; pair `i`'s own probabilities
            (and, on return, its own counts) occupy
            ``probabilities_flat[offsets[i]:offsets[i + 1]]``.
        probabilities_flat: Every pair's own row-stochastic probability
            vector, concatenated in the same order as `ns`.

    Returns:
        Integer counts, same flat layout as `probabilities_flat`, each
        pair's own slice summing to that pair's own `ns` entry.
    """

    # `_inversion_binomial`, as a nested closure, not a call to the
    # actual module-level function — see that function's own docstring
    # for the algorithm and why it exists. Nested, not called directly,
    # for the same reason `_multinomial_via_binomial`'s own
    # decomposition is inlined rather than called from here: Numba's
    # `nopython` mode cannot compile a call to a plain, undecorated
    # module-level Python function as an internal callee (confirmed
    # directly — attempting to call `_inversion_binomial` from here
    # raised `numba.core.errors.TypingError: Untyped global name`), and
    # decorating `_inversion_binomial` itself with `@numba.jit` at
    # module level would force every caller — including every
    # `jit=False` one — to pay `numba`'s own import cost, violating
    # this module's own "numba stays fully optional, imported only when
    # explicitly requested" invariant. A *nested* function, by
    # contrast, compiles fine — Numba treats it as part of the
    # enclosing function's own compiled body, not a separate global
    # callee — and keeps this duplication's own complexity out of
    # `_drift_counts_batched`'s own top-level body.
    def draw_one(n: int, p: float) -> int:
        if n <= 0 or p <= 0.0:
            return 0
        if p >= 1.0:
            return n
        u = rng.random()
        reflect = p > _REFLECT_THRESHOLD
        q = 1.0 - p if reflect else p
        mode = min(int((n + 1) * q), n)
        log_pmf_mode = (
            math.lgamma(n + 1.0)
            - math.lgamma(mode + 1.0)
            - math.lgamma(n - mode + 1.0)
            + mode * math.log(q)
            + (n - mode) * math.log1p(-q)
        )
        pmf_mode = math.exp(log_pmf_mode)
        low_pmf = [pmf_mode]
        current = pmf_mode
        for offset in range(mode, 0, -1):
            current = current * offset / (n - offset + 1) * (1.0 - q) / q
            low_pmf.append(current)
        low_pmf.reverse()
        cdf = 0.0
        for candidate in range(mode + 1):
            cdf += low_pmf[candidate]
            if cdf >= u:
                return n - candidate if reflect else candidate
        pmf = pmf_mode
        candidate = mode
        while cdf < u and candidate < n:
            candidate += 1
            pmf *= (n - candidate + 1) / candidate * q / (1.0 - q)
            cdf += pmf
        return n - candidate if reflect else candidate

    counts_flat = np.empty(probabilities_flat.shape[0], dtype=np.int64)
    pair_count = ns.shape[0]
    for pair_index in range(pair_count):
        start = offsets[pair_index]
        end = offsets[pair_index + 1]
        remaining_n = ns[pair_index]
        remaining_p = 1.0
        for position in range(start, end - 1):
            p = probabilities_flat[position]
            target_p = p / remaining_p if remaining_p > 0.0 else 0.0
            if target_p < 0.0:
                target_p = 0.0
            elif target_p > 1.0:
                target_p = 1.0
            drawn = draw_one(remaining_n, target_p)
            counts_flat[position] = drawn
            remaining_n -= drawn
            remaining_p -= p
        counts_flat[end - 1] = remaining_n
    return counts_flat


def _jit_drift_counts_batched(
    rng: np.random.Generator,
    ns: np.ndarray,
    offsets: np.ndarray,
    probabilities_flat: np.ndarray,
) -> np.ndarray:
    """`_drift_counts_batched`, JIT-compiled with `nogil=True`.

    This, not `_jit_multinomial_via_binomial` called once per pair, is
    what `drift`'s own `jit=True` path actually calls. It removes the
    naive per-pair version's own measured ~5x wall-clock *regression*
    (`spike/jit-per-deme-call-overhead`) — but, measured honestly, does
    not turn `drift` into a clear net win on its own; see
    `_drift_counts_batched`'s own docstring for the full, corrected
    measurement and why. Lazily imports and compiles `numba` exactly
    like `_jit_multinomial_via_binomial` does, and for the same reason
    (including `cache=True` -- see that function's own docstring for
    the measured cross-process benefit and the confirmed-safe
    read-only-install fallback).

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_DRIFT_COUNTS_BATCHED  # noqa: PLW0603
    if _JIT_DRIFT_COUNTS_BATCHED is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_DRIFT_COUNTS_BATCHED = numba.jit(nogil=True, cache=True)(
            _drift_counts_batched
        )
    return _JIT_DRIFT_COUNTS_BATCHED(rng, ns, offsets, probabilities_flat)


def _build_flat_drift_buffers(
    state: ModelState, sizes: tuple[int, ...]
) -> tuple[list[tuple[AlleleId, ...]], np.ndarray, np.ndarray, np.ndarray]:
    """Flatten every (deme, locus) pair's own allele ids/size/probabilities.

    Visits pairs in deme-major, locus-minor order, and — within each
    pair — categories in **ascending allele-id order**, not
    `frequency_map`'s own insertion order: the order `_drift_counts_
    batched` depends on for bit-identity, but also, independently, the
    order Backend V's own dense array representation always uses
    (`fim.model.vectorized`, indexed `0 .. capacity - 1` natively).
    `frequency_map`'s own insertion order tracks each allele's first
    appearance *in that specific deme*, which drifts out of numeric
    order once recurrence events happen — sorting here is what actually
    makes "canonical order" a well-defined, cross-backend-shared thing
    rather than an artifact of one backend's own internal bookkeeping
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §11, "what canonical draw order actually means once allele
    recurrence is in play"). Scoped narrowly and deliberately: this
    reorders only the ephemeral probability vector built here for the
    draw itself — `state`'s own `frequency_map` dicts, and everything
    else that reads them (`ModelState.to_rows`, persistence), are
    untouched.

    Returns:
        A tuple of: each pair's own allele ids, in ascending order (for
        unpacking counts back into frequency maps afterward), the
        per-pair `ns` array, the `offsets` array, and the concatenated
        `probabilities_flat` array — see `_drift_counts_batched`'s own
        docstring for what each of the last three actually holds.
    """
    allele_ids_per_pair: list[tuple[AlleleId, ...]] = []
    ns: list[int] = []
    probability_chunks: list[np.ndarray] = []
    for deme, size in zip(state.frequencies, sizes, strict=True):
        for frequency_map in deme:
            allele_ids = tuple(sorted(frequency_map))
            probabilities = np.fromiter(
                (frequency_map[allele_id] for allele_id in allele_ids),
                dtype=np.float64,
                count=len(allele_ids),
            )
            probabilities /= probabilities.sum()
            allele_ids_per_pair.append(allele_ids)
            ns.append(size)
            probability_chunks.append(probabilities)

    offsets = np.zeros(len(probability_chunks) + 1, dtype=np.int64)
    for index, chunk in enumerate(probability_chunks):
        offsets[index + 1] = offsets[index] + chunk.shape[0]
    probabilities_flat = (
        np.concatenate(probability_chunks)
        if probability_chunks
        else np.empty(0, dtype=np.float64)
    )
    return (
        allele_ids_per_pair,
        np.asarray(ns, dtype=np.int64),
        offsets,
        probabilities_flat,
    )


def drift(
    state: ModelState,
    population_size: PopulationSize,
    rng: np.random.Generator,
    *,
    jit: bool = False,
) -> ModelState:
    """Resample ``N`` gene copies per deme and locus.

    "Genetic drift" is the random change in allele frequencies from one
    generation to the next that happens purely because a real
    population is finite — even with no selection, migration, or
    mutation at all, a fair coin flipped 10 times does not always come
    up exactly 5 heads, and a deme's `N` gene copies are exactly that
    kind of finite, random draw from the previous generation's own
    frequencies. This function is what actually performs that draw: for
    every deme and locus, it treats the current frequency map as the
    probabilities of a multinomial draw of size `N` (multinomial being
    the many-outcomes generalization of the familiar two-outcome
    binomial coin flip), then converts the drawn integer counts back
    into frequencies — which, unlike migration's or mutation's smooth,
    continuous frequency changes, always land exactly on the ``1 / N``
    grid (a frequency of, say, `3/50`, never `3.2/50`), since they came
    from literally counting whole gene copies.

    **Not `rng.multinomial` itself, deliberately, as of Stage F8**
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4): both branches below decompose the multinomial draw into
    sequential conditional-binomial draws via `_inversion_binomial`
    (`_multinomial_via_inversion_binomial` for `jit=False`,
    `_drift_counts_batched` for `jit=True`), visiting categories in
    ascending allele-id order rather than `frequency_map`'s own
    insertion order — the one shared algorithm, in the one canonical
    order, this project's array-native Backend V (`fim.model.
    vectorized`) also uses, which is what makes cross-backend numerical
    agreement possible at all (`_inversion_binomial`'s own docstring
    has the full argument for why `rng.multinomial`/`rng.binomial`
    themselves cannot give this property). No longer bit-identical to
    `rng.multinomial`'s own output for the same seed — an accepted,
    deliberate cost, not an oversight (§5.4's own "the real cost, named
    plainly"; `_multinomial_via_binomial`, this module's own earlier,
    still-correct-and-tested building block, is what stayed
    bit-identical to `rng.multinomial`, and is kept for that reason,
    just no longer what this function calls).

    Args:
        state: Post-migration state (the parental pool).
        population_size: Shared or per-deme gene-copy count.
        rng: The run's explicitly threaded random generator.
        jit: When `True`, draw every (deme, locus) pair's own counts in
            one Numba-JIT-compiled, `nogil=True` call
            (`_jit_drift_counts_batched`) instead of one call per pair
            — bit-identical output either way (both branches now share
            the identical primitive and visiting order, not merely
            happen to agree — see `_drift_counts_batched`'s own
            docstring), and free of GIL-release concerns during the
            compiled call itself. Measured honestly, this is not yet a
            clear standalone win for `drift`: it fixes a real,
            separately measured regression an earlier per-pair-call
            version had, but the compiled draw was never `drift`'s own
            dominant cost at this project's reference scale — the
            Python-level marshaling to and from `ModelState`'s own
            sparse representation is, and `jit=True` does not remove
            that (see `_drift_counts_batched`'s own docstring for the
            full, measured picture). `False` (the default) is every
            prior release's own behavior *shape*, unchanged, though no
            longer its own exact numeric output (see above) — needs
            `numba` installed only when `True`.

    Returns:
        The next generation with frequencies on a ``1 / N`` grid.
    """
    sizes = _population_sizes(population_size, state.deme_count)
    if jit:
        allele_ids_per_pair, ns, offsets, probabilities_flat = (
            _build_flat_drift_buffers(state, sizes)
        )
        counts_flat = _jit_drift_counts_batched(rng, ns, offsets, probabilities_flat)
        demes: list[tuple[Mapping[AlleleId, float], ...]] = []
        pair_index = 0
        for deme, size in zip(state.frequencies, sizes, strict=True):
            locus_maps: list[Mapping[AlleleId, float]] = []
            for _frequency_map in deme:
                allele_ids = allele_ids_per_pair[pair_index]
                start, end = offsets[pair_index], offsets[pair_index + 1]
                counts = counts_flat[start:end]
                locus_maps.append(
                    {
                        allele_id: int(count) / size
                        for allele_id, count in zip(allele_ids, counts, strict=True)
                        if count
                    }
                )
                pair_index += 1
            demes.append(tuple(locus_maps))
    else:
        demes = []
        for deme, size in zip(state.frequencies, sizes, strict=True):
            locus_maps = []
            for frequency_map in deme:
                allele_ids = tuple(sorted(frequency_map))
                probabilities = np.fromiter(
                    (frequency_map[allele_id] for allele_id in allele_ids),
                    dtype=np.float64,
                    count=len(allele_ids),
                )
                probabilities /= probabilities.sum()
                counts = _multinomial_via_inversion_binomial(rng, size, probabilities)
                locus_maps.append(
                    {
                        allele_id: int(count) / size
                        for allele_id, count in zip(
                            allele_ids,
                            counts,
                            strict=True,
                        )
                        if count
                    }
                )
            demes.append(tuple(locus_maps))
    result = ModelState(
        loci=state.loci,
        frequencies=tuple(demes),
        generation=state.generation + 1,
    )
    result.validate_support(sizes)
    return result


def migrate(
    state: ModelState,
    m: Migration,
    population_size: PopulationSize | None = None,
    *,
    rng: np.random.Generator | None = None,
    jit: bool = False,
) -> ModelState:
    """Blend each deme with the current all-other-deme migrant pool.

    Every deme keeps a ``1 - rate`` share of its own current
    frequencies and mixes in a ``rate`` share of the "migrant pool" —
    a weighted average of every *other* deme's own frequencies, the
    weighting coming either from a flat symmetric rate (`m` as a plain
    number, applied identically between every pair of demes) or from a
    full custom weight matrix (`m` as a matrix, letting some pairs of
    demes exchange more migrants than others — see `fim.model.topology`
    for building one). This is the process that keeps demes from
    drifting apart in isolation: without any migration at all, each
    deme's own genetic drift (see `drift`, above) is independent, so
    over time they diverge; migration is the counteracting force that
    homogenizes them, and the balance between the two is exactly what
    the differentiation measures in `fim.statistics.differentiation`
    are designed to quantify.

    Args:
        state: Current generation.
        m: Symmetric migration rate or a complete row-stochastic matrix.
        population_size: Optional gene-copy counts used to size-weight pools.
        rng: Optional random generator selecting the opt-in stochastic
            migrant-count model (``SimulationParams.migrant_sampling ==
            "stochastic"``). ``None`` (the default) applies the migration
            rate as an exact fraction, unchanged from every prior release.
            When given, each deme's migrant *count* is instead drawn from
            ``Binomial(size, rate)`` each generation — mean ``size * rate``,
            matching the deterministic case in expectation, but varying
            generation to generation — while migrant *composition* stays
            the existing deterministic, weighted pool average. Requires
            ``population_size``.
        jit: When `True`, and only when eligible (`m` a plain scalar rate
            and `rng` is `None` — the default, deterministic "continuous"
            migration path), blend every locus's own dense (deme, allele)
            frequency block in one Numba-JIT-compiled, `nogil=True` call
            (`_migrate_symmetric_jit`) instead of the ordinary per-deme
            dict loop — bit-identical output either way (see
            `_migrate_symmetric_jit`'s own docstring for the argument),
            and free of GIL-release concerns during the compiled call
            itself. Silently ignored, not an error, for a full custom
            weight matrix or the opt-in stochastic-migrant-count model —
            unlike `VectorizedAdvancer`'s own hard config-scope
            `ValueError` (`fim.engine`), `jit` here is purely a
            same-output performance hint, exactly like `drift`'s own
            `jit` argument already is, not a behavioral mode switch, so
            an ineligible combination just keeps using the existing,
            always-correct dict-based path rather than failing. Needs
            `numba` installed only when both `True` and eligible.

    Returns:
        A state at the same generation containing post-migration frequencies.

    Raises:
        ValueError: If ``rng`` is given without ``population_size``; if a
            scalar ``m`` is a `bool`, non-finite, or outside ``[0, 1]``;
            or if a matrix ``m`` does not have exactly one row, and one
            entry per row, for every deme in ``state``.
    """
    if rng is not None and population_size is None:
        raise ValueError("migrate() requires population_size when rng is given")
    # Reachable directly through this public function even though
    # `SimulationParams.from_mapping` already rejects every one of these
    # shapes at config-load time — silently absorbed into a plausible-
    # looking wrong distribution otherwise, rather than raising, for a
    # caller (or a future backend) that builds `m` itself instead of
    # going through `SimulationParams` (this project's own multi-model
    # engine review, 2026-09-04, `FIM-23`/finding Kimi-FIM-23).
    if isinstance(m, int | float):
        if isinstance(m, bool) or not math.isfinite(m) or not 0.0 <= m <= 1.0:
            raise ValueError("migrate() requires m to be a number between 0 and 1")
    elif len(m) != state.deme_count:
        raise ValueError(
            f"migrate() requires one matrix row per deme ({state.deme_count}), "
            f"got {len(m)}"
        )
    else:
        for row_index, row in enumerate(m):
            if len(row) != state.deme_count:
                raise ValueError(
                    f"migrate() m[{row_index}] must have {state.deme_count} "
                    f"entries, got {len(row)}"
                )
    matrix_sizes: tuple[int, ...] | None = (
        _population_sizes(population_size, state.deme_count)
        if population_size is not None
        else None
    )
    if isinstance(m, int | float):
        symmetric_sizes = (
            matrix_sizes if matrix_sizes is not None else (1,) * state.deme_count
        )
        demes = _migrate_symmetric(state, float(m), symmetric_sizes, rng=rng, jit=jit)
    else:
        demes = _migrate_matrix(state, m, sizes=matrix_sizes, rng=rng)
    # `validate=False`: this is `step()`'s own intermediate state,
    # immediately consumed as `mutate`'s input, never returned to a
    # caller raw -- `ModelState.__post_init__`'s own docstring, and
    # `_normalize_frequency_map`'s, have the full argument for why
    # skipping the per-allele checks here (not the transform, which
    # still runs identically) cannot change what gets computed
    # (`20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
    # §6.3 item 3).
    return ModelState(
        loci=state.loci,
        frequencies=demes,
        generation=state.generation,
        validate=False,
    )


_JIT_MUTATE_EVENT_COUNTS_BATCHED: (
    Callable[[np.random.Generator, np.ndarray, np.ndarray], np.ndarray] | None
) = None


def _mutate_event_counts_batched(
    rng: np.random.Generator,
    ns: np.ndarray,
    ps: np.ndarray,
) -> np.ndarray:
    """Draw one `Binomial(n, p)` mutant count per gene-copy group, in one pass.

    A "group" is every gene copy of one allele in one `(deme, locus)`
    pair: `mutate` mutates each gene copy independently with
    probability `mu`, so the number of an allele's own `n` copies that
    mutate is `Binomial(n, mu)` — one draw per group, no
    conditional-binomial decomposition across categories (unlike
    `drift`'s own multinomial, `_drift_counts_batched`), batched the
    same way: pay the Python/Numba call-boundary crossing once per call
    instead of once per group.

    **Nested closure, not a call to the module-level `_inversion_
    binomial`, for the identical reason `_drift_counts_batched`
    already duplicates it** — see that function's own docstring:
    Numba's `nopython` mode cannot compile a call to a plain,
    undecorated module-level function as an internal callee, and
    decorating `_inversion_binomial` itself would force every caller,
    including every `jit=False` one, to pay `numba`'s own import cost.
    `draw_one` below is `_inversion_binomial`, line for line, so a
    group drawn here and a group drawn by `_inversion_binomial` from the
    same stream position give the same count.

    **Bit-identity depends on visiting groups in exactly the order
    `mutate`'s own unjitted loop does** — deme-major, then locus, then
    ascending allele id: every group's own draw consumes real,
    sequential positions in the shared `rng`'s own bit stream, so an
    out-of-order batched pass would desync every later draw. A group
    with `n == 0` or `p == 0` consumes no draw at all, which is what
    lets `fim.model.vectorized.mutate_vectorized` pass a deme's whole
    dense count row through the same kernel.

    Args:
        rng: The run's explicitly threaded random generator.
        ns: One gene-copy count per group, in visiting order.
        ps: One mutation probability per group, the same order — the
            group's own locus's own rate.

    Returns:
        One mutant count per group, the same order as `ns`/`ps`.
    """

    def draw_one(n: int, p: float) -> int:
        if n <= 0 or p <= 0.0:
            return 0
        if p >= 1.0:
            return n
        u = rng.random()
        reflect = p > _REFLECT_THRESHOLD
        q = 1.0 - p if reflect else p
        mode = min(int((n + 1) * q), n)
        log_pmf_mode = (
            math.lgamma(n + 1.0)
            - math.lgamma(mode + 1.0)
            - math.lgamma(n - mode + 1.0)
            + mode * math.log(q)
            + (n - mode) * math.log1p(-q)
        )
        pmf_mode = math.exp(log_pmf_mode)
        low_pmf = [pmf_mode]
        current = pmf_mode
        for offset in range(mode, 0, -1):
            current = current * offset / (n - offset + 1) * (1.0 - q) / q
            low_pmf.append(current)
        low_pmf.reverse()
        cdf = 0.0
        for candidate in range(mode + 1):
            cdf += low_pmf[candidate]
            if cdf >= u:
                return n - candidate if reflect else candidate
        pmf = pmf_mode
        candidate = mode
        while cdf < u and candidate < n:
            candidate += 1
            pmf *= (n - candidate + 1) / candidate * q / (1.0 - q)
            cdf += pmf
        return n - candidate if reflect else candidate

    counts = np.empty(ns.shape[0], dtype=np.int64)
    for index in range(ns.shape[0]):
        counts[index] = draw_one(ns[index], ps[index])
    return counts


def _jit_mutate_event_counts_batched(
    rng: np.random.Generator,
    ns: np.ndarray,
    ps: np.ndarray,
) -> np.ndarray:
    """`_mutate_event_counts_batched`, JIT-compiled with `nogil=True`.

    Lazily imports and compiles `numba` exactly like `_jit_drift_
    counts_batched` does, and for the same reason (including
    `cache=True` -- see `_jit_multinomial_via_binomial`'s own docstring
    for the measured cross-process benefit and the confirmed-safe
    read-only-install fallback). `numba` is an optional dependency
    (``pip install fim[jit]``) — only a caller that explicitly requests
    `mutate(..., jit=True)` ever pays its import/compilation cost or
    needs it installed at all.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MUTATE_EVENT_COUNTS_BATCHED  # noqa: PLW0603
    if _JIT_MUTATE_EVENT_COUNTS_BATCHED is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MUTATE_EVENT_COUNTS_BATCHED = numba.jit(nogil=True, cache=True)(
            _mutate_event_counts_batched
        )
    return _JIT_MUTATE_EVENT_COUNTS_BATCHED(rng, ns, ps)


def _gene_copy_counts(
    frequency_map: Mapping[AlleleId, float], size: int
) -> tuple[tuple[AlleleId, ...], np.ndarray]:
    """Return one `(deme, locus)` pair's own whole gene-copy counts.

    `mutate` acts on gene copies, so its input must be a state whose
    frequencies are whole copies out of `size` — what `drift` produces
    (`count / size`). Rounding `frequency * size` recovers each count
    exactly; anything farther than `_GRID_TOLERANCE` from a whole number,
    or counts that do not add up to `size`, means the caller passed a
    continuous (pre-drift) state, which has no gene copies to mutate.

    Args:
        frequency_map: One pair's own frequencies, on the `1 / size`
            grid.
        size: That pair's own deme's gene-copy count.

    Returns:
        The pair's own allele ids in ascending order (the canonical
        visiting order every backend shares — see `drift`'s own
        docstring) and their gene-copy counts, the same order.

    Raises:
        ValueError: If the frequencies are not whole gene copies out of
            `size`.
    """
    allele_ids = tuple(sorted(frequency_map))
    scaled = np.fromiter(
        (frequency_map[allele_id] * size for allele_id in allele_ids),
        dtype=np.float64,
        count=len(allele_ids),
    )
    counts = np.rint(scaled).astype(np.int64)
    if np.any(np.abs(scaled - counts) > _GRID_TOLERANCE) or int(counts.sum()) != size:
        raise ValueError(
            "mutate needs whole gene copies: every frequency must be a "
            f"multiple of 1/{size} (a state produced by drift)"
        )
    return allele_ids, counts


def _mint_infinite_allele_ids(
    mutated: dict[AlleleId, int],
    registry: AlleleRegistry,
    event_count: int,
    minted_ids: np.ndarray | None,
    minted_offset: int,
) -> int:
    """Give `event_count` mutant gene copies one fresh identity each, in place.

    Reads this pair's own contiguous slice of `minted_ids` — a single
    whole-call reservation `mutate`'s own `jit` docstring explains is
    safe to draw up front, since minting consumes no `rng` draw at all —
    when one was already reserved (`jit=True` under the infinite-alleles
    model); mints one at a time via `registry.next_id()` otherwise. Both
    hand out the same identities in the same order.

    Args:
        mutated: This pair's own working gene-copy counts, given one new
            entry (count `1`) per minted identity.
        registry: Global mutant-allele allocator, used only when
            `minted_ids` is `None`.
        event_count: How many mutant copies need an identity.
        minted_ids: The whole-call reservation, or `None` if nothing was
            reserved up front.
        minted_offset: Where this pair's own slice of `minted_ids`
            starts.

    Returns:
        `minted_offset`, advanced past this pair's own slice — pass
        straight back in as the next pair's own `minted_offset`.
    """
    if minted_ids is None:
        for _event in range(event_count):
            mutated[registry.next_id()] = 1
        return minted_offset
    for minted_id in minted_ids[minted_offset : minted_offset + event_count]:
        mutated[AlleleId(int(minted_id))] = 1
    return minted_offset + event_count


def _attribute_finite_allele_targets(
    mutated: dict[AlleleId, int],
    finite_alleles: FiniteAlleleRegistry,
    locus: LocusSpec,
    allele_ids: tuple[AlleleId, ...],
    source_counts: np.ndarray,
    rng: np.random.Generator,
    jit: bool,
) -> None:
    """Add every mutant copy's own finite-alleles target into `mutated`.

    `source_counts[i]` mutant copies came from `allele_ids[i]`; each
    gets its own target, drawn in ascending source order, one copy at a
    time. Two paths, chosen per pair, per this call — never a fixed
    choice for the whole run, since different loci can carry different
    capacities in the same `FiniteAlleleRegistry`
    (`test_finite_allele_registry_dispatches_by_locus_id`):

    - **Eligible** (`jit` and this locus's own `capacity <= _MAX_JIT_
      FINITE_ALLELE_CAPACITY`): every target this pair needs is drawn in
      one Numba-JIT-compiled, `nogil=True` call
      (`_jit_mutate_targets_batched`) against this locus's own
      `FiniteAlleleSpace`, exported to dense arrays
      (`FiniteAlleleSpace.to_arrays`) and written back afterward
      (`FiniteAlleleSpace.restore_from_arrays`) so the next pair at this
      same locus sees the updated minted state (one `FiniteAlleleSpace`
      is shared across every deme at a given locus).
    - **Ineligible** (`jit` is `False`, or this locus's own `capacity`
      exceeds the bound): one `FiniteAlleleSpace.mutate_target` call per
      copy — unbounded-capacity-safe, silently, not an error, matching
      the "performance hint, not a mode switch" contract.
      `_MAX_JIT_FINITE_ALLELE_CAPACITY`'s own module-level comment has
      the reasoning for where that bound sits.

    Both paths draw the same targets from the same stream positions
    (`test_attribute_finite_allele_targets_batches_when_eligible_or_
    falls_back`).

    Args:
        mutated: This pair's own working gene-copy counts, each target
            adding one copy (a target can coincide with another copy's
            target or with an allele already present, so counts
            accumulate rather than overwrite).
        finite_alleles: This run's own per-locus finite-allele-space
            registry.
        locus: This pair's own locus.
        allele_ids: This pair's own source alleles, ascending order —
            `source_counts`'s own index order.
        source_counts: How many mutant copies came from each of
            `allele_ids`, the same order.
        rng: The run's explicitly threaded random generator.
        jit: Whether the caller requested the batched path at all.
    """
    space: FiniteAlleleSpace = finite_alleles.space_for(locus.locus_id)
    if jit and space.capacity <= _MAX_JIT_FINITE_ALLELE_CAPACITY:
        sources = np.repeat(
            np.asarray(allele_ids, dtype=np.int64),
            np.asarray(source_counts, dtype=np.int64),
        )
        minted_mask, minted_list, minted_count, next_unminted = space.to_arrays()
        targets, minted_mask, minted_list, minted_count, next_unminted = (
            _jit_mutate_targets_batched(
                rng,
                sources,
                space.capacity,
                minted_mask,
                minted_list,
                minted_count,
                next_unminted,
            )
        )
        space.restore_from_arrays(minted_mask, minted_list, minted_count, next_unminted)
        for target in targets:
            allele_id = AlleleId(int(target))
            mutated[allele_id] = mutated.get(allele_id, 0) + 1
        return
    for source_id, source_count in zip(allele_ids, source_counts, strict=True):
        for _event in range(int(source_count)):
            target = finite_alleles.mutate_target(locus.locus_id, source_id, rng)
            mutated[target] = mutated.get(target, 0) + 1


def _draw_mutant_counts(
    rng: np.random.Generator,
    copy_counts: np.ndarray,
    rate: float,
    jit: bool,
) -> np.ndarray:
    """Draw `Binomial(n_i, rate)` mutant copies for each allele of one pair.

    One `_inversion_binomial` draw per allele, in the given (ascending
    allele-id) order; with `jit`, the same draws through the compiled
    `_jit_mutate_event_counts_batched` kernel in one call — identical
    counts from identical stream positions either way.

    Args:
        rng: The run's explicitly threaded random generator.
        copy_counts: Each allele's own gene-copy count in this pair.
        rate: This pair's own locus's own mutation probability.
        jit: Whether to use the compiled kernel.

    Returns:
        One mutant count per allele, each in `[0, copy_counts[i]]`.
    """
    if jit:
        return _jit_mutate_event_counts_batched(
            rng, copy_counts, np.full(copy_counts.shape[0], rate, dtype=np.float64)
        )
    return np.fromiter(
        (_inversion_binomial(rng, int(count), rate) for count in copy_counts),
        dtype=np.int64,
        count=copy_counts.shape[0],
    )


def mutate(
    state: ModelState,
    mu: MutationRate,
    population_size: PopulationSize,
    registry: AlleleRegistry,
    rng: np.random.Generator,
    *,
    finite_alleles: FiniteAlleleRegistry | None = None,
    jit: bool = False,
) -> ModelState:
    """Mutate every gene copy independently with probability `mu`.

    A "mutation" is one gene copy switching to a different allele than
    the one it inherited — biologically, a copying error when the copy
    was made. This is the textbook Wright-Fisher mutation step: `state`
    holds the `N` gene copies `drift` has just drawn for the new
    generation, and each one of them, independently, mutates with
    probability `mu`. For an allele carried by `n` copies, the number
    that mutate is therefore `Binomial(n, mu)`, drawn allele by allele
    in ascending allele-id order (the canonical order every backend
    shares — see `drift`'s own docstring). Each mutant copy then
    becomes:

    - under the default infinite-alleles model, a brand-new allele that
      has never existed anywhere in the run (a fresh identity from
      `fim.model.allele.AlleleRegistry`, carried by that one copy);
    - under the opt-in finite-alleles (K-allele) model, one of the other
      `K - 1` states of its locus, uniformly at random — possibly one
      already present (see `fim.model.allele.FiniteAlleleSpace`).

    Because every copy mutates on its own, two gene copies of the next
    generation are still identical only if they descend from identical
    parental copies *and* neither mutated: the identity recursion is
    `F' = (1 - mu)^2 [1/N + (1 - 1/N) F]` for two distinct copies of one
    panmictic population (`fim.statistics.identity_recursion` has the
    island-model form). Earlier releases instead drew one
    `Binomial(N, mu)` event count per deme and scaled every allele's
    frequency down by the same factor before drift, which adds a small
    `O(mu / N)` excess identity per generation; that is not the textbook
    model, and this replaced it.

    The input must be on the `1 / N` grid (whole gene copies, as `drift`
    leaves it); the output is too. `step` runs this after `drift`.

    **Not `rng.binomial` itself, deliberately, as of Stage F8**
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4) — every count draws via `_inversion_binomial`, a fixed number
    of uniforms per draw, the primitive every backend shares.

    Args:
        state: The post-drift state of the new generation.
        mu: Per-copy mutation probability — shared by every locus, or one
            rate per locus (`SimulationParams.mutation_rates`; typically
            derived from a per-base rate and each locus's own length via
            `SimulationParams.from_mapping`'s `mu_b`).
        population_size: Shared or per-deme gene-copy count.
        registry: Global mutant-allele allocator for the run — used under
            the default infinite-alleles model, where every mutant copy
            receives a fresh global identity.
        rng: The run's explicitly threaded random generator.
        finite_alleles: Optional per-locus finite-allele-space registry
            selecting the opt-in finite-alleles (K-allele) model instead
            (`SimulationParams.mutation_model == "finite_alleles"`). A
            mutant copy's target then depends on its own source allele —
            never itself, but possibly a state already present elsewhere
            in the run.
        jit: When `True`, the same draws run through compiled kernels.
            Bit-identical output either way.

            Under the default infinite-alleles model
            (`finite_alleles is None`): every `(deme, locus, allele)`
            mutant count of the whole call is drawn in one
            Numba-JIT-compiled, `nogil=True` call
            (`_jit_mutate_event_counts_batched`), in the same
            deme-major, locus, ascending-allele order the unjitted loop
            uses, and every fresh identity the call needs is reserved in
            one `AlleleRegistry.next_k_ids` call — safe because minting
            consumes no `rng` draw at all, so nothing else draws in
            between.

            Under the opt-in finite-alleles model: target selection draws
            from `rng` between one pair's own counts and the next pair's,
            so counts are drawn one pair at a time (compiled, one call
            per pair), followed by that pair's own targets — batched per
            pair through `_jit_mutate_targets_batched` when the locus's
            own `FiniteAlleleSpace.capacity` is at most
            `_MAX_JIT_FINITE_ALLELE_CAPACITY`, one
            `FiniteAlleleSpace.mutate_target` call per copy above it
            (`_attribute_finite_allele_targets`). Needs `numba`
            installed only when `True`.

    Returns:
        A post-mutation state at the same generation, on the `1 / N`
        grid.

    Raises:
        ValueError: If a frequency in `state` is not a whole number of
            gene copies out of its deme's `N` (`_gene_copy_counts`).
    """
    if isinstance(mu, float) and mu == 0.0:
        return state
    mutation_rates = mu if isinstance(mu, tuple) else (mu,) * state.locus_count
    sizes = _population_sizes(population_size, state.deme_count)

    # Every pair's own gene-copy counts, deme-major, locus-minor,
    # alleles ascending: the visiting order of every draw below.
    pairs = [
        (_gene_copy_counts(frequency_map, size), size, locus, rate)
        for deme, size in zip(state.frequencies, sizes, strict=True)
        for frequency_map, locus, rate in zip(
            deme, state.loci, mutation_rates, strict=True
        )
    ]

    # `jit` under infinite alleles: no draw happens between one pair's own
    # counts and the next pair's, so every count of the call is drawn up
    # front in one compiled call, in the identical order the loop below
    # would draw them, and every fresh identity is reserved at once.
    mutant_counts_flat: np.ndarray | None = None
    minted_ids: np.ndarray | None = None
    minted_offset = 0
    if jit and finite_alleles is None:
        copy_counts_flat = np.concatenate(
            [counts for (_ids, counts), _size, _locus, _rate in pairs]
        )
        rates_flat = np.concatenate(
            [
                np.full(counts.shape[0], rate, dtype=np.float64)
                for (_ids, counts), _size, _locus, rate in pairs
            ]
        )
        mutant_counts_flat = _jit_mutate_event_counts_batched(
            rng, copy_counts_flat, rates_flat
        )
        minted_ids = registry.next_k_ids(int(mutant_counts_flat.sum()))

    locus_maps: list[Mapping[AlleleId, float]] = []
    group_offset = 0
    for (allele_ids, copy_counts), size, locus, rate in pairs:
        if mutant_counts_flat is not None:
            mutant_counts = mutant_counts_flat[
                group_offset : group_offset + copy_counts.shape[0]
            ]
            group_offset += copy_counts.shape[0]
        else:
            mutant_counts = _draw_mutant_counts(rng, copy_counts, rate, jit)
        event_count = int(mutant_counts.sum())
        if event_count == 0:
            locus_maps.append(
                {
                    allele_id: int(count) / size
                    for allele_id, count in zip(allele_ids, copy_counts, strict=True)
                }
            )
            continue
        # The copies that did not mutate keep their allele; each mutant
        # copy is added back under its new identity.
        mutated: dict[AlleleId, int] = {
            allele_id: int(kept)
            for allele_id, kept in zip(
                allele_ids, copy_counts - mutant_counts, strict=True
            )
            if kept
        }
        if finite_alleles is None:
            minted_offset = _mint_infinite_allele_ids(
                mutated, registry, event_count, minted_ids, minted_offset
            )
        else:
            _attribute_finite_allele_targets(
                mutated,
                finite_alleles,
                locus,
                allele_ids,
                mutant_counts,
                rng,
                jit,
            )
        locus_maps.append(
            {allele_id: mutated[allele_id] / size for allele_id in sorted(mutated)}
        )

    locus_count = state.locus_count
    demes = tuple(
        tuple(locus_maps[index : index + locus_count])
        for index in range(0, len(locus_maps), locus_count)
    )
    # `validate=False`: every map above is whole gene copies over `size`
    # by construction — positive counts summing to `size` — the same
    # representation `drift` returns.
    result = ModelState(
        loci=state.loci,
        frequencies=demes,
        generation=state.generation,
        validate=False,
    )
    result.validate_support(sizes)
    return result


def step(
    state: ModelState,
    params: SimulationParams,
    registry: AlleleRegistry,
    rng: np.random.Generator,
    *,
    finite_alleles: FiniteAlleleRegistry | None = None,
    jit: bool = False,
) -> ModelState:
    """Advance one generation: migration, then drift, then mutation.

    This is the one function `fim.engine`'s run loop actually calls,
    once per generation. It is the textbook Wright-Fisher island model:

    1. `migrate` forms each deme's parental pool — its own frequencies
       blended with the migrant pool.
    2. `drift` draws the new generation's `N` gene copies from that pool
       (multinomial sampling, with replacement).
    3. `mutate` then mutates each of those new copies independently
       with probability `mu`.

    Mutation acts on the sampled copies, not on the parental
    frequencies before sampling, so two copies of the new generation
    are identical only when their parental copies were and neither of
    them mutated — the `(1 - mu)^2` factor of the textbook identity
    recursions (`fim.statistics.identity_recursion`). Releases before
    this one ran migrate, mutate, drift, with a mutation step that
    scaled allele frequencies down before sampling; see `mutate`'s own
    docstring for why that was not the textbook model.

    Args:
        state: Current model state.
        params: Validated run parameters.
        registry: Global mutant-allele allocator.
        rng: The run's explicitly threaded random generator.
        finite_alleles: Optional per-locus finite-allele-space registry,
            built once per run by the caller and threaded through every
            generation — required when
            ``params.mutation_model == "finite_alleles"``, unused
            otherwise.
        jit: Passed through to `drift`'s own `jit` argument (see its
            docstring) and, since `20260901-claude-sonnet-5-fim-engine-
            backend-factory-design.md` §10 item 10e's own stages 1-3,
            to `migrate`'s and `mutate`'s own `jit` arguments too —
            silently a no-op in `migrate` for a full custom weight
            matrix or stochastic migrant sampling (see `migrate`'s own
            docstring), real for the default scalar-rate, deterministic
            case; real in `mutate` under both mutation models — the
            mutant-count draws compiled (all of them in one call under
            the default infinite-alleles model, one call per pair under
            the opt-in finite-alleles model) and target selection batched
            per pair below a capacity bound (`_MAX_JIT_FINITE_ALLELE_
            CAPACITY`) — see `mutate`'s own `jit` docstring.

    Returns:
        The next generation.
    """
    # Only the opt-in "stochastic" mode passes rng into migrate(); the
    # default "continuous" mode passes None, so migrate() consumes zero
    # rng draws and every existing reproducible run is bit-for-bit
    # unaffected by this feature's existence.
    migration_rng = rng if params.migrant_sampling == "stochastic" else None
    migrated = migrate(state, params.m, params.gene_copies, rng=migration_rng, jit=jit)
    drifted = drift(migrated, params.gene_copies, rng, jit=jit)
    return mutate(
        drifted,
        params.mu,
        params.gene_copies,
        registry,
        rng,
        finite_alleles=finite_alleles,
        jit=jit,
    )


def _allele_union(frequency_maps: Sequence[FrequencyMap]) -> tuple[AlleleId, ...]:
    """Return all present IDs in deterministic first-observed order."""
    observed: dict[AlleleId, None] = {}
    for frequency_map in frequency_maps:
        for allele_id in frequency_map:
            observed.setdefault(allele_id, None)
    return tuple(observed)


def _migrant_fraction(rng: np.random.Generator, size: int, rate: float) -> float:
    """Draw one deme's own stochastic migrant fraction for this generation.

    ``Binomial(size, rate) / size``, via `_inversion_binomial` — the same
    fixed-draw-count algorithm every other stochastic draw in this module
    already uses, not `rng.binomial` directly (this project's own
    multi-model engine review, 2026-09-04, `FIM-11`/finding M-04/finding
    P2-3: `rng.binomial` is the one call in this module not migrated to
    `_inversion_binomial` when the rest of it was rebuilt around that
    algorithm specifically for its fixed, `n`/`p`-independent stream
    consumption — without it, `migrant_sampling="stochastic"` cannot be
    cross-backend bit-matched the way every other stochastic draw here
    already is).

    Called once per (deme, generation), never once per (deme, locus): a
    deme's own individual gene copies carry every locus together, so how
    many of a deme's ``size`` gene copies migrated this generation is one
    shared fact about the deme, not something a caller should draw
    independently for each locus (`FIM-13`/finding Kimi-FIM-13 — `_blend`,
    below, used to draw its own migrant count internally, once per call,
    and every caller called it once per locus).
    """
    return _inversion_binomial(rng, size, rate) / size


def _blend(
    local: Mapping[AlleleId, float],
    pool: Mapping[AlleleId, float],
    migrant_fraction: float,
) -> Mapping[AlleleId, float]:
    """Blend one deme/locus using an already-drawn migrant fraction.

    The migrant *count* varies generation to generation with mean
    ``size * rate`` while the migrant *composition* stays exactly
    ``pool`` — the same deterministic weighted average the continuous path
    always used. Drift, not this step, remains the pipeline's only operator
    that resamples every gene copy; this only blends by however much of a
    deme's own gene copies `migrant_fraction` already says came from the
    pool this generation.

    Args:
        local: This deme's own pre-migration frequency map.
        pool: The deterministic migrant-pool frequency map.
        migrant_fraction: This deme's own migrant fraction for this
            generation, already drawn once via `_migrant_fraction` and
            shared across every locus this same call blends — never drawn
            here, so one call site cannot accidentally draw it once per
            locus again (`FIM-13`).

    Returns:
        A normalized post-migration frequency map.
    """
    blended = {
        allele_id: (1.0 - migrant_fraction) * local.get(allele_id, 0.0)
        + migrant_fraction * pool.get(allele_id, 0.0)
        for allele_id in _allele_union((local, pool))
    }
    return _normalize(blended)


def _migrate_matrix(
    state: ModelState,
    matrix: tuple[tuple[float, ...], ...],
    *,
    sizes: tuple[int, ...] | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[tuple[Mapping[AlleleId, float], ...], ...]:
    """Apply a complete source-weight matrix."""
    result: list[tuple[Mapping[AlleleId, float], ...]] = []
    # Every deme's own frequency map at a given locus is a fact about
    # `state`, not about which destination is currently being blended —
    # built once per locus, up front, rather than rebuilt from scratch
    # for every (destination, locus) pair below (`FIM-36`, Phase 7 item
    # 5, `20260904-claude-sonnet-5-fim-engine-review-remediations.md`).
    # `state.frequency_map` is a pure, deterministic read of `state`, so
    # caching its result across destinations changes nothing it returns.
    sources_by_locus = tuple(
        tuple(
            state.frequency_map(source, locus_index)
            for source in range(state.deme_count)
        )
        for locus_index in range(state.locus_count)
    )
    for destination, weights in enumerate(matrix):
        # Stochastic path: the row's own diagonal entry is this
        # destination's self-retention weight; everything else is the
        # migrant pool, exactly as in the deterministic case below, but
        # the count drawn from that pool this generation is now random
        # rather than exactly ``migrant_weight * size``. Drawn once per
        # destination, before the locus loop, not once per locus inside
        # it — a deme's own gene copies carry every locus together, so
        # this generation's own migrant count is one fact about the
        # deme, shared by every locus's own blend below (`FIM-13`).
        migrant_weight = 1.0 - weights[destination]
        destination_migrant_fraction: float | None = None
        if rng is not None and migrant_weight > 0.0:
            if sizes is None:
                raise ValueError("migrate() requires population_size when rng is given")
            destination_migrant_fraction = _migrant_fraction(
                rng, sizes[destination], migrant_weight
            )
        locus_maps: list[Mapping[AlleleId, float]] = []
        for locus_index in range(state.locus_count):
            sources = sources_by_locus[locus_index]
            if rng is None:
                allele_ids = _allele_union(sources)
                blended = {
                    allele_id: math.fsum(
                        weight * source.get(allele_id, 0.0)
                        for weight, source in zip(weights, sources, strict=True)
                    )
                    for allele_id in allele_ids
                }
                locus_maps.append(_normalize(blended))
                continue
            local = sources[destination]
            if migrant_weight <= 0.0:
                locus_maps.append(dict(local))
                continue
            if destination_migrant_fraction is None:
                # Unreachable: `migrant_weight > 0.0` here is exactly the
                # condition already checked, above the locus loop, before
                # `destination_migrant_fraction` was drawn — guarded
                # explicitly rather than assumed, the same discipline
                # `_require_lane_result` (`fim.engine`) already uses.
                raise RuntimeError(
                    "migrant fraction was not drawn for a migrating destination"
                )
            pool = _row_pool(weights, sources, destination, migrant_weight)
            locus_maps.append(_blend(local, pool, destination_migrant_fraction))
        result.append(tuple(locus_maps))
    return tuple(result)


_JIT_MIGRATE_SYMMETRIC_BLEND: (
    Callable[
        [float, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
        np.ndarray,
    ]
    | None
) = None


def _build_migrate_symmetric_buffers(
    state: ModelState,
) -> tuple[list[tuple[AlleleId, ...]], np.ndarray, np.ndarray, np.ndarray]:
    """Flatten every locus's own dense (deme, allele) frequency block.

    Each locus has its own segregating-allele count (`_allele_union`'s
    own union across every deme at that locus, this generation), so — the
    same ragged-array-as-flat-buffer-plus-offsets layout
    `_build_flat_drift_buffers` already uses one axis over (`_migrate_
    symmetric_blend_batched`'s own docstring has the consumer side) —
    every locus's own `(deme_count, width)` block is flattened row-major
    (deme-major within the block, matching the deme-ascending
    accumulation order `_migrate_symmetric_blend_batched` depends on for
    bit-identity) and concatenated into one buffer, with `offsets`
    marking where each locus's own block starts. `widths` gives Numba's
    own compiled loop the one extra integer it needs per locus that a
    ``block.shape`` lookup would otherwise supply.

    Allele indices within a locus are assigned in ascending allele-id
    order — not required for correctness here (see `_migrate_symmetric_
    blend_batched`'s own docstring: no computation in this stage depends
    on allele visiting order, only deme visiting order does), chosen
    only for consistency with the project's own established canonical
    order (`_build_flat_drift_buffers`'s own docstring; Backend V's dense
    arrays are always indexed the same way).

    Returns:
        Each locus's own allele ids in ascending order (for unpacking
        the blended result back into frequency maps afterward), the
        per-locus `widths` array, the `offsets` array (length
        `state.locus_count + 1`), and the concatenated `frequencies_flat`
        array — see `_migrate_symmetric_blend_batched`'s own docstring
        for how the last three are consumed.
    """
    allele_ids_per_locus: list[tuple[AlleleId, ...]] = []
    widths: list[int] = []
    blocks: list[np.ndarray] = []
    for locus_index in range(state.locus_count):
        frequency_maps = tuple(
            state.frequency_map(deme_index, locus_index)
            for deme_index in range(state.deme_count)
        )
        allele_ids = tuple(sorted(_allele_union(frequency_maps)))
        width = len(allele_ids)
        block = np.zeros((state.deme_count, width), dtype=np.float64)
        for deme_index, frequency_map in enumerate(frequency_maps):
            for allele_index, allele_id in enumerate(allele_ids):
                value = frequency_map.get(allele_id)
                if value is not None:
                    block[deme_index, allele_index] = value
        allele_ids_per_locus.append(allele_ids)
        widths.append(width)
        blocks.append(block.reshape(-1))
    offsets = np.zeros(len(blocks) + 1, dtype=np.int64)
    for index, block in enumerate(blocks):
        offsets[index + 1] = offsets[index] + block.shape[0]
    frequencies_flat = (
        np.concatenate(blocks) if blocks else np.empty(0, dtype=np.float64)
    )
    return (
        allele_ids_per_locus,
        np.asarray(widths, dtype=np.int64),
        offsets,
        frequencies_flat,
    )


def _migrate_symmetric_blend_batched(
    rate: float,
    sizes: np.ndarray,
    other_weights: np.ndarray,
    widths: np.ndarray,
    offsets: np.ndarray,
    frequencies_flat: np.ndarray,
) -> np.ndarray:
    """Blend every locus's own dense (deme, allele) frequency block in one pass.

    The array-shaped, two-phase restatement of `_migrate_symmetric`'s
    own `rng is None` loop above, deliberately **not** a
    `sizes @ frequencies` matrix product: BLAS's own internal reduction
    order for a matmul does not, in general, match a plain left-to-right
    running sum, so a matmul-based version would trade this stage's own
    bit-identity goal for a speed difference nothing here actually
    needs (`migrate`'s own `jit` docstring; unlike Backend V's own
    `migrate_vectorized`, §5.1 of the vector design, this stage does not
    need BLAS's own throughput — it needs a same-output, GIL-releasing
    compiled replacement for an existing dict loop). Phase one, per
    locus, per allele: accumulate ``mass[a] = sum_i sizes[i] *
    frequencies[i, a]`` by walking every deme in ascending order,
    ``i = 0 .. deme_count - 1`` — the identical order, and the identical
    per-term floating-point operation, `_migrate_symmetric`'s own
    dict-based accumulation already uses (`mass.get(allele_id, 0.0) +
    size * frequency`, visited deme-major). The two loops are not
    literally the same code, but are bit-identical by construction:
    IEEE 754 addition of an exact `+0.0` never changes a finite running
    total's own bit pattern, so this loop's explicit ``sizes[i] * 0.0``
    contribution from a deme where allele `a` is absent (this function's
    own dense input always holds an exact `0.0` there, never skips a
    cell) reproduces precisely the dict-based loop's own implicit
    "skip this deme's contribution entirely" behavior — same running
    total, same rounding, at every step. Phase two, per destination deme
    `i`, per allele `a`: the same three-line formula `_migrate_
    symmetric`'s own dict-based branch already uses (``pool = (mass[a] -
    sizes[i] * local) / other_weights[i]``, ``blended = (1 - rate) *
    local + rate * pool``), evaluated once per `(i, a)` cell — no
    reduction at all here, so no ordering question to begin with. The
    caller (`_migrate_symmetric_jit`) still finishes every locus/
    destination's own result through the unmodified `_normalize` — whose
    own `math.fsum`-based sum is exact and therefore order-independent —
    so the two phases above are the *only* places this whole path could
    diverge from the dict-based one, and both are shown bit-identical
    above, not merely close.

    Args:
        rate: The scalar migration rate (``_migrate_symmetric``'s own
            already-validated ``rate != 0.0`` — the caller never invokes
            this for the ``rate == 0.0`` short-circuit).
        sizes: Every deme's own gene-copy count, as `float64` (exact for
            any population size a real run uses — see
            `_migrate_symmetric_jit`'s own docstring).
        other_weights: ``total_size - sizes[i]`` per deme, precomputed
            once by the caller.
        widths: Each locus's own segregating-allele count, in the same
            order `offsets` uses.
        offsets: Length ``widths.shape[0] + 1``; locus `j`'s own
            ``(deme_count, widths[j])`` block, flattened row-major,
            occupies ``frequencies_flat[offsets[j]:offsets[j + 1]]``.
        frequencies_flat: Every locus's own dense frequency block,
            concatenated in the same order as `widths`/`offsets` — see
            `_build_migrate_symmetric_buffers`'s own docstring for the
            exact layout.

    Returns:
        The blended result, same flat layout as `frequencies_flat`
        — not yet normalized (`_migrate_symmetric_jit` finishes that
        through `_normalize`, exactly as the dict-based path does).
    """
    blended_flat = np.empty_like(frequencies_flat)
    deme_count = sizes.shape[0]
    locus_count = widths.shape[0]
    for locus_index in range(locus_count):
        width = widths[locus_index]
        base = offsets[locus_index]
        mass = np.zeros(width, dtype=np.float64)
        for allele_index in range(width):
            total = 0.0
            for deme_index in range(deme_count):
                total += (
                    sizes[deme_index]
                    * frequencies_flat[base + deme_index * width + allele_index]
                )
            mass[allele_index] = total
        for deme_index in range(deme_count):
            weight = other_weights[deme_index]
            size = sizes[deme_index]
            for allele_index in range(width):
                position = base + deme_index * width + allele_index
                local = frequencies_flat[position]
                pool = (mass[allele_index] - size * local) / weight
                blended_flat[position] = (1.0 - rate) * local + rate * pool
    return blended_flat


def _jit_migrate_symmetric_blend(
    rate: float,
    sizes: np.ndarray,
    other_weights: np.ndarray,
    widths: np.ndarray,
    offsets: np.ndarray,
    frequencies_flat: np.ndarray,
) -> np.ndarray:
    """`_migrate_symmetric_blend_batched`, JIT-compiled with `nogil=True`.

    No internal call to another project-defined function happens inside
    `_migrate_symmetric_blend_batched` (unlike `_drift_counts_batched`,
    which needs `_inversion_binomial`'s own algorithm and so duplicates
    it as a nested closure — see that function's own docstring for why),
    so this compiles directly, the same lazy, cached, `nogil=True`,
    `cache=True` pattern as `_jit_multinomial_via_binomial` (see that
    function's own docstring for the measured cross-process benefit and
    the confirmed-safe read-only-install fallback). `numba` is an
    optional dependency (``pip install fim[jit]``), imported here and
    nowhere else in this function — only a caller that explicitly
    requests `migrate(..., jit=True)` on an eligible (scalar-rate,
    deterministic) call ever pays its import/compilation cost or needs
    it installed at all. Compiled once, on first call, and cached at
    module level.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MIGRATE_SYMMETRIC_BLEND  # noqa: PLW0603
    if _JIT_MIGRATE_SYMMETRIC_BLEND is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MIGRATE_SYMMETRIC_BLEND = numba.jit(nogil=True, cache=True)(
            _migrate_symmetric_blend_batched
        )
    return _JIT_MIGRATE_SYMMETRIC_BLEND(
        rate, sizes, other_weights, widths, offsets, frequencies_flat
    )


def _migrate_symmetric_jit(
    state: ModelState, rate: float, sizes: tuple[int, ...]
) -> tuple[tuple[Mapping[AlleleId, float], ...], ...]:
    """`_migrate_symmetric`'s own ``rng is None`` branch, `nogil`-JIT-compiled.

    Bit-identical to that branch for the same state, not merely
    close — the one genuine reduction in the whole computation (the
    per-locus, per-allele size-weighted `mass` sum) is reproduced term
    for term, in the identical deme-ascending order, and every other
    step is either a single per-cell formula (no reduction, hence no
    ordering question) or `_normalize`'s own already-exact,
    order-independent `math.fsum` — see `_migrate_symmetric_blend_
    batched`'s own docstring for the full argument. Called only from
    `_migrate_symmetric`'s own ``rng is None and jit`` branch — the
    ``rate == 0.0`` short-circuit above it already returns before this
    function would ever be reached with nothing to blend.

    Args:
        state: Current generation.
        rate: The scalar migration rate.
        sizes: Every deme's own gene-copy count.

    Returns:
        The same nested-tuple-of-frequency-maps shape
        `_migrate_symmetric`'s own dict-based branch returns.
    """
    sizes_array = np.asarray(sizes, dtype=np.float64)
    total_size = float(sum(sizes))
    other_weights = total_size - sizes_array
    allele_ids_per_locus, widths, offsets, frequencies_flat = (
        _build_migrate_symmetric_buffers(state)
    )
    blended_flat = _jit_migrate_symmetric_blend(
        rate, sizes_array, other_weights, widths, offsets, frequencies_flat
    )
    result: list[tuple[Mapping[AlleleId, float], ...]] = []
    for destination in range(state.deme_count):
        locus_maps: list[Mapping[AlleleId, float]] = []
        for locus_index in range(state.locus_count):
            allele_ids = allele_ids_per_locus[locus_index]
            width = int(widths[locus_index])
            base = int(offsets[locus_index])
            start = base + destination * width
            row = blended_flat[start : start + width]
            blended = {
                allele_id: float(value)
                for allele_id, value in zip(allele_ids, row, strict=True)
            }
            locus_maps.append(_normalize(blended))
        result.append(tuple(locus_maps))
    return tuple(result)


def _migrate_symmetric(
    state: ModelState,
    rate: float,
    sizes: tuple[int, ...],
    *,
    rng: np.random.Generator | None = None,
    jit: bool = False,
) -> tuple[tuple[Mapping[AlleleId, float], ...], ...]:
    """Apply scalar all-other-deme migration in ``O(d * A)`` time.

    The all-other-deme migrant pool for a destination equals the global
    size-weighted allele mass with the destination's own contribution removed:
    ``pool(a) = (sum_k size_k f_k(a) - size_dest f_dest(a))
    / (sum_k size_k - size_dest)``. Precomputing the per-locus global mass once
    turns the naive ``O(d^2 * A)`` recomputation (each destination re-summing
    every other deme) into a single pass plus one subtraction per destination,
    which matters at large ``d`` such as the 100-deme Dear-Nolan scenario.

    With ``rng is None`` (the default), ``rate`` is applied to every deme
    as an exact fraction — the original, still-default behavior, computed
    exactly as before. With an ``rng``, each destination's migrant count is
    instead drawn from ``Binomial(size, rate)`` (see ``_blend``); the two
    branches are kept fully separate below so the default path's
    floating-point arithmetic is untouched by the new one.

    Args:
        jit: When `True` and `rng is None`, dispatch to
            `_migrate_symmetric_jit` instead of the dict-based loop
            below — see `migrate`'s own `jit` argument. Ignored (not an
            error) when `rng` is given; the stochastic path stays
            dict-based, out of this stage's own scope
            (`20260901-claude-sonnet-5-fim-engine-backend-factory-
            design.md` §10 item 10e's own phased plan, stage 1).
    """
    # `rate == 0.0` (nothing to blend) and `state.deme_count == 1` (no
    # "other" deme to blend *with* — `other_weight` below would be
    # exactly `0.0`, raising `ZeroDivisionError`) both make migration a
    # well-defined no-op, the identical identity this function already
    # returns for the first case; `deme_count == 1` added here for the
    # second, previously unguarded one — this project's own multi-model
    # engine review, 2026-09-04, found the three backends disagreeing on
    # how this case fails (`ZeroDivisionError` here, silent `NaN` in
    # `migrate_vectorized_symmetric`) rather than agreeing this is a
    # no-op (`FIM-02`/finding C-06/finding P2-2). Unreachable via a
    # validated `SimulationParams` (`d >= 2`), but `migrate`/`ModelState`
    # are public.
    if rate == 0.0 or state.deme_count == 1:
        return tuple(
            tuple(dict(frequency_map) for frequency_map in deme)
            for deme in state.frequencies
        )
    if rng is None and jit:
        return _migrate_symmetric_jit(state, rate, sizes)
    total_size = float(sum(sizes))
    global_mass: list[dict[AlleleId, float]] = []
    for locus_index in range(state.locus_count):
        mass: dict[AlleleId, float] = {}
        for deme_index in range(state.deme_count):
            size = sizes[deme_index]
            frequency_map = state.frequency_map(deme_index, locus_index)
            for allele_id, frequency in frequency_map.items():
                mass[allele_id] = mass.get(allele_id, 0.0) + size * frequency
        global_mass.append(mass)
    result: list[tuple[Mapping[AlleleId, float], ...]] = []
    for destination in range(state.deme_count):
        destination_size = sizes[destination]
        other_weight = total_size - destination_size
        # Drawn once per destination, before the locus loop, not once per
        # locus inside it — the identical `FIM-13` reasoning `_migrate_
        # matrix`'s own stochastic branch already applies. `rate == 0.0`/
        # `deme_count == 1` already returned above, so every destination
        # here genuinely migrates; no `migrant_weight > 0.0` gate needed
        # the way `_migrate_matrix`'s own per-row weight requires.
        migrant_fraction = (
            _migrant_fraction(rng, destination_size, rate) if rng is not None else None
        )
        locus_maps: list[Mapping[AlleleId, float]] = []
        for locus_index in range(state.locus_count):
            local = state.frequency_map(destination, locus_index)
            if rng is None:
                blended: dict[AlleleId, float] = {}
                for allele_id, total in global_mass[locus_index].items():
                    local_frequency = local.get(allele_id, 0.0)
                    pool_frequency = _symmetric_pool_mass(
                        total, destination_size, local_frequency, other_weight
                    )
                    blended[allele_id] = (
                        1.0 - rate
                    ) * local_frequency + rate * pool_frequency
                locus_maps.append(_normalize(blended))
            else:
                if migrant_fraction is None:
                    # Unreachable: this branch only runs when `rng is not
                    # None`, the exact condition `migrant_fraction` was
                    # just computed under, above — guarded explicitly
                    # rather than assumed, matching `_migrate_matrix`'s
                    # own identical guard.
                    raise RuntimeError(
                        "migrant fraction was not drawn for a stochastic destination"
                    )
                pool = {
                    allele_id: _symmetric_pool_mass(
                        total, destination_size, local.get(allele_id, 0.0), other_weight
                    )
                    for allele_id, total in global_mass[locus_index].items()
                }
                locus_maps.append(_blend(local, pool, migrant_fraction))
        result.append(tuple(locus_maps))
    return tuple(result)


def _symmetric_pool_mass(
    total: float,
    destination_size: int,
    local_frequency: float,
    other_weight: float,
) -> float:
    """Return one allele's own migrant-pool frequency, clamped against cancellation.

    ``(total - destination_size * local_frequency) / other_weight``
    removes one deme's own contribution from the global size-weighted
    mass to leave every *other* deme's own share — exact in real
    arithmetic, but floating-point catastrophic cancellation can round a
    rare allele's own tiny, genuinely positive migrant mass to a tiny
    *negative* number instead of exactly `0.0` for a heavily skewed
    allele (this project's own multi-model engine review, 2026-09-04,
    `FIM-14`/finding Kimi-FIM-14). Clamped here rather than left to
    propagate into a caller's own weighted blend as a small, physically
    meaningless negative frequency.
    """
    return max(0.0, (total - destination_size * local_frequency) / other_weight)


def _normalize(
    frequency_map: Mapping[AlleleId, float],
) -> dict[AlleleId, float]:
    """Normalize positive floating-point mass to exactly one."""
    positive = {
        allele_id: frequency
        for allele_id, frequency in frequency_map.items()
        if frequency > 0.0
    }
    total = math.fsum(positive.values())
    if total <= 0.0:
        raise ValueError("operator produced no positive allele mass")
    return {allele_id: frequency / total for allele_id, frequency in positive.items()}


def _population_sizes(
    value: PopulationSize,
    deme_count: int,
) -> tuple[int, ...]:
    """Expand and validate a population-size argument."""
    sizes = (value,) * deme_count if isinstance(value, int) else tuple(value)
    if len(sizes) != deme_count:
        raise ValueError("N must contain one gene-copy count per deme")
    if any(isinstance(size, bool) or size < 1 for size in sizes):
        raise ValueError("all N values must be positive integers")
    return sizes


def _row_pool(
    weights: tuple[float, ...],
    sources: tuple[Mapping[AlleleId, float], ...],
    destination: int,
    migrant_weight: float,
) -> Mapping[AlleleId, float]:
    """Return one migration-matrix row's non-self weighted-average pool.

    Args:
        weights: The destination's full source-weight row.
        sources: Every deme's current frequency map for one locus.
        destination: The destination's index within ``weights``/``sources``.
        migrant_weight: ``1 - weights[destination]``, the row's total
            non-self weight, used to renormalize the excluded-self average.

    Returns:
        The migrant pool's frequency map; mass sums to 1.
    """
    others = tuple(
        (weight, source)
        for index, (weight, source) in enumerate(zip(weights, sources, strict=True))
        if index != destination
    )
    allele_ids = _allele_union(tuple(source for _weight, source in others))
    return {
        allele_id: math.fsum(
            weight * source.get(allele_id, 0.0) for weight, source in others
        )
        / migrant_weight
        for allele_id in allele_ids
    }
