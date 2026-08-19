"""Researcher-facing command-line interface for the simulator."""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import pickle
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from matplotlib import pyplot as plt

from fim import __version__
from fim.engine import (
    RunResult,
    deterministic_run_id,
    fim,
    replicate_summary,
    report_for_state,
)
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import (
    CURRENT_BATCH_SCHEMA_VERSION,
    ArtifactDigest,
    BatchManifest,
    RunManifest,
    hash_file,
    read_manifest,
    write_batch_manifest,
    write_manifest,
)
from fim.statistics.differentiation import differentiation_q
from fim.viz.scatter import plot_frequency_scatter

RELEASES_API = (
    "https://api.github.com/repos/selby-botany/"
    "jost-finite-island-model/releases/latest"
)
SEMANTIC_VERSION_PARTS = 3

STARTER_CONFIG = """\
# Finite island model starter configuration
N: 450
d: 20
m: 0.001
mu: 0.00003
seed: 20260814
loci:
  - locus_id: 1
    length: 200
initial_allele_count: 2
initial_concentration: 1.0
deme_weighting: size
convergence_statistic: D
convergence_window: 50
convergence_tolerance: 0.01
max_generations: 10000
"""

ReleaseFetcher: TypeAlias = Callable[[], Mapping[str, Any]]


