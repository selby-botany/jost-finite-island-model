"""Bounded-K (finite-alleles), array-native migrate/mutate/drift.

Scope, deliberately: the finite-alleles mutation model only (bounded
`K = finite_allele_capacity(length)` per locus — see `fim.model.locus`).
Infinite alleles is out of scope here, matching the vector design's own
"V2 before V1" recommendation: under finite alleles, every allele
identity at a locus is already a fixed integer in `0 .. K - 1` for the
whole run, so a dense `(d, K)` array can be allocated once and reused
generation after generation with no reindexing at all — the genuinely
hard problem (an unbounded, per-generation-ragged identity space) that
infinite alleles would require simply does not arise here. Deterministic
("continuous") migration only — `SimulationParams.migrant_sampling ==
"stochastic"` is not supported by this path yet, the same kind of
documented, deliberate scope boundary `fim.model.operators.step`'s own
`jit` argument already draws around `migrate`/`mutate`.

`ModelState`'s own public shape is untouched by anything in this module:
`VectorizedState`, below, is a compute-only, internal representation.
`migrate_vectorized`, `mutate_vectorized`, and `drift_vectorized` all
operate on this same dense representation in sequence within one
generation, with no `ModelState` round-trip between them — the first
real test of `20260901-claude-sonnet-5-fim-engine-backend-factory-
design.md` §11's "fusing migrate -> mutate -> drift across stage
boundaries" open question. `fim.engine.VectorizedAdvancer`, the one
caller, originally converted to and from `ModelState` once *every*
generation regardless (`ReplicaLane.state` was `ModelState`-typed
across the whole batch driving loop) — a real, separately measured
secondary cost this module's own within-generation fusion did nothing
about, since it sat entirely outside `step_vectorized`. Fixed since:
`fim.engine.ReplicaLane.vectorized_state` now caches the live
`VectorizedState` itself across generations, and `VectorizedAdvancer`
reads convergence statistics straight off it
(`_convergence_values_vectorized`), so `vectorized_state_to_model_
state` (below) is only ever called once per lane, when it actually
stops — not once per generation.

That within-generation fusion alone was not enough to make the whole
pipeline competitive with the dict-based backends once actually
benchmarked end to end, though: the real, measured dominant cost turned
out to be neither the random draw nor that per-generation round-trip,
but a *third* thing — `mutate_vectorized`/`drift_vectorized`'s own
shared multinomial-decomposition helper issuing one array-valued
`rng.binomial` call per allele-id category, `capacity - 1` times per
invocation, each call vectorized only across `d` demes at a time. With
`d` typically far smaller than `capacity`, per-call NumPy dispatch
overhead dominated the actual compute — profiled directly, not assumed,
the same "tested, not just reasoned about" discipline this project's
own JIT work already established for `drift`'s dict-based path. Fixed
by JIT-compiling that helper into one call covering the entire `(d,
capacity)` grid at once (`_jit_multinomial_rows_batched`, below) — the
exact same "batch every unit of work into one call, don't call once per
small unit of it" lesson `fim.model.operators`'s own `_drift_counts_
batched` already had to learn, rediscovered here independently on a
second function before being generalized.

Statistical, not bit-identical, parity with the dict-based operators in
`fim.model.operators` was this module's own original correctness bar
throughout (vector design §6) — every function below names, in its own
docstring, exactly where and why its own random-draw sequence diverges
from the dict-based original. **`migrate_vectorized`, `drift_vectorized`,
and `mutate_vectorized` all clear a materially higher bar, as of Stage
F8** (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
§5.4): each now draws via the identical `_inversion_binomial`-based
algorithm, in the identical order, that `fim.model.operators`'s own
dict-based path uses (`migrate_vectorized` was always exact — no
randomness at all in "continuous" mode) — checked directly, given an
identical starting state and an identically seeded `rng`, across many
seeds and a deliberately non-saturated capacity
(`test_drift_vectorized_matches_dict_based_drift_exactly*`,
`test_mutate_vectorized_matches_dict_based_mutate_exactly`,
`test/model/test_vectorized.py`).

**That per-operator proof alone was not enough to make a full,
multi-generation `fim.engine` run agree with the dict-based backends
either — a second, separate, more serious bug sat between "every
operator is exact in isolation" and "a real run produces the same
trajectory," found only by actually running one end to end, not
inferred from the operator-level tests.** `build_vectorized_state`,
below, used to re-derive each locus's own finite-alleles minted
bookkeeping (`minted_mask`/`minted_list`/`minted_count`/`next_
unminted`) from scratch every time it was called, using only whichever
allele ids were currently present in the `ModelState` handed to it —
and `fim.engine.VectorizedAdvancer` calls it once *every generation*
(`ReplicaLane`'s own docstring). Any allele minted and then driven to
extinction — including within the very generation it was minted in,
the ordinary fate of a brand-new mutant sitting at `1/N` frequency, not
a rare one — was silently forgotten, letting this backend re-mint an
identity `FiniteAlleleSpace`'s own dict-based bookkeeping had already
permanently retired, and systematically undercounting `minted_count`
(hence biasing `_mutate_targets_batched`'s own `recurrence_probability`
low). Measured directly: one real generation of a real run left the
dict-based path's own `next_unminted` at `12` while re-deriving it from
that generation's own output alone gave `2`. Fixed by carrying that
bookkeeping forward across generations instead of re-deriving it
(`build_vectorized_state`'s own `previous_locus_states` argument has
the full mechanism, still used for a lane's first generation);
`fim.engine.ReplicaLane.vectorized_state` is the persistence point —
every generation after the first reuses that cached `VectorizedState`
directly, via `step_vectorized`, so `build_vectorized_state` is not
even called again for the rest of that lane's own run.

With that fixed, a full **single-locus** run *is* bit-identical to
`LinealBackend` when migration is off
(`test_generational_vector_backend_matches_lineal_exactly_without_
migration`, `test/engine/test_engine.py`) — but not in general with
migration active, and not at all with **two or more loci**, migration
on or off: `step_vectorized` fuses `migrate`/`mutate`/`drift` per
locus, one whole locus's own dense `(deme, capacity)` array per call,
while `operators.step` runs each stage across every tracked locus
first — `mutate`/`drift`'s own dict-based loops are deme-major,
locus-minor, so the two draw from the shared RNG stream in a genuinely
different order the instant a run tracks more than one locus, migration
active or not. Reconciling the two would mean flattening deme and
locus together inside the batched RNG kernels themselves (drawing
deme-major across every locus, one flat index space, in place of one
call per locus) — a substantially larger change to this module's own
one-locus-per-array design, for a benefit (cross-backend bit-identity)
with no scientific value beyond test convenience: each backend's own
per-locus output is already independently correct regardless of draw
order (this project's own multi-model engine review, 2026-09-04,
`FIM-09`/finding C-01/finding P1-1). A genuine multi-locus config
instead gets the same *statistical* parity guarantee the
migration-active single-locus case already has, below
(`test_generational_vector_matches_lineal_statistically_multi_locus`,
`test/engine/test_engine.py`). With
migration active (any locus count): `migrate_vectorized`'s own dense
matmul and `fim.model.operators.migrate`'s own dict-based blend are
two different, both fully deterministic, floating-point reduction
orders for the identical computation (BLAS's own summation order is
not obligated to match a hand-written sequential one), and that
sub-ULP disagreement occasionally sits close enough to a discrete
draw's own decision boundary to flip it — measured directly, 23 of 30
seeds with migration active diverged from `LinealBackend` within the
first three generations. This is the honest limit `20260901-...-
design.md` §5.4 names explicitly ("agreeing to within floating-point
tolerance... not full bit-identity end to end") and not eliminable
without giving up the vectorized/BLAS performance this backend exists
for. What actually matters, and what makes the divergence acceptable
rather than a defect, is that it carries no directional bias — checked
directly, not assumed, by comparing each backend's own mean `D`/`G_ST`
across many independently seeded replicates
(`test_generational_vector_backend_matches_lineal_statistically`): a
small sample can show a borderline gap, but it narrows back to noise
as the sample grows, which is what an unbiased alternate realization
looks like and a real bias would not.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import _inversion_binomial
from fim.model.state import ModelState

_JIT_MUTATE_TARGETS_BATCHED: (
    Callable[
        [np.random.Generator, np.ndarray, int, np.ndarray, np.ndarray, int, int],
        tuple[np.ndarray, np.ndarray, np.ndarray, int, int],
    ]
    | None
) = None

_JIT_MULTINOMIAL_ROWS_BATCHED: (
    Callable[[np.random.Generator, np.ndarray, np.ndarray, np.ndarray], np.ndarray]
    | None
) = None

# Mirrors `fim.model.operators._REFLECT_THRESHOLD` — kept as its own
# copy here rather than imported, matching the nested-closure
# duplication `_multinomial_rows_batched`'s own inline comment explains.
_REFLECT_THRESHOLD = 0.5


@dataclass(slots=True)
class VectorizedLocusState:
    """One locus's own dense working state, mid-batch.

    Mirrors `fim.model.allele.FiniteAlleleSpace`'s own bookkeeping
    (`_minted`/`_minted_set`/`_next_unminted`) in array form instead of a
    Python list/set, so it can be threaded through a JIT-compiled target-
    selection call (`_jit_mutate_targets_batched`) — this state is
    entirely independent of any real `FiniteAlleleSpace` instance;
    `build_vectorized_state`, below, reconstructs it directly from a
    `ModelState`'s own generation-zero allele ids, the same way
    `FiniteAlleleSpace.__init__` does.
    """

    frequencies: np.ndarray  # (d, capacity) float64, each row sums to 1.0
    capacity: int
    minted_mask: np.ndarray  # (capacity,) bool — has this state ever appeared
    minted_list: np.ndarray  # (capacity,) int64 — minted ids, mint order
    minted_count: int
    next_unminted: int


@dataclass(slots=True)
class VectorizedState:
    """A whole state's own dense working representation, one locus at a time."""

    loci: tuple[LocusSpec, ...]
    locus_states: tuple[VectorizedLocusState, ...]
    generation: int


