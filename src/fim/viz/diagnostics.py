"""Convergence and per-deme frequency diagnostic plots."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=True)

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from fim.model.allele import AlleleId
from fim.model.state import ModelState

MAX_LEGEND_ALLELES = 12

logger = logging.getLogger(__name__)


def plot_convergence_trace(
    generations: Sequence[int],
    values: Sequence[float],
    statistic: str,
    path: Path | str | None = None,
) -> Figure:
    """Plot one convergence-statistic value per recorded generation.

    Args:
        generations: Ordered generation numbers.
        values: Statistic values aligned with ``generations``.
        statistic: Display name for the watched statistic.
        path: Optional PNG output path.

    Returns:
        The created figure.
    """
    if len(generations) != len(values):
        raise ValueError("generations and values must have the same length")
    if not generations:
        raise ValueError("a convergence trace needs at least one observation")
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(generations, values, color="tab:blue")
    axis.set_xlabel("Generation")
    axis.set_ylabel(statistic)
    axis.set_title(f"Convergence trace: {statistic}")
    _save(figure, path)
    return figure


def plot_frequency_bars(
    state: ModelState,
    path: Path | str | None = None,
    *,
    locus_index: int = 0,
) -> Figure:
    """Plot a STRUCTURE-style stacked frequency bar for every deme.

    Args:
        state: State containing the requested locus.
        path: Optional PNG output path.
        locus_index: Zero-based locus index to plot.

    Returns:
        The created figure.
    """
    if not 0 <= locus_index < state.locus_count:
        raise ValueError("locus_index is outside the state")
    allele_ids: dict[int, None] = {}
    for deme_index in range(state.deme_count):
        for allele_id in state.frequency_map(deme_index, locus_index):
            allele_ids.setdefault(int(allele_id), None)
    figure, axis = plt.subplots(figsize=(max(6, state.deme_count * 0.6), 5))
    horizontal = np.arange(state.deme_count)
    bottom = np.zeros(state.deme_count, dtype=np.float64)
    for allele_number in allele_ids:
        heights = np.asarray(
            [
                state.frequency_map(deme_index, locus_index).get(
                    AlleleId(allele_number),
                    0.0,
                )
                for deme_index in range(state.deme_count)
            ],
            dtype=np.float64,
        )
        axis.bar(
            horizontal,
            heights,
            bottom=bottom,
            width=0.8,
            label=f"Allele {allele_number}",
        )
        bottom += heights
    axis.set_xticks(
        horizontal, [str(index) for index in range(1, state.deme_count + 1)]
    )
    axis.set_xlabel("Deme")
    axis.set_ylabel("Allele frequency")
    axis.set_ylim(0.0, 1.0)
    axis.set_title(f"Locus {state.loci[locus_index].locus_id} frequencies")
    if len(allele_ids) <= MAX_LEGEND_ALLELES:
        axis.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))
    figure.tight_layout()
    _save(figure, path)
    return figure


def _save(figure: Figure, path: Path | str | None) -> None:
    """Write a figure if an output path was supplied."""
    if path is None:
        return
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=150, metadata={"Software": "fim"})
    logger.debug("wrote diagnostic figure: %s", output_path)
