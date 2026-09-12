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

import bisect
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.store import TrajectoryRow
from fim.reanalyze import group_rows_by_generation
from fim.viz.scatter import FloatArray, frequency_points, pooled_frequency_points

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


def pre_render_batch_frames(
    replicates: Sequence[tuple[str, Path]],
    params: SimulationParams,
    *,
    max_frames: int = GUI_ANIMATION_MAX_FRAMES,
) -> list[AnimationFrame]:
    """Sample up to `max_frames` *pooled* frames' worth of coordinates from a batch.

    The completed-batch counterpart to `pre_render_frames`, needed
    because a batch has no single `trajectory.jsonl` to sample from —
    one per replicate instead, each stopping at its own generation
    (batch trajectory panel design `20260912-claude-sonnet-5-batch-
    trajectory-panel-design.md`, `selby/restricted`). A replicate that
    already stopped by a given sampled generation contributes its own
    *final* state at that point rather than dropping out of the pooled
    frame entirely — the identical "hold each replicate's own last
    value constant once it stops" choice `fim.engine.pooled_
    convergence_histories` already makes for the trajectory panel's own
    confidence band, applied here to the scatter instead: dropping a
    converged replicate from later frames would be the same
    survivorship-biased picture that function's own docstring explains
    was a real, reported defect for the band.

    Args:
        replicates: One `(run_id, trajectory_path)` pair per replicate
            — `run_id` is that replicate's own id (`RunResult.run_id`,
            `"{batch_run_id}-r{index:03}"`), not the batch's own id,
            matching every row's own recorded `run_id` in that
            replicate's `trajectory.jsonl`.
        params: The batch's own validated parameters, shared by every
            replicate.
        max_frames: See `select_sample_generations`.

    Returns:
        One `AnimationFrame` per sampled generation, sorted ascending
        by generation, each `points` already pooled across every
        replicate (`fim.viz.scatter.pooled_frequency_points`) — the
        same per-frame shape `pre_render_frames` returns for a single
        replicate, so the bridge method building the client payload
        (`Api.get_batch_animation_frames`) converts it with the
        identical `panels_from_points` call `get_animation_frames`
        already uses, no batch-specific client shape needed. Empty if
        no replicate has persisted anything yet.
    """
    per_replicate: list[tuple[list[int], dict[int, list[TrajectoryRow]]]] = []
    for run_id, trajectory_path in replicates:
        grouped = group_rows_by_generation(trajectory_path, run_id)
        if grouped:
            per_replicate.append((sorted(grouped), grouped))
    if not per_replicate:
        return []
    max_generation = max(generations[-1] for generations, _ in per_replicate)
    sampled = select_sample_generations(range(max_generation + 1), max_frames)
    frames: list[AnimationFrame] = []
    for generation in sampled:
        states = []
        for generations, grouped in per_replicate:
            # The largest recorded generation at or before this sampled
            # one -- always found (`index >= 0`), since every
            # replicate's own first recorded generation is 0
            # (`_run_one`'s own docstring: persisted unconditionally
            # before the main loop ever runs) and every sampled
            # generation is itself `>= 0`.
            index = bisect.bisect_right(generations, generation) - 1
            use_generation = generations[index]
            states.append(ModelState.from_rows(grouped[use_generation], params.loci))
        points = pooled_frequency_points(states)
        frames.append(AnimationFrame(generation=generation, points=points))
    logger.debug(
        "pre-rendered %d pooled batch animation frame(s) from %d replicate(s)",
        len(frames),
        len(per_replicate),
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
