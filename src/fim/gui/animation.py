"""Sample animation frames from a persisted trajectory as raw scatter
coordinates, not rendered images (`doc/fim-gui-design.md` §8).

A converged run can persist hundreds or thousands of generations, and
turning every one of them into a separate frame is both slow and
unnecessary for a human watching a scatter drift. This module samples at
most `GUI_ANIMATION_MAX_FRAMES` generations, evenly spaced across the
run's persisted range, always including generation 0 and the final
generation — unchanged from an earlier, Tk-era revision of this module
— but each sampled generation now produces a plain coordinate array
(`fim.viz.scatter.frequency_points`), not a rendered `Figure`:
pywebview's own scrubber ships the whole sampled set to the page once
and drives play/pause/scrub entirely with client-side Canvas redraws
(`doc/fim-gui-design.md` §5.2, §8), so nothing here needs to render
anything at all. This also makes pre-computation itself cheaper, not
just playback: building `max_frames` coordinate arrays costs a
fraction of what building `max_frames` Matplotlib figures did, since
no rasterization happens on this path.

Reached only after the trajectory's integrity has already been
verified — the unified run view's `completed` state, reached either by
a run that just finished (its manifest was just written, never edited)
or by opening a persisted run (`fim.reanalyze.reanalyze_trajectory`
itself calls `fim.persistence.manifest.verify_trajectory_integrity`
first). This module therefore reads the trajectory directly, trusting
the caller, rather than re-verifying it a second time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.reanalyze import group_rows_by_generation
from fim.viz.scatter import FloatArray, frequency_points

GUI_ANIMATION_MAX_FRAMES: Final = 100

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnimationFrame:
    """One sampled animation frame's raw scatter coordinates.

    Args:
        generation: The persisted generation this frame represents.
        points: `frequency_points`' own return shape — one row per
            (locus, allele) pair, one column per deme. Whoever renders
            this (the GUI bridge, `doc/fim-gui-design.md` §4) is
            responsible for any further reduction a high deme count
            needs (the pairwise-grid or first-deme-pair cases
            `panels_from_points` itself handles) and for the
            client-side Canvas draw itself; this module never touches
            either.
    """

    generation: int
    points: FloatArray


def pre_render_frames(
    trajectory_path: Path,
    params: SimulationParams,
    run_id: str,
    *,
    max_frames: int = GUI_ANIMATION_MAX_FRAMES,
) -> list[AnimationFrame]:
    """Sample up to `max_frames` frames' worth of coordinates from a trajectory.

    Args:
        trajectory_path: The `trajectory.jsonl` to read.
        params: The run's validated parameters.
        run_id: The run identity every row must belong to.
        max_frames: See `select_sample_generations`.

    Returns:
        One `AnimationFrame` per sampled generation, sorted ascending by
        generation. No rendering happens on this path at all — each
        frame's `points` is a plain `frequency_points` array, computed
        directly from the persisted rows, nothing written to disk and
        nothing for the caller to close.
    """
    grouped = group_rows_by_generation(trajectory_path, run_id)
    sampled = select_sample_generations(sorted(grouped), max_frames)
    frames: list[AnimationFrame] = []
    for generation in sampled:
        state = ModelState.from_rows(grouped[generation], params.loci)
        points = frequency_points(state)
        frames.append(AnimationFrame(generation=generation, points=points))
    logger.debug(
        "pre-rendered %d animation frame(s) from %d persisted generation(s) in %s",
        len(frames),
        len(grouped),
        trajectory_path,
    )
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
        `available_generations` is non-empty (always including
        generation 0 and the final generation). Returns
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
