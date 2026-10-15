"""Run a configuration and save the result as a directory.

A saved run is built in a hidden sibling directory and published with one
atomic rename (`fim.paths.atomic_directory`), so a run interrupted anywhere
leaves no trace at its target. A single replicate is a flat directory
(trajectory log, report, scatter plot, convergence history, deme-pair
statistics, manifest). A batch has one `replicate-NNN/` subdirectory per
replicate in that same layout, plus a batch-level `summary.json` and
`manifest.json` that record a digest of each.

The command line, the desktop app, and `fim.simulate` all save runs through
this module.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from functools import partial
from pathlib import Path

from fim import __version__, paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.config.expert import ExpertSettings
from fim.engine import RunResult, replicate_summary, simulate_params
from fim.engine.identity import deterministic_run_id
from fim.engine.records import Clock, format_timestamp, utc_now
from fim.model.params import SimulationParams
from fim.persistence.artifacts import run_artifact_targets, write_run_artifacts
from fim.persistence.binary_store import (
    TRAJECTORY_LOG_FILENAME,
    BinaryLogStore,
    log_options,
)
from fim.persistence.manifest import (
    CURRENT_BATCH_SCHEMA_VERSION,
    ArtifactDigest,
    BatchManifest,
    hash_file,
    write_batch_manifest,
)
from fim.persistence.report import write_report

logger = logging.getLogger(__name__)

__all__ = [
    "prune_orphan_replicate_directories",
    "replicate_index",
    "replicate_output_directory",
    "replicate_store_factory",
    "save_run",
    "write_batch_artifacts",
]


def replicate_index(batch_run_id: str, replicate_run_id: str) -> int:
    """Return one replicate's 1-based ordinal within its batch.

    A replicate's run ID is always ``f"{batch_run_id}-r{index:03}"``, so the
    index is recovered from it directly.

    Args:
        batch_run_id: The batch's run ID.
        replicate_run_id: A replicate's run ID.

    Returns:
        The ordinal, from 1.
    """
    return int(replicate_run_id.removeprefix(f"{batch_run_id}-r"))


def replicate_output_directory(
    base: Path, batch_run_id: str, replicate_run_id: str
) -> Path:
    """Return one replicate's own artifact subdirectory.

    Args:
        base: The batch directory.
        batch_run_id: The batch's run ID.
        replicate_run_id: A replicate's run ID.

    Returns:
        ``base / "replicate-NNN"``.
    """
    return base / f"replicate-{replicate_index(batch_run_id, replicate_run_id):03}"


def prune_orphan_replicate_directories(
    working_directory: Path,
    batch_run_id: str,
    published_run_ids: frozenset[str],
) -> None:
    """Remove any replicate directory not among the batch's results.

    Under worker processes the adaptive stop is applied only after a whole
    wave completes, in replicate order. A worker beyond the replicate that
    triggered the stop still runs to completion, so its directory already
    exists even though its result is discarded. Without this pass the
    orphan would be published, present on disk and absent from the summary
    and manifest.

    Args:
        working_directory: The batch's temporary build directory.
        batch_run_id: The batch's run ID.
        published_run_ids: Every replicate run ID the engine returned.
    """
    for entry in sorted(working_directory.glob("replicate-*")):
        if not entry.is_dir():
            continue
        replicate_run_id = f"{batch_run_id}-r{entry.name.removeprefix('replicate-')}"
        if replicate_run_id not in published_run_ids:
            shutil.rmtree(entry)


def replicate_store_factory(
    output_directory: Path,
    batch_run_id: str,
    expert: ExpertSettings,
    replicate_run_id: str,
) -> BinaryLogStore:
    """Build one replicate's on-disk trajectory store.

    Module-level so a worker process can pickle a reference to it (bind the
    leading arguments with `functools.partial`, never a closure).

    Args:
        output_directory: The batch directory.
        batch_run_id: The batch's run ID.
        expert: Settings that tune the log.
        replicate_run_id: The replicate being stored.

    Returns:
        A store writing `replicate-NNN/trajectory.tlog`.
    """
    directory = replicate_output_directory(
        output_directory, batch_run_id, replicate_run_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    return BinaryLogStore(directory / TRAJECTORY_LOG_FILENAME, **log_options(expert))


def write_batch_artifacts(
    results: tuple[RunResult, ...],
    working_directory: Path,
    run_id: str,
    params: SimulationParams,
    started_at: str,
    ended_at: str,
    *,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    after_replicate: Callable[[Path], None] | None = None,
) -> None:
    """Write every replicate's files, then the batch summary and manifest.

    Orphan replicate directories are pruned first. Each kept replicate gets
    the single-run layout; then `summary.json`; then, last, the batch
    `manifest.json` with a digest of each replicate manifest and of the
    summary.

    Args:
        results: The replicates the engine returned.
        working_directory: The batch's temporary build directory.
        run_id: The batch's run ID.
        params: The batch's parameters.
        started_at: Start time text (UTC).
        ended_at: End time text (UTC).
        pairwise_max_demes: Largest deme count with deme-pair statistics.
        after_replicate: Called with each replicate's directory once its
            files are written (the desktop app removes a progress sidecar).
    """
    prune_orphan_replicate_directories(
        working_directory, run_id, frozenset(result.run_id for result in results)
    )
    artifact_digests: dict[str, ArtifactDigest] = {}
    for result in results:
        directory = replicate_output_directory(working_directory, run_id, result.run_id)
        logger.debug("writing replicate artifacts to %s", directory)
        write_run_artifacts(result, directory, pairwise_max_demes=pairwise_max_demes)
        if after_replicate is not None:
            after_replicate(directory)
        artifact_digests[directory.name] = hash_file(directory / "manifest.json")
    write_report(working_directory / "summary.json", replicate_summary(results))
    artifact_digests["summary"] = hash_file(working_directory / "summary.json")
    write_batch_manifest(
        working_directory / "manifest.json",
        BatchManifest(
            schema_version=CURRENT_BATCH_SCHEMA_VERSION,
            run_id=run_id,
            replicate_run_ids=tuple(result.run_id for result in results),
            parameters=params.to_dict(),
            started_at=started_at,
            ended_at=ended_at,
            software_version=__version__,
            artifacts=artifact_digests,
        ),
    )


def save_run(
    params: SimulationParams,
    directory: Path,
    *,
    max_workers: int | None = None,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    clock: Clock | None = None,
) -> tuple[RunResult, ...]:
    """Run `params` and publish the result at `directory`.

    Args:
        params: The validated run configuration.
        directory: Where to publish the run; must not exist yet.
        max_workers: Worker processes for a `lineal` batch (`None` runs the
            replicates one after another).
        pairwise_max_demes: Largest deme count with deme-pair statistics.
        clock: Source of "now" for timestamps; a test seam.

    Returns:
        The replicates, in order (one for a single run).

    Raises:
        RuntimeError: The engine returned a shape that does not match
            `params.n_replicates`.
    """
    run_id = deterministic_run_id(params)
    now = clock or utc_now
    if params.n_replicates == 1:
        with paths.atomic_directory(directory) as working_directory:
            targets = run_artifact_targets(working_directory)
            # Closed here as well as by the engine, before `atomic_directory`
            # renames (or, on failure, removes) the directory: an open handle
            # blocks both on Windows.
            with BinaryLogStore(
                targets["trajectory"], **log_options(params.expert)
            ) as store:
                output = simulate_params(
                    params=params,
                    store=store,
                    run_id=run_id,
                    clock=clock,
                )
            if not isinstance(output, RunResult):
                raise RuntimeError("a single-replicate run returned a batch")
            write_run_artifacts(
                output, working_directory, pairwise_max_demes=pairwise_max_demes
            )
        return (output,)
    started_at = format_timestamp(now())
    with paths.atomic_directory(directory) as working_directory:
        batch = simulate_params(
            params=params,
            run_id=run_id,
            clock=clock,
            max_workers=max_workers,
            store_factory=partial(
                replicate_store_factory, working_directory, run_id, params.expert
            ),
        )
        if not isinstance(batch, tuple):
            raise RuntimeError("a batch run returned a single result")
        ended_at = format_timestamp(now())
        write_batch_artifacts(
            batch,
            working_directory,
            run_id,
            params,
            started_at,
            ended_at,
            pairwise_max_demes=pairwise_max_demes,
        )
    return batch
