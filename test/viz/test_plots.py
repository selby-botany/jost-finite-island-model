"""Structural smoke tests for headless visualizations."""

from pathlib import Path

import numpy as np
import pytest
from matplotlib import pyplot as plt
from matplotlib.collections import PathCollection
from mpl_toolkits.mplot3d import Axes3D

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.viz import scatter as scatter_module
from fim.viz.diagnostics import (
    MAX_LEGEND_ALLELES,
    plot_convergence_trace,
    plot_frequency_bars,
)
from fim.viz.scatter import (
    deme_pair_panel,
    frequency_points,
    grouped_points,
    marker_groups,
    panels_from_points,
    pca_axis_labels,
    pca_project,
    pca_summary,
    plot_frequency_scatter,
    pooled_frequency_points,
    pooled_scatter_panels,
    scatter_panels,
)


def _params(d: int) -> SimulationParams:
    """Return parameters for one visualization shape."""
    return SimulationParams(
        N=20,
        m=0.1,
        mu=0.001,
        d=d,
        seed=3,
        loci=(LocusSpec(1, 100),),
    )


def _state(d: int) -> ModelState:
    """Return a valid state with one shared and one varying allele."""
    return ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=tuple(
            (
                {
                    AlleleId(0): (index + 1) / (d + 2),
                    AlleleId(1): 1.0 - (index + 1) / (d + 2),
                },
            )
            for index in range(d)
        ),
    )


def test_scatter_rejects_a_deme_count_mismatch() -> None:
    """`state.deme_count` must agree with `params.d`.

    Regression test for R15: `plot_frequency_scatter` had this guard
    (`src/fim/viz/scatter.py:53`) from the start, but nothing exercised
    it — the `viz` package's coverage was entirely omitted from the
    gate (`omit = ["src/fim/viz/*"]`, also removed by this change), so
    a broken guard here could regress silently.
    """
    with pytest.raises(ValueError, match="deme count does not match"):
        plot_frequency_scatter(_state(2), _params(3))


def test_scatter_rejects_a_too_small_pairwise_max_demes() -> None:
    """`pairwise_max_demes` below the documented floor is rejected."""
    with pytest.raises(ValueError, match="pairwise_max_demes must be at least 4"):
        plot_frequency_scatter(_state(2), _params(2), pairwise_max_demes=3)


def test_direct_scatter_has_deme_axes_and_parameter_title(tmp_path: Path) -> None:
    """Two demes render directly with self-describing metadata."""
    output = tmp_path / "scatter.png"
    figure = plot_frequency_scatter(_state(2), _params(2), output)

    assert output.is_file()
    assert len(figure.axes) == 1
    assert figure.axes[0].get_xlabel() == "Deme 1"
    assert figure.axes[0].get_ylabel() == "Deme 2"
    assert "N=20" in figure.get_suptitle()
    plt.close(figure)


def test_moderate_dimensions_render_pairwise_matrix() -> None:
    """Four demes produce all six pairwise panels."""
    figure = plot_frequency_scatter(_state(4), _params(4))

    assert len([axis for axis in figure.axes if axis.get_visible()]) == 6
    plt.close(figure)


def test_pairwise_matrix_hides_unused_grid_cells() -> None:
    """A pair count that doesn't fill its grid hides the leftover cells.

    Five demes need `C(5, 2) = 10` panels in a 3-column, 4-row grid (12
    cells) — the two cells with no pair to render must stay present
    (`figure.axes` still counts them) but explicitly hidden, unlike the
    four-deme case above, where 6 pairs exactly fill a 3x2 grid and this
    path never runs.
    """
    figure = plot_frequency_scatter(_state(5), _params(5))

    visible = [axis for axis in figure.axes if axis.get_visible()]
    hidden = [axis for axis in figure.axes if not axis.get_visible()]
    assert len(visible) == 10
    assert len(hidden) == 2
    plt.close(figure)


