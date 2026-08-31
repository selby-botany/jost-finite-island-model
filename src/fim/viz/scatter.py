"""Canonical deme-coordinate allele-frequency scatter plots."""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeAlias, TypedDict, cast

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

logger = logging.getLogger(__name__)


class PcaSummary(TypedDict):
    """`pca_summary`'s own return shape -- see that function's docstring."""

    explained_variance: tuple[float, ...]
    top_demes: tuple[tuple[int, ...], ...]


PAIRWISE_MAX_DEMES = 6
COMMON_ALLELE_THRESHOLD = 0.05
DIRECT_2D_DEMES = 2
DIRECT_3D_DEMES = 3
MINIMUM_PAIRWISE_MAX_DEMES = 4
PCA_COMPONENTS_SHOWN = 2
PCA_TOP_LOADING_DEMES = 3


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
        # First pair, not PCA -- the same
        # default-projection change `panels_from_points` makes for the
        # GUI, applied identically here for consistency: neither the
        # eigenvector-instability argument (moot for one static image)
        # nor the speed argument (measured, never a real constraint) is
        # why this changed -- the interpretability argument and the
        # reference visualization's own complete absence of PCA at any
        # `d` both apply just as much to a static PNG as to a live view.
        # `_plot_pca` stays, reachable directly for whoever wants it.
        logger.debug(
            "d=%d exceeds pairwise_max_demes=%d; falling back to the first "
            "deme pair instead of a full pairwise matrix",
            state.deme_count,
            pairwise_max_demes,
        )
        figure = _plot_deme_pair(points, 0, 1)
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
        logger.debug("wrote scatter figure: %s", output_path)
    return figure


