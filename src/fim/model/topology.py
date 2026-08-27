"""Sparse migration-topology construction and densification.

A migration matrix for a spatial (stepping-stone) topology is mostly
zero — every deme migrates with a handful of neighbors, not with the
whole population — so writing it out as a full ``d`` by ``d`` matrix by
hand stops being practical once ``d`` grows past a handful of demes. This
module works in the more natural sparse shape instead: a one-based map
from each deme to the neighbors it exchanges migrants with and at what
weight, with each deme's self-retention left implicit (the complement of
its listed weights, exactly as the symmetric island model already treats
`m`). ``dense_matrix_from_neighbors`` is the one place that turns such a
map, from any source, into the fully validated dense matrix
``fim.model.operators.migrate`` already knows how to use — the rest of
the simulator never needs to know a sparse map was ever involved.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final, Literal

MINIMUM_DEMES: Final = 2
MINIMUM_RING_DEMES: Final = 3
Topology = Literal["ring", "linear"]
_TOPOLOGIES: Final = frozenset({"ring", "linear"})


def stepping_stone_neighbors(
    d: int,
    *,
    topology: Topology,
    rate: float,
) -> dict[int, dict[int, float]]:
    """Build a sparse nearest-neighbor migration map.

    A "stepping-stone" topology is the standard population-genetics
    term for demes arranged along a line or a circle, each exchanging
    migrants only with its immediate one or two neighbors — as opposed
    to the symmetric island model's assumption that every deme
    exchanges migrants equally with every other deme, regardless of
    "distance." This function builds exactly that neighbor structure,
    in the sparse form `dense_matrix_from_neighbors`, below, then
    expands into the full matrix the rest of the simulator actually uses.

    Args:
        d: Number of demes, numbered ``1`` through ``d`` along the line
            or ring.
        topology: ``"ring"`` wraps deme ``d``'s next neighbor back to
            deme ``1``; ``"linear"`` is a bounded chain where the two end
            demes have only one neighbor instead of two.
        rate: Every deme's total outgoing migration fraction, split evenly
            among its actual neighbors — the same meaning ``m`` already
            has in the symmetric island model (§4.3), applied locally
            instead of globally. Each deme keeps ``1 - rate`` of its own
            frequency; that self-retention is implicit, matching the
            general sparse map's own off-diagonal-only convention.

    Returns:
        A one-based sparse map: ``{deme: {neighbor: weight, ...}, ...}``.
        A deme with no neighbors in this topology is simply absent.

    Raises:
        ValueError: If ``d``, ``topology``, or ``rate`` is invalid.
    """
    if d < MINIMUM_DEMES:
        raise ValueError("d must be at least 2")
    if topology not in _TOPOLOGIES:
        allowed = ", ".join(sorted(_TOPOLOGIES))
        raise ValueError(f"topology must be one of: {allowed}")
    if topology == "ring" and d < MINIMUM_RING_DEMES:
        raise ValueError("a ring topology needs at least 3 demes")
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be between 0 and 1")

    return {
        deme: _neighbor_weights(deme, d, topology, rate) for deme in range(1, d + 1)
    }


def dense_matrix_from_neighbors(
    neighbors: Mapping[int, Mapping[int, float]],
    d: int,
) -> tuple[tuple[float, ...], ...]:
    """Densify a one-based sparse off-diagonal map into a full matrix.

    "Densify" means turning the compact, mostly-implicit sparse map
    (which lists only which demes migrate with which, and skips every
    zero entry) into the complete `d` by `d` matrix `fim.model.
    operators.migrate` actually operates on, where every deme's row
    (including its own self-retention) is spelled out explicitly, zero
    entries included. This is the one point where a sparse topology,
    from any source, becomes the ordinary dense form the rest of the
    simulator already knows how to use — nothing downstream of this
    function ever needs to know a sparse representation was involved.

    Every deme's self-retention is derived, not read from the map: a
    deme's diagonal entry is ``1`` minus the sum of its listed neighbor
    weights, exactly how the scalar and topology-generated forms of ``m``
    already define self-retention. A deme absent from ``neighbors``
    migrates with nobody — its row is the identity row.

    Args:
        neighbors: A one-based sparse map, as returned by
            ``stepping_stone_neighbors`` or written out by hand.
        d: Number of demes; every key and neighbor id must fall in
            ``1..d``.

    Returns:
        A ``d`` by ``d`` row-stochastic matrix suitable for ``m``.

    Raises:
        ValueError: If a deme or neighbor id is outside ``1..d``, a deme
            lists itself as its own neighbor, a weight is outside
            ``[0, 1]``, or one deme's weights sum to more than ``1``.
    """
    out_of_range = [deme for deme in neighbors if not 1 <= deme <= d]
    if out_of_range:
        names = ", ".join(str(deme) for deme in sorted(out_of_range))
        raise ValueError(f"m deme(s) outside 1..{d}: {names}")

    rows: list[tuple[float, ...]] = []
    for deme in range(1, d + 1):
        row_weights = neighbors.get(deme, {})
        row = [0.0] * d
        total = 0.0
        for neighbor, weight in row_weights.items():
            if not 1 <= neighbor <= d:
                raise ValueError(f"m[{deme}] neighbor {neighbor} is outside 1..{d}")
            if neighbor == deme:
                raise ValueError(f"m[{deme}] cannot list itself as a neighbor")
            if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
                raise ValueError(f"m[{deme}][{neighbor}] must be between 0 and 1")
            row[neighbor - 1] = weight
            total += weight
        if total > 1.0 and not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"m[{deme}] neighbor weights sum to more than 1")
        row[deme - 1] = max(0.0, 1.0 - total)
        rows.append(tuple(row))
    return tuple(rows)


def _neighbor_weights(
    deme: int,
    d: int,
    topology: Topology,
    rate: float,
) -> dict[int, float]:
    """Return one deme's neighbor-to-weight map, split evenly."""
    if topology == "ring":
        candidates: tuple[int, ...] = (_wrap(deme - 1, d), _wrap(deme + 1, d))
    else:
        candidates = tuple(
            candidate for candidate in (deme - 1, deme + 1) if 1 <= candidate <= d
        )
    weight = rate / len(candidates)
    return dict.fromkeys(candidates, weight)


def _wrap(deme: int, d: int) -> int:
    """Wrap a one-based deme index around a ring of size ``d``."""
    return (deme - 1) % d + 1