def test_three_demes_render_direct_three_dimensional_axes() -> None:
    """Three demes retain direct coordinates rather than using a projection."""
    figure = plot_frequency_scatter(_state(3), _params(3))
    axis = figure.axes[0]

    assert axis.get_xlabel() == "Deme 1"
    assert axis.get_ylabel() == "Deme 2"
    # `figure.axes` is stub-typed as the base (2D) `Axes`; narrowing to the
    # real runtime `Axes3D` both satisfies mypy and asserts the projection
    # this test exists to check.
    assert isinstance(axis, Axes3D)
    assert axis.get_zlabel() == "Deme 3"
    plt.close(figure)


def test_large_dimensions_default_to_the_first_deme_pair() -> None:
    """Large `d` defaults to one explicit pair, not a PCA projection.

    Unified-run-view design §3.6: dropped as the CLI's own default for
    the same reasons `panels_from_points` dropped it as the GUI's —
    `_plot_pca` itself is unchanged and still directly reachable, see
    `test_pca_is_still_directly_reachable_for_a_large_dimension` below.
    """
    figure = plot_frequency_scatter(_state(7), _params(7))
    axis = figure.axes[0]

    assert len(figure.axes) == 1
    assert axis.get_xlabel() == "Deme 1"
    assert axis.get_ylabel() == "Deme 2"
    plt.close(figure)


def test_pca_is_still_directly_reachable_for_a_large_dimension() -> None:
    """`_plot_pca` itself is unchanged — no longer the CLI's default, still callable.

    Direct regression proof for the "PCA is not deleted, only demoted"
    half of design §3.6's decision: calling it directly on the same
    seven-deme points `plot_frequency_scatter` no longer routes there
    reproduces exactly what that branch used to render.
    """
    points = frequency_points(_state(7))

    figure = scatter_module._plot_pca(points)

    assert len(figure.axes) == 1
    assert "PCA projection" in figure.axes[0].get_title()
    plt.close(figure)


def test_pca_projection_handles_a_single_point_without_svd() -> None:
    """A one-row point matrix (every deme fixed for the same allele) skips SVD.

    `numpy.linalg.svd` is not called at all when there is only one
    (locus, allele) point to project — `_plot_pca` special-cases it to
    avoid a degenerate decomposition. Called directly (design §3.6:
    `plot_frequency_scatter` no longer reaches `_plot_pca` for any `d`),
    matching `test_pca_is_still_directly_reachable_for_a_large_dimension`
    above; fixing every deme for the same single allele collapses the
    whole state to exactly one point.
    """
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=tuple(({AlleleId(0): 1.0},) for _ in range(7)),
    )

    figure = scatter_module._plot_pca(frequency_points(state))

    assert len(figure.axes) == 1
    markers = figure.axes[0].collections[0]
    assert isinstance(markers, PathCollection)
    # `get_offsets()` is stub-typed as the broad `ArrayLike` its setter
    # accepts, not the concrete ndarray it actually returns; `np.asarray`
    # makes that concrete, matching the same pattern already used for
    # `Line2D.get_xdata()` elsewhere in this file.
    (point,) = np.asarray(markers.get_offsets())
    assert tuple(point) == (0.0, 0.0)
    plt.close(figure)


