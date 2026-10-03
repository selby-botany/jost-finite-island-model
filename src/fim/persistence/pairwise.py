"""`pairwise.json`: every deme pair's Nei identities at a run's last generation.

The scatter plot compares two demes at a time, and the pair-scope Nei
statistics describe whichever pair is chosen. This file records them for
*every* pair, so a researcher can compare any pair later, or analyze the
whole matrix outside fim, without reopening the run in the GUI.

Format (version 1)::

    {
      "schema_version": 1,
      "generation": 1234,
      "deme_count": 4,
      "mode": "full",
      "encoding": "upper-triangle-row-major",
      "matrices": {
        "NEI_I_PAIR_GEO": [I_01, I_02, I_03, I_12, I_13, I_23],
        "NEI_I_PAIR_GEO_LOCUS_MEAN": [...],
        "NEI_I_PAIR_ARITH": [...],
        "NEI_I_PAIR_ARITH_LOCUS_MEAN": [...],
        "F_ST_PAIR": [...]
      }
    }

Each list is the strict upper triangle of a symmetric ``d x d`` matrix,
row by row (pair ``(0, 1)``, ``(0, 2)``, ..., ``(1, 2)``, ...); the
diagonal is known (1 for an identity, 0 for F_ST). For the Nei family only
identities are stored: they are always finite (a distance is infinite
when nothing is shared, and strict JSON has no infinity), and each
distance is exactly ``-ln(identity)``. A pairwise F_ST is ``null`` where it
is undefined (both demes fixed for the same allele).

Above the deme-count limit (`fim.statistics.catalog.
DEFAULT_PAIRWISE_MAX_DEMES` unless the researcher sets another) the file
records ``"mode": "skipped"`` and the limit instead of the matrices; any
specific pair can still be recomputed from `trajectory.jsonl`.

Size: five lists of ``d (d - 1) / 2`` numbers, about 20 bytes each in
compact JSON. At ``d = 1024`` that is about 52 MB per run (each replicate
of a batch writes its own); at ``d = 100``, about 0.5 MB.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from fim.model.state import ModelState
from fim.statistics.catalog import nei_key
from fim.statistics.genetic_distance import NEI_DENOMINATORS, NEI_LOCUS_RULES
from fim.statistics.pairwise import pairwise_matrices, upper_triangle

__all__ = [
    "PAIRWISE_SCHEMA_VERSION",
    "SLOW_PAIRWISE_DEMES",
    "pair_value",
    "pairwise_payload",
    "read_pairwise",
    "upper_triangle_index",
    "write_pairwise",
]

logger = logging.getLogger(__name__)

PAIRWISE_SCHEMA_VERSION: Final = 1

SLOW_PAIRWISE_DEMES: Final = 256
"""Deme count from which saving `pairwise.json` is slow enough to announce.

Below it the file takes a fraction of a second; at 1024 demes it is about
52 MB per run, dominated by writing the JSON, and a batch writes one per
replicate. The GUI shows a status line with a spinner while it saves.
"""


def upper_triangle_index(deme_count: int, first: int, second: int) -> int:
    """Return the position of pair ``(first, second)`` in an upper-triangle list.

    Args:
        deme_count: ``d``.
        first: Zero-based deme index.
        second: Zero-based deme index, different from `first` (order does
            not matter).

    Returns:
        The index into a `pairwise.json` identity list.

    Raises:
        ValueError: For equal or out-of-range indices.
    """
    low, high = sorted((first, second))
    if low == high or low < 0 or high >= deme_count:
        raise ValueError(
            f"pair ({first}, {second}) is not two distinct demes of {deme_count}"
        )
    return low * deme_count - low * (low + 1) // 2 + (high - low - 1)


def pairwise_payload(state: ModelState, *, max_demes: int) -> dict[str, Any]:
    """Return the `pairwise.json` content for `state`.

    Args:
        state: The population state (a run's final generation).
        max_demes: Largest deme count whose full matrices are computed.

    Returns:
        The version-1 payload described in the module docstring.
    """
    deme_count = state.deme_count
    header: dict[str, Any] = {
        "schema_version": PAIRWISE_SCHEMA_VERSION,
        "generation": state.generation,
        "deme_count": deme_count,
    }
    if deme_count > max_demes:
        logger.info(
            "pairwise.json: %d demes exceeds the %d-deme limit; matrices skipped",
            deme_count,
            max_demes,
        )
        return {**header, "mode": "skipped", "max_demes": max_demes}
    tables = [
        [state.frequency_map(deme, locus) for deme in range(deme_count)]
        for locus in range(state.locus_count)
    ]
    family, f_st = pairwise_matrices(tables)
    matrices: dict[str, list[float | None]] = {
        nei_key("identity", "pair", denominator, locus_rule): upper_triangle(
            family[(denominator, locus_rule)]
        )
        for denominator in NEI_DENOMINATORS
        for locus_rule in NEI_LOCUS_RULES
    }
    matrices["F_ST_PAIR"] = upper_triangle(f_st)
    return {
        **header,
        "mode": "full",
        "encoding": "upper-triangle-row-major",
        "matrices": matrices,
    }


def write_pairwise(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Write `payload` as compact, deterministic JSON.

    Compact (no indentation) rather than `write_report`'s two-space
    indentation, which would add a line and its indent per number and
    roughly double the size; still sorted keys, strict JSON, and one
    trailing newline, so identical results give identical bytes.

    Args:
        path: Destination; parent directories are created.
        payload: A `pairwise_payload` result.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    target.write_text(text + "\n", encoding="utf-8")


def read_pairwise(path: Path | str) -> dict[str, Any]:
    """Read a `pairwise.json`.

    Raises:
        ValueError: For an unknown schema version or a malformed file.
        OSError: If the file cannot be read.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("pairwise.json root must be an object")
    version = payload.get("schema_version")
    if version != PAIRWISE_SCHEMA_VERSION:
        raise ValueError(f"unsupported pairwise.json schema_version: {version!r}")
    return payload


def pair_value(
    payload: Mapping[str, Any], key: str, first: int, second: int
) -> float | None:
    """Return one pair's saved value, or `None` if the file has none.

    Args:
        payload: A `read_pairwise` result.
        key: A matrix key such as ``"NEI_I_PAIR_ARITH"`` or ``"F_ST_PAIR"``.
        first: Zero-based deme index.
        second: Zero-based deme index.

    Returns:
        The value (a deme with itself: identity 1, F_ST 0); `None` when the
        matrices were skipped for a large deme count, or the value is
        undefined (a `null` pairwise F_ST).
    """
    if payload.get("mode") != "full":
        return None
    if first == second:
        return 0.0 if key == "F_ST_PAIR" else 1.0
    values = payload["matrices"][key]
    value = values[upper_triangle_index(int(payload["deme_count"]), first, second)]
    return None if value is None else float(value)
