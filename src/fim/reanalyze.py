"""Re-analyze a persisted trajectory (design doc §3.8).

Every generation of a completed run is saved to disk, as one row per
deme/locus/allele combination actually present that generation (in a
file called `trajectory.jsonl` — see `fim.persistence`). "Re-analyzing"
that file means reading it back afterward and computing fresh statistics
from it — the differentiation numbers for whichever generation you
actually want to look at, computed the exact same way they were the
first time, without needing to re-run the simulation itself at all. This
is what makes it possible to, for example, open a run you finished last
week and see its statistics at generation 200 even though the run itself
stopped (and was reported) at generation 500 — the full history was
saved, so any of it can be revisited later.

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

    What `reanalyze_trajectory` (below) actually returns: everything
    needed to display one specific, chosen generation of a previously
    completed run — not just its statistics, but the full state that
    produced them and the run's own original bookkeeping, bundled
    together so a caller never has to separately go fetch any of it.

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

    `order` here is what the
    [differentiation-measures guide](../../doc/jost-differentiation-measures.md)
    calls "q": a single number that a whole family of differentiation
    measures turns out to be special cases of, once written in a common
    mathematical form — `differentiation_q(table, order=0, ...)` is
    exactly `K_ST`, `order=1` is exactly `E_ST`, and `order=2` is exactly
    Jost's `D`, all from literally the same underlying formula, just
    evaluated at a different value of `order`. Reporting several
    different `order` values side by side for the same run — a
    "differentiation-q sweep" — is one way of seeing how sensitive a
    conclusion is to which particular measure happened to be chosen,
    since (as the linked guide explains in depth) different measures can
    genuinely disagree about how differentiated the very same population
    actually is.

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

    A `trajectory.jsonl` file already stores its rows generation by
    generation, in the order they were written during the original run —
    but as one long, flat sequence, not indexed for picking out a
    specific generation's own rows directly. This function reads that
    whole sequence once and turns it into exactly that index (a mapping
    from generation number straight to that generation's own rows), so a
    caller can then look up whichever specific generation(s) it actually
    needs without re-scanning the file for each one.

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

    This is the function behind `fim stats` and "open an existing run"
    (see this module's own docstring, above, for what re-analysis means
    and why it is useful) — the exact same algorithm both go through:
    load the run's own manifest (its recorded bookkeeping — see
    `fim.engine.RunResult`'s own docstring for what a manifest is),
    confirm the trajectory file has not been tampered with or corrupted
    since the run finished (by checking it against a checksum — a short
    fingerprint computed from the file's own content, recorded in the
    manifest at the time the run completed, and any edit to the file
    changes that fingerprint, so a mismatch reveals the file was altered
    — see `fim.persistence.manifest.verify_trajectory_integrity`), pick
    out the one generation actually being asked for, and build that
    generation's own report.

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
    # The tamper/corruption check described in this function's own
    # docstring, above; raises before anything else here does real work
    # if the trajectory file no longer matches what the manifest
    # recorded about it.
    verify_trajectory_integrity(trajectory_path, manifest)
    params = manifest.params()
    rows = list(JSONLTrajectoryStore(trajectory_path).read(manifest.run_id))
    if not rows:
        raise ValueError(f"trajectory has no rows for {manifest.run_id}")
    # A second, independent consistency check beyond the checksum above:
    # even a file whose bytes match their own recorded fingerprint could
    # in principle be paired with the *wrong* manifest (one from a
    # different run) — comparing how many distinct generations are
    # actually present against how many the manifest claims catches that
    # mismatch too.
    observed_generation_count = len({row["generation"] for row in rows})
    if observed_generation_count != manifest.generation_count:
        raise ValueError(
            f"trajectory has {observed_generation_count} generation(s), "
            f"manifest records {manifest.generation_count} — the file may "
            "have been edited since the run completed"
        )
    # No `generation` requested (the ordinary case) means "show me the
    # final result," exactly like `fim stats` with no extra flags —
    # the highest generation number actually present in the file.
    resolved_generation = (
        generation if generation is not None else max(row["generation"] for row in rows)
    )
    generation_rows = [row for row in rows if row["generation"] == resolved_generation]
    if not generation_rows:
        raise ValueError(f"trajectory has no generation {resolved_generation}")
    state = ModelState.from_rows(generation_rows, params.loci)
    # Whether this is the run's own *final* generation matters for how
    # honestly the report can describe why it stopped: only the true
    # final generation actually reflects the original run's own
    # converged/stop-reason outcome. Re-analyzing any *earlier*
    # generation reports it plainly as "re-analysis," rather than
    # reusing the final outcome's own `converged`/`reason` values, which
    # would misleadingly claim this earlier generation converged (or
    # give its own specific stop reason) when it did not actually stop
    # there at all — the run simply kept going past it.
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
        # See `differentiation_q_for_state`'s own docstring for what a
        # "differentiation-q sweep" is and why someone would want one.
        report["Differentiation_q"] = {
            str(order): differentiation_q_for_state(state, params, order)
            for order in differentiation_orders
        }
    return ReanalyzedGeneration(
        manifest=manifest, params=params, state=state, report=report
    )
