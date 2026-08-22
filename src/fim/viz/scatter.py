"""Canonical deme-coordinate allele-frequency scatter plots."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast

import matplotlib

matplotlib.use("Agg", force=True)

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from numpy.typing import NDArray

from fim.model.allele import AlleleId
from fim.model.params import SimulationParams
from fim.model.state import ModelState

FloatArray: TypeAlias = NDArray[np.float64]

PAIRWISE_MAX_DEMES = 6
COMMON_ALLELE_THRESHOLD = 0.05
DIRECT_2D_DEMES = 2
DIRECT_3D_DEMES = 3
MINIMUM_PAIRWISE_MAX_DEMES = 4


def plot_frequency_scatter(
    state: ModelState,
    params: SimulationParams,
    path: Path | str | None = None,
    *,
    pairwise_max_demes: int = PAIRWISE_MAX_DEMES,
) -> Figure:
    """Plot one point per locus/allele in deme-frequency coordinate space.

    Args:
        state: State to visualize.
        params: Parameters used for title metadata.
        path: Optional PNG output path.
        pairwise_max_demes: Largest ``d`` rendered as a pairwise matrix.

    Returns:
        The created Matplotlib figure.
    """
    if state.deme_count != params.d:
        raise ValueError("state deme count does not match params.d")
    if pairwise_max_demes < MINIMUM_PAIRWISE_MAX_DEMES:
        raise ValueError("pairwise_max_demes must be at least 4")
    points = frequency_points(state)
    if state.deme_count == DIRECT_2D_DEMES:
        figure = _plot_two_dimensional(points)
    elif state.deme_count == DIRECT_3D_DEMES:
        figure = _plot_three_dimensional(points)
    elif state.deme_count <= pairwise_max_demes:
        figure = _plot_pairwise(points, state.deme_count)
    else:
        figure = _plot_pca(points)
    figure.suptitle(_title(params))
    figure.tight_layout()
    if path is not None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(
            output_path,
            dpi=150,
            metadata={"Software": "fim"},
        )
    return figure


def frequency_points(state: ModelState) -> FloatArray:
    """Return one row per locus/allele and one column per deme.

    Public (graphical-interface migration design doc §3.3, §3.5): the
    GUI's bridge calls this directly to get raw scatter coordinates for
    client-side rendering, without going through `plot_frequency_scatter`
    at all — it never builds a `Figure`. `plot_frequency_scatter` itself
    still calls this internally for the CLI's own `scatter.png`; nothing
    about that path changes.
    """
    locus_allele_pairs: list[tuple[int, AlleleId]] = []
    for locus_index in range(state.locus_count):
        allele_ids: dict[AlleleId, None] = {}
        for deme_index in range(state.deme_count):
            for allele_id in state.frequency_map(deme_index, locus_index):
                allele_ids.setdefault(allele_id, None)
        locus_allele_pairs.extend((locus_index, allele_id) for allele_id in allele_ids)
    if not locus_allele_pairs:  # pragma: no cover
        # Unreachable through any validly constructed ModelState:
        # `ModelState.__post_init__` already requires at least one locus,
        # at least one deme, and every per-deme-locus frequency map to sum
        # to 1 (so it cannot be empty) -- guarded defensively rather than
        # asserted, so a future relaxation of that invariant fails loudly
        # here instead of returning a meaningless empty plot.
        raise ValueError("state contains no allele-frequency points")
    point_rows = [
        [
            state.frequency_map(deme_index, locus_index).get(
                allele_id,
                0.0,
            )
            for deme_index in range(state.deme_count)
        ]
        for locus_index, allele_id in locus_allele_pairs
    ]
    return np.asarray(point_rows, dtype=np.float64)


def pooled_frequency_points(states: Sequence[ModelState]) -> FloatArray:
    """Pool several states' `frequency_points` into one combined array.

    Public (graphical-interface migration design doc §0.5, §3.3): the
    GUI's live/batch-results bridge methods call this to build the
    pooled, multi-replicate overlay scatter the reference visualization
    (Lou Jost's `Dear-NolanMarch17Final.pdf` Figs. 1-2) uses — the
    frequency of each allele in one deme plotted against another,
    pooled across every replicate run, not one run's own loci/alleles
    alone. `frequency_points` already returns one row per (locus,
    allele) pair for a single state; this concatenates that same
    per-state result across several states (independent replicates, or
    the same replicate sampled at different generations) before
    `marker_groups` groups the pooled rows — coincidence counting then
    treats a point shared by two replicates exactly the same way it
    already treats a point shared by two loci within one replicate, no
    special case needed either way.

    Args:
        states: One or more states sharing the same deme count.

    Returns:
        The row-wise concatenation of `frequency_points(state)` for
        every state, in the given order. Empty (zero rows, but still
        correctly shaped) if `states` is empty.
    """
    if not states:
        return np.empty((0, 0), dtype=np.float64)
    return np.concatenate([frequency_points(state) for state in states], axis=0)


def marker_groups(
    coordinates: Sequence[tuple[float, float]],
) -> tuple[FloatArray, FloatArray, list[str], list[str]]:
    """Collapse coincident points and derive marker sizes, colors, and labels.

    Public (graphical-interface migration design doc §3.3, §3.5): the
    GUI's bridge calls this directly, over `pooled_frequency_points`'s
    output as readily as over one state's own `frequency_points` output
    — coincidence counting has no notion of where a point came from.
    """
    counts = Counter(coordinates)
    unique = np.asarray(tuple(counts), dtype=np.float64)
    sizes = np.asarray(
        [30.0 + 18.0 * math.sqrt(counts[point]) for point in counts],
        dtype=np.float64,
    )
    colors = [
        "tab:blue" if max(point) >= COMMON_ALLELE_THRESHOLD else "tab:orange"
        for point in counts
    ]
    labels = [str(counts[point]) if counts[point] > 1 else "" for point in counts]
    return unique, sizes, colors, labels


def _plot_pairwise(points: FloatArray, deme_count: int) -> Figure:
    """Render every pair of deme dimensions."""
    pairs = [
        (first, second)
        for first in range(deme_count)
        for second in range(first + 1, deme_count)
    ]
    columns = min(3, len(pairs))
    rows = math.ceil(len(pairs) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(4.5 * columns, 4.0 * rows),
        squeeze=False,
    )
    for axis, pair in zip(axes.flat, pairs, strict=False):
        _scatter_on_axis(axis, points[:, pair[0]], points[:, pair[1]])
        axis.set_xlabel(f"Deme {pair[0] + 1}")
        axis.set_ylabel(f"Deme {pair[1] + 1}")
    for axis in tuple(axes.flat)[len(pairs) :]:
        axis.set_visible(False)
    return figure


def _plot_pca(points: FloatArray) -> Figure:
    """Render a labeled two-dimensional principal-component projection."""
    centered = points - points.mean(axis=0, keepdims=True)
    if len(points) == 1:
        projected = np.zeros((1, 2), dtype=np.float64)
    else:
        left, singular_values, _right = np.linalg.svd(
            centered,
            full_matrices=False,
        )
        dimensions = min(2, left.shape[1])
        projected = np.zeros((len(points), 2), dtype=np.float64)
        projected[:, :dimensions] = left[:, :dimensions] * singular_values[:dimensions]
    figure, axis = plt.subplots(figsize=(7, 6))
    _scatter_on_axis(axis, projected[:, 0], projected[:, 1], reference=False)
    axis.set_xlabel("Principal component 1")
    axis.set_ylabel("Principal component 2")
    axis.set_title("2-D PCA projection of deme-frequency coordinates")
    return figure


def _plot_three_dimensional(points: FloatArray) -> Figure:
    """Render direct three-dimensional deme coordinates."""
    figure = plt.figure(figsize=(8, 7))
    axis = cast(Any, figure.add_subplot(111, projection="3d"))
    colors = np.where(
        points.max(axis=1) >= COMMON_ALLELE_THRESHOLD,
        "tab:blue",
        "tab:orange",
    )
    axis.scatter(points[:, 0], points[:, 1], points[:, 2], c=colors, alpha=0.75)
    axis.set_xlabel("Deme 1")
    axis.set_ylabel("Deme 2")
    axis.set_zlabel("Deme 3")
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(0.0, 1.0)
    axis.set_zlim(0.0, 1.0)
    return figure


def _plot_two_dimensional(points: FloatArray) -> Figure:
    """Render direct two-dimensional deme coordinates."""
    figure, axis = plt.subplots(figsize=(7, 6))
    _scatter_on_axis(axis, points[:, 0], points[:, 1])
    axis.set_xlabel("Deme 1")
    axis.set_ylabel("Deme 2")
    return figure


def _scatter_on_axis(
    axis: Axes,
    horizontal: FloatArray,
    vertical: FloatArray,
    *,
    reference: bool = True,
) -> None:
    """Render grouped points, coincidence labels, and optional diagonal."""
    coordinates = tuple(
        (float(x), float(y)) for x, y in zip(horizontal, vertical, strict=True)
    )
    unique, sizes, colors, labels = marker_groups(coordinates)
    axis.scatter(
        unique[:, 0],
        unique[:, 1],
        s=sizes,
        c=colors,
        alpha=0.75,
        edgecolors="black",
        linewidths=0.4,
    )
    for point, label in zip(unique, labels, strict=True):
        if label:
            axis.annotate(
                label, (point[0], point[1]), xytext=(4, 4), textcoords="offset points"
            )
    if reference:
        axis.plot((0.0, 1.0), (0.0, 1.0), color="0.65", linestyle="--")
        axis.set_xlim(0.0, 1.0)
        axis.set_ylim(0.0, 1.0)


def _title(params: SimulationParams) -> str:
    """Return the required self-describing plot title."""
    return (
        f"Finite island model: N={params.N}, m={params.m}, mu={params.mu}, d={params.d}"
    )
