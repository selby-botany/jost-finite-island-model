"""Re-analyze a persisted trajectory (design doc §3.8).

Extracted from `fim.cli._command_stats` so every consumer that needs to
"read a persisted `trajectory.jsonl` the same way `cli._command_stats`
already does" (§3.8) — Screen 6, "open an existing run" (§4.6), and
Screen 5, "animated trajectory" (§4.5, via `group_rows_by_generation`)
— shares the exact same algorithm `fim stats` uses, rather than a
second, independently maintained copy of it. The developer guide's "do
not duplicate model logic" rule, applied to trajectory re-analysis
instead of engine logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fim.engine import report_for_state
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import (
    RunManifest,
    read_manifest,
    verify_trajectory_integrity,
)
from fim.persistence.store import TrajectoryRow
from fim.statistics.differentiation import differentiation_q


@dataclass(frozen=True, slots=True)
class ReanalyzedGeneration:
    """One re-analyzed generation's manifest, params, state, and report.

    Args:
        manifest: The run's manifest, as recorded at completion time.
        params: The run's validated parameters, reconstructed from the
            manifest (`RunManifest.params()`).
        state: The selected generation's model state.
        report: The same JSON-serializable mapping `fim stats` prints —
            `fim.engine.FinalReport`'s fields, plus `"Differentiation_q"`
            when `reanalyze_trajectory`'s `differentiation_orders` was
            non-empty.
    """

    manifest: RunManifest
    params: SimulationParams
    state: ModelState
    report: dict[str, object]


def differentiation_q_for_state(
    state: ModelState,
    params: SimulationParams,
    order: float,
) -> float:
    """Average the requested differentiation order across loci.

    `deme_weighting` has a defined effect only at ``q = 1``
    (`fim.statistics.differentiation.differentiation_q` raises if
    weights are passed at any other order) — the same order that
    matches `E_ST`. Deriving the weights the same way
    `fim.engine._statistics_for_locus` does keeps `Differentiation_1`
    here identical to the report's own `E_ST`, rather than the two
    silently disagreeing whenever `deme_weighting` is `"size"`.
    """
    weights = (
        params.population_sizes
        if order == 1.0 and params.deme_weighting == "size"
        else None
    )
    values: list[float] = []
    for locus_index in range(state.locus_count):
        table = [
            {
                int(allele_id): frequency
                for allele_id, frequency in state.frequency_map(
                    deme_index,
                    locus_index,
                ).items()
            }
            for deme_index in range(state.deme_count)
        ]
        values.append(differentiation_q(table, order, weights))
    return sum(values) / len(values)


def group_rows_by_generation(
    trajectory_path: Path,
    run_id: str,
) -> dict[int, list[TrajectoryRow]]:
    """Group every persisted row by its generation number, in stored order.

    Shared by `reanalyze_trajectory` — which instead filters `rows` to
    just the one selected generation, matching `cli._command_stats`'s
    own exact algorithm — and the animation screen's frame sampler
    (design §3.8: several, evenly spaced generations), which needs
    every persisted generation's rows available at once; only how many
    of the resulting generations each caller turns into a `ModelState`
    differs.

    Args:
        trajectory_path: The `trajectory.jsonl` to read.
        run_id: The run identity every row must belong to.

    Returns:
        Every persisted generation's rows, keyed by generation number.
    """
    grouped: dict[int, list[TrajectoryRow]] = {}
    for row in JSONLTrajectoryStore(trajectory_path).read(run_id):
        grouped.setdefault(row["generation"], []).append(row)
    return grouped


def reanalyze_trajectory(
    trajectory_path: Path,
    *,
    manifest_path: Path | None = None,
    generation: int | None = None,
    differentiation_orders: Sequence[float] = (),
) -> ReanalyzedGeneration:
    """Recompute one generation's statistics from a persisted trajectory.

    The exact algorithm `fim stats` runs: verify the trajectory against
    its manifest's recorded digest, verify the trajectory's observed
    generation count still matches the manifest's, select a generation,
    and build its report — optionally including a differentiation-q
    sweep.

    Args:
        trajectory_path: The `trajectory.jsonl` to read.
        manifest_path: Its companion manifest; defaults to
            `trajectory_path.with_name("manifest.json")`, `fim stats`'s
            own default.
        generation: Generation to analyze; defaults to the run's final
            persisted generation.
        differentiation_orders: Optional differentiation-q sweep
            orders; each becomes one `"Differentiation_q"` entry in the
            returned report.

    Returns:
        The manifest, validated params, the selected generation's
        state, and its report.

    Raises:
        ValueError: If the trajectory has been edited, truncated, or
            replaced since the run completed, has no rows, or the
            requested generation does not exist.
    """
    manifest = read_manifest(
        manifest_path
        if manifest_path is not None
        else trajectory_path.with_name("manifest.json")
    )
    verify_trajectory_integrity(trajectory_path, manifest)
    params = manifest.params()
    rows = list(JSONLTrajectoryStore(trajectory_path).read(manifest.run_id))
    if not rows:
        raise ValueError(f"trajectory has no rows for {manifest.run_id}")
    observed_generation_count = len({row["generation"] for row in rows})
    if observed_generation_count != manifest.generation_count:
        raise ValueError(
            f"trajectory has {observed_generation_count} generation(s), "
            f"manifest records {manifest.generation_count} — the file may "
            "have been edited since the run completed"
        )
    resolved_generation = (
        generation if generation is not None else max(row["generation"] for row in rows)
    )
    generation_rows = [row for row in rows if row["generation"] == resolved_generation]
    if not generation_rows:
        raise ValueError(f"trajectory has no generation {resolved_generation}")
    state = ModelState.from_rows(generation_rows, params.loci)
    final_generation = resolved_generation == manifest.generation
    report: dict[str, object] = dict(
        report_for_state(
            state,
            params,
            run_id=manifest.run_id,
            converged=manifest.converged if final_generation else False,
            reason=manifest.stop_reason if final_generation else "re-analysis",
        )
    )
    if differentiation_orders:
        report["Differentiation_q"] = {
            str(order): differentiation_q_for_state(state, params, order)
            for order in differentiation_orders
        }
    return ReanalyzedGeneration(
        manifest=manifest, params=params, state=state, report=report
    )