def test_coincident_common_and_rare_points_are_grouped_and_labeled() -> None:
    """Repeated coordinates scale markers, show counts, and retain two colors."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    state = ModelState(
        loci=loci,
        frequencies=tuple(
            (
                {AlleleId(0): 0.99, AlleleId(1): 0.01},
                {AlleleId(0): 0.99, AlleleId(1): 0.01},
            )
            for _ in range(2)
        ),
    )

    figure = plot_frequency_scatter(state, _params(2))
    axis = figure.axes[0]
    markers = axis.collections[0]
    # `Axes.collections` is stub-typed as the base `Collection`; narrowing
    # to `PathCollection` (what `Axes.scatter` actually returns) both
    # satisfies mypy and asserts this really is a scatter layer.
    assert isinstance(markers, PathCollection)

    assert len(markers.get_sizes()) == 2
    # `get_facecolors` is a real matplotlib `_api.define_aliases`-generated
    # alias for `get_facecolor` — identical at runtime, invisible to the
    # stubs, which declare only the singular name (and with a broader,
    # single-or-many-colors return type unhelpful for this specific
    # known-plural case). One targeted ignore, not a suppression of a
    # genuine issue.
    facecolors = markers.get_facecolors()  # type: ignore[attr-defined]
    assert len({tuple(color[:3]) for color in facecolors}) == 2
    assert {text.get_text() for text in axis.texts} == {"2"}
    plt.close(figure)


def test_diagnostic_views_have_one_trace_and_one_bar_per_deme() -> None:
    """Supporting views preserve the expected observation count."""
    trace = plot_convergence_trace([0, 1, 2], [0.1, 0.2, 0.2], "D")
    bars = plot_frequency_bars(_state(3))

    # `Line2D.get_xdata()` is stub-typed as the broad `ArrayLike` (the same
    # alias its setter accepts), not the concrete, always-`Sized` ndarray
    # it actually returns; `np.asarray` makes that concrete for both mypy
    # and any genuinely list-like runtime value.
    assert len(np.asarray(trace.axes[0].lines[0].get_xdata())) == 3
    assert len(bars.axes[0].patches) == 6
    plt.close(trace)
    plt.close(bars)


def test_convergence_trace_rejects_a_length_mismatch() -> None:
    """`generations` and `values` must be the same length."""
    with pytest.raises(ValueError, match="same length"):
        plot_convergence_trace([0, 1], [0.1], "D")


def test_convergence_trace_rejects_an_empty_history() -> None:
    """A trace needs at least one recorded observation."""
    with pytest.raises(ValueError, match="at least one observation"):
        plot_convergence_trace([], [], "D")


def test_frequency_bars_rejects_an_out_of_range_locus_index() -> None:
    """`locus_index` must name a locus the state actually tracks."""
    with pytest.raises(ValueError, match="locus_index is outside the state"):
        plot_frequency_bars(_state(3), locus_index=1)


def test_convergence_trace_and_frequency_bars_write_a_png_when_given_a_path(
    tmp_path: Path,
) -> None:
    """Both diagnostic views honor their documented optional `path` argument.

    Regression test for R15: neither function's file-writing branch
    (`_save`) was exercised anywhere — `test_diagnostic_views_have_one_
    trace_and_one_bar_per_deme` above calls both with no `path` at all.
    """
    trace_path = tmp_path / "trace.png"
    bars_path = tmp_path / "bars.png"

    trace = plot_convergence_trace([0, 1, 2], [0.1, 0.2, 0.2], "D", trace_path)
    bars = plot_frequency_bars(_state(3), bars_path)

    assert trace_path.is_file()
    assert trace_path.stat().st_size > 0
    assert bars_path.is_file()
    assert bars_path.stat().st_size > 0
    plt.close(trace)
    plt.close(bars)


def test_frequency_bars_omits_the_legend_beyond_the_display_cap() -> None:
    """A locus with more alleles than `MAX_LEGEND_ALLELES` renders without one.

    An unbounded legend for a highly polymorphic locus would be
    unreadable; `plot_frequency_bars` deliberately drops it once there
    are too many alleles to list, rather than rendering an illegible
    one.
    """
    allele_count = MAX_LEGEND_ALLELES + 1
    frequency_map = {
        AlleleId(index): 1.0 / allele_count for index in range(allele_count)
    }
    state = ModelState(loci=(LocusSpec(1, 100),), frequencies=((frequency_map,),))

    figure = plot_frequency_bars(state)

    assert figure.axes[0].get_legend() is None
    plt.close(figure)


def test_frequency_points_shape_is_locus_allele_rows_by_deme_columns() -> None:
    """Public data function (graphical-interface migration §3.3): shape and content.

    Direct regression test that the GUI's own bridge can rely on this
    function without ever building a `Figure` — no `matplotlib.pyplot`
    call anywhere in this test.
    """
    state = _state(2)

    points = frequency_points(state)

    assert points.shape == (2, 2)
    # Row 0 is allele 0's frequency in each deme (deme 0's own construction:
    # {AlleleId(0): 1/4, AlleleId(1): 3/4}; deme 1's: {AlleleId(0): 2/4, ...}).
    assert points[0][0] == pytest.approx(1 / 4)
    assert points[0][1] == pytest.approx(2 / 4)


def test_pooled_frequency_points_matches_frequency_points_for_one_state() -> None:
    """Pooling a single state is exactly that state's own `frequency_points`."""
    state = _state(2)

    pooled = pooled_frequency_points([state])

    assert np.array_equal(pooled, frequency_points(state))