def build_vectorized_state(
    state: ModelState,
    *,
    previous_locus_states: tuple[VectorizedLocusState, ...] | None = None,
) -> VectorizedState:
    """Build a `VectorizedState` from a real `ModelState`.

    The one place this module's own array representation is built from
    (or, via `vectorized_state_to_model_state`, converted back to)
    `ModelState`'s own sparse shape — everything between the two stays
    array-native. Every locus's own `capacity` is fixed for the run
    (`finite_allele_capacity`); the initial minted set (`previous_
    locus_states` not given) is exactly the allele ids already present
    at generation zero, mirroring `fim.engine._build_finite_allele_
    spaces`'s own construction.

    Args:
        state: The generation to build a dense array view of. Only
            frequencies are read from this — the minted bookkeeping
            below is not re-derived from it once `previous_locus_
            states` is given.
        previous_locus_states: This lane's own `VectorizedLocusState`
            sequence from the *previous* generation's own `step_
            vectorized` output, one per locus, if this is not the
            first generation. When given, `minted_mask`/`minted_list`/
            `minted_count`/`next_unminted` are carried forward from it
            directly (copied, not aliased) rather than re-derived from
            `state`'s own currently-present allele ids.

            This parameter exists to fix a real, confirmed correctness
            bug: re-deriving "which states have ever been minted"
            from `state` alone — the shape every caller used before
            this parameter existed — silently forgets any allele that
            was minted and then drifted to extinction, including
            *within the same generation it was minted in* (a fresh
            mutant at `1/N` frequency is routinely lost to the very
            next drift step). `FiniteAlleleSpace`'s own dict-based
            bookkeeping never forgets a minted identity, no matter how
            long ago it went extinct — measured directly, one real
            generation of a real run left the dict-based path's own
            `next_unminted` at `12` while re-deriving it from that same
            generation's own output alone gave `2`, since three of
            those twelve ids had been minted and gone extinct within
            that one generation alone. Without carrying the bookkeeping
            forward, `fim.engine.VectorizedAdvancer` (the one caller
            that runs more than a single generation) would re-mint
            already-retired identities every time this happened, and
            `minted_count` would run systematically undercounted,
            biasing `_mutate_targets_batched`'s own `recurrence_
            probability` low — not a rare edge case, since "a new
            low-frequency mutant does not survive its own first drift
            step" is the normal outcome, not a corner one. Found via a
            direct, multi-generation `LinealBackend`-vs-`GenerationalBackend
            (VectorizedAdvancer())` probe on a real config: the two
            backends' own trajectories matched exactly for generation 0
            and 1, then diverged starting at generation 2 — exactly the
            generation after the first such within-generation
            extinction, and not before.

    Raises:
        ValueError: If any allele id already present is outside
            `0 .. capacity - 1` — the same bounds `FiniteAlleleSpace`
            itself enforces at construction. Also if `previous_locus_
            states` is given with a different length or a different
            `capacity` per locus than `state.loci` implies — a caller
            passing state from a different run or a different locus
            configuration is a real bug, not a case to paper over.
    """
    if previous_locus_states is not None and len(previous_locus_states) != len(
        state.loci
    ):
        raise ValueError(
            f"previous_locus_states has {len(previous_locus_states)} entries, "
            f"expected one per locus ({len(state.loci)})"
        )
    locus_states = []
    for locus_index, locus in enumerate(state.loci):
        capacity = finite_allele_capacity(locus.length)
        deme_count = state.deme_count
        frequencies = np.zeros((deme_count, capacity), dtype=np.float64)
        minted_ids: set[int] = set()
        for deme_index, deme in enumerate(state.frequencies):
            for allele_id, frequency in deme[locus_index].items():
                if not (0 <= int(allele_id) < capacity):
                    raise ValueError(
                        f"allele id {int(allele_id)} outside 0..{capacity - 1} "
                        f"at locus {locus.locus_id}"
                    )
                frequencies[deme_index, int(allele_id)] = frequency
                minted_ids.add(int(allele_id))
        if previous_locus_states is not None:
            previous = previous_locus_states[locus_index]
            if previous.capacity != capacity:
                raise ValueError(
                    f"previous_locus_states[{locus_index}] has capacity "
                    f"{previous.capacity}, expected {capacity}"
                )
            # Every allele id currently present must already be known to
            # the carried-forward bookkeeping -- if one isn't, the
            # bookkeeping and the state have drifted apart (a caller
            # bug, e.g. mismatched lanes), not a case to silently patch
            # over by re-deriving from `state` after all.
            missing = minted_ids - {
                int(a) for a in previous.minted_list[: previous.minted_count]
            }
            if missing:
                raise ValueError(
                    f"previous_locus_states[{locus_index}] does not know "
                    f"about allele id(s) {sorted(missing)} present in state"
                )
            minted_mask = previous.minted_mask.copy()
            minted_list = previous.minted_list.copy()
            minted_count = previous.minted_count
            next_unminted = previous.next_unminted
        else:
            sorted_minted = sorted(minted_ids)
            minted_mask = np.zeros(capacity, dtype=np.bool_)
            minted_mask[sorted_minted] = True
            minted_list = np.zeros(capacity, dtype=np.int64)
            minted_list[: len(sorted_minted)] = sorted_minted
            minted_count = len(sorted_minted)
            next_unminted = 0
        locus_states.append(
            VectorizedLocusState(
                frequencies=frequencies,
                capacity=capacity,
                minted_mask=minted_mask,
                minted_list=minted_list,
                minted_count=minted_count,
                next_unminted=next_unminted,
            )
        )
    return VectorizedState(
        loci=state.loci,
        locus_states=tuple(locus_states),
        generation=state.generation,
    )


