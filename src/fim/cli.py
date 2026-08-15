"""Researcher-facing command-line interface for the simulator."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from matplotlib import pyplot as plt

from fim import __version__
from fim.engine import RunResult, deterministic_run_id, fim, report_for_state
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import read_manifest, write_manifest
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
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as error:
        print(f"fim: error: {error}", file=sys.stderr)
        return 2
    parser.error("a command is required")


def _command_init(arguments: argparse.Namespace) -> int:
    """Write a starter configuration."""
    output = (
        Path(arguments.output)
        if arguments.output is not None
        else _default_runs_directory() / "example-run.yaml"
    )
    if output.exists() and not arguments.force:
        raise ValueError(f"starter config already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(STARTER_CONFIG, encoding="utf-8")
    print(f"Wrote starter config: {output}")
    return 0


def _command_run(arguments: argparse.Namespace) -> int:
    """Execute one validated configuration."""
    params = load_config(arguments.config)
    if params.n_replicates != 1:
        raise ValueError(
            "CLI runs require n_replicates: 1; use fim.engine.fim for batches"
        )
    output_directory = (
        Path(arguments.output)
        if arguments.output is not None
        else _default_output_directory()
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    targets = {
        "trajectory": output_directory / "trajectory.jsonl",
        "manifest": output_directory / "manifest.json",
        "report": output_directory / "report.json",
        "scatter": output_directory / "scatter.png",
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing:
        names = ", ".join(path.name for path in existing)
        raise ValueError(f"output directory already contains run artifacts: {names}")

    run_id = deterministic_run_id(params)
    store = JSONLTrajectoryStore(targets["trajectory"])
    if not arguments.quiet:
        print(
            f"Running {run_id} "
            f"(N={params.N}, d={params.d}, m={params.m}, "
            f"mu={params.mu}, seed={params.seed})"
        )
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

    write_manifest(targets["manifest"], output.manifest)
    _write_json(targets["report"], output.report)
    figure = plot_frequency_scatter(
        output.final_state,
        params,
        targets["scatter"],
    )
    plt.close(figure)
    if not arguments.quiet:
        print(
            f"{output.report['reason'].capitalize()}: generation "
            f"{output.report['generation']}, D={output.report['D']:.6g}, "
            f"G_ST={_format_optional(output.report['G_ST'])}"
        )
        for label, path in targets.items():
            print(f"{label.capitalize():10} -> {path}")
    return 0


def _command_stats(arguments: argparse.Namespace) -> int:
    """Handle a persisted trajectory."""
    trajectory_path = Path(arguments.trajectory)
    manifest_path = (
        Path(arguments.manifest)
        if arguments.manifest is not None
        else trajectory_path.with_name("manifest.json")
    )
    manifest = read_manifest(manifest_path)
    params = manifest.params()
    rows = list(JSONLTrajectoryStore(trajectory_path).read(manifest.run_id))
    if not rows:
        raise ValueError(f"trajectory has no rows for {manifest.run_id}")
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
    """Handle a release check."""
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


def _default_output_directory() -> Path:
    """Return a timestamped output folder without affecting run data."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return _default_runs_directory() / f"run-{stamp}"


def _default_runs_directory() -> Path:
    """Return the researcher-facing Documents run folder."""
    return Path.home() / "Documents" / "FIM Runs"


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
        help="config path (default: Documents/FIM Runs/example-run.yaml)",
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
        help="artifact directory",
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress and artifact messages",
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