def test_pooled_frequency_points_concatenates_before_grouping() -> None:
    """Two states' points pool into one array; coincident rows across states group.

    The direct regression test for the "some engine work" the migration
    design (§0.5) anticipated: two independent states that happen to
    share one (deme-1, deme-2) coordinate must group into one bigger,
    numbered marker exactly as two coincident loci within a single
    state already do (`test_coincident_common_and_rare_points_are_
    grouped_and_labeled` above) — coincidence counting must not care
    whether the coincidence came from two loci or two replicates.
    """
    loci = (LocusSpec(1, 100),)
    # A single allele fixed at every deme (mirrors
    # test_pca_projection_handles_a_single_point_without_svd above): each
    # state alone contributes exactly one (locus, allele) row, at the same
    # coordinate — the direct replicate-level analog of that test's own
    # single-run collapse.
    first = ModelState(loci=loci, frequencies=(({AlleleId(0): 1.0},),) * 2)
    second = ModelState(loci=loci, frequencies=(({AlleleId(0): 1.0},),) * 2)

    pooled = pooled_frequency_points([first, second])

    assert pooled.shape == (2, 2)
    unique, sizes, colors, labels = marker_groups(
        tuple((float(x), float(y)) for x, y in pooled)
    )
    assert len(unique) == 1
    assert sizes[0] > 30.0
    assert labels == ["2"]
    assert colors == ["tab:blue"]


def test_pooled_frequency_points_of_no_states_is_empty() -> None:
    """An empty pool is empty, not an error — a batch with no replicates yet."""
    assert pooled_frequency_points([]).shape == (0, 0)


def test_grouped_points_matches_marker_groups_exactly() -> None:
    """`marker_groups` is now a thin reshaping of `grouped_points` — proven directly.

    Regression test for the refactor introduced alongside `scatter_
    panels` (graphical-interface migration §3.5): the two functions'
    grouping must never silently drift apart, since `marker_groups` is
    implemented in terms of `grouped_points` specifically to make that
    impossible by construction.
    """
    coordinates = ((0.1, 0.9), (0.1, 0.9), (0.5, 0.5))

    grouped = grouped_points(coordinates)
    unique, sizes, colors, labels = marker_groups(coordinates)

    assert len(grouped) == len(unique) == 2
    by_point = {(entry["x"], entry["y"]): entry for entry in grouped}
    for point, size, color, label in zip(unique, sizes, colors, labels, strict=True):
        entry = by_point[(point[0], point[1])]
        assert size == pytest.approx(30.0 + 18.0 * entry["count"] ** 0.5)
        assert color == ("tab:blue" if entry["common"] else "tab:orange")
        assert label == (str(entry["count"]) if entry["count"] > 1 else "")


def test_panels_from_points_matches_scatter_panels_directly() -> None:
    """`scatter_panels` is a thin wrapper: calling the shared dispatch directly agrees.

    Milestone W6's own caller (`Api.get_animation_frames`, via `fim.gui.
    animation.pre_render_frames`'s already-computed `frequency_points`
    per frame) never has a `ModelState` to hand `scatter_panels` — this
    is the direct regression proof the newly-public function it calls
    instead produces identical output.
    """
    state = _state(4)

    assert panels_from_points(frequency_points(state), state.deme_count) == (
        scatter_panels(state)
    )


def test_scatter_panels_two_demes_is_one_direct_panel() -> None:
    """`d == 2` produces exactly one panel, the direct two demes."""
    panels = scatter_panels(_state(2))

    assert len(panels) == 1
    assert panels[0]["x_label"] == "Deme 1"
    assert panels[0]["y_label"] == "Deme 2"
    assert panels[0]["kind"] == "frequency"
    points = panels[0]["points"]
    assert isinstance(points, list)
    assert len(points) == 2


