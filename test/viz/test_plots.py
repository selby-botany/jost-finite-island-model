"""Structural smoke tests for headless visualizations."""

from pathlib import Path

from matplotlib import pyplot as plt

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.viz.diagnostics import plot_convergence_trace, plot_frequency_bars
from fim.viz.scatter import plot_frequency_scatter


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


def test_direct_scatter_has_deme_axes_and_parameter_title(tmp_path: Path) -> None:
    """Two demes render directly with self-describing metadata."""
    output = tmp_path / "scatter.png"
    figure = plot_frequency_scatter(_state(2), _params(2), output)

    assert output.is_file()
    assert len(figure.axes) == 1
    assert figure.axes[0].get_xlabel() == "Deme 1"
    assert figure.axes[0].get_ylabel() == "Deme 2"
    assert "N=20" in figure._suptitle.get_text()
    plt.close(figure)


def test_moderate_dimensions_render_pairwise_matrix() -> None:
    """Four demes produce all six pairwise panels."""
    figure = plot_frequency_scatter(_state(4), _params(4))

    assert len([axis for axis in figure.axes if axis.get_visible()]) == 6
    plt.close(figure)


def test_three_demes_render_direct_three_dimensional_axes() -> None:
    """Three demes retain direct coordinates rather than using a projection."""
    figure = plot_frequency_scatter(_state(3), _params(3))
    axis = figure.axes[0]

    assert axis.get_xlabel() == "Deme 1"
    assert axis.get_ylabel() == "Deme 2"
    assert axis.get_zlabel() == "Deme 3"
    plt.close(figure)


def test_large_dimensions_render_labeled_pca_projection() -> None:
    """Large d falls back to an explicitly labeled projection."""
    figure = plot_frequency_scatter(_state(7), _params(7))

    assert len(figure.axes) == 1
    assert "PCA projection" in figure.axes[0].get_title()
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

    assert len(markers.get_sizes()) == 2
    assert len({tuple(color[:3]) for color in markers.get_facecolors()}) == 2
    assert {text.get_text() for text in axis.texts} == {"2"}
    plt.close(figure)


def test_diagnostic_views_have_one_trace_and_one_bar_per_deme() -> None:
    """Supporting views preserve the expected observation count."""
    trace = plot_convergence_trace([0, 1, 2], [0.1, 0.2, 0.2], "D")
    bars = plot_frequency_bars(_state(3))

    assert len(trace.axes[0].lines[0].get_xdata()) == 3
    assert len(bars.axes[0].patches) == 6
    plt.close(trace)
    plt.close(bars)