def vectorized_state_to_model_state(state: VectorizedState) -> ModelState:
    """Convert back to `ModelState`'s own public, sparse shape.

    `migrate_vectorized`/`mutate_vectorized`/`drift_vectorized`
    themselves never call this — the fusion within one generation this
    module's own docstring describes holds regardless of how often a
    caller converts back. `fim.engine.VectorizedAdvancer`, the one real
    caller, calls this exactly once per lane, only when that lane's own
    monitor reports it has stopped (`ReplicaLane.vectorized_state`
    carries the live array state across every generation in between) —
    not once per generation, which this module's own docstring records
    as a real, measured cost this function used to be paid for
    needlessly, on every tick, for a run that might last hundreds of
    them.

    Restricts each row to `np.flatnonzero(row)` rather than
    `enumerate`-ing the full, mostly-zero `capacity`-wide row in plain
    Python — `vectorized_state_to_rows`, below, already used this same
    shortcut; this function did not, until caught by a sweep for the
    same "dense array walked element-by-element in Python" mismatch
    `mutate_vectorized`'s own renormalization step had (that function's
    own inline comment has the measured cost). Even though this
    function is only called once per lane rather than once per
    generation, walking every one of `capacity` slots in a Python loop
    to test `if frequency` on each is the identical waste at a smaller
    multiplier, not a different problem.
    """
    deme_count = state.locus_states[0].frequencies.shape[0] if state.locus_states else 0
    demes = []
    for deme_index in range(deme_count):
        locus_maps = []
        for locus_state in state.locus_states:
            row = locus_state.frequencies[deme_index]
            locus_maps.append(
                {
                    AlleleId(int(allele_id)): float(row[allele_id])
                    for allele_id in np.flatnonzero(row)
                }
            )
        demes.append(tuple(locus_maps))
    return ModelState(
        loci=state.loci,
        frequencies=tuple(demes),
        generation=state.generation,
    )


def vectorized_state_to_rows(
    state: VectorizedState, run_id: str
) -> list[dict[str, int | float | str]]:
    """Serialize directly to the public trajectory row schema.

    The array-native counterpart to `ModelState.to_rows` — deliberately
    bypasses `vectorized_state_to_model_state` (no sparse dict-of-dicts
    construction just to immediately flatten it back into rows), the
    same "stay array-native as long as possible" principle this whole
    module is built around. Row shape, field order, and the same
    one-based `deme`/`locus_id` numbering match `ModelState.to_rows`
    exactly, so a caller (a `TrajectoryStore`) cannot tell which path
    produced a given row.
    """
    if not run_id:
        raise ValueError("run_id must not be empty")
    rows: list[dict[str, int | float | str]] = []
    deme_count = state.locus_states[0].frequencies.shape[0] if state.locus_states else 0
    for deme_index in range(deme_count):
        for locus, locus_state in zip(state.loci, state.locus_states, strict=True):
            row = locus_state.frequencies[deme_index]
            nonzero = np.flatnonzero(row)
            rows.extend(
                {
                    "run_id": run_id,
                    "generation": state.generation,
                    "deme": deme_index + 1,
                    "locus_id": locus.locus_id,
                    "allele_id": int(allele_id),
                    "frequency": float(row[allele_id]),
                }
                for allele_id in nonzero
            )
    return rows


def _present_only_row_sums(probabilities: np.ndarray) -> np.ndarray:
    """Return each row's own present-only sum, matching dict's own `mutate`/`drift`.

    **Corrected 2026-09-05** (this project's own multi-model engine
    review, 2026-09-04, `FIM-12`/Kimi's own finding of that number,
    confirmed live rather than only reasoned about): `_multinomial_
    rows_batched`'s own inline comment already explains why summing a
    zero-padded, `capacity`-wide row does not equal summing just the
    present values — NumPy's reduction is not associative, and padding
    zeros shift which nonzero terms get grouped together first. The fix
    already in place for that (a hand-rolled *sequential* `+=` loop over
    the present entries, inside the JIT kernel) rested on an assumption
    that turned out to be false: NumPy's own `ndarray.sum()` — what the
    dict-based path actually calls, on its own short, present-values-
    only array — is sequential only below 8 elements; from 8 up it
    switches to pairwise summation, which does not, in general, produce
    the same final bit as a strictly sequential accumulation of the
    identical values in the identical order. Every existing exact-match
    test stayed below that threshold (at most 6 present alleles), so
    this second, deeper gap stayed invisible until `FIM-46`'s own fix
    (uniform-random, not deterministically sequential, minting) made an
    8-present-allele deme newly reachable in short, already-existing
    tests — confirmed live by a direct trace: `LinealBackend`/
    `GenerationalBackend(VectorizedAdvancer())` agreed exactly through
    generation 2, then diverged in exactly one deme at generation 3,
    isolated down to one allele's own identity differing (`11` vs. `14`,
    both at the correct `0.025` frequency) the instant that deme reached
    8 present alleles.

    The only way to reproduce NumPy's own pairwise result exactly is to
    build the identical short, present-only array dict's own `mutate`/
    `drift` build (via `sorted(frequency_map)`, ascending allele-id
    order — the same order boolean indexing below preserves) and call
    real `ndarray.sum()` on it, outside the JIT boundary: `numba`'s own
    `.sum()`, even called from inside a JIT-compiled function, reduces
    sequentially, not pairwise, so computing this same value *inside*
    `_multinomial_rows_batched` could never close this gap no matter how
    it was written there.

    Args:
        probabilities: `(deme_count, capacity)`, each row dense over the
            full capacity, present alleles nonzero, absent ones exactly
            `0.0`.

    Returns:
        `(deme_count,)` float64 — row `i`'s own present-only sum, bit-
        identical to what `probabilities[i][probabilities[i] > 0].sum()`
        (equivalently, what dict's own `sorted(frequency_map)`-ordered
        `probabilities.sum()`) computes for the same underlying values.
    """
    return np.array(
        [row[row > 0.0].sum() for row in probabilities],
        dtype=np.float64,
    )


