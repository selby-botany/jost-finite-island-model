"""Sparse, validated state representation for every deme and locus."""

from __future__ import annotations

import copyreg
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fim.model.allele import AlleleId
from fim.model.identifiers import parse_bounded_frequency, parse_integer_identifier
from fim.model.locus import LocusSpec

FrequencyMap = Mapping[AlleleId, float]
MutableFrequencyMap = dict[AlleleId, float]

FREQUENCY_ABS_TOLERANCE = 1e-12


def _construct_mapping_proxy(mapping: dict[Any, Any]) -> MappingProxyType[Any, Any]:
    """Rebuild a `MappingProxyType` from its unpickled backing `dict`.

    A module-level function, not `MappingProxyType` itself: pickle
    resolves a reduced object's constructor by module and name, and the
    built-in `mappingproxy` type reports its module as ``builtins``,
    where that name does not actually exist — pickling a bare
    `MappingProxyType` reference fails even with a reducer registered.
    """
    return MappingProxyType(mapping)


_MappingProxyConstructor = Callable[[dict[Any, Any]], MappingProxyType[Any, Any]]
_MappingProxyReduction = tuple[_MappingProxyConstructor, tuple[dict[Any, Any]]]


def _reduce_mapping_proxy(proxy: MappingProxyType[Any, Any]) -> _MappingProxyReduction:
    """Round-trip a `MappingProxyType` through `dict`, its only pickle gap."""
    return (_construct_mapping_proxy, (dict(proxy),))


# `ModelState.frequencies` wraps every deme/locus frequency map in a
# `MappingProxyType` for enforced immutability, but the standard library's
# `pickle` has no built-in support for that type (`TypeError: cannot
# pickle 'mappingproxy' object`). `fim.engine`'s opt-in parallel replicate
# execution (`max_workers`) sends whole `RunResult`s — which nest a
# `ModelState`, and `SimulationParams.initial_frequencies`, which uses the
# same wrapper — across a process boundary, so this process-wide
# `copyreg` registration is required for that to work at all. It is a
# property of the type, not of any one caller, so it is registered here
# once at import time rather than duplicated wherever `MappingProxyType`
# is used.
copyreg.pickle(MappingProxyType, _reduce_mapping_proxy)


