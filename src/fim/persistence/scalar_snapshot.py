"""Exact export and import of a scalar replicate's population and random state.

A checkpoint stores two parts: a JSON metadata document and a set of named
NumPy arrays (written as an `.npz` archive without pickling). This module
converts the population (`fim.model.state.ModelState`), the random-number
generator, and the allele-identity registries to and from those two parts,
exactly: restoring and continuing produces the same generations, bit for
bit, as never having stopped.

Exactness details that matter:

- Each sparse frequency map keeps its insertion order, because the
  operators visit alleles in that order when they draw random numbers.
- The 128-bit PCG64 state words are stored as decimal strings, because
  canonical JSON cannot carry integers above 2**53 exactly.
- Finite-allele capacities can exceed 64 bits (``4**length``), so they are
  stored as decimal strings too, and minted states keep minting order,
  including states minted and later lost (they still count as minted).

Every import validates shapes, types and cross-references before building
anything, and raises `SnapshotError` naming the first problem.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.state import ModelState

POPULATION_COUNTS: Final = "population.counts"
POPULATION_IDS: Final = "population.ids"
POPULATION_FREQUENCIES: Final = "population.frequencies"
FINITE_LOCUS_IDS: Final = "finite.locus_ids"
FINITE_COUNTS: Final = "finite.counts"
FINITE_MINTED: Final = "finite.minted"

Arrays = dict[str, npt.NDArray[Any]]


class SnapshotError(ValueError):
    """Raised when snapshot data is malformed or inconsistent."""


def export_population(state: ModelState) -> tuple[dict[str, Any], Arrays]:
    """Export a population to metadata and arrays.

    Args:
        state: The population to export.

    Returns:
        The metadata (generation, deme count, loci) and three arrays: one
        entry count per deme and locus, then every allele identity and
        frequency, concatenated in deme, locus and insertion order.
    """
    counts: list[int] = []
    ids: list[int] = []
    frequencies: list[float] = []
    for deme in state.frequencies:
        for frequency_map in deme:
            counts.append(len(frequency_map))
            for allele_id, frequency in frequency_map.items():
                ids.append(int(allele_id))
                frequencies.append(float(frequency))
    metadata = {
        "deme_count": state.deme_count,
        "generation": state.generation,
        "loci": [
            {"length": locus.length, "locus_id": locus.locus_id} for locus in state.loci
        ],
    }
    arrays: Arrays = {
        POPULATION_COUNTS: np.asarray(counts, dtype=np.int64),
        POPULATION_IDS: np.asarray(ids, dtype=np.int64),
        POPULATION_FREQUENCIES: np.asarray(frequencies, dtype=np.float64),
    }
    return metadata, arrays


def export_registries(
    registry: AlleleRegistry, finite_alleles: FiniteAlleleRegistry | None
) -> tuple[dict[str, Any], Arrays]:
    """Export the allele-identity registries to metadata and arrays.

    Args:
        registry: The infinite-alleles identity counter.
        finite_alleles: The finite-alleles spaces, or `None` under the
            infinite-alleles model.

    Returns:
        The metadata (counter, and per finite locus its capacity and inert
        `next_unminted`) and, under finite alleles, the minted states of
        every locus in minting order.
    """
    metadata: dict[str, Any] = {"next_allele_id": registry.next_value}
    if finite_alleles is None:
        metadata["finite"] = None
        return metadata, {}
    locus_ids: list[int] = []
    counts: list[int] = []
    minted: list[int] = []
    loci_metadata: list[dict[str, Any]] = []
    for locus_id in sorted(finite_alleles.locus_ids):
        space = finite_alleles.space_for(locus_id)
        states = space.minted_states
        locus_ids.append(locus_id)
        counts.append(len(states))
        minted.extend(int(state) for state in states)
        loci_metadata.append(
            {
                "capacity": str(space.capacity),
                "locus_id": locus_id,
                "next_unminted": space.next_unminted,
            }
        )
    metadata["finite"] = loci_metadata
    arrays: Arrays = {
        FINITE_LOCUS_IDS: np.asarray(locus_ids, dtype=np.int64),
        FINITE_COUNTS: np.asarray(counts, dtype=np.int64),
        FINITE_MINTED: np.asarray(minted, dtype=np.int64),
    }
    return metadata, arrays


def export_rng(rng: np.random.Generator) -> dict[str, Any]:
    """Export a PCG64 generator's exact state as JSON-safe metadata.

    Args:
        rng: The generator to export.

    Returns:
        The bit generator's name, its two 128-bit words as decimal strings,
        and its buffered 32-bit draw.

    Raises:
        SnapshotError: If the generator is not PCG64.
    """
    state = rng.bit_generator.state
    if state.get("bit_generator") != "PCG64":
        raise SnapshotError(
            "only PCG64 generators can be checkpointed; got "
            f"{state.get('bit_generator')!r}"
        )
    return {
        "bit_generator": "PCG64",
        "has_uint32": int(state["has_uint32"]),
        "inc": str(int(state["state"]["inc"])),
        "state": str(int(state["state"]["state"])),
        "uinteger": int(state["uinteger"]),
    }


def import_population(
    metadata: Mapping[str, Any], arrays: Mapping[str, npt.NDArray[Any]]
) -> ModelState:
    """Rebuild a population exported by `export_population`.

    Args:
        metadata: The exported metadata.
        arrays: The exported arrays.

    Returns:
        The identical population, with every map in its original order.

    Raises:
        SnapshotError: If anything is missing, mistyped or inconsistent.
    """
    generation = _require_int(metadata, "generation", 0)
    deme_count = _require_int(metadata, "deme_count", 1)
    loci = _import_loci(metadata.get("loci"))
    counts = _require_array(arrays, POPULATION_COUNTS, np.int64)
    ids = _require_array(arrays, POPULATION_IDS, np.int64)
    frequencies = _require_array(arrays, POPULATION_FREQUENCIES, np.float64)
    if counts.shape != (deme_count * len(loci),):
        raise SnapshotError(
            f"{POPULATION_COUNTS} has {counts.size} entries; expected "
            f"{deme_count * len(loci)}"
        )
    if np.any(counts < 1):
        raise SnapshotError("every deme and locus must hold at least one allele")
    total = int(counts.sum())
    if ids.shape != (total,) or frequencies.shape != (total,):
        raise SnapshotError(
            f"population arrays disagree: {total} counted entries, "
            f"{ids.size} identities and {frequencies.size} frequencies"
        )
    if np.any(ids < 0) or not np.all(np.isfinite(frequencies)):
        raise SnapshotError("population holds a negative identity or bad frequency")
    demes: list[tuple[dict[AlleleId, float], ...]] = []
    cursor = 0
    for deme_index in range(deme_count):
        maps: list[dict[AlleleId, float]] = []
        for locus_index in range(len(loci)):
            count = int(counts[deme_index * len(loci) + locus_index])
            segment = range(cursor, cursor + count)
            frequency_map = {
                AlleleId(int(ids[position])): float(frequencies[position])
                for position in segment
            }
            if len(frequency_map) != count:
                raise SnapshotError(
                    f"deme {deme_index + 1}, locus {loci[locus_index].locus_id} "
                    "repeats an allele identity"
                )
            maps.append(frequency_map)
            cursor += count
        demes.append(tuple(maps))
    try:
        return ModelState(loci=loci, frequencies=tuple(demes), generation=generation)
    except ValueError as error:
        raise SnapshotError(f"population is not a valid state: {error}") from error


def import_registries(
    metadata: Mapping[str, Any],
    arrays: Mapping[str, npt.NDArray[Any]],
    state: ModelState,
) -> tuple[AlleleRegistry, FiniteAlleleRegistry | None]:
    """Rebuild the registries exported by `export_registries`.

    Args:
        metadata: The exported metadata.
        arrays: The exported arrays.
        state: The restored population, checked against the registries so
            that no identity in use can be minted again.

    Returns:
        The identity counter and, under finite alleles, the finite spaces.

    Raises:
        SnapshotError: If anything is missing, mistyped or inconsistent.
    """
    next_allele_id = _require_int(metadata, "next_allele_id", MINTED_ID_START)
    if any(
        int(allele_id) >= next_allele_id
        for deme in state.frequencies
        for frequency_map in deme
        for allele_id in frequency_map
        if int(allele_id) >= MINTED_ID_START
    ):
        raise SnapshotError(
            "a minted identity in the population is not below the registry counter"
        )
    registry = AlleleRegistry(start=next_allele_id)
    finite_metadata = metadata.get("finite")
    if finite_metadata is None:
        return registry, None
    return registry, _import_finite(finite_metadata, arrays, state)


def import_rng(metadata: Mapping[str, Any]) -> np.random.Generator:
    """Rebuild a generator exported by `export_rng`.

    Args:
        metadata: The exported metadata.

    Returns:
        A PCG64 generator whose next draws match the exported one's.

    Raises:
        SnapshotError: If anything is missing or malformed.
    """
    if metadata.get("bit_generator") != "PCG64":
        raise SnapshotError("random state must be a PCG64 generator")
    words = {name: _require_decimal(metadata, name) for name in ("state", "inc")}
    if any(word >= 1 << 128 for word in words.values()):
        raise SnapshotError("random state words must fit in 128 bits")
    has_uint32 = _require_int(metadata, "has_uint32", 0)
    uinteger = _require_int(metadata, "uinteger", 0)
    if has_uint32 > 1 or uinteger >= 1 << 32:
        raise SnapshotError("random state buffered draw is out of range")
    bit_generator = np.random.PCG64()
    exact_state: Any = {
        "bit_generator": "PCG64",
        "has_uint32": has_uint32,
        "state": words,
        "uinteger": uinteger,
    }
    bit_generator.state = exact_state
    return np.random.Generator(bit_generator)


def _import_finite(
    loci_metadata: object,
    arrays: Mapping[str, npt.NDArray[Any]],
    state: ModelState,
) -> FiniteAlleleRegistry:
    """Rebuild the finite-allele spaces, validated against the population."""
    if not isinstance(loci_metadata, list):
        raise SnapshotError("finite registry metadata must be a list")
    locus_ids = _require_array(arrays, FINITE_LOCUS_IDS, np.int64)
    counts = _require_array(arrays, FINITE_COUNTS, np.int64)
    minted = _require_array(arrays, FINITE_MINTED, np.int64)
    expected_ids = sorted(locus.locus_id for locus in state.loci)
    if [int(value) for value in locus_ids] != expected_ids or len(loci_metadata) != len(
        expected_ids
    ):
        raise SnapshotError("finite registry loci do not match the population's")
    if counts.shape != locus_ids.shape or int(counts.sum()) != minted.size:
        raise SnapshotError("finite registry arrays disagree in length")
    lengths = {locus.locus_id: locus.length for locus in state.loci}
    index_of = {locus.locus_id: index for index, locus in enumerate(state.loci)}
    spaces: dict[int, FiniteAlleleSpace] = {}
    cursor = 0
    for position, entry in enumerate(loci_metadata):
        if not isinstance(entry, Mapping):
            raise SnapshotError("each finite registry locus must be an object")
        locus_id = _require_int(entry, "locus_id", 1)
        if locus_id != int(locus_ids[position]):
            raise SnapshotError("finite registry loci are out of order")
        capacity = _require_decimal(entry, "capacity")
        if capacity != finite_allele_capacity(lengths[locus_id]):
            raise SnapshotError(f"locus {locus_id} capacity does not match its length")
        count = int(counts[position])
        states = [int(value) for value in minted[cursor : cursor + count]]
        cursor += count
        if len(set(states)) != count or any(
            value < 0 or value >= capacity for value in states
        ):
            raise SnapshotError(
                f"locus {locus_id} minted states repeat or fall outside its capacity"
            )
        present = {
            int(allele_id)
            for deme in state.frequencies
            for allele_id in deme[index_of[locus_id]]
        }
        if not present <= set(states):
            raise SnapshotError(
                f"locus {locus_id} holds an allele its registry never minted"
            )
        space = FiniteAlleleSpace(capacity, ())
        space.restore_minted(
            [AlleleId(value) for value in states],
            _require_int(entry, "next_unminted", 0),
        )
        spaces[locus_id] = space
    return FiniteAlleleRegistry(spaces)


def _import_loci(value: object) -> tuple[LocusSpec, ...]:
    """Parse the exported locus list."""
    if not isinstance(value, list) or not value:
        raise SnapshotError("population loci must be a non-empty list")
    loci: list[LocusSpec] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise SnapshotError("each population locus must be an object")
        try:
            loci.append(
                LocusSpec(
                    _require_int(entry, "locus_id", 1), _require_int(entry, "length", 1)
                )
            )
        except ValueError as error:
            raise SnapshotError(f"invalid locus: {error}") from error
    return tuple(loci)


def _require_array(
    arrays: Mapping[str, npt.NDArray[Any]], name: str, dtype: type[np.generic]
) -> npt.NDArray[Any]:
    """Return a one-dimensional array of exactly `dtype`, or raise."""
    array = arrays.get(name)
    if not isinstance(array, np.ndarray):
        raise SnapshotError(f"snapshot array {name!r} is missing")
    if array.dtype != np.dtype(dtype) or array.ndim != 1:
        raise SnapshotError(
            f"snapshot array {name!r} must be one-dimensional {np.dtype(dtype)}; "
            f"got {array.ndim}-dimensional {array.dtype}"
        )
    return array


def _require_decimal(mapping: Mapping[str, Any], name: str) -> int:
    """Return a non-negative integer stored as a decimal string, or raise."""
    value = mapping.get(name)
    if not isinstance(value, str) or not value.isdecimal() or not value.isascii():
        raise SnapshotError(f"{name!r} must be a decimal string")
    return int(value)


def _require_int(mapping: Mapping[str, Any], name: str, minimum: int) -> int:
    """Return an integer field at least `minimum`, or raise."""
    value = mapping.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SnapshotError(f"{name!r} must be an integer of at least {minimum}")
    return value
