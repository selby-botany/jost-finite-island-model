"""Backend-independent trajectory row schema and store protocol.

`TrajectoryRow` is the one, single-observation record shape every
persisted trajectory row uses (see `fim.model.state.ModelState.to_rows`
for how a state turns into a batch of these), and `TrajectoryStore` is
the shared interface both `fim.persistence.jsonl_store.
JSONLTrajectoryStore` (the real, file-backed store) and
`InMemoryTrajectoryStore`, below (a lighter-weight stand-in for
library calls and tests that never need an actual file), implement —
so `fim.engine`'s run loop can write to either without knowing which
one it actually has.

`write_generation`'s own `validate` keyword (default `True`, unchanged
behavior for every existing caller): a real, measured cost. Profiling a
representative Backend V run found `normalize_row` — full schema
presence/absence checks, then a per-field `isinstance` gauntlet on
every row of every generation — as the single largest cost center in
the whole run, ~36% of wall clock, ahead of the actual migrate/mutate/
drift step. That check earns its cost for a row `normalize_row` cannot
otherwise vouch for: a hand-edited or externally-produced row, or one
`JSONLTrajectoryStore.read` is parsing back off disk. It earns nothing
for a row `fim.engine`'s own run loop just built, in the same
expression, from `ModelState.to_rows`/`fim.model.vectorized.
vectorized_state_to_rows` — both of which construct every field
already well-typed and in-bounds by construction (a real, finite
`float` frequency in `(0, 1]`, a positive `int` id, a nonempty `str`
run id), from a `ModelState`/`VectorizedState` whose own construction
already enforced those same invariants. Re-running `normalize_row` on
such a row cannot find a defect `ModelState`'s/`VectorizedState`'s own
construction did not already rule out — it can only re-confirm what is
already known, on every single row, every single generation. `fim.
engine`'s own internal call sites (including the equilibrium-split
ancestral phase's, which writes `ModelState.to_rows` rows too) pass
`validate=False` specifically because each one is provably in this
position; no other
caller in this codebase does, and a new one should not either without
the same proof.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast, runtime_checkable

from fim.model.identifiers import parse_bounded_frequency

if TYPE_CHECKING:
    from fim.persistence.frame import FrameLayout, TrajectoryFrame


class TrajectoryRow(TypedDict):
    """One nonzero allele-frequency observation.

    One row means one specific allele, at one specific locus, in one
    specific deme, at one specific generation, had a nonzero frequency
    — an allele that is entirely absent from a given deme/locus/
    generation simply has no row at all (see `fim.model.state.
    ModelState`'s own docstring for why this sparse representation is
    used). `run_id`, `generation`, `deme`, and `locus_id` together
    identify *which* observation this is; `allele_id` and `frequency`
    are the observation itself.
    """

    run_id: str
    generation: int
    deme: int
    locus_id: int
    allele_id: int
    frequency: float


class TrajectoryStore(Protocol):
    """Incrementally persist and iterate long-form trajectory rows.

    A "protocol" here means any object with these two methods — this
    class is never instantiated itself; both `InMemoryTrajectoryStore`,
    below, and `fim.persistence.jsonl_store.JSONLTrajectoryStore`
    satisfy it, so a caller can be written against this one shared
    interface regardless of which concrete store it is actually given.
    """

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Persist all rows for one generation.

        Args:
            run_id: This batch's own run identity.
            generation: This batch's own generation number.
            rows: The rows themselves.
            validate: Whether to run every row through `normalize_row`'s
                full schema/type/bounds check before writing it. Default
                `True` is always safe — every existing caller keeps its
                current behavior unchanged. `False` is an opt-in fast
                path for a caller that already knows its own rows are
                well-formed (this module's own top docstring has the
                full reasoning and the two internal producers this
                applies to); passing `False` for a row from anywhere
                else is a real correctness risk, not a style choice.
        """
        ...

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield rows for one run in stored order."""
        ...

    def discard(self, run_id: str) -> None:
        """Permanently remove every row belonging to one run, if any exist.

        A safe no-op when ``run_id`` has no rows at all — "get rid of
        this run's own data, if there is any" is the whole contract, not
        "assert that some existed first." Exists so an abandoned
        replicate lane's own already-written rows (`fim.engine.
        run_batch`'s own generational adaptive stop, or `_run_batch_
        parallel`'s own worker-batch overshoot — see this project's own
        multi-model engine review, 2026-09-04, `FIM-49`/`FIM-50`) do not
        outlive the fact that the replicate they belong to was never
        actually finished or returned to a caller: a reader of the store
        should never see a `run_id` with no corresponding `RunResult` to
        explain it.

        Args:
            run_id: The run whose rows should no longer exist in this
                store, whether they were ever written or not.
        """
        ...


@runtime_checkable
class EquilibriumStoreProvider(Protocol):
    """A trajectory store that knows where its run's ancestral phase goes.

    An equilibrium-split run (`fim.model.initial.
    EquilibriumSplitInitialCondition`) simulates one panmictic ancestral
    population before founding its demes. That phase's own trajectory
    is persisted separately from the main one — the
    `equilibrium_trajectory.jsonl` artifact — in the identical
    `TrajectoryRow` schema, with its own generation counter starting at
    zero, so it can never be mistaken for the main run's own
    generations. A store implementing this method names the companion
    store those rows belong in: a sibling file for
    `fim.persistence.jsonl_store.JSONLTrajectoryStore`, a second
    in-memory store for `InMemoryTrajectoryStore`. Optional — `fim.
    engine` falls back to a fresh `InMemoryTrajectoryStore` for a store
    without it (`equilibrium_store_for`).
    """

    def equilibrium_store(self, run_id: str) -> TrajectoryStore:
        """Return the store `run_id`'s ancestral-phase rows are written to."""
        ...


def equilibrium_store_for(store: TrajectoryStore, run_id: str) -> TrajectoryStore:
    """Return the ancestral-phase companion of `store` for one run.

    Args:
        store: The run's own main trajectory store.
        run_id: The run whose ancestral phase is being persisted.

    Returns:
        `store.equilibrium_store(run_id)` when `store` provides one
        (`EquilibriumStoreProvider`); otherwise a fresh
        `InMemoryTrajectoryStore`, so a custom store still gets a
        readable ancestral trajectory on `fim.engine.RunResult.
        equilibrium_store`, just not a file.
    """
    if isinstance(store, EquilibriumStoreProvider):
        return store.equilibrium_store(run_id)
    return InMemoryTrajectoryStore()


@runtime_checkable
class FrameStore(Protocol):
    """A trajectory store that can take a generation as a `TrajectoryFrame`.

    Optional, like `EquilibriumStoreProvider`. A frame is the flat-array
    form of one generation (`fim.persistence.frame`); a store that keeps
    its data in a compact form (the binary log) can take it without a
    dictionary ever being built per row. `begin_run` tells the store the
    run's shape once; `write_frame` then takes each generation.

    `wants_frames` says whether the engine should *prefer* handing this
    store frames. Every `FrameStore` accepts them (the JSON Lines and
    in-memory stores turn a frame back into rows), but only a store that
    gains from them says `True`; for the others the engine keeps handing
    over rows, which is cheaper than converting to a frame and back.
    """

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Record the shape of `run_id`'s frames; repeating it is harmless.

        Raises:
            ValueError: If `run_id` was already begun with another layout.
        """
        ...

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Persist one generation given as a frame, after `begin_run`."""
        ...

    def wants_frames(self, run_id: str) -> bool:
        """Whether the engine should hand `run_id`'s generations over as frames."""
        ...


def begin_store_run(store: TrajectoryStore, run_id: str, layout: FrameLayout) -> None:
    """Tell `store` the shape of `run_id`'s frames, when it is a `FrameStore`.

    Args:
        store: Any `TrajectoryStore`; one that is not a `FrameStore` is
            left alone.
        run_id: The run about to be written.
        layout: Its layout.
    """
    if isinstance(store, FrameStore):
        store.begin_run(run_id, layout)


def store_wants_frames(store: TrajectoryStore, run_id: str) -> bool:
    """Whether the engine should hand `run_id`'s generations to `store` as frames.

    Args:
        store: Any `TrajectoryStore`.
        run_id: The run being written.

    Returns:
        `True` only for a `FrameStore` that answers `True` for the run.
    """
    wants = getattr(store, "wants_frames", None)
    return bool(wants(run_id)) if callable(wants) else False


@runtime_checkable
class ClosableStore(Protocol):
    """A trajectory store that holds an open resource until it is closed.

    Optional, like `EquilibriumStoreProvider`: `TrajectoryStore` itself
    needs no `close` (a custom store, or a test double, can omit it).
    `fim.persistence.jsonl_store.JSONLTrajectoryStore` keeps its file open
    between generations and implements it; closing is idempotent, and a
    closed store re-opens itself if written to again.
    """

    def close(self) -> None:
        """Release whatever the store holds open; safe to call repeatedly."""
        ...


def close_run_store(store: TrajectoryStore, run_id: str) -> None:
    """Close the resources one run of a multi-run store holds, if it can.

    Args:
        store: A store that may hold one child store per run
            (`ReplicateFanoutStore`); any other store is left alone.
        run_id: The run whose own writing is finished.
    """
    close_run = getattr(store, "close_run", None)
    if callable(close_run):
        close_run(run_id)


def close_store(store: TrajectoryStore) -> None:
    """Close `store`'s open resources when it has any; otherwise do nothing.

    Args:
        store: Any `TrajectoryStore`; only a `ClosableStore` is closed.
    """
    if isinstance(store, ClosableStore):
        store.close()


class InMemoryTrajectoryStore:
    """Store trajectories in memory for library calls and focused tests.

    Implements the same `TrajectoryStore` protocol as `fim.persistence.
    jsonl_store.JSONLTrajectoryStore`, but keeps every row in an
    ordinary Python list rather than writing to a file — used whenever
    a caller (a unit test, or a library user who only wants the final
    result and does not care about a persisted trajectory file) has no
    need to actually write anything to disk.

    One instance may be shared across several concurrently-running
    replicates (`fim.engine.GenerationalBackend`'s own `ThreadedAdvancer`)
    — each replicate's own rows are already disambiguated by `run_id`,
    so the only real hazard is two threads mutating `_rows` at the same
    moment. `list.extend` happens to be atomic under CPython's own GIL
    today, but relying on that is relying on an interpreter
    implementation detail a future free-threaded (no-GIL) CPython build
    would not honor — `_lock` guards the mutation explicitly instead, so
    correctness never depends on which CPython build happens to be
    running this.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._rows: list[TrajectoryRow] = []
        self._lock = threading.Lock()
        self._equilibrium: InMemoryTrajectoryStore | None = None
        self._layouts: dict[str, FrameLayout] = {}

    def __getstate__(self) -> dict[str, Any]:
        """Drop `_lock` before pickling.

        `RunResult.store` crosses a real process boundary under
        `LinealBackend`'s own `max_workers` path (`ProcessPoolExecutor`
        pickles a worker's returned `RunResult`, store included, to send
        it back to the parent process) — a `threading.Lock` cannot be
        pickled at all, and would not mean anything in a different
        process even if it could be. `__setstate__` rebuilds a fresh
        lock on the other side instead.
        """
        state = self.__dict__.copy()
        del state["_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore everything but `_lock`, then rebuild a fresh one."""
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Append one generation, validated unless the caller vouches for it.

        See this module's own top docstring for exactly what
        `validate=False` skips, and why it is safe only for the two
        internal row producers named there.
        """
        if validate:
            generation_rows = [
                normalize_row(row, run_id=run_id, generation=generation) for row in rows
            ]
        else:
            generation_rows = [cast("TrajectoryRow", dict(row)) for row in rows]
        if not generation_rows:
            raise ValueError("a generation must contain at least one row")
        with self._lock:
            self._rows.extend(generation_rows)

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Record `run_id`'s layout so `write_frame` can turn frames into rows.

        Raises:
            ValueError: If `run_id` was already begun with another layout.
        """
        with self._lock:
            known = self._layouts.setdefault(run_id, layout)
        if known != layout:
            raise ValueError(f"run {run_id!r} was already begun with another layout")

    def wants_frames(self, run_id: str) -> bool:
        """Return `False`: rows are what this store keeps, so rows are cheaper."""
        del run_id
        return False

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Append one generation given as a frame, as the rows it stands for.

        Raises:
            ValueError: If `begin_run` was not called for `run_id`, or the
                frame does not fit the layout.
        """
        from fim.persistence.frame import frame_to_rows  # noqa: PLC0415

        layout = self._layouts.get(run_id)
        if layout is None:
            raise ValueError(f"begin_run was not called for run {run_id!r}")
        rows = frame_to_rows(frame, layout, run_id)
        with self._lock:
            self._rows.extend(rows)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield rows matching ``run_id`` in insertion order.

        Snapshots `_rows` under `_lock` before filtering, rather than
        iterating the live list directly, so a concurrent
        `write_generation` call from another thread can never produce a
        torn read.
        """
        with self._lock:
            rows_snapshot = list(self._rows)
        return (row.copy() for row in rows_snapshot if row["run_id"] == run_id)

    def discard(self, run_id: str) -> None:
        """Drop every row matching ``run_id``; a no-op if there are none.

        See `TrajectoryStore.discard`'s own docstring for why this
        exists at all. Held under `_lock`, the same guard `write_
        generation`/`read` already use, so a concurrent write from
        another thread can never interleave with this rebuild of `_rows`.
        """
        with self._lock:
            self._rows = [row for row in self._rows if row["run_id"] != run_id]

    def close(self) -> None:
        """Do nothing: rows live in memory, so there is nothing to release.

        Present so a caller can close any store without checking which
        kind it has (`ClosableStore`).
        """

    def equilibrium_store(self, run_id: str) -> TrajectoryStore:
        """Return this store's one in-memory ancestral-phase companion.

        Built on first use and shared by every run this store holds,
        the same way this store's own rows are: each run's ancestral
        rows are told apart by `run_id`, exactly like its main rows
        (`EquilibriumStoreProvider`).
        """
        del run_id
        with self._lock:
            if self._equilibrium is None:
                self._equilibrium = InMemoryTrajectoryStore()
            return self._equilibrium


class ReplicateFanoutStore:
    """Route each ``run_id`` to its own store, built lazily on first use.

    Gives a caller with only one `store_factory` (one replicate's own
    fresh store, built by `run_id` — `LinealBackend`'s own long-standing
    shape, `fim()`'s own docstring) a single object satisfying
    `TrajectoryStore` that a *generation-first* batch (`fim.engine.
    run_batch`, driving `GenerationalBackend`) can pass around as its one
    shared `store` argument without ever needing to know several
    replicates are behind it. `run_batch`/`ReplicaLane`/every `Advancer`
    implementation already treats `store` as opaque and keys every call
    by `run_id` — this class is the only thing that changed to let a
    `generational`/`generational-vector` batch produce one real,
    independent file per replicate the same way `LinealBackend`'s own
    `store_factory` path already does, rather than a new execution model
    (`20260914-claude-sonnet-5-non-lineal-batch-execution-design.md`,
    `selby/restricted`, §5.1).

    Thread-safe at the one point it needs to be: `_lock` guards only the
    lazy get-or-create step below. Once a child store exists for a given
    `run_id`, every further call for that `run_id` delegates straight to
    it, and each concrete `TrajectoryStore` implementation
    (`JSONLTrajectoryStore`, `InMemoryTrajectoryStore`) is already safe
    under concurrent `write_generation` calls in its own right
    (`ThreadedAdvancer`'s own docstring) — this class adds no new
    contention beyond the one-time creation, and never itself calls two
    child stores' methods at once.
    """

    def __init__(self, store_factory: Callable[[str], TrajectoryStore]) -> None:
        """Wrap a per-replicate store factory as one shared `TrajectoryStore`.

        Args:
            store_factory: Builds one replicate's own real store, given
                that replicate's own `run_id` — the same shape `fim()`'s
                own `store_factory` argument already has.
        """
        self._store_factory = store_factory
        self._stores: dict[str, TrajectoryStore] = {}
        self._lock = threading.Lock()

    def __getstate__(self) -> dict[str, Any]:
        """Drop `_lock` before pickling — see `InMemoryTrajectoryStore`'s
        own identical method for why."""
        state = self.__dict__.copy()
        del state["_lock"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore everything but `_lock`, then rebuild a fresh one."""
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def _store_for(self, run_id: str) -> TrajectoryStore:
        """Return `run_id`'s own child store, building it on first use."""
        with self._lock:
            store = self._stores.get(run_id)
            if store is None:
                store = self._store_factory(run_id)
                self._stores[run_id] = store
            return store

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Delegate to `run_id`'s own child store; see `TrajectoryStore`."""
        self._store_for(run_id).write_generation(
            run_id, generation, rows, validate=validate
        )

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Tell `run_id`'s own child store its layout (`FrameStore`)."""
        begin_store_run(self._store_for(run_id), run_id, layout)

    def wants_frames(self, run_id: str) -> bool:
        """Whether `run_id`'s own child store prefers frames (`FrameStore`)."""
        return store_wants_frames(self._store_for(run_id), run_id)

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Delegate a frame to `run_id`'s own child store (`FrameStore`).

        Raises:
            TypeError: If the child store cannot take frames.
        """
        child = self._store_for(run_id)
        if not isinstance(child, FrameStore):
            raise TypeError(f"{type(child).__name__} cannot take frames")
        child.write_frame(run_id, frame)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Delegate to `run_id`'s own child store; see `TrajectoryStore`."""
        return self._store_for(run_id).read(run_id)

    def discard(self, run_id: str) -> None:
        """Discard `run_id`'s own rows; a no-op if no child store exists.

        Matches `TrajectoryStore.discard`'s own "no rows, no error"
        contract exactly: a `run_id` this store never saw (an adaptive
        stop's own abandoned lane, `run_batch`'s own docstring) has no
        child store to create just to immediately discard from.
        """
        store = self._stores.get(run_id)
        if store is not None:
            store.discard(run_id)

    def close(self) -> None:
        """Close every child store built so far (`ClosableStore`)."""
        with self._lock:
            children = list(self._stores.values())
        for child in children:
            close_store(child)

    def close_run(self, run_id: str) -> None:
        """Close `run_id`'s own child store, if one was built.

        Called once a replicate has written its last generation, so a
        batch holds open only the files of the replicates still running
        rather than one per replicate for the whole batch. A no-op for a
        run this store never saw; the child re-opens if written again.
        """
        child = self._stores.get(run_id)
        if child is not None:
            close_store(child)

    def equilibrium_store(self, run_id: str) -> TrajectoryStore:
        """Return the ancestral-phase companion of `run_id`'s own child store.

        So a replicate's `equilibrium_trajectory.jsonl` lands beside its
        own `trajectory.jsonl`, in its own directory
        (`EquilibriumStoreProvider`).
        """
        return equilibrium_store_for(self._store_for(run_id), run_id)


def normalize_row(
    row: Mapping[str, Any],
    *,
    run_id: str | None = None,
    generation: int | None = None,
) -> TrajectoryRow:
    """Validate and normalize one public-schema trajectory row.

    "Public schema" means this is the one row shape a trajectory file
    is allowed to contain — exactly the six `TrajectoryRow` fields,
    nothing missing and nothing extra — so a hand-edited or externally
    produced row is checked against the identical rules a row
    generated by the simulator itself already satisfies. When `run_id`
    and/or `generation` are supplied, the row's own values for those
    fields are additionally cross-checked against them (used by
    `write_generation`, below, and by `fim.persistence.jsonl_store.
    JSONLTrajectoryStore.write_generation`, to catch a row that claims
    to belong to a different run or generation than the batch it was
    handed in).

    Args:
        row: Mapping containing all six schema fields.
        run_id: Optional required run identity.
        generation: Optional required generation.

    Returns:
        A typed row with primitive values.
    """
    expected = {
        "run_id",
        "generation",
        "deme",
        "locus_id",
        "allele_id",
        "frequency",
    }
    missing = expected - set(row)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"trajectory row is missing: {names}")
    extra = set(row) - expected
    if extra:
        names = ", ".join(sorted(extra))
        raise ValueError(f"trajectory row has unknown fields: {names}")

    normalized_run_id = _string_field(row, "run_id")
    normalized_generation = _int_field(row, "generation", minimum=0)
    normalized = TrajectoryRow(
        run_id=normalized_run_id,
        generation=normalized_generation,
        deme=_int_field(row, "deme", minimum=1),
        locus_id=_int_field(row, "locus_id", minimum=1),
        allele_id=_int_field(row, "allele_id", minimum=0),
        frequency=_frequency_field(row),
    )
    if run_id is not None and normalized_run_id != run_id:
        raise ValueError(f"row run_id {normalized_run_id!r} does not match {run_id!r}")
    if generation is not None and normalized_generation != generation:
        raise ValueError(
            f"row generation {normalized_generation} does not match {generation}"
        )
    return normalized


def _frequency_field(row: Mapping[str, Any]) -> float:
    """Read one positive finite row frequency.

    Delegates to `fim.model.identifiers.parse_bounded_frequency`, the
    same rule `fim.model.state.ModelState.from_rows` uses for the
    identical row schema (S5/S6) — one shared validator for both
    readers of one row schema, rather than two independently
    maintained rules that can silently drift apart.
    """
    return parse_bounded_frequency(
        "trajectory row frequency must be in (0, 1]", row["frequency"]
    )


def _int_field(
    row: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
) -> int:
    """Read one bounded integer row field."""
    raw_value = row[key]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"trajectory row {key} must be an integer")
    if raw_value < minimum:
        raise ValueError(f"trajectory row {key} must be at least {minimum}")
    return int(raw_value)


def _string_field(row: Mapping[str, Any], key: str) -> str:
    """Read one nonempty string row field."""
    raw_value = row[key]
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"trajectory row {key} must be a nonempty string")
    return raw_value
