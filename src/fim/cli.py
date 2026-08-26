"""Researcher-facing command-line interface for the simulator.

This is what actually runs when you type `fim` at a terminal — the part
of the program that reads what you typed, figures out which of the four
things you asked for, and calls the right code to do it. It has four
commands, each its own subsection below:

- `fim init` — write out a starter configuration file (a filled-in
  example, ready to run or edit) so a new user has something concrete to
  start from rather than an empty file and a blank page of documentation
  (`_command_init`).
- `fim run CONFIG` — actually run a simulation from a configuration file
  and write its results to disk. Dispatches to one of two paths
  depending on the configuration's own `n_replicates` (`_command_run`):
  a single simulation (`_command_run_scalar`) or a whole batch of
  independent, differently seeded repeats of the same configuration
  (`_command_run_batch`) — see `fim.engine`'s own docstring for why
  running several repeats matters at all.
- `fim stats TRAJECTORY` — recompute statistics from a run's own saved
  data, for any generation, without re-running the simulation
  (`_command_stats`; see `fim.reanalyze`'s own docstring for what
  "re-analysis" means and why it is useful).
- `fim update --check` — the one place this whole program ever makes a
  network connection: ask GitHub whether a newer release exists
  (`_command_update`; see `fim.update`'s own docstring for the full
  reasoning and the security posture behind it).

This file's own job stops at parsing arguments, calling the right
function, and printing the result or a plain-language error message —
the actual scientific work (running the simulation, computing
statistics) always lives in `fim.engine`/`fim.reanalyze`, never here.
That separation is what lets the exact same underlying logic also power
the desktop app (`fim.gui`) without duplicating it.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import pickle
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import yaml
from matplotlib import pyplot as plt

from fim import __version__, paths, reanalyze, update
from fim.engine import RunResult, deterministic_run_id, fim, replicate_summary
from fim.model.params import SimulationParams
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import (
    CURRENT_BATCH_SCHEMA_VERSION,
    ArtifactDigest,
    BatchManifest,
    hash_file,
    write_batch_manifest,
    write_manifest,
)

# Explicit re-export (not a rename): test/cli/test_cli.py patches
# `cli.write_report` directly to inject a failure at a specific artifact
# boundary, so it must resolve as this module's own attribute under
# mypy strict, not merely an unexported transitive import.
from fim.persistence.report import write_report as write_report  # noqa: PLC0414
from fim.viz.scatter import plot_frequency_scatter

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


def load_config(path: Path | str) -> SimulationParams:
    """Load one YAML config file into validated simulation parameters.

    YAML is the plain-text, human-editable file format every
    configuration in this project is written in (see `fim init`'s own
    starter config, below, for a real example) — this function is what
    turns that text file into the fully checked, ready-to-use
    `SimulationParams` object every other part of the program actually
    works with. "Validated" here means every value has already been
    checked for being sensible on its own (a population size cannot be
    negative, a probability must be between 0 and 1, and so on) — by the
    time this function returns successfully, nothing downstream needs to
    re-check any of that.

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

    This is the single entry point every invocation of `fim` from a
    terminal reaches (via `pyproject.toml`'s own `[project.scripts]`
    entry, by way of `fim.launcher`) — it parses whatever was typed,
    figures out which of the four commands (see this module's own
    docstring, above) was requested, and calls the matching function.

    Every error that any command can reasonably raise on genuinely bad
    input (a malformed configuration file, an invalid parameter
    combination, a missing file, and so on) is caught here in one place
    and turned into a short, plain "fim: error: ..." message on a single
    line, with an exit status a shell script can check, rather than a
    long, intimidating Python traceback — this project's programming
    mistakes should look like tracebacks (so they get noticed and
    fixed), but a *user's* mistake (a typo in a config file, an
    out-of-range value) should look like a normal, readable command-line
    error, the same way a real command-line tool a person did not write
    themselves would report it. The specific exception types listed are
    exactly the ones the actual work below (`fim.engine`, YAML parsing,
    file I/O) can raise for an ordinary, expectable mistake; anything
    else escaping this function uncaught is treated as a real bug in
    this project's own code, and is deliberately allowed to surface as
    a full traceback instead of being hidden behind a generic message.

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
    """Write the documented starter configuration.

    `fim init` — writes `STARTER_CONFIG` (a real, complete, ready-to-run
    configuration, defined near the top of this file) to disk exactly as
    written, so a first-time user has a working example to run
    immediately (`fim run` on the file this writes) or to copy and edit,
    rather than starting from an empty file and this project's own
    reference documentation alone.
    """
    output = (
        Path(arguments.output)
        if arguments.output is not None
        else paths.results_directory() / "example-run.yaml"
    )
    if output.exists() and not arguments.force:
        raise ValueError(f"starter config already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(STARTER_CONFIG, encoding="utf-8")
    print(f"Wrote starter config: {output}")
    return 0


def _command_run(arguments: argparse.Namespace) -> int:
    """Execute one config and write its documented artifacts.

    `fim run CONFIG` — the dispatcher for this project's own two shapes
    of "run a simulation": a single, ordinary run
    (`_command_run_scalar`) whenever the loaded configuration's own
    `n_replicates` is 1 (the default), or a whole batch of independently
    seeded repeats (`_command_run_batch`) whenever it is set higher than
    that — see `fim.engine`'s own docstring for why running several
    repeats of the same configuration is useful in the first place.
    """
    params = load_config(arguments.config)
    output_directory = (
        Path(arguments.output)
        if arguments.output is not None
        else paths.default_output_directory()
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

    The ordinary "run one simulation" path — every `fim run` invocation
    whose configuration does not set `n_replicates` above 1 reaches this
    function. Produces exactly four files in `output_directory`:
    `trajectory.jsonl` (every generation's own full state, for later
    replay or re-analysis — see `fim.reanalyze`), `report.json` (the
    final differentiation statistics, see `fim.engine.FinalReport`),
    `scatter.png` (a plot of the final population), and `manifest.json`
    (this run's own bookkeeping and integrity record).

    Every artifact is built inside a hidden temporary sibling directory
    and published at `output_directory` with one atomic rename, only
    once `trajectory.jsonl`, `report.json`, and `scatter.png` are all
    flushed and `manifest.json` — written last, and only then — records
    each of their own checksums, a short fingerprint of each file's
    exact content that later reveals whether it has been altered since
    (`_write_run_artifacts`, and see `fim.paths.atomic_directory`'s own
    docstring for why building everything in a temporary location first,
    then publishing all at once, matters). A run interrupted anywhere
    along the way leaves no trace at `output_directory` at all, rather
    than a partial directory silently indistinguishable from a complete
    one.
    """
    run_id = deterministic_run_id(params)
    if not quiet:
        print(
            f"Running {run_id} "
            f"(N={params.N}, d={params.d}, m={params.m}, "
            f"mu={params.mu}, seed={params.seed})"
        )
    with paths.atomic_directory(output_directory) as working_directory:
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

    Reached whenever the loaded configuration's own `n_replicates` is
    set above 1 — runs that many independently seeded repeats of the
    identical configuration (see `fim.engine`'s own docstring for why),
    by default using several worker processes at once to run more than
    one replicate's own generations simultaneously (`--workers`/
    `--sequential`, see `_parser`), rather than one replicate fully
    finishing before the next one starts.

    Each replicate gets its own subdirectory keeping the exact four-file
    scalar-run contract; a batch-level ``manifest.json`` (schema-versioned
    and digest-verified, like each replicate's own — `BatchManifest`) and
    ``summary.json`` (each watched statistic's across-replicate
    confidence interval, from `fim.engine.replicate_summary`) sit
    alongside them. The whole tree is built inside one hidden temporary
    sibling directory and published at `output_directory` with a single
    atomic rename (`fim.paths.atomic_directory`), so a batch interrupted at any
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
    with paths.atomic_directory(output_directory) as working_directory:
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
        write_report(working_directory / "summary.json", replicate_summary(output))
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
    """Recompute statistics from a persisted generation.

    Delegates to `fim.reanalyze.reanalyze_trajectory` (design doc
    `20260819-claude-sonnet-5-graphical-interface.md` §3.8) — the exact
    algorithm this command has always run, extracted so `fim.gui`'s
    "open an existing run" (§4.6) and "animated trajectory" (§4.5)
    screens share it rather than a second, independently maintained
    copy.
    """
    trajectory_path = Path(arguments.trajectory)
    manifest_path = Path(arguments.manifest) if arguments.manifest is not None else None
    reanalyzed = reanalyze.reanalyze_trajectory(
        trajectory_path,
        manifest_path=manifest_path,
        generation=arguments.generation,
        differentiation_orders=(
            tuple(float(order) for order in arguments.q) if arguments.q else ()
        ),
    )
    rendered = json.dumps(reanalyzed.report, indent=2, sort_keys=True, allow_nan=False)
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
    """Perform the explicit, opt-in release check.

    `fim update --check` — see `fim.update`'s own module docstring for
    the full explanation of what this checks, why `--check` itself is
    required (there is genuinely nothing else `fim update` can do; this
    command never downloads or installs anything), and why this is the
    only network access anywhere in this whole program.
    """
    if not arguments.check:
        parser.error("fim update requires --check")
    latest_tag, release_url = update.latest_release()
    comparison = update.compare_versions(__version__, latest_tag.removeprefix("v"))
    if comparison < 0:
        print(f"A newer fim release is available: {latest_tag}")
        print(release_url)
    elif comparison == 0:
        print(f"fim {__version__} is current")
    else:
        print(f"fim {__version__} is newer than the latest release {latest_tag}")
    return 0


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
    """Return the default parallel worker count for a CLI batch run.

    Used as the default `max_workers` for a batch run when neither
    `--workers` nor `--sequential` is given (see `_parser`) — one
    worker process per available processor core, the same rule of
    thumb most parallel command-line tools use for a sensible default,
    on the theory that using every available core (and no more, which
    would just make separate processes compete with each other for the
    same limited cores) gets the most work done in the least wall-clock
    time.
    """
    return os.cpu_count() or 1


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`.

    See `fim.engine._format_timestamp`'s own docstring for what this
    format is and why every timestamp in this project is recorded in
    UTC. This is a separate, tiny copy of that same formatting rather
    than a shared import specifically because a `BatchManifest`'s own
    timestamps are recorded by `cli.py` directly (see
    `_command_run_batch`), never by `fim.engine` itself.
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _prune_orphan_replicate_directories(
    working_directory: Path,
    batch_run_id: str,
    published_run_ids: frozenset[str],
) -> None:
    """Remove any replicate directory not among the batch's published results.

    Cleans up after a specific, real race condition in the adaptive
    stop described in `fim.engine`'s own docstring — this project's own
    internal tracked-issue numbering calls it S1, kept here purely as a
    cross-reference, not something you need to look up to follow this
    docstring's own explanation: under `max_workers` (parallel, the default),
    `fim.engine._run_batch_parallel` submits a whole worker batch and
    applies an adaptive `replicate_tolerance` stop only afterward, in
    ascending replicate order. A worker beyond the replicate that
    triggered the stop still runs to completion — its `store_factory`
    call has already created its `replicate-NNN/` directory and
    streamed a full `trajectory.jsonl` into it — even though its
    result is discarded, never appearing in the tuple `fim` returns.
    Without this pass, `fim.paths.atomic_directory` would publish that orphan
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

    This is the `store_factory` `_command_run_batch` hands to `fim()`
    (see that function's own docstring for what a `store_factory` is
    and why a batch needs one) — called once per replicate to give each
    one a fresh, independent place to write its own trajectory data,
    inside its own subdirectory of the batch's overall output.

    Module-level, closed over only via `functools.partial` (never a
    closure or lambda), so a parallel `max_workers` worker process can
    pickle a reference to it — see `fim.engine._require_picklable`'s
    own docstring for what "picklable" means and why it matters here.
    """
    directory = _replicate_output_directory(
        output_directory, batch_run_id, replicate_run_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    return JSONLTrajectoryStore(directory / "trajectory.jsonl")


def _run_artifact_targets(directory: Path) -> dict[str, Path]:
    """Return the four documented scalar-run artifact paths in one directory.

    A single, shared source for these four exact filenames, used by
    both `_command_run_scalar` (writing them) and anything checking a
    completed run's own output (reading them back) — so the two can
    never quietly disagree about where a given artifact actually lives.
    """
    return {
        "trajectory": directory / "trajectory.jsonl",
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "scatter": directory / "scatter.png",
    }


def _utc_now() -> datetime:
    """Return the current UTC time for batch-manifest timestamps only.

    The `_command_run_batch` counterpart to `fim.engine._utc_now` —
    see that function's own docstring for why this small wrapper exists
    at all rather than calling `datetime.now(UTC)` inline.
    """
    return datetime.now(UTC)


def _write_run_artifacts(result: RunResult, directory: Path) -> dict[str, Path]:
    """Write one run's report, scatter plot, and — last — its verifiable manifest.

    ``trajectory.jsonl`` is not written here: it is streamed
    generation-by-generation by the `TrajectoryStore` already passed
    into `fim`, so it exists before this function ever runs. Every other
    artifact is written and flushed first; ``manifest.json`` is written
    only once every sibling artifact is flushed, augmented with each
    one's own checksum and byte count (`fim.persistence.manifest.
    hash_file`) — the record `fim.persistence.manifest.
    verify_trajectory_integrity` later checks against (see
    `fim.reanalyze`'s own docstring for what that check actually
    catches). "Flushed" means the data has reached the operating
    system's own page cache via the normal file-close path, not that it
    has been confirmed as physically written to the disk itself — no
    `fsync` (the specific, slower operation that would force that
    physical confirmation) is called anywhere in this pipeline
    (internal tracked-issue reference S11), so this ordering protects
    against a process dying mid-write, not against a genuine, unclean
    loss of power to the machine itself; see `fim.paths.
    atomic_directory`'s own docstring for the identical caveat spelled
    out in more detail.
    """
    targets = _run_artifact_targets(directory)
    write_report(targets["report"], result.report)
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


def _format_optional(value: float | None) -> str:
    """Format an optional statistic for terminal output.

    Only `G_ST` can genuinely be `None` (see
    `fim.engine.FinalReport`'s own docstring for why) — printed here as
    the plain word "undefined" rather than Python's own "None", which
    would look like a bug to a reader who does not already know this is
    an expected, legitimate outcome for this one specific statistic.
    """
    return "undefined" if value is None else f"{value:.6g}"


def _parser() -> argparse.ArgumentParser:
    """Build the complete command parser.

    Defines the exact text of every command, flag, and `--help` message
    `fim` shows at a terminal — the four subcommands described in this
    module's own docstring, above, each with its own arguments. This
    function only ever *describes* the command line; none of it decides
    what to actually do with the parsed result — that happens back in
    `main`, once `argparse` (Python's own standard library tool for
    exactly this job) has already turned the raw command-line text into
    a structured, validated set of values.
    """
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


if __name__ == "__main__":
    raise SystemExit(main())