def load_config(path: Path | str) -> SimulationParams:
    """Load one YAML config file into validated simulation parameters.

    Args:
        path: YAML file path.

    Returns:
        Validated immutable parameters.
    """
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("configuration root must be a mapping")
    return SimulationParams.from_mapping(payload)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and dispatch one operation.

    Args:
        argv: Arguments excluding the program name, or ``None`` for ``sys.argv``.

    Returns:
        Process-style exit status.
    """
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "init":
            return _command_init(arguments)
        if arguments.command == "run":
            return _command_run(arguments)
        if arguments.command == "stats":
            return _command_stats(arguments)
        if arguments.command == "update":
            return _command_update(arguments, parser)
    except (
        ArithmeticError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        pickle.PicklingError,
        yaml.YAMLError,
    ) as error:
        print(f"fim: error: {error}", file=sys.stderr)
        return 2
    parser.error("a command is required")


def _command_init(arguments: argparse.Namespace) -> int:
    """Write the documented starter configuration."""
    output = (
        Path(arguments.output)
        if arguments.output is not None
        else _results_directory() / "example-run.yaml"
    )
    if output.exists() and not arguments.force:
        raise ValueError(f"starter config already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(STARTER_CONFIG, encoding="utf-8")
    print(f"Wrote starter config: {output}")
    return 0


def _command_run(arguments: argparse.Namespace) -> int:
    """Execute one config and write its documented artifacts."""
    params = load_config(arguments.config)
    output_directory = (
        Path(arguments.output)
        if arguments.output is not None
        else _default_output_directory()
    )
    if params.n_replicates == 1:
        return _command_run_scalar(params, output_directory, arguments.quiet)
    return _command_run_batch(params, output_directory, arguments)


def _command_run_scalar(
    params: SimulationParams,
    output_directory: Path,
    quiet: bool,
) -> int:
    """Execute one scalar run and write the four documented artifacts.

    Every artifact is built inside a hidden temporary sibling directory
    and published at `output_directory` with one atomic rename, only
    once `trajectory.jsonl`, `report.json`, and `scatter.png` are all
    fully durable and `manifest.json` — written last, and only then —
    records each of their SHA-256 digests (`_write_run_artifacts`, and
    see `_atomic_directory`). A run interrupted anywhere along the way
    leaves no trace at `output_directory` at all, rather than a partial
    directory silently indistinguishable from a complete one.
    """
    run_id = deterministic_run_id(params)
    if not quiet:
        print(
            f"Running {run_id} "
            f"(N={params.N}, d={params.d}, m={params.m}, "
            f"mu={params.mu}, seed={params.seed})"
        )
    with _atomic_directory(output_directory) as working_directory:
        targets = _run_artifact_targets(working_directory)
        store = JSONLTrajectoryStore(targets["trajectory"])
        output = fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=store,
            run_id=run_id,
        )
        if not isinstance(output, RunResult):
            raise RuntimeError("scalar CLI run unexpectedly returned a batch")
        _write_run_artifacts(output, working_directory)

    if not quiet:
        print(
            f"{output.report['reason'].capitalize()}: generation "
            f"{output.report['generation']}, D={output.report['D']:.6g}, "
            f"G_ST={_format_optional(output.report['G_ST'])}"
        )
        for label, path in _run_artifact_targets(output_directory).items():
            print(f"{label.capitalize():10} -> {path}")
    return 0


def _command_run_batch(
    params: SimulationParams,
    output_directory: Path,
    arguments: argparse.Namespace,
) -> int:
    """Execute a multi-replicate batch and write its documented artifacts.

    Each replicate gets its own subdirectory keeping the exact four-file
    scalar-run contract; a batch-level ``manifest.json`` (schema-versioned
    and digest-verified, like each replicate's own — `BatchManifest`) and
    ``summary.json`` (each watched statistic's across-replicate
    confidence interval, from `fim.engine.replicate_summary`) sit
    alongside them. The whole tree is built inside one hidden temporary
    sibling directory and published at `output_directory` with a single
    atomic rename (`_atomic_directory`), so a batch interrupted at any
    replicate, or between the last replicate and the batch-level
    summary/manifest, leaves no trace at `output_directory` at all.

    Under `max_workers` (parallel, the default), an adaptive
    `replicate_tolerance` stop is only ever applied after a whole
    concurrent worker batch completes (`fim.engine._run_batch_parallel`),
    so a worker whose replicate is not among the returned results can
    still have fully written its own `replicate-*` directory before the
    stop was decided. `_prune_orphan_replicate_directories` removes any
    such directory before publishing, so the published `replicate-*` set
    always equals `manifest.json`'s `replicate_run_ids` exactly.
    """
    run_id = deterministic_run_id(params)
    max_workers = (
        None
        if arguments.sequential
        else (arguments.workers if arguments.workers is not None else _cpu_count())
    )
    if not arguments.quiet:
        print(f"Running batch {run_id} {_batch_description(params, max_workers)}")

    started_at = _format_timestamp(_utc_now())
    with _atomic_directory(output_directory) as working_directory:
        output = fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            run_id=run_id,
            max_workers=max_workers,
            store_factory=functools.partial(
                _replicate_store_factory, working_directory, run_id
            ),
        )
        if not isinstance(output, tuple):
            raise RuntimeError("batch CLI run unexpectedly returned a scalar result")
        ended_at = _format_timestamp(_utc_now())

        published_run_ids = frozenset(result.run_id for result in output)
        _prune_orphan_replicate_directories(
            working_directory, run_id, published_run_ids
        )
        artifact_digests: dict[str, ArtifactDigest] = {}
        for result in output:
            directory = _replicate_output_directory(
                working_directory, run_id, result.run_id
            )
            _write_run_artifacts(result, directory)
            artifact_digests[directory.name] = hash_file(directory / "manifest.json")
        _write_json(working_directory / "summary.json", replicate_summary(output))
        artifact_digests["summary"] = hash_file(working_directory / "summary.json")
        write_batch_manifest(
            working_directory / "manifest.json",
            BatchManifest(
                schema_version=CURRENT_BATCH_SCHEMA_VERSION,
                run_id=run_id,
                replicate_run_ids=tuple(result.run_id for result in output),
                parameters=params.to_dict(),
                started_at=started_at,
                ended_at=ended_at,
                software_version=__version__,
                artifacts=artifact_digests,
            ),
        )

    if not arguments.quiet:
        print(f"Completed {len(output)} replicate(s)")
        for result in output:
            directory = _replicate_output_directory(
                output_directory, run_id, result.run_id
            )
            print(f"Replicate  -> {directory}")
        print(f"Summary    -> {output_directory / 'summary.json'}")
        print(f"Manifest   -> {output_directory / 'manifest.json'}")
    return 0


def _command_stats(arguments: argparse.Namespace) -> int:
    """Recompute statistics from a persisted generation."""
    trajectory_path = Path(arguments.trajectory)
    manifest_path = (
        Path(arguments.manifest)
        if arguments.manifest is not None
        else trajectory_path.with_name("manifest.json")
    )
    manifest = read_manifest(manifest_path)
    _verify_trajectory_integrity(trajectory_path, manifest)
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
    generation = (
        arguments.generation
        if arguments.generation is not None
        else max(row["generation"] for row in rows)
    )
    generation_rows = [row for row in rows if row["generation"] == generation]
    if not generation_rows:
        raise ValueError(f"trajectory has no generation {generation}")
    state = ModelState.from_rows(generation_rows, params.loci)
    final_generation = generation == manifest.generation
    report: dict[str, object] = dict(
        report_for_state(
            state,
            params,
            run_id=manifest.run_id,
            converged=manifest.converged if final_generation else False,
            reason=manifest.stop_reason if final_generation else "re-analysis",
        )
    )
    if arguments.q:
        report["Differentiation_q"] = {
            str(order): _differentiation_q_for_state(state, float(order))
            for order in arguments.q
        }
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if arguments.output is not None:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0


def _command_update(
    arguments: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    """Perform the explicit, opt-in release check."""
    if not arguments.check:
        parser.error("fim update requires --check")
    latest_tag, release_url = _latest_release()
    comparison = _compare_versions(__version__, latest_tag.removeprefix("v"))
    if comparison < 0:
        print(f"A newer fim release is available: {latest_tag}")
        print(release_url)
    elif comparison == 0:
        print(f"fim {__version__} is current")
    else:
        print(f"fim {__version__} is newer than the latest release {latest_tag}")
    return 0


def _compare_versions(current: str, latest: str) -> int:
    """Compare two three-part semantic versions."""
    current_parts = _version_parts(current)
    latest_parts = _version_parts(latest)
    return (current_parts > latest_parts) - (current_parts < latest_parts)


@contextlib.contextmanager
def _atomic_directory(target: Path) -> Iterator[Path]:
    """Build a directory's contents in a hidden temporary sibling, then
    publish it at `target` with one atomic rename.

    Regression fix for R7: an interrupted run used to leave a partial
    output directory that was silently indistinguishable from a
    complete one. Every write inside the `with` block happens in a
    temporary sibling of `target` — on the same filesystem, since it is
    always created directly inside `target.parent`, which guarantees the
    final publish is a single atomic rename rather than a copy. If the
    block raises anything, the temporary directory is discarded and
    `target` is left completely untouched. `target` therefore either
    does not exist yet or exists complete; there is no third, partial
    state to observe from outside this function, no matter when a crash
    happens (a hard kill or power loss skips the `except` cleanup too,
    but still cannot leave anything at `target` itself — only an
    orphaned temporary directory beside it).

    Args:
        target: The directory's final path. Must not already exist.

    Yields:
        The temporary directory to build the run's output inside.

    Raises:
        FileExistsError: If `target` already exists.
    """
    if target.exists():
        raise FileExistsError(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    working_directory = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
    )
    try:
        yield working_directory
    except BaseException:
        shutil.rmtree(working_directory, ignore_errors=True)
        raise
    else:
        working_directory.replace(target)


def _batch_description(params: SimulationParams, max_workers: int | None) -> str:
    """Return the batch-run progress line's parameter summary."""
    adaptive = (
        f", replicate_tolerance={params.replicate_tolerance}"
        if params.replicate_tolerance is not None
        else ""
    )
    workers = "sequential" if max_workers is None else f"{max_workers} workers"
    return (
        f"(N={params.N}, d={params.d}, m={params.m}, mu={params.mu}, "
        f"seed={params.seed}, n_replicates={params.n_replicates}{adaptive}) "
        f"[{workers}]"
    )


def _cpu_count() -> int:
    """Return the default parallel worker count for a CLI batch run."""
    return os.cpu_count() or 1


def _default_output_directory() -> Path:
    """Return a timestamped output folder without affecting run data."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return _results_directory() / f"run-{stamp}"


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _prune_orphan_replicate_directories(
    working_directory: Path,
    batch_run_id: str,
    published_run_ids: frozenset[str],
) -> None:
    """Remove any replicate directory not among the batch's published results.

    Regression fix for S1: under `max_workers` (parallel, the default),
    `fim.engine._run_batch_parallel` submits a whole worker batch and
    applies an adaptive `replicate_tolerance` stop only afterward, in
    ascending replicate order. A worker beyond the replicate that
    triggered the stop still runs to completion — its `store_factory`
    call has already created its `replicate-NNN/` directory and
    streamed a full `trajectory.jsonl` into it — even though its
    result is discarded, never appearing in the tuple `fim` returns.
    Without this pass, `_atomic_directory` would publish that orphan
    directory verbatim: complete, present on disk, and absent from
    both `summary.json` and `manifest.json`.

    Args:
        working_directory: The batch's hidden temporary build directory.
        batch_run_id: The batch's own run ID.
        published_run_ids: Every replicate run ID actually returned by
            `fim` — the set that will appear in `manifest.json`.
    """
    for entry in sorted(working_directory.glob("replicate-*")):
        if not entry.is_dir():
            continue
        replicate_run_id = f"{batch_run_id}-r{entry.name.removeprefix('replicate-')}"
        if replicate_run_id not in published_run_ids:
            shutil.rmtree(entry)


def _replicate_output_directory(
    base: Path,
    batch_run_id: str,
    replicate_run_id: str,
) -> Path:
    """Return one replicate's own artifact subdirectory.

    `replicate_run_id` is always exactly ``f"{batch_run_id}-r{index:03}"``
    (`fim.engine.fim`'s own batch run-ID convention), so the zero-padded
    index is recovered from it directly rather than threaded through as a
    separate argument.
    """
    suffix = replicate_run_id.removeprefix(f"{batch_run_id}-r")
    return base / f"replicate-{suffix}"


def _replicate_store_factory(
    output_directory: Path,
    batch_run_id: str,
    replicate_run_id: str,
) -> JSONLTrajectoryStore:
    """Build one replicate's real on-disk trajectory store.

    Module-level, closed over only via `functools.partial` (never a
    closure or lambda), so a parallel `max_workers` worker process can
    pickle a reference to it.
    """
    directory = _replicate_output_directory(
        output_directory, batch_run_id, replicate_run_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    return JSONLTrajectoryStore(directory / "trajectory.jsonl")


def _run_artifact_targets(directory: Path) -> dict[str, Path]:
    """Return the four documented scalar-run artifact paths in one directory."""
    return {
        "trajectory": directory / "trajectory.jsonl",
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "scatter": directory / "scatter.png",
    }


def _utc_now() -> datetime:
    """Return the current UTC time for batch-manifest timestamps only."""
    return datetime.now(UTC)


def _verify_trajectory_integrity(trajectory_path: Path, manifest: RunManifest) -> None:
    """Refuse to analyze a trajectory that no longer matches its manifest.

    Regression fix for R7: an edited or truncated trajectory used to
    re-analyze silently under `fim stats`. `_write_run_artifacts` records
    the trajectory's exact SHA-256 digest and byte count in
    `manifest.json` at the moment the run finished writing it durably;
    recomputing that digest now and comparing catches any edit,
    truncation, or replacement since.

    Args:
        trajectory_path: The trajectory file about to be read.
        manifest: Its companion manifest.

    Raises:
        ValueError: If the manifest has no recorded trajectory digest
            (written before this check existed), or the file no longer
            matches the digest it does have.
    """
    if manifest.artifacts is None or "trajectory" not in manifest.artifacts:
        raise ValueError(
            f"manifest for {manifest.run_id!r} has no recorded trajectory "
            "digest to verify against (written by a version of fim "
            "predating this integrity check)"
        )
    expected = manifest.artifacts["trajectory"]
    actual = hash_file(trajectory_path)
    if actual != expected:
        raise ValueError(
            f"trajectory does not match its manifest: expected sha256 "
            f"{expected['sha256']} ({expected['bytes']} bytes), found "
            f"{actual['sha256']} ({actual['bytes']} bytes) — the file may "
            "have been edited, truncated, or replaced since the run "
            "completed"
        )


def _write_run_artifacts(result: RunResult, directory: Path) -> dict[str, Path]:
    """Write one run's report, scatter plot, and — last — its verifiable manifest.

    ``trajectory.jsonl`` is not written here: it is streamed
    generation-by-generation by the `TrajectoryStore` already passed
    into `fim`, so it exists before this function ever runs. Every other
    artifact is written and flushed first; ``manifest.json`` is written
    only once every sibling artifact is durable, augmented with each
    one's SHA-256 digest and byte count (`fim.persistence.manifest.
    hash_file`) — the record `_verify_trajectory_integrity` later checks
    against.
    """
    targets = _run_artifact_targets(directory)
    _write_json(targets["report"], result.report)
    figure = plot_frequency_scatter(
        result.final_state, result.params, targets["scatter"]
    )
    plt.close(figure)
    manifest = replace(
        result.manifest,
        artifacts={
            name: hash_file(targets[name])
            for name in ("trajectory", "report", "scatter")
        },
    )
    write_manifest(targets["manifest"], manifest)
    return targets


def _project_root() -> Path:
    """Return the source checkout root, falling back to the working directory."""
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return Path.cwd()


def _results_directory() -> Path:
    """Return the project-local results directory."""
    return _project_root() / "results"


def _differentiation_q_for_state(state: ModelState, order: float) -> float:
    """Average the requested differentiation order across loci."""
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
        values.append(differentiation_q(table, order))
    return sum(values) / len(values)


def _fetch_latest_release() -> Mapping[str, Any]:
    """Fetch the latest GitHub release; this is the sole network path."""
    request = Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"fim/{__version__}",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"update check failed: {error}") from error
    if not isinstance(payload, Mapping):
        raise RuntimeError("update check returned a non-object response")
    return payload


def _format_optional(value: float | None) -> str:
    """Format an optional statistic for terminal output."""
    return "undefined" if value is None else f"{value:.6g}"


def _latest_release(
    fetcher: ReleaseFetcher = _fetch_latest_release,
) -> tuple[str, str]:
    """Validate the two release fields needed by the update command."""
    payload = fetcher()
    tag = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("latest release response is missing tag_name")
    if not isinstance(release_url, str) or not release_url:
        raise RuntimeError("latest release response is missing html_url")
    _version_parts(tag.removeprefix("v"))
    return tag, release_url


def _parser() -> argparse.ArgumentParser:
    """Build the complete command parser."""
    parser = argparse.ArgumentParser(
        prog="fim",
        description="Simulate and analyze the finite island model.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_parser = subcommands.add_parser(
        "init",
        help="write a starter YAML configuration",
    )
    init_parser.add_argument(
        "--output",
        metavar="PATH",
        help="config path (default: project-root/results/example-run.yaml)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing starter config",
    )

    run_parser = subcommands.add_parser(
        "run",
        help="run a simulation from YAML configuration",
    )
    run_parser.add_argument("config", metavar="CONFIG")
    run_parser.add_argument(
        "-o",
        "--output",
        metavar="DIRECTORY",
        help="artifact directory (default: project-root/results/run-TIMESTAMP)",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress and artifact messages",
    )
    workers_group = run_parser.add_mutually_exclusive_group()
    workers_group.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help=(
            "batch (n_replicates > 1) worker-process count "
            "(default: the CPU count; ignored for a scalar run)"
        ),
    )
    workers_group.add_argument(
        "--sequential",
        action="store_true",
        help="run a batch's replicates one at a time instead of in parallel",
    )

    stats_parser = subcommands.add_parser(
        "stats",
        help="re-analyze a persisted trajectory",
    )
    stats_parser.add_argument("trajectory", metavar="TRAJECTORY")
    stats_parser.add_argument(
        "--manifest",
        metavar="PATH",
        help="manifest path (default: beside trajectory)",
    )
    stats_parser.add_argument(
        "--generation",
        type=int,
        help="generation to analyze (default: final)",
    )
    stats_parser.add_argument(
        "--q",
        action="append",
        type=float,
        help="also compute Differentiation_q; repeat to sweep q",
    )
    stats_parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="also write the JSON result",
    )

    update_parser = subcommands.add_parser(
        "update",
        help="perform an opt-in version check",
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        help="query the latest GitHub release without downloading",
    )
    return parser


def _version_parts(value: str) -> tuple[int, int, int]:
    """Parse a stable three-part semantic version."""
    parts = value.split(".")
    if len(parts) != SEMANTIC_VERSION_PARTS:
        raise RuntimeError(f"release version is not semantic: {value}")
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError as error:
        raise RuntimeError(f"release version is not semantic: {value}") from error
    if any(part < 0 for part in parsed):
        raise RuntimeError(f"release version is not semantic: {value}")
    return parsed  # type: ignore[return-value]


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    """Write deterministic UTF-8 JSON."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
