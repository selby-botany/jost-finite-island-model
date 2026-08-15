"""Backend-independent trajectory row schema and store protocol."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Protocol, TypedDict


class TrajectoryRow(TypedDict):
    """One nonzero allele-frequency observation."""

    run_id: str
    generation: int
    deme: int
    locus_id: int
    allele_id: int
    frequency: float


class TrajectoryStore(Protocol):
    """Incrementally persist and iterate long-form trajectory rows."""

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Persist all rows for one generation."""
        ...

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield rows for one run in stored order."""
        ...


class InMemoryTrajectoryStore:
    """Store trajectories in memory for library calls and focused tests."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._rows: list[TrajectoryRow] = []

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Append one validated generation."""
        generation_rows = [
            normalize_row(row, run_id=run_id, generation=generation) for row in rows
        ]
        if not generation_rows:
            raise ValueError("a generation must contain at least one row")
        self._rows.extend(generation_rows)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield rows matching ``run_id`` in insertion order."""
        return (row.copy() for row in self._rows if row["run_id"] == run_id)


def normalize_row(
    row: Mapping[str, Any],
    *,
    run_id: str | None = None,
    generation: int | None = None,
) -> TrajectoryRow:
    """Validate and normalize one public-schema trajectory row.

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
    """Read one positive finite row frequency."""
    raw_value = row["frequency"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError("trajectory row frequency must be numeric")
    value = float(raw_value)
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise ValueError("trajectory row frequency must be in (0, 1]")
    return value


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
