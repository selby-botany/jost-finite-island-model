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
from fim.viz.diagnostics import (
    MAX_LEGEND_ALLELES,
    plot_convergence_trace,
    plot_frequency_bars,
)
from fim.viz.scatter import (
    frequency_points,
    marker_groups,
    plot_frequency_scatter,
    pooled_frequency_points,
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


def test_large_dimensions_render_labeled_pca_projection() -> None:
    """Large d falls back to an explicitly labeled projection."""
    figure = plot_frequency_scatter(_state(7), _params(7))

    assert len(figure.axes) == 1
    assert "PCA projection" in figure.axes[0].get_title()
    plt.close(figure)


def test_pca_projection_handles_a_single_point_without_svd() -> None:
    """A one-row point matrix (every deme fixed for the same allele) skips SVD.

    `numpy.linalg.svd` is not called at all when there is only one
    (locus, allele) point to project — `_plot_pca` special-cases it to
    avoid a degenerate decomposition. Seven demes keeps this in the PCA
    branch (`d > PAIRWISE_MAX_DEMES`); fixing every deme for the same
    single allele collapses the whole state to exactly one point.
    """
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=tuple(({AlleleId(0): 1.0},) for _ in range(7)),
    )

    figure = plot_frequency_scatter(state, _params(7))

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
