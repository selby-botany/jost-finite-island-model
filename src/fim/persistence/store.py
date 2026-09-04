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
engine`'s own five internal call sites pass `validate=False`
specifically because each one is provably in this position; no other
caller in this codebase does, and a new one should not either without
the same proof.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Protocol, TypedDict, cast

from fim.model.identifiers import parse_bounded_frequency


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