def _multinomial_rows_batched(
    rng: np.random.Generator,
    sizes: np.ndarray,
    probabilities: np.ndarray,
    present_sums: np.ndarray,
) -> np.ndarray:
    """Draw one multinomial sample per row, each row its own `p` vector.

    NumPy has no batched multinomial with a different `pvals` per row —
    only a shared one across `size` independent draws (`drift`'s own
    `fim.model.operators` docstring covers this same gap for the
    per-pair, dict-based case). This reproduces the same distribution,
    per row, via the multinomial-as-sequential-conditional-binomial
    identity, scalar-at-a-time across both the deme axis and the
    category axis — deliberately, not one array-valued `rng.binomial`
    call per category the way an earlier version of this function did.

    **Draws via the same mode-anchored inversion-binomial algorithm as
    `fim.model.operators._inversion_binomial`, not `rng.binomial`, as
    of Stage F8**
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4) — reimplemented here as a nested closure (`draw_one`, below)
    rather than calling that function directly: Numba's `nopython`
    mode cannot compile a call to a plain, undecorated, cross-module
    Python function as an internal callee (confirmed directly), and
    decorating `_inversion_binomial` itself with `@numba.jit` would
    force every caller, including every `jit=False` one anywhere in
    `fim.model.operators`, to pay `numba`'s own import cost — the exact
    "numba stays fully optional" invariant that module's own docstring
    already establishes. `fim.model.operators.drift`'s own dict-based
    path uses the identical algorithm, visited in the identical order
    that path's own `sorted(frequency_map)` produces: this function's
    own `column` loop already runs `0 .. capacity - 1` in ascending
    order, which *is* ascending allele-id order natively, no reindexing
    needed on this side. A category with exactly zero probability (an
    allele not currently present at that deme) makes `draw_one` return
    `0` without consuming any draw at all — which is what keeps this
    full `0 .. capacity - 1` sweep numerically equivalent to the
    dict-based path's own shorter, present-alleles-only sequence: every
    *real* draw lines up, in the same order, against the same
    accumulated `remaining_n`/`remaining_p`, with the skipped
    categories on this side costing nothing on either side.

    That earlier, array-valued version measured *slower* than the
    dict-based `LinealBackend` at this project's own reference scale
    (`d=20`, `capacity=256`, 300 generations) once actually benchmarked
    end to end, not just reasoned about: each of its `capacity - 1`
    array-valued `rng.binomial` calls is one genuinely vectorized draw,
    but only across `d` rows at a time — with `d` typically far smaller
    than `capacity`, per-call NumPy dispatch overhead, repeated
    `capacity - 1` times per invocation, dominated the actual compute.
    The exact same "batch every unit of work into one call, don't call
    once per small unit of it" lesson `drift`'s own `_drift_counts_
    batched` already had to learn the hard way (its own docstring)
    applies here too, just discovered on a second, independent function
    rather than generalized from the first: this scalar nested-loop
    form exists to be JIT-compiled (`_jit_multinomial_rows_batched`,
    below) into one call covering the *entire* `(d, capacity)` grid,
    with no Python- or NumPy-dispatch-level overhead paid per category
    or per deme at all. A scalar `Generator.binomial` compiles under
    `nogil=True`; an array-valued one does not (confirmed during Stage
    F5's own spike, `fim.model.operators`'s own module docstring) — this
    function's own scalar form is what makes that compilation possible.

    Args:
        rng: The run's explicitly threaded random generator.
        sizes: `(d,)` int64 — each row's own total count.
        probabilities: `(d, capacity)`, each row nonnegative — normalized
            internally over its own nonzero (present) entries, not
            assumed to already sum to 1.0 across the full `capacity`
            width (a row with no gene copies at all may be all zero).
            Callers should pass raw frequencies as-is rather than pre-
            normalizing over the full row themselves; see this
            function's own inline comment for why pre-normalizing over
            `capacity` elements, rather than letting this function sum
            only the present ones, is not bit-equivalent.
        present_sums: `(d,)` float64, row `i`'s own present-only sum —
            `_present_only_row_sums(probabilities)`, computed by the
            caller with real NumPy, outside this function's own JIT
            boundary. Not recomputed in here (see `_present_only_row_
            sums`'s own docstring, `FIM-12`, for why a hand-rolled
            sequential sum inside this function cannot reproduce it).

    Returns:
        `(d, capacity)` int64 counts, each row summing to that row's
        own `sizes` entry.
    """

    # `fim.model.operators._inversion_binomial`, as a nested closure,
    # not a call to that actual function — Numba's `nopython` mode
    # cannot compile a call to a plain, undecorated module-level Python
    # function as an internal callee (confirmed directly, and for the
    # identical reason `fim.model.operators._drift_counts_batched` does
    # the same thing — see its own inline comment); a *nested* function
    # compiles fine, since Numba treats it as part of the enclosing
    # function's own compiled body, not a separate global callee.
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

    deme_count, capacity = probabilities.shape
    # Zero-initialized, not `np.empty`: the inner loop below deliberately
    # stops at each row's own last nonzero-probability column, leaving
    # every column after it untouched. Those trailing columns must read
    # back as 0, not whatever garbage `np.empty` happened to leave there.
    counts = np.zeros((deme_count, capacity), dtype=np.int64)
    for deme_index in range(deme_count):
        # The dict-based `LinealBackend` decomposition (`_multinomial_
        # via_inversion_binomial`, `fim.model.operators`) only ever
        # iterates over the alleles actually PRESENT in that deme's own
        # `frequency_map` -- its last category is whichever present
        # allele sorts highest, and that category never draws (it just
        # absorbs whatever `remaining_n` is left). This function's own
        # `probabilities` row is dense over the FULL `capacity`, not
        # just the present alleles, so "the last column" and "the last
        # PRESENT column" are different things whenever the deme's
        # locus has not minted every capacity slot -- which is the
        # common case, not an edge case. Unconditionally treating
        # `capacity - 1` as the no-draw column (the original form of
        # this loop) draws one spurious extra uniform whenever the true
        # last-present column sits below `capacity - 1` and still has
        # `remaining_n > 0` when reached: real output values can still
        # coincidentally match that day (as the very first deme in a
        # multi-deme cross-backend probe did), but the two backends'
        # RNG streams are now permanently out of step, corrupting every
        # later deme's own draws. Found via a 2-deme trace where deme 0
        # matched exactly and deme 1 did not. Scanning for the row's own
        # last nonzero column, and stopping the draw loop there exactly
        # as the dict-based path stops at its own last present allele,
        # restores the same category count and the same "final category
        # never draws" placement on both sides.
        last_nonzero = 0
        for column in range(capacity):
            if probabilities[deme_index, column] > 0.0:
                last_nonzero = column
        # Normalize over the present-only sum the caller already
        # computed with real NumPy (`present_sums`, `_present_only_row_
        # sums`), not by assuming the row already sums to exactly 1.0
        # and not by re-accumulating it sequentially in here. NumPy's
        # own reduction is not associative: summing a length-`capacity`
        # row padded with zeros beyond the last present column gives a
        # different last bit than summing just the present values,
        # because the terms get grouped differently even though the
        # trailing zeros individually contribute nothing -- confirmed
        # directly (`probs16.sum() != probs16[:6].sum()` for six equal
        # 1/6 entries padded to a 16-wide row, one ULP apart). The dict-
        # based `LinealBackend` decomposition (`operators.mutate`'s own
        # `probabilities /= probabilities.sum()`) does this in two
        # separate steps: divide the whole array by its own sum once,
        # THEN run the draw loop with `remaining_p` starting at a clean
        # `1.0`. An earlier version of this fix collapsed that into one
        # step -- dividing each column directly by `remaining_p`, itself
        # initialized to `present_sum` rather than `1.0` -- which is
        # algebraically the same fraction but NOT the same floating-
        # point computation (`(x/s) / ((s-y)/s)` vs `x/(s-y)` are not
        # bit-identical), and a direct primitive-level comparison caught
        # it still diverging (2907/6000 mismatches) even after that
        # first attempt. Replicating the dict-based path's own two-step
        # shape exactly -- normalize each column against `present_sum`
        # once, then track `remaining_p` from a clean `1.0` the same way
        # `operators.mutate` does -- closed that first gap. A second,
        # deeper one remained even so: `present_sum` itself, accumulated
        # here by a hand-rolled sequential loop, silently stopped
        # matching dict's own `probabilities.sum()` the moment 8 or more
        # alleles were present in one row -- NumPy's own reduction is
        # sequential only below that count, pairwise from it up
        # (`_present_only_row_sums`'s own docstring, `FIM-12`). Passing
        # `present_sums` in, computed the one way that actually
        # reproduces NumPy's real behavior, closes that second gap. This
        # is the same latent gap `drift_vectorized` carries too (it also
        # passes a capacity-wide, not present-only, row here) -- fixing
        # it in this one shared function closes it for both callers, not
        # just the one that surfaced it.
        present_sum = present_sums[deme_index]
        remaining_n = sizes[deme_index]
        remaining_p = 1.0
        for column in range(capacity):
            if column == last_nonzero:
                counts[deme_index, column] = remaining_n
                break
            column_p = (
                probabilities[deme_index, column] / present_sum
                if present_sum > 0.0
                else 0.0
            )
            fraction = column_p / remaining_p if remaining_p > 0.0 else 0.0
            fraction = min(max(fraction, 0.0), 1.0)
            drawn = draw_one(remaining_n, fraction)
            counts[deme_index, column] = drawn
            remaining_n -= drawn
            remaining_p -= column_p
    return counts