def frequency_points(state: ModelState) -> FloatArray:
    """Return one row per locus/allele and one column per deme.

    Public (`doc/fim-gui-design.md` §12): the
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

    Public (`doc/fim-gui-design.md` §12): the
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


def grouped_points(
    coordinates: Sequence[tuple[float, float]],
) -> list[dict[str, float | int | bool]]:
    """Collapse coincident points into JSON-ready `{x, y, count, common}` entries.

    Public (`doc/fim-gui-design.md` §12): the GUI
    bridge's own shape for `webui/scatter.js`'s Canvas renderer —
    `marker_groups`' data-only sibling. `marker_groups` itself is
    rewritten in terms of this function's own grouping (below) rather
    than repeating `Counter(coordinates)` independently, so the two
    functions' outputs can never silently drift out of sync with each
    other.
    """
    counts = Counter(coordinates)
    return [
        {
            "x": point[0],
            "y": point[1],
            "count": count,
            "common": max(point) >= COMMON_ALLELE_THRESHOLD,
        }
        for point, count in counts.items()
    ]


def marker_groups(
    coordinates: Sequence[tuple[float, float]],
) -> tuple[FloatArray, FloatArray, list[str], list[str]]:
    """Collapse coincident points and derive marker sizes, colors, and labels.

    Public (`doc/fim-gui-design.md` §12): the
    GUI's bridge calls this directly, over `pooled_frequency_points`'s
    output as readily as over one state's own `frequency_points` output
    — coincidence counting has no notion of where a point came from.
    """
    grouped = grouped_points(coordinates)
    unique = np.asarray(
        [(point["x"], point["y"]) for point in grouped], dtype=np.float64
    )
    sizes = np.asarray(
        [30.0 + 18.0 * math.sqrt(point["count"]) for point in grouped],
        dtype=np.float64,
    )
    colors = ["tab:blue" if point["common"] else "tab:orange" for point in grouped]
    labels = [str(point["count"]) if point["count"] > 1 else "" for point in grouped]
    return unique, sizes, colors, labels


def scatter_panels(
    state: ModelState,
    *,
    pairwise_max_demes: int = PAIRWISE_MAX_DEMES,
) -> list[dict[str, object]]:
    """Return the single Deme-x-Deme 2-D panel, ready for client rendering.

    The data-only equivalent of the GUI's own run-view plot (simplify-
    main-plot design): the client never performs dimensionality
    reduction or picks which deme pair to show automatically — it only
    ever draws the one already-2-D panel `webui/scatter.js` hands to a
    `<canvas>`, always demes 1 and 2. A different pair is reachable only
    on request, through `deme_pair_panel` (the GUI's "Compare demes
    directly" selector); no small-multiples grid and no PCA projection
    are ever dispatched to automatically any more, at any `d`.

    Args:
        state: State to visualize.
        pairwise_max_demes: Unused; retained only so existing callers
            need not change. `pca_project`/`pca_summary`/`kind: "pca"`
            all remain directly callable for an explicit exploratory
            view; this dispatch never reaches them automatically.

    Returns:
        A one-element list holding `{"x_label", "y_label", "points",
        "kind"}`, `points` being `grouped_points`' own list of `{x, y,
        count, common}` entries. `kind` is always `"frequency"`.
    """
    return panels_from_points(
        frequency_points(state), state.deme_count, pairwise_max_demes
    )


def pooled_scatter_panels(
    states: Sequence[ModelState],
    deme_count: int,
    *,
    pairwise_max_demes: int = PAIRWISE_MAX_DEMES,
) -> list[dict[str, object]]:
    """`scatter_panels`' own single-panel dispatch, over several pooled states at once.

    The GUI's batch progress/results screens' own data source (design
    §4.2, §4.4, §7.6): the same "always the one Deme 1/Deme 2 panel"
    rule `scatter_panels` applies to one state's points applies
    identically here to `pooled_frequency_points(states)`'s pooled rows
    — coincidence counting (`grouped_points`) already treats a point
    shared across replicates exactly like one shared across loci, so
    layout dispatch needs no special case for "pooled" either.

    Args:
        states: Every replicate's current (possibly in-flight) final
            state to pool — see `pooled_frequency_points`'s own
            docstring. Commonly not every replicate the batch will
            eventually run: a live batch's own progress screen calls
            this with whichever replicates have reported at least one
            generation so far.
        deme_count: The batch's own `d`, taken as an explicit argument
            rather than inferred from `states[0]`, since `states` can
            legitimately be empty (design's own "live, before any
            replicate has reported a generation yet" case) — an empty
            `states` still needs a real `deme_count` to answer whether
            there is even a deme pair to plot, even though the answer
            is moot once `points` turns out to have zero rows.
        pairwise_max_demes: Unused; retained only so existing callers
            need not change.

    Returns:
        `[]` if `states` is empty (nothing to pool yet); otherwise the
        same `scatter_panels`-shaped single-panel list, built from the
        pooled points.
    """
    if not states:
        return []
    return panels_from_points(
        pooled_frequency_points(states), deme_count, pairwise_max_demes
    )


def panels_from_points(
    points: FloatArray,
    deme_count: int,
    pairwise_max_demes: int = PAIRWISE_MAX_DEMES,
) -> list[dict[str, object]]:
    """Share `scatter_panels`/`pooled_scatter_panels`' own single-panel dispatch.

    `points` is `frequency_points`-shaped either way (one row per
    (locus, allele) pair, one column per deme) — a single state's own
    rows for `scatter_panels`, or several states' pooled rows for
    `pooled_scatter_panels`; this function does not know or care which.

    Public (Milestone W6, `doc/fim-gui-design.md` §8): `fim.gui.animation.
    pre_render_frames` deliberately stops at a plain `frequency_points`
    array per sampled generation — "whoever renders this... is
    responsible for any further reduction a high deme count needs," by
    its own docstring — so the GUI bridge (`Api.get_animation_frames`)
    calls this directly, once per frame, to ship the page already-2-D
    points for any `d`.

    Always returns exactly one panel, demes 1 and 2 (simplify-main-plot
    design): neither a small-multiples pairwise grid (one panel per
    `C(d, 2)` pair, `3 <= d <= pairwise_max_demes`) nor a PCA projection
    (`d > pairwise_max_demes`) is dispatched to automatically any more,
    at any `d` — both added complexity the reference visualization
    itself (`Dear-NolanMarch17Final.pdf` Figs. 1-2) never needed, since
    it always shows one deme pair at a time. `deme_pair_panel` (the
    GUI's own "Compare demes directly" selector) is how a caller reaches
    any other pair; `pca_project`/`pca_summary`/`kind: "pca"` all stay,
    reachable directly by a caller that wants the exploratory view, just
    no longer the automatic dispatch target. `pairwise_max_demes` is
    accepted and ignored, so `scatter_panels`/`pooled_scatter_panels`
    need not change their own signatures.
    """
    del pairwise_max_demes, deme_count
    return [deme_pair_panel(points, 0, 1)]


def deme_pair_panel(points: FloatArray, first: int, second: int) -> dict[str, object]:
    """Return one explicit deme-pair 2-D panel, chosen by index rather than by layout.

    The on-demand counterpart to `panels_from_points`'s own automatic
    dispatch (GUI Screens 3/4's "large-d deme-pair selector"): once `d`
    exceeds `pairwise_max_demes`, `panels_from_points` returns only a
    PCA panel, with no way to ask for one specific raw pair instead --
    this function is that specific-pair escape hatch, built from the
    same `_panel` construction every other panel in this module uses,
    so its `points`/label shape is identical either way. Not folded
    into `panels_from_points` itself: that function's whole contract is
    "decide the layout automatically from `deme_count` alone," and a
    caller-chosen pair is a different question with a different
    (always exactly one panel) answer.

    Args:
        points: `frequency_points`/`pooled_frequency_points`-shaped:
            one row per (locus, allele) pair, one column per deme.
        first: Zero-based index of the deme to plot on the X axis.
        second: Zero-based index of the deme to plot on the Y axis.

    Returns:
        The same `{"x_label", "y_label", "points", "kind"}` shape every other
        panel in this module returns.

    Raises:
        ValueError: If `first`/`second` are out of range for `points`'
            own deme count, or name the same deme twice.
    """
    deme_count = points.shape[1]
    for index, which in ((first, "first"), (second, "second")):
        if not 0 <= index < deme_count:
            raise ValueError(
                f"{which} deme index {index} is out of range for {deme_count} deme(s)"
            )
    if first == second:
        raise ValueError("first and second must name different demes")
    return _panel(
        points[:, first], points[:, second], f"Deme {first + 1}", f"Deme {second + 1}"
    )


def _panel(
    horizontal: FloatArray,
    vertical: FloatArray,
    x_label: str,
    y_label: str,
    *,
    kind: str = "frequency",
) -> dict[str, object]:
    """Build one `scatter_panels` entry from a pair of coordinate columns.

    Args:
        kind: `"frequency"` (the default) for a genuine deme-vs-deme
            allele-frequency panel — bounded to `[0, 1]` by construction,
            an `x=y` diagonal is meaningful (same allele, two demes).
            `"pca"` for a principal-component projection — unbounded,
            no meaningful diagonal, an axis-scale/rendering distinction
            `webui/scatter.js`'s own `drawScatterCell` needs to draw
            either one correctly (visualization-and-config-editors
            design's own follow-up: a PCA panel was being drawn with the
            same fixed `[0, 1]` probability scale and diagonal reference
            a frequency panel gets, which is simply wrong for PCA's own
            unbounded coordinates — real points render as if they carried
            "negative probability").
    """
    coordinates = tuple(
        (float(x), float(y)) for x, y in zip(horizontal, vertical, strict=True)
    )
    return {
        "x_label": x_label,
        "y_label": y_label,
        "points": grouped_points(coordinates),
        "kind": kind,
    }


def pca_project(points: FloatArray) -> FloatArray:
    """Return `points` projected onto its first two principal components.

    Public (`doc/fim-gui-design.md` §12): the same
    projection `_plot_pca` renders as a `Figure`, factored out so
    `scatter_panels` can reuse the identical math for the data-only
    path rather than a second SVD implementation. `_plot_pca` itself now
    calls this too — one implementation, two consumers.
    """
    centered = points - points.mean(axis=0, keepdims=True)
    if len(points) == 1:
        return np.zeros((1, 2), dtype=np.float64)
    left, singular_values, _right = np.linalg.svd(centered, full_matrices=False)
    dimensions = min(2, left.shape[1])
    projected = np.zeros((len(points), 2), dtype=np.float64)
    projected[:, :dimensions] = left[:, :dimensions] * singular_values[:dimensions]
    return projected


def pca_summary(points: FloatArray) -> PcaSummary:
    """Return each shown principal component's own explained variance and top demes.

    A PCA scatter's bare "Principal component 1"/"2" axis titles give no
    way to judge what the plot actually shows — the request that
    prompted this function named it directly: "plotted PCA without
    saying anything about the D combinations (the eigenvectors,
    basically), so interpreting the plot is tough." Reuses `pca_project`'s
    own SVD rather than a second one: `right`'s rows are each principal
    axis's own weight on every original deme dimension (`numpy.linalg.
    svd`'s own `Vh`/`right` return, discarded by `pca_project` today).

    Returns:
        `{"explained_variance": (ratio, ratio), "top_demes": (demes,
        demes)}` — one entry per shown component (`PCA_COMPONENTS_
        SHOWN`). `explained_variance` is each component's own share of
        total variance in `[0, 1]`. `top_demes` is each component's
        `PCA_TOP_LOADING_DEMES` largest-magnitude-loading demes, as
        1-based deme numbers, ranked by `|loading|` descending. Both
        default to all-zero/all-empty for a degenerate input (a single
        point, or fewer real dimensions than components requested) —
        `pca_project` itself already returns an all-zero projection for
        that same case, so the labels stay consistent with the plot.
    """
    if len(points) <= 1:
        return {
            "explained_variance": (0.0,) * PCA_COMPONENTS_SHOWN,
            "top_demes": ((),) * PCA_COMPONENTS_SHOWN,
        }
    centered = points - points.mean(axis=0, keepdims=True)
    _left, singular_values, right = np.linalg.svd(centered, full_matrices=False)
    total_variance = float(np.sum(singular_values**2))
    available = right.shape[0]
    explained_variance: list[float] = []
    top_demes: list[tuple[int, ...]] = []
    for component in range(PCA_COMPONENTS_SHOWN):
        if component >= available:
            explained_variance.append(0.0)
            top_demes.append(())
            continue
        ratio = (
            float(singular_values[component] ** 2 / total_variance)
            if total_variance > 0
            else 0.0
        )
        explained_variance.append(ratio)
        loadings = right[component]
        order = np.argsort(-np.abs(loadings))[:PCA_TOP_LOADING_DEMES]
        top_demes.append(tuple(int(index) + 1 for index in order))
    return {
        "explained_variance": tuple(explained_variance),
        "top_demes": tuple(top_demes),
    }


def pca_axis_labels(points: FloatArray) -> tuple[str, str]:
    """Return the two PCA axis titles `pca_summary`'s own diagnostics produce.

    One formatting rule, shared by `panels_from_points`' own client-ready
    label and `_plot_pca`'s matplotlib title — never two.
    """
    summary = pca_summary(points)
    labels = []
    for component in range(PCA_COMPONENTS_SHOWN):
        ratio = summary["explained_variance"][component]
        demes = summary["top_demes"][component]
        deme_text = ", ".join(str(deme) for deme in demes) if demes else "none"
        labels.append(
            f"Principal component {component + 1} "
            f"({ratio:.0%} of variance; demes {deme_text})"
        )
    return labels[0], labels[1]


def _plot_deme_pair(points: FloatArray, first: int, second: int) -> Figure:
    """Render one caller-chosen pair of deme dimensions.

    `_plot_two_dimensional`, generalized to any pair rather than the
    fixed `d == 2` case: `plot_frequency_
    scatter`'s own `d > pairwise_max_demes` branch calls this with
    `(0, 1)` as its new default projection, in place of `_plot_pca`.
    """
    figure, axis = plt.subplots(figsize=(7, 6))
    _scatter_on_axis(axis, points[:, first], points[:, second])
    axis.set_xlabel(f"Deme {first + 1}")
    axis.set_ylabel(f"Deme {second + 1}")
    return figure


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
    projected = pca_project(points)
    x_label, y_label = pca_axis_labels(points)
    figure, axis = plt.subplots(figsize=(7, 6))
    _scatter_on_axis(axis, projected[:, 0], projected[:, 1], reference=False)
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
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