@dataclass(frozen=True, slots=True)
class ModelState:
    """Hold sparse allele frequencies for each deme and locus.

    Args:
        loci: Ordered locus descriptions shared by every deme.
        frequencies: A deme-major, then locus-major collection of sparse maps.
        generation: Non-negative generation represented by this state.
    """

    loci: tuple[LocusSpec, ...]
    frequencies: tuple[tuple[FrequencyMap, ...], ...]
    generation: int = 0

    def __post_init__(self) -> None:
        """Normalize maps and enforce the probability-vector invariant."""
        loci = tuple(self.loci)
        if not loci:
            raise ValueError("state must contain at least one locus")
        if len({locus.locus_id for locus in loci}) != len(loci):
            raise ValueError("state locus IDs must be unique")
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if not self.frequencies:
            raise ValueError("state must contain at least one deme")

        normalized_demes: list[tuple[FrequencyMap, ...]] = []
        for deme_index, deme in enumerate(self.frequencies, start=1):
            if len(deme) != len(loci):
                raise ValueError(
                    f"deme {deme_index} has {len(deme)} loci; expected {len(loci)}"
                )
            normalized_loci: list[FrequencyMap] = []
            for locus, frequency_map in zip(loci, deme, strict=True):
                normalized_loci.append(
                    MappingProxyType(
                        _normalize_frequency_map(
                            frequency_map,
                            context=(f"deme {deme_index}, locus {locus.locus_id}"),
                        )
                    )
                )
            normalized_demes.append(tuple(normalized_loci))

        object.__setattr__(self, "loci", loci)
        object.__setattr__(self, "frequencies", tuple(normalized_demes))

    @property
    def deme_count(self) -> int:
        """Return the number of demes represented by this state."""
        return len(self.frequencies)

    @property
    def locus_count(self) -> int:
        """Return the number of tracked loci."""
        return len(self.loci)

    def frequency_map(self, deme: int, locus_index: int) -> FrequencyMap:
        """Return one read-only sparse frequency map.

        Args:
            deme: Zero-based deme index.
            locus_index: Zero-based locus index.

        Returns:
            The requested allele-to-frequency mapping.
        """
        return self.frequencies[deme][locus_index]

    def support_sizes(self) -> tuple[tuple[int, ...], ...]:
        """Return the number of present alleles for each deme and locus."""
        return tuple(
            tuple(len(frequency_map) for frequency_map in deme)
            for deme in self.frequencies
        )

    def to_rows(self, run_id: str) -> list[dict[str, int | float | str]]:
        """Serialize the state to the public long-form trajectory row schema.

        Args:
            run_id: Stable identifier grouping rows from one simulation.

        Returns:
            One row for every nonzero allele frequency.
        """
        if not run_id:
            raise ValueError("run_id must not be empty")
        rows: list[dict[str, int | float | str]] = []
        for deme_index, deme in enumerate(self.frequencies, start=1):
            for locus, frequency_map in zip(self.loci, deme, strict=True):
                for allele_id, frequency in frequency_map.items():
                    rows.append(
                        {
                            "run_id": run_id,
                            "generation": self.generation,
                            "deme": deme_index,
                            "locus_id": locus.locus_id,
                            "allele_id": int(allele_id),
                            "frequency": frequency,
                        }
                    )
        return rows

    def total_frequency(self) -> dict[tuple[int, int], float]:
        """Return the frequency sum for every one-based deme and locus ID."""
        totals: dict[tuple[int, int], float] = {}
        for deme_index, deme in enumerate(self.frequencies, start=1):
            for locus, frequency_map in zip(self.loci, deme, strict=True):
                totals[(deme_index, locus.locus_id)] = math.fsum(frequency_map.values())
        return totals

    def validate_support(self, population_sizes: Sequence[int]) -> None:
        """Reject support that exceeds the available gene-copy count.

        Args:
            population_sizes: One positive gene-copy count per deme.

        Raises:
            ValueError: If the shape differs or a support is too large.
        """
        if len(population_sizes) != self.deme_count:
            raise ValueError("population_sizes must contain one value for every deme")
        for deme_index, (deme, size) in enumerate(
            zip(self.frequencies, population_sizes, strict=True),
            start=1,
        ):
            for locus, frequency_map in zip(self.loci, deme, strict=True):
                if len(frequency_map) > size:
                    raise ValueError(
                        f"deme {deme_index}, locus {locus.locus_id} has "
                        f"{len(frequency_map)} alleles but N is {size}"
                    )

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        loci: Sequence[LocusSpec],
    ) -> ModelState:
        """Reconstruct one generation from long-form trajectory rows.

        Args:
            rows: Rows for exactly one run and generation.
            loci: Locus descriptions in the desired state order.

        Returns:
            A validated sparse model state.

        Raises:
            ValueError: If rows are empty, inconsistent, duplicated, or malformed.
        """
        row_list = list(rows)
        if not row_list:
            raise ValueError("cannot construct ModelState from no rows")
        locus_tuple = tuple(loci)
        locus_positions = {
            locus.locus_id: index for index, locus in enumerate(locus_tuple)
        }
        if not locus_positions:
            raise ValueError("loci must not be empty")

        generations = {_required_int(row, "generation") for row in row_list}
        if len(generations) != 1:
            raise ValueError("rows must describe exactly one generation")
        run_ids = {_required_string(row, "run_id") for row in row_list}
        if len(run_ids) != 1:
            raise ValueError("rows must describe exactly one run")

        deme_numbers = {_required_int(row, "deme") for row in row_list}
        if not deme_numbers or min(deme_numbers) != 1:
            raise ValueError("deme numbering must start at 1")
        expected_demes = set(range(1, max(deme_numbers) + 1))
        if deme_numbers != expected_demes:
            raise ValueError("deme numbering must be contiguous")

        mutable: list[list[MutableFrequencyMap]] = [
            [{} for _ in locus_tuple] for _ in expected_demes
        ]
        for row in row_list:
            deme = _required_int(row, "deme")
            locus_id = _required_int(row, "locus_id")
            try:
                locus_index = locus_positions[locus_id]
            except KeyError as error:
                raise ValueError(f"unknown locus_id in row: {locus_id}") from error
            allele_id = AlleleId(_required_int(row, "allele_id"))
            frequency = _required_frequency(row, "frequency")
            target = mutable[deme - 1][locus_index]
            if allele_id in target:
                raise ValueError(
                    f"duplicate row for deme {deme}, locus {locus_id}, "
                    f"allele {int(allele_id)}"
                )
            target[allele_id] = frequency

        return cls(
            loci=locus_tuple,
            frequencies=tuple(tuple(deme) for deme in mutable),
            generation=generations.pop(),
        )