def _jit_multinomial_rows_batched(
    rng: np.random.Generator,
    sizes: np.ndarray,
    probabilities: np.ndarray,
    present_sums: np.ndarray,
) -> np.ndarray:
    """`_multinomial_rows_batched`, JIT-compiled with `nogil=True`.

    `numba` is an optional dependency (`pip install fim[jit]`), imported
    here and nowhere else in this module — importing `fim.model.
    vectorized` never requires it. Compiled once, on first call, and
    cached at module level, exactly like `_jit_mutate_targets_batched`.

    `cache=True` additionally persists the compiled machine code to an
    on-disk `__pycache__/*.nbi`/`*.nbc` file, keyed by this function's
    own module, qualified name, and source hash — a *second* process
    (a fresh `fim run`, or one more `benchmark-engines`/`benchmark-
    queue` job; the module-level cache above only helps a second call
    within the *same* process) reuses that compiled code instead of
    paying the same compile cost again. Measured directly: ~0.95s cold
    compile vs. ~0.17s to load an existing cache, in two genuinely
    separate processes. Confirmed to degrade gracefully, not raise, when
    the cache cannot be written at all (an installed package's own
    directory with no write permission, e.g. a system-wide install run
    by a different user than installed it) — `numba` silently falls
    back to recompiling every time in that case, exactly today's
    pre-`cache=True` behavior, never a new failure mode.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MULTINOMIAL_ROWS_BATCHED  # noqa: PLW0603
    if _JIT_MULTINOMIAL_ROWS_BATCHED is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MULTINOMIAL_ROWS_BATCHED = numba.jit(nogil=True, cache=True)(
            _multinomial_rows_batched
        )
    return _JIT_MULTINOMIAL_ROWS_BATCHED(rng, sizes, probabilities, present_sums)


def migrate_vectorized(
    locus_state: VectorizedLocusState, weights: np.ndarray
) -> VectorizedLocusState:
    """Blend every deme with the migrant pool via one dense matmul.

    `F_next = W @ F` — `weights` (`W`, `(d, d)`, row-stochastic:
    self-retention on the diagonal, migrant weights off-diagonal) is
    exactly what `fim.model.operators._migrate_matrix`'s own deterministic
    branch already computes row by row (`blended[allele] =
    sum_source(weight * source_frequency[allele])`); this is the
    identical computation, as the one matrix product the vector design's
    own §5.1 names as the natural generalization. Deterministic
    ("continuous") migration only — see this module's own docstring.
    """
    return replace(locus_state, frequencies=weights @ locus_state.frequencies)


def migrate_vectorized_symmetric(
    locus_state: VectorizedLocusState,
    rate: float,
    sizes: np.ndarray,
) -> VectorizedLocusState:
    """Blend every deme with the migrant pool in `O(d*K)` time, no `(d,d)` matrix.

    The array-native counterpart to `fim.model.operators._migrate_
    symmetric`'s own `O(d*A)` shortcut, for the identical common case:
    a plain scalar migration rate, not a full custom weight matrix.
    `migrate_vectorized`'s own dense `W @ F` matmul is `O(d^2*K)`
    compute and `O(d^2)` memory to hold `W` at all, regardless of `rate`
    being a scalar — the documented memory wall (`20260901-claude-
    sonnet-5-fim-engine-backend-factory-design.md` §10, "`migrate_
    vectorized`'s own `O(d^2)` memory wall": 0.8GB at `d=10^4`, 20GB at
    `d=5x10^4`, genuinely unrepresentable beyond that, not merely slow).
    This function never builds `W` at all — it restates `_migrate_
    symmetric`'s own two-pass formula (global size-weighted mass once,
    then a per-destination pool) directly in dense-array form:

    ```
    global_mass[a] = sum_i sizes[i] * frequencies[i, a]  # sizes @ frequencies
    other_weight[i] = total_size - sizes[i]
    pool[i, a] = (global_mass[a] - sizes[i] * frequencies[i, a]) / other_weight[i]
    blended[i, a] = (1 - rate) * frequencies[i, a] + rate * pool[i, a]
    ```

    `O(d*K)` in both compute and memory — the identical order the
    dict-based backend's own shortcut already achieves, now available
    for the array-native path too. No separate normalization step:
    every row of `blended` sums to exactly 1 whenever every row of
    `frequencies` already does (the same row-stochastic-by-construction
    property `symmetric_migration_weights`'s own docstring proves for
    `W` directly — provable here the same way, by summing `blended`'s
    own formula over `a` and simplifying), matching `migrate_
    vectorized`'s own no-normalization contract exactly, not a new one.

    Args:
        locus_state: This locus's own dense working state.
        rate: The scalar migration rate.
        sizes: `(d,)` int64, each deme's own gene-copy count.

    Returns:
        This locus's own post-migration state.
    """
    if sizes.shape[0] == 1:
        # No "other" deme to blend with -- `other_weight` below would be
        # exactly `0.0`, producing a silent `NaN` rather than a raised
        # error (unlike the dict-based `_migrate_symmetric`, which at
        # least fails loudly here). Migration among one deme is a
        # well-defined no-op, the identical identity `_migrate_symmetric`
        # itself now returns for this same input (this project's own
        # multi-model engine review, 2026-09-04, found the two backends
        # disagreeing on how this case fails —
        # `FIM-02`/finding C-06/finding P2-2). Unreachable via a
        # validated `SimulationParams` (`d >= 2`), but this function is
        # public.
        return locus_state
    frequencies = locus_state.frequencies
    sizes_f64 = sizes.astype(np.float64)
    total_size = float(sizes_f64.sum())
    global_mass = sizes_f64 @ frequencies
    other_weight = total_size - sizes_f64
    pool = (global_mass[None, :] - sizes_f64[:, None] * frequencies) / other_weight[
        :, None
    ]
    blended = (1.0 - rate) * frequencies + rate * pool
    return replace(locus_state, frequencies=blended)


def symmetric_migration_weights(rate: float, sizes: np.ndarray) -> np.ndarray:
    """Build the dense `(d, d)` weight matrix for a scalar migration rate.

    Derived directly from `fim.model.operators._migrate_symmetric`'s own
    deterministic formula: `pool_i(a) = (total_mass(a) - size_i *
    local_i(a)) / (total_size - size_i)`, `blended_i(a) = (1 - rate) *
    local_i(a) + rate * pool_i(a)`. Expanding `pool_i` in terms of every
    other deme's own frequency gives `W[i, i] = 1 - rate` and, for `j !=
    i`, `W[i, j] = rate * size_j / (total_size - size_i)` — row `i` sums
    to exactly 1 by construction (the migrant weights alone sum to
    `rate`, since `sum_{j != i} size_j == total_size - size_i`).

    **No longer `fim.engine.VectorizedAdvancer`'s own path for a scalar
    rate** — `migrate_vectorized_symmetric`, above, computes the
    identical blend directly, in `O(d*K)`, without ever materializing
    this `O(d^2)` matrix at all (`20260903-claude-sonnet-5-fim-vg-
    performance-campaign-design.md` §6.1 item 1). Kept as a real, tested
    public function in its own right: a genuine caller-supplied weight
    matrix (`SimulationParams.m` given as a full matrix, not a scalar)
    still needs an actual `(d, d)` array — `migrate_vectorized` itself,
    unlike this narrower symmetric case, is not being retired — and a
    caller who wants the materialized matrix directly (inspection,
    building a custom topology from a symmetric base) still has it.
    """
    deme_count = sizes.shape[0]
    total_size = float(sizes.sum())
    weights = np.zeros((deme_count, deme_count), dtype=np.float64)
    for destination in range(deme_count):
        other_weight = total_size - sizes[destination]
        for source in range(deme_count):
            if source == destination:
                weights[destination, source] = 1.0 - rate
            else:
                weights[destination, source] = rate * sizes[source] / other_weight
    return weights


def mutate_vectorized(
    locus_state: VectorizedLocusState,
    sizes: np.ndarray,
    rate: float,
    rng: np.random.Generator,
) -> VectorizedLocusState:
    """Replace a binomially sampled number of copies with new-or-recurring alleles.

    The array-native counterpart to `fim.model.operators.mutate`'s own
    finite-alleles branch — same three steps (event count, source
    attribution, target selection), same underlying distributions, and,
    as of Stage F8
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4), the identical primitive and the identical *interleaving*.

    **Interleaved per deme, deliberately, not batched by step across
    every deme first.** An earlier version of this function drew every
    deme's own event count first, then every deme's own source
    attribution, then every event's own target, in three separate
    passes — each pass individually using the unified primitive and a
    plausible-looking canonical order (ascending deme, then ascending
    allele id), but *as a whole* still consuming this lane's own `rng`
    in a different sequence than `mutate`'s own dict-based path does,
    which interleaves all three steps *within* each deme before moving
    to the next (draw deme 0's own event count, then its own source
    attribution, then all of deme 0's own targets, only then deme 1's
    own event count, and so on). Found by a direct cross-backend test,
    not caught by reasoning about each step in isolation — the
    single-deme case matched exactly from the very first attempt (both
    orderings agree trivially when there is only one deme to interleave
    with), which is what let the batched-by-step version pass every
    test written against it up to that point; a multi-deme test caught
    the real divergence. Fixed by looping over demes explicitly, in
    ascending order, running all three of one deme's own steps before
    moving to the next — the real cost this pays, not minimized: each
    of `_jit_multinomial_rows_batched`/`_jit_mutate_targets_batched` is
    now called once per deme with active mutation events rather than
    once per generation across every deme at once, reintroducing some
    of the per-call overhead Stage F5's own investigation found
    dominant for a structurally similar case (`fim.model.operators.
    _drift_counts_batched`'s own docstring) — accepted here because
    `mutate`'s own event counts are typically far smaller than
    `drift`'s own full per-deme resampling (`mu` is a small
    probability), so this operator's own share of a generation's total
    cost is small enough that the correctness this buys is judged
    worth it; not separately re-benchmarked end to end as part of this
    change.

    Target selection specifically preserves `FiniteAlleleSpace.
    mutate_target`'s own real, load-bearing choice — a recurrence
    probability that grows as more of the bounded state space fills up,
    which is what lets the finite-alleles model recover infinite-alleles
    behavior as capacity grows (its own docstring), and, as of the same
    Stage F8 pass, the identical single-fixed-draw mechanism
    `FiniteAlleleSpace.mutate_target`'s own recurrence branch uses
    (`_mutate_targets_batched`'s own inline comment has the full
    argument for why an earlier, statistically-equivalent-but-not-
    same-draw-count rejection-sampling version needed replacing too).

    Two more, independent divergence sources remained even after both
    of the above were fixed, neither one to do with the random draw
    itself: a rare (roughly 1-in-150 demes, at this project's own
    reference scale), floating-point-boundary-triggered mismatch traced
    to source attribution's own probability normalization
    (`_multinomial_rows_batched`'s own inline comment in this module has
    the full argument), and a small, systematic ULP-level drift from
    `mutate`'s own final `_normalize` rescaling step, which this
    function did not originally replicate at all (this function's own
    inline comment right after the `np.add.at` call has that argument).
    Closing both is what makes this function's own output agree with
    `mutate`'s dict-based path *exactly* now, not merely "almost
    always" — checked directly
    (`test_mutate_vectorized_matches_dict_based_mutate_exactly`,
    `test/model/test_vectorized.py`), across 30 seeds and a deliberately
    non-saturated capacity, not assumed from the first two fixes alone.
    """
    new_frequencies = locus_state.frequencies.copy()
    minted_mask = locus_state.minted_mask.copy()
    minted_list = locus_state.minted_list.copy()
    minted_count = locus_state.minted_count
    next_unminted = locus_state.next_unminted
    capacity = locus_state.capacity

    for deme in range(sizes.shape[0]):
        size = int(sizes[deme])
        event_count = _inversion_binomial(rng, size, rate)
        if event_count == 0:
            continue
        retained_mass = 1.0 - event_count / size
        new_frequencies[deme] *= retained_mass

        # No explicit re-normalization needed here: `_multinomial_rows_
        # batched`/`_jit_multinomial_rows_batched` normalize internally
        # over each row's own present (nonzero) prefix -- see that
        # function's own inline comment for why summing over the full,
        # zero-padded `capacity` width first (an earlier version of
        # this call site did exactly that) is not equivalent, down to
        # the last bit.
        source_row = locus_state.frequencies[deme : deme + 1]
        source_counts = _jit_multinomial_rows_batched(
            rng,
            np.array([event_count], dtype=np.int64),
            source_row,
            _present_only_row_sums(source_row),
        )[0]
        event_sources = np.repeat(np.arange(capacity, dtype=np.int64), source_counts)

        targets, minted_mask, minted_list, minted_count, next_unminted = (
            _jit_mutate_targets_batched(
                rng,
                event_sources,
                capacity,
                minted_mask,
                minted_list,
                minted_count,
                next_unminted,
            )
        )
        event_frequency = 1.0 / size
        np.add.at(new_frequencies[deme], targets, event_frequency)

        # `operators.mutate`'s own dict-based path renormalizes each
        # locus's frequency map with `_normalize` (`math.fsum`-based,
        # correctly rounded) after applying every mutation event,
        # guarding against floating-point total-mass drift accumulating
        # across many generations of repeated multiplicative scaling.
        # `drift`/`drift_vectorized`'s own clean integer-count-over-
        # `size` grid never needs this (dividing the same integer by
        # the same `size` lands on the same bits on both backends), but
        # `mutate`'s own output is not on a clean grid -- without this
        # step, this function's own final values can differ from
        # `mutate`'s by a few ULPs, found directly via a 30-seed exact-
        # match test (`test_mutate_vectorized_matches_dict_based_mutate_
        # exactly`), not assumed. `math.fsum`, not `.sum()`, matches
        # `_normalize`'s own choice of summation algorithm exactly --
        # confirmed directly that trailing zero-padding is inert under
        # `math.fsum` even though it is *not* inert under NumPy's own
        # pairwise `.sum()` (`_multinomial_rows_batched`'s own inline
        # comment has that separate finding). `_normalize` itself only
        # ever sees a dict's present (non-zero) keys in the first
        # place, so restrict this call to the row's own nonzero entries
        # too rather than converting the full, mostly-zero `capacity`-
        # wide row: `fsum`'s own running sum is bit-for-bit unaffected
        # by omitting exact `0.0` terms (adding one is a no-op at any
        # partial sum), so this is not an approximation -- confirmed
        # directly by `test_mutate_vectorized_renormalization_fsum_
        # ignores_zero_padding` (`test/model/test_vectorized.py`), not
        # assumed from the argument alone. `.tolist()` over the full
        # zero-padded row measured as a genuinely large cost at
        # realistic capacity (profiling at `d=60`, `capacity=4096`:
        # ~19% of a whole step+convergence loop, comparable to the
        # JIT-compiled multinomial kernel itself), almost all of it
        # wasted on zeros this restriction skips entirely.
        row = new_frequencies[deme]
        total = math.fsum(row[np.flatnonzero(row)].tolist())
        new_frequencies[deme] /= total

    return VectorizedLocusState(
        frequencies=new_frequencies,
        capacity=capacity,
        minted_mask=minted_mask,
        minted_list=minted_list,
        minted_count=minted_count,
        next_unminted=next_unminted,
    )


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

    A direct array/loop translation of `FiniteAlleleSpace.mutate_target`,
    called once per event in `sources` rather than once from `mutate`'s
    own dict-based loop — see `mutate_vectorized`'s own docstring for
    what is, and is not, preserved exactly. As of Stage F8
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §5.4), the recurrence branch's own random-draw count exactly
    matches `FiniteAlleleSpace.mutate_target`'s — one `rng.integers()`
    call, never a variable number — via the same "exclude one element,
    shift the drawn index past it" construction that branch's own
    inline comment explains in full.

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
            arrays`'s own docstring has the full reasoning for why the
            argument/return shape did not change along with the fix).

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
            # `FiniteAlleleSpace.mutate_target`'s own dict-based
            # recurrence branch filters `current` out of the minted
            # list first (`[a for a in minted if a != current]`), then
            # draws one fixed index into that shrunk, `minted_count -
            # 1`-sized list — never a second draw. An earlier version
            # of this function drew directly into the *unfiltered*
            # `minted_count`-sized list and rejected/redrew whenever it
            # landed on `current` — statistically equivalent (uniform
            # over the same `minted_count - 1` remaining states either
            # way), but not the same *number of draws*, found by a
            # direct cross-backend test (95 mismatches across 15
            # seeds, `test/model/test_vectorized.py`'s own commit
            # history) before this fix, not assumed correct. Fixed by
            # replicating the filter-then-index mechanism exactly, via
            # the standard "exclude one element" index-shift trick
            # instead of an actual list filter: find `current`'s own
            # position in `minted_list[0:minted_count]` (a linear
            # scan — `current` is guaranteed present, since it is a
            # real mutation event's own source), draw one fixed index
            # into the `minted_count - 1`-sized space with that one
            # position removed, and shift the drawn index past it when
            # the draw lands at or beyond it — exactly what indexing
            # into the filtered list would return, without ever
            # constructing it.
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
    here and nowhere else in this module — importing `fim.model.
    vectorized` never requires it. Compiled once, on first call, and
    cached at module level, exactly like `fim.model.operators`'s own
    lazy-JIT helpers.

    `cache=True` additionally persists the compiled machine code across
    separate processes, not just separate calls within one -- see
    `_jit_multinomial_rows_batched`'s own docstring, above, for the
    measured benefit and the confirmed-safe read-only-install fallback.

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


