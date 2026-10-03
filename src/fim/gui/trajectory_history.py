"""Sample a full statistic-vs-generation history from a persisted
trajectory, for the Compare workspace's own trajectory-overlay panel
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
redesign.md` §8: "overlay their trajectory plots on one set of axes,
one color per run").

`fim.reanalyze.reanalyze_trajectory` already recomputes statistics for
one chosen generation of a persisted run; this module does the same
underlying work — verify the trajectory's own integrity, group its rows
by generation, rebuild a `ModelState` and call `report_for_state` — but
for a whole, evenly-spaced sample of generations at once, not just one,
so a full curve can be drawn for a run that was never watched live.

Reuses `fim.gui.animation.select_sample_generations`/`GUI_ANIMATION_
MAX_FRAMES` exactly as already established for the animation screen's
own frame sampler, rather than inventing a second, independent sampling
density: a run that persisted hundreds or thousands of generations
should not turn into hundreds or thousands of `report_for_state` calls
here any more than it turns into that many rendered animation frames,
for the identical reason (`fim.gui.animation`'s own module docstring).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from fim.engine import report_for_state
from fim.gui.animation import GUI_ANIMATION_MAX_FRAMES, select_sample_generations
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import (
    RunManifest,
    read_manifest,
    verify_trajectory_integrity,
)
from fim.reanalyze import group_rows_by_generation
from fim.statistics.catalog import report_keys

# Every global statistic `report_for_state` computes, from
# `fim.statistics.catalog` -- the same list `fim.gui.app.
# _RESULT_STATISTIC_NAMES` derives from, so the two can no longer drift
# (they once did: `A_CGD` in one but not the other raised `KeyError`
# in `Api.compare_runs`). `report_for_state` computes every one of them
# for any persisted trajectory, `track_expensive_statistics` or not.
STATISTIC_NAMES: Final = report_keys()


@dataclass(frozen=True, slots=True)
class TrajectoryHistory:
    """One run's own sampled statistic-vs-generation history.

    Args:
        manifest: The run's manifest, as recorded at completion time.
        params: The run's validated parameters, reconstructed from the
            manifest.
        generations: The sampled generation numbers, strictly ascending
            (`select_sample_generations`'s own contract).
        histories: One list per requested statistic name, each the same
            length as `generations` and in the same order — a `None`
            entry means that statistic was undefined at that specific
            sampled generation (`G_ST` at a currently-monomorphic
            locus, the one named case `fim.engine.report_for_state`
            itself can produce), not a missing sample; the caller
            decides how to skip it, the same "leave a statistic's own
            history shorter than `generations`" choice `webui/screens/
            run-view-completed.js`'s own `renderTrajectory` already
            makes for a live run's identical edge case.
    """

    manifest: RunManifest
    params: SimulationParams
    generations: list[int]
    histories: dict[str, list[float | None]]


def sampled_statistic_history(
    trajectory_path: Path,
    *,
    manifest_path: Path | None = None,
    max_samples: int = GUI_ANIMATION_MAX_FRAMES,
) -> TrajectoryHistory:
    """Sample up to `max_samples` generations' worth of `STATISTIC_NAMES`.

    Args:
        trajectory_path: The `trajectory.jsonl` to read.
        manifest_path: Its companion manifest; defaults to
            `trajectory_path.with_name("manifest.json")`, matching
            `reanalyze_trajectory`'s own default.
        max_samples: See `select_sample_generations`.

    Returns:
        The run's own manifest/params, the sampled generation numbers,
        and each requested statistic's own value at every one of them.

    Raises:
        ValueError: If the trajectory has been edited, truncated, or
            replaced since the run completed, or has no rows —
            identical failure modes to `reanalyze_trajectory`, checked
            the same way.
    """
    manifest = read_manifest(
        manifest_path
        if manifest_path is not None
        else trajectory_path.with_name("manifest.json")
    )
    verify_trajectory_integrity(trajectory_path, manifest)
    params = manifest.params()
    grouped = group_rows_by_generation(trajectory_path, manifest.run_id)
    if not grouped:
        raise ValueError(f"trajectory has no rows for {manifest.run_id}")
    sampled = select_sample_generations(sorted(grouped), max_samples)
    histories: dict[str, list[float | None]] = {name: [] for name in STATISTIC_NAMES}
    for generation in sampled:
        state = ModelState.from_rows(grouped[generation], params.loci)
        final_generation = generation == manifest.generation
        report = report_for_state(
            state,
            params,
            run_id=manifest.run_id,
            converged=manifest.converged if final_generation else False,
            reason=manifest.stop_reason if final_generation else "re-analysis",
        )
        for name in STATISTIC_NAMES:
            histories[name].append(cast("Mapping[str, float | None]", report)[name])
    return TrajectoryHistory(
        manifest=manifest, params=params, generations=sampled, histories=histories
    )