def _normalize_frequency_map(
    frequency_map: Mapping[AlleleId, float],
    *,
    context: str,
) -> MutableFrequencyMap:
    """Copy and validate one sparse probability vector.

    Allele identity uses the same `parse_integer_identifier` rule as
    the config parser's own `p_0` handling — this is `ModelState`'s
    public constructor path, reachable by a downstream embedder
    directly (not only through YAML config), so it needs the identical
    guard against a truncated float (`1.9` silently becoming `1`) or a
    negative identifier sneaking through as a bare Python key (S5).
    """
    normalized: MutableFrequencyMap = {}
    for raw_allele_id, raw_frequency in frequency_map.items():
        identity = parse_integer_identifier(
            f"{context}: allele ID {raw_allele_id!r} must be an integer",
            raw_allele_id,
        )
        if identity < 0:
            raise ValueError(
                f"{context}: allele ID {raw_allele_id!r} must be a "
                "non-negative integer"
            )
        allele_id = AlleleId(identity)
        frequency = float(raw_frequency)
        if not math.isfinite(frequency):
            raise ValueError(f"{context}: frequencies must be finite")
        if frequency < 0.0:
            raise ValueError(f"{context}: frequencies must be non-negative")
        if frequency > 0.0:
            normalized[allele_id] = frequency
    if not normalized:
        raise ValueError(f"{context}: at least one allele must be present")
    total = math.fsum(normalized.values())
    if not math.isclose(
        total,
        1.0,
        rel_tol=0.0,
        abs_tol=FREQUENCY_ABS_TOLERANCE,
    ):
        raise ValueError(f"{context}: frequencies sum to {total}, not 1")
    return normalized


def _required_frequency(row: Mapping[str, Any], key: str) -> float:
    """Read one required row frequency, in ``(0, 1]`` and never a boolean.

    Delegates to `parse_bounded_frequency`, the same rule
    `fim.persistence.store.normalize_row` uses for the identical row
    schema — before this fix, this was a bare ``float(row[key])`` that
    accepted `True` (coerced to ``1.0``) and a numeric string like
    ``"1"``, both of which persistence's own reader already rejected
    for the same field (S6).
    """
    if key not in row:
        raise ValueError(f"trajectory row is missing {key!r}")
    return parse_bounded_frequency(
        f"trajectory row field {key!r} must be in (0, 1]", row[key]
    )


def _required_int(row: Mapping[str, Any], key: str) -> int:
    """Read one required integer from a serialized row."""
    if key not in row:
        raise ValueError(f"trajectory row is missing {key!r}")
    raw_value = row[key]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"trajectory row field {key!r} must be an integer")
    return int(raw_value)


def _required_string(row: Mapping[str, Any], key: str) -> str:
    """Read one required nonempty string from a serialized row."""
    if key not in row:
        raise ValueError(f"trajectory row is missing {key!r}")
    raw_value = row[key]
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"trajectory row field {key!r} must be a string")
    return raw_value