def drift_vectorized(
    locus_state: VectorizedLocusState, sizes: np.ndarray, rng: np.random.Generator
) -> VectorizedLocusState:
    """Resample `N` gene copies per deme, across every deme in one pass.

    The array-native counterpart to `fim.model.operators.drift` —
    `_jit_multinomial_rows_batched` above uses the same conditional-
    binomial decomposition and the same `_inversion_binomial` primitive
    `drift`'s own dict-based path now uses (Stage F8), applied across
    the entire `(d, capacity)` array in one JIT-compiled call. See this
    module's own docstring, and `_multinomial_rows_batched`'s own, for
    exactly what property that gives this function relative to
    `drift`'s own output for the same seed.

    Raises:
        ValueError: If any deme's own size is not a positive integer —
            `counts / sizes[:, None]` below would otherwise divide by
            `0` for that deme, producing a silent `NaN` rather than a
            raised error (this project's own multi-model engine review,
            2026-09-04, `FIM-15`). `fim.model.operators.drift`'s own
            dict-based path already rejects this via `_population_
            sizes`; unreachable via a validated `SimulationParams`
            (every `N` value must be a positive integer), but this
            function is public and takes `sizes` directly, unvalidated.
    """
    if np.any(sizes <= 0):
        raise ValueError("drift_vectorized requires every deme size to be at least 1")
    counts = _jit_multinomial_rows_batched(
        rng,
        sizes,
        locus_state.frequencies,
        _present_only_row_sums(locus_state.frequencies),
    )
    return replace(locus_state, frequencies=counts / sizes[:, None])