def test_scatter_panels_four_demes_defaults_to_the_first_deme_pair() -> None:
    """`3 <= d` still produces exactly one panel, demes 1 and 2 (simplify-main-plot
    design) — no small-multiples grid of every `C(d, 2)` pair any more."""
    panels = scatter_panels(_state(4))

    assert len(panels) == 1
    assert panels[0]["x_label"] == "Deme 1"
    assert panels[0]["y_label"] == "Deme 2"
    assert panels[0]["kind"] == "frequency"


def test_scatter_panels_large_d_defaults_to_the_first_deme_pair() -> None:
    """`d > pairwise_max_demes` also produces one frequency panel, demes 1 and 2.

    Not a PCA projection (unified-run-view design §3.6) — `pca_project`/
    `pca_summary`/`pca_axis_labels` are unchanged and still directly
    testable (`test_pca_project_matches_the_rendered_pca_plot` and the
    `pca_summary`/`pca_axis_labels` tests below); only this dispatch's
    own default no longer reaches them automatically.
    """
    panels = scatter_panels(_state(7))

    assert len(panels) == 1
    assert panels[0]["x_label"] == "Deme 1"
    assert panels[0]["y_label"] == "Deme 2"
    assert panels[0]["kind"] == "frequency"


def test_pooled_scatter_panels_of_no_states_is_empty() -> None:
    """An empty pool is zero panels, not an error — the "no replicate has
    reported a generation yet" case a live batch progress screen starts
    from."""
    assert pooled_scatter_panels([], deme_count=2) == []


def test_pooled_scatter_panels_matches_scatter_panels_for_one_state() -> None:
    """Pooling a single state produces exactly that state's own `scatter_panels`."""
    state = _state(4)

    assert pooled_scatter_panels([state], deme_count=4) == scatter_panels(state)


def test_pooled_scatter_panels_pools_coincident_points_across_states() -> None:
    """Two states sharing a coordinate group into one bigger, numbered marker.

    The `scatter_panels`-shaped analog of `test_pooled_frequency_points_
    concatenates_before_grouping` above.
    """
    loci = (LocusSpec(1, 100),)
    first = ModelState(loci=loci, frequencies=(({AlleleId(0): 1.0},),) * 2)
    second = ModelState(loci=loci, frequencies=(({AlleleId(0): 1.0},),) * 2)

    panels = pooled_scatter_panels([first, second], deme_count=2)

    assert len(panels) == 1
    points = panels[0]["points"]
    assert isinstance(points, list)
    assert len(points) == 1
    assert points[0]["count"] == 2


def test_pooled_scatter_panels_dispatches_layout_by_deme_count() -> None:
    """Any `deme_count >= 2` still produces the one default panel, matching
    `scatter_panels`."""
    states = [_state(7), _state(7)]

    panels = pooled_scatter_panels(states, deme_count=7)

    assert len(panels) == 1
    assert panels[0]["x_label"] == "Deme 1"
    assert panels[0]["y_label"] == "Deme 2"
    assert panels[0]["kind"] == "frequency"


def test_deme_pair_panel_names_the_requested_pair() -> None:
    """A caller-chosen pair labels its axes by 1-based deme number."""
    points = frequency_points(_state(20))

    panel = deme_pair_panel(points, first=2, second=9)

    assert panel["x_label"] == "Deme 3"
    assert panel["y_label"] == "Deme 10"
    assert panel["kind"] == "frequency"


# A hand-constructed, rank-2 input: deme 3 is constant (contributes no
# variance at all), deme 2 alone drives the top-variance direction, deme 1
# the second. Exact golden values below were computed once directly
# (`numpy.linalg.svd` against this same array) and asserted verbatim --
# this project's own "exact golden values for formulas" testing rule
# (`doc/developer.md`), not a looser approximate check.
_PCA_GOLDEN_POINTS = np.array(
    [
        [0.0, 0.0, 0.5],
        [1.0, 0.0, 0.5],
        [0.5, 1.0, 0.5],
    ],
    dtype=np.float64,
)


def test_pca_summary_names_the_explained_variance_and_top_loading_demes() -> None:
    """`pca_summary` reproduces a hand-verified SVD exactly, not approximately."""
    summary = pca_summary(_PCA_GOLDEN_POINTS)

    variance = summary["explained_variance"]
    assert variance[0] == pytest.approx(4 / 7)
    assert variance[1] == pytest.approx(3 / 7)
    assert summary["top_demes"][0][0] == 2
    assert summary["top_demes"][1][0] == 1


