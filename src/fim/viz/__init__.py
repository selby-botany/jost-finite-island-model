"""Headless visualizations for simulation results and diagnostics."""

from fim.viz.diagnostics import (
    plot_convergence_trace,
    plot_frequency_bars,
)
from fim.viz.scatter import plot_frequency_scatter

__all__ = [
    "plot_convergence_trace",
    "plot_frequency_bars",
    "plot_frequency_scatter",
]