def step_vectorized(
    state: VectorizedState,
    weights_per_locus: tuple[np.ndarray, ...] | None,
    mutation_rates: tuple[float, ...],
    sizes: np.ndarray,
    rng: np.random.Generator,
    *,
    symmetric_rate: float | None = None,
) -> VectorizedState:
    """Advance one generation: migrate, then mutate, then drift, fused.

    The array-native counterpart to `fim.model.operators.step` — every
    locus stays a dense array from this call's own start to its own end,
    across all three stages, with no `ModelState` reconstructed in
    between (the actual thing this whole module exists to test — see the
    module's own docstring). `mutation_rates` is already resolved per
    locus by the caller (`SimulationParams.mutation_rates` itself
    already resolves a scalar `mu` to one rate per locus).

    Loops `for locus in state.locus_states` below, running all three
    stages for one locus before moving to the next — locus-major, not
    `operators.step`'s own stage-major (`migrate` over every locus, then
    `mutate` over every locus, then `drift` over every locus). This
    module's own docstring has the full argument for why a single-locus
    run is still bit-identical to `operators.step` despite that
    difference (migrate draws no RNG in the deterministic mode this
    fusion is scoped to) and why a multi-locus run is not (`mutate`/
    `drift`'s own dict-based loops are deme-major, locus-minor; this
    loop is unavoidably locus-major, since each iteration processes one
    locus's whole `(deme, capacity)` array in one call) — deliberately
    not "fixed" by reordering the loop below to stage-major instead,
    since that only changes *which* wrong order results, not whether the
    order matches (this project's own multi-model engine review,
    2026-09-04, `FIM-09`).

    Exactly one of `weights_per_locus`/`symmetric_rate` must be given,
    choosing which of `migrate_vectorized`'s own two implementations
    runs this generation — see either function's own docstring:

    - `weights_per_locus`: an already-row-stochastic `(d, d)` matrix per
      locus (`symmetric_migration_weights`, or a genuine caller-supplied
      weight matrix) — `O(d^2)` per locus, the general case.
    - `symmetric_rate`: a plain scalar migration rate, dispatched to
      `migrate_vectorized_symmetric` instead — `O(d*K)`, no `(d, d)`
      matrix ever built, the common case
      (`20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
      §6.1 item 1).

    Raises:
        ValueError: If both or neither of `weights_per_locus`/
            `symmetric_rate` are given.
    """
    if (weights_per_locus is None) == (symmetric_rate is None):
        raise ValueError(
            "step_vectorized() requires exactly one of weights_per_locus "
            "or symmetric_rate"
        )
    # Only the migrate step itself differs between the two paths — mutate
    # and drift stay one shared loop, not duplicated across an if/else,
    # so there is exactly one place their own fusion order is stated.
    # A fixed-length `(None,) * n` placeholder, not `itertools.repeat`
    # (infinite, and `zip`'s own `strict=True` check would need to pull
    # one further element from it to notice the other iterables already
    # stopped, which it never will — a real bug caught before running,
    # not found by a failing test).
    weights_or_none: tuple[np.ndarray | None, ...] = (
        weights_per_locus
        if weights_per_locus is not None
        else (None,) * len(state.locus_states)
    )
    new_locus_states = []
    for locus_state, weights, rate in zip(
        state.locus_states, weights_or_none, mutation_rates, strict=True
    ):
        migrated = (
            migrate_vectorized_symmetric(locus_state, symmetric_rate, sizes)
            if symmetric_rate is not None
            else migrate_vectorized(locus_state, weights)  # type: ignore[arg-type]
        )
        mutated = mutate_vectorized(migrated, sizes, rate, rng)
        drifted = drift_vectorized(mutated, sizes, rng)
        new_locus_states.append(drifted)
    return VectorizedState(
        loci=state.loci,
        locus_states=tuple(new_locus_states),
        generation=state.generation + 1,
    )