def test_pca_summary_top_demes_never_exceeds_the_configured_count() -> None:
    """`PCA_TOP_LOADING_DEMES` caps the list even when every deme has a real loading."""
    points = frequency_points(_state(20))

    summary = pca_summary(points)

    for demes in summary["top_demes"]:
        assert len(demes) <= 3


def test_pca_summary_of_a_single_point_is_all_zero() -> None:
    """A degenerate one-row input matches `pca_project`'s own all-zero case."""
    summary = pca_summary(np.array([[0.2, 0.3, 0.5]], dtype=np.float64))

    assert summary["explained_variance"] == (0.0, 0.0)
    assert summary["top_demes"] == ((), ())


def test_pca_axis_labels_name_the_explained_variance_and_top_demes() -> None:
    """The client-ready axis title carries `pca_summary`'s own diagnostics.

    Only the dominant deme is asserted, not the full three-deme list
    (`_PCA_GOLDEN_POINTS` has exactly `PCA_TOP_LOADING_DEMES` demes, so
    every one appears) — the trailing two are ordered by a floating-point
    tie-break among near-zero loadings, not a meaningful ranking worth
    pinning exactly.
    """
    x_label, y_label = pca_axis_labels(_PCA_GOLDEN_POINTS)

    assert x_label.startswith("Principal component 1 (57% of variance; demes 2")
    assert y_label.startswith("Principal component 2 (43% of variance; demes 1")


def test_deme_pair_panel_matches_panels_from_points_own_default_output() -> None:
    """`panels_from_points`' own single default panel (demes 1/2) reproduces exactly
    what `deme_pair_panel` builds for that same pair -- the automatic-dispatch path
    and the on-demand path share the same underlying `_panel` call, so this is a
    direct equality check, not a separately reimplemented one."""
    state = _state(4)
    points = frequency_points(state)

    assert panels_from_points(points, state.deme_count) == [
        deme_pair_panel(points, first=0, second=1)
    ]


@pytest.mark.parametrize(
    ("first", "second", "match"),
    [
        (-1, 5, "first deme index -1"),
        (20, 5, "first deme index 20"),
        (5, -1, "second deme index -1"),
        (5, 20, "second deme index 20"),
        (5, 5, "must name different demes"),
    ],
)
def test_deme_pair_panel_rejects_invalid_indices(
    first: int, second: int, match: str
) -> None:
    """Out-of-range or identical indices fail loudly, not with a silent misread."""
    points = frequency_points(_state(20))

    with pytest.raises(ValueError, match=match):
        deme_pair_panel(points, first, second)


def test_pca_project_matches_the_rendered_pca_plot() -> None:
    """`pca_project`'s standalone output matches what `_plot_pca` actually draws.

    Direct regression test that factoring the SVD out of `_plot_pca` and
    into `pca_project` (graphical-interface migration §3.5) changed
    nothing about the rendered figure. Calls `_plot_pca` directly rather
    than through `plot_frequency_scatter` (unified-run-view design §3.6:
    that dispatch no longer reaches PCA for any `d`).
    """
    state = _state(7)
    points = frequency_points(state)

    projected = pca_project(points)
    figure = scatter_module._plot_pca(points)
    markers = figure.axes[0].collections[0]
    assert isinstance(markers, PathCollection)
    rendered = np.asarray(markers.get_offsets())
    plt.close(figure)

    # The rendered figure groups coincident projected points first
    # (`_scatter_on_axis`), so compare the *set* of projected
    # coordinates, not a positional row-for-row match.
    projected_set = {tuple(np.round(row, 6)) for row in projected}
    rendered_set = {tuple(np.round(row, 6)) for row in rendered}
    assert projected_set == rendered_set


def test_pca_project_handles_a_single_point_without_svd() -> None:
    """A single-row input skips SVD entirely, matching `_plot_pca`'s special case."""
    single_point = np.asarray([[1.0]], dtype=np.float64)

    projected = pca_project(single_point)

    assert projected.shape == (1, 2)
    assert tuple(projected[0]) == (0.0, 0.0)
