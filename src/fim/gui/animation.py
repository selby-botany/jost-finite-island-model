"""Sample and pre-render animation frames from a persisted trajectory
(design doc §3.8).

A converged run can persist hundreds or thousands of generations, and
rendering every one of them as a separate Matplotlib frame is both slow
and unnecessary for a human watching a scatter drift. This module
samples at most `GUI_ANIMATION_MAX_FRAMES` generations, evenly spaced
across the run's persisted range, always including generation 0 and the
final generation, and pre-renders each as its own `Figure` — reusing
`fim.viz.scatter.plot_frequency_scatter` per sampled generation, the
same call the results screen embeds its own live scatter with
(design §3.5).

Reached only after the same trajectory-integrity check §4.6 already
performed: Screen 5 (`fim.gui.screens.animation_screen`) is reached
only from Screen 3, which by the time "Animate" is clickable has
already shown a `ResultsView` built either from a just-completed live
run (whose manifest was just written, never edited) or from
`fim.reanalyze.reanalyze_trajectory` (which calls
`fim.persistence.manifest.verify_trajectory_integrity` itself). This
module therefore reads the trajectory directly, trusting the caller,
rather than re-verifying it a second time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from matplotlib.figure import Figure

from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.reanalyze import group_rows_by_generation
from fim.viz.scatter import plot_frequency_scatter

GUI_ANIMATION_MAX_FRAMES: Final = 100


@dataclass(frozen=True, slots=True)
class AnimationFrame:
    """One pre-rendered animation frame.

    Args:
        generation: The persisted generation this frame renders.
        figure: The rendered scatter figure. Caller-owned: whoever
            pre-rendered it is responsible for closing it
            (`matplotlib.pyplot.close`) once it is no longer needed —
            this module never closes a figure it returns.
    """

    generation: int
    figure: Figure


def pre_render_frames(
    trajectory_path: Path,
    params: SimulationParams,
    run_id: str,
    *,
    max_frames: int = GUI_ANIMATION_MAX_FRAMES,
) -> list[AnimationFrame]:
    """Sample and pre-render up to `max_frames` frames from a persisted trajectory.

    Args:
        trajectory_path: The `trajectory.jsonl` to read.
        params: The run's validated parameters.
        run_id: The run identity every row must belong to.
        max_frames: See `select_sample_generations`.

    Returns:
        One `AnimationFrame` per sampled generation, sorted ascending
        by generation. Each figure is built independently — not
        written to disk. The caller owns closing every one of them.
    """
    grouped = group_rows_by_generation(trajectory_path, run_id)
    sampled = select_sample_generations(sorted(grouped), max_frames)
    frames: list[AnimationFrame] = []
    for generation in sampled:
        state = ModelState.from_rows(grouped[generation], params.loci)
        figure = plot_frequency_scatter(state, params, None)
        frames.append(AnimationFrame(generation=generation, figure=figure))
    return frames


def select_sample_generations(
    available_generations: Sequence[int],
    max_frames: int = GUI_ANIMATION_MAX_FRAMES,
) -> list[int]:
    """Return at most `max_frames` generation numbers, evenly spaced.

    Args:
        available_generations: Every persisted generation number (need
            not be sorted or unique).
        max_frames: The largest number of generations to return.

    Returns:
        A strictly ascending, deduplicated list of at most
        `max_frames` generation numbers, drawn from
        `available_generations`. Always includes the lowest and the
        highest generation number when `max_frames >= 2` and
        `available_generations` is non-empty (design §3.8: "always
        including generation 0 and the final generation"). Returns
        every available generation, sorted, when there are
        `max_frames` or fewer of them. `max_frames <= 0` returns
        `[]`; `max_frames == 1` returns only the highest generation —
        a run's terminal state is the single most informative frame
        to keep alone.
    """
    unique_sorted = sorted(set(available_generations))
    if not unique_sorted or max_frames <= 0:
        return []
    if max_frames == 1:
        return [unique_sorted[-1]]
    if len(unique_sorted) <= max_frames:
        return unique_sorted
    last_index = len(unique_sorted) - 1
    sampled_indices = sorted(
        {round(step * last_index / (max_frames - 1)) for step in range(max_frames)}
    )
    return [unique_sorted[index] for index in sampled_indices]
