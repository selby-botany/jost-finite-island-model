"""`fim sweep`: plan, run, resume and report a parameter sweep.

The command-line half of the sweep-as-Study feature
(`20260923-claude-sonnet-5-sweep-as-study-implementation-plan.md`,
`selby/restricted`, section 5). A sweep file is an ordinary configuration
plus a `sweep:` block; `fim.sweep.spec_from_config` splits it, so the
ordinary configuration validator never sees `sweep`. The work itself is in
`fim.sweep` (what the points are) and `fim.sweep_run` (running them); this
module parses arguments and prints.
"""

from __future__ import annotations

import argparse
import csv
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from fim.persistence import groups
from fim.sweep import (
    SIZE_CONFIRMATION_THRESHOLD,
    SweepPlan,
    SweepSpec,
    enumerate_points,
    spec_from_config,
    work_estimate,
)
from fim.sweep_run import (
    LocalPointRunner,
    SweepEvent,
    create_sweep_study,
    run_sweep,
    sweep_point_results,
    sweep_point_statuses,
)


def add_sweep_subcommands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Wire `fim sweep plan/run/resume/report` onto `subcommands`."""
    sweep_parser = subcommands.add_parser(
        "sweep", help="run a configuration over a parameter space as one study"
    )
    sweep_subcommands = sweep_parser.add_subparsers(dest="sweep_command", required=True)
    plan_parser = sweep_subcommands.add_parser(
        "plan", help="list the points a sweep file would run; runs nothing"
    )
    plan_parser.add_argument("file", metavar="FILE", help="a sweep file (YAML)")
    run_parser = sweep_subcommands.add_parser(
        "run", help="create the sweep's study and run its points"
    )
    run_parser.add_argument("file", metavar="FILE", help="a sweep file (YAML)")
    run_parser.add_argument("--study", help="name for the sweep's study")
    run_parser.add_argument("--experiment", help="experiment id to place the study in")
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help=f"confirm a sweep of {SIZE_CONFIRMATION_THRESHOLD} or more points",
    )
    resume_parser = sweep_subcommands.add_parser(
        "resume", help="run the points of a sweep study that are still missing"
    )
    resume_parser.add_argument("study_id", metavar="STUDY_ID")
    resume_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="also retry points that failed before",
    )
    report_parser = sweep_subcommands.add_parser(
        "report", help="print each point's statistic, with its interval"
    )
    report_parser.add_argument("study_id", metavar="STUDY_ID")
    report_parser.add_argument(
        "--statistic", default="D", help="statistic to report (default: D)"
    )
    report_parser.add_argument(
        "--csv", action="store_true", help="write CSV instead of a text table"
    )


def command_sweep(
    arguments: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    """Dispatch one `fim sweep` subcommand and return its exit status."""
    command = arguments.sweep_command
    if command == "plan":
        return _plan(Path(arguments.file))
    if command == "run":
        return _run(arguments)
    if command == "resume":
        return _resume(arguments.study_id, retry_failed=arguments.retry_failed)
    if command == "report":
        return _report(arguments.study_id, arguments.statistic, as_csv=arguments.csv)
    parser.error("a sweep subcommand is required")


def _load(path: Path) -> tuple[SweepSpec, str | None]:
    """Read a sweep file into its spec and optional name."""
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("a sweep file's root must be a mapping")
    return spec_from_config(payload)


def _plan(path: Path) -> int:
    """Print the plan for a sweep file; run nothing."""
    spec, _ = _load(path)
    plan = enumerate_points(spec)
    _print_plan(spec, plan)
    return 0 if plan.points else 2


def _print_plan(spec: SweepSpec, plan: SweepPlan) -> None:
    """Print the points, the invalid ones with reasons, and the size."""
    keys = [axis.key for axis in spec.axes]
    print("  ".join(["point", *keys, "run_id"]))
    for point in plan.points:
        values = [_format(point.coordinates[key]) for key in keys]
        print("  ".join([f"{point.index:>5}", *values, point.run_id]))
    for invalid in plan.invalid:
        values = [_format(invalid.coordinates[key]) for key in keys]
        print("  ".join([f"{invalid.index:>5}", *values, f"INVALID: {invalid.reason}"]))
    estimate = work_estimate(spec, plan)
    print(
        f"{len(plan.points)} valid point(s), {len(plan.invalid)} invalid, "
        f"{plan.collapsed} duplicate(s) of {plan.grid_size} grid position(s); "
        f"{estimate['replicates']} replicate(s) each, up to "
        f"{estimate['max_generations']} generations "
        f"(at most {estimate['replicate_generations']} replicate-generations)"
    )
    if plan.needs_confirmation:
        print(
            f"This is {SIZE_CONFIRMATION_THRESHOLD} or more points; "
            "'fim sweep run' will need --yes."
        )


def _run(arguments: argparse.Namespace) -> int:
    """Create the sweep's study and run it."""
    path = Path(arguments.file)
    spec, file_name = _load(path)
    plan = enumerate_points(spec)
    _print_plan(spec, plan)
    if not plan.points:
        print("fim: error: the sweep has no valid points to run", file=sys.stderr)
        return 2
    if plan.needs_confirmation and not arguments.yes:
        print("fim: error: pass --yes to run a sweep this large", file=sys.stderr)
        return 2
    name = arguments.study or file_name or path.stem
    study = create_sweep_study(spec, plan, name, experiment_id=arguments.experiment)
    print(f"Created sweep study {study.study_id}: {study.name}")
    return _execute(study.study_id, retry_failed=False)


def _resume(study_id: str, *, retry_failed: bool) -> int:
    """Run the points of an existing sweep study that are missing."""
    groups.get_study(study_id)
    return _execute(study_id, retry_failed=retry_failed)


def _execute(study_id: str, *, retry_failed: bool) -> int:
    """Run a sweep study, print a line per point, and return an exit status."""
    cancel_event = threading.Event()
    try:
        outcome = run_sweep(
            study_id,
            LocalPointRunner(),
            _print_event,
            cancel_event,
            retry_failed=retry_failed,
        )
    except KeyboardInterrupt:
        cancel_event.set()
        print(f"\nInterrupted. Continue with: fim sweep resume {study_id}")
        return 130
    print(
        f"{outcome.done} run, {outcome.reused} reused, "
        f"{outcome.already_present} already present, {outcome.failed} failed, "
        f"{outcome.skipped_failed} skipped after an earlier failure"
    )
    if outcome.recomputed or outcome.differing:
        print(
            f"{outcome.recomputed} point(s) recomputed under this version and "
            f"matched the earlier result bit for bit; {outcome.differing} differed"
        )
    if outcome.differing:
        print(
            "fim: warning: the simulator guarantees bit-for-bit reproducibility; "
            "both runs were kept for inspection",
            file=sys.stderr,
        )
    if outcome.cancelled:
        print(f"Cancelled. Continue with: fim sweep resume {study_id}")
        return 130
    if outcome.failed or outcome.skipped_failed:
        print(
            f"Retry the failed points with: fim sweep resume {study_id} --retry-failed"
        )
        return 1
    return 1 if outcome.differing else 0


def _describe_differences(comparison: object) -> str:
    """List what changed in a reproducibility comparison, briefly."""
    if not isinstance(comparison, dict):
        return "see reproducibility.json"
    items = comparison.get("differences", [])
    return "; ".join(
        f"{item['label']}: {item['old']} -> {item['new']}" for item in items[:6]
    )


def _print_event(event: SweepEvent) -> None:
    """Print one line per point as it finishes."""
    prefix = f"[{event.position}/{event.total}] point {event.index}"
    if event.kind == "point_started":
        print(f"{prefix}: running", flush=True)
    elif event.kind == "point_done":
        print(f"{prefix}: done", flush=True)
    elif event.kind == "point_reused":
        print(f"{prefix}: reused an existing run", flush=True)
    elif event.kind == "point_recomputed":
        print(f"{prefix}: recomputed; matches the earlier version's result", flush=True)
    elif event.kind == "point_differs":
        print(
            f"{prefix}: recomputed; DIFFERS from the earlier version's result "
            f"({_describe_differences(event.detail)})",
            flush=True,
        )
    elif event.kind == "point_failed":
        print(f"{prefix}: FAILED ({event.detail})", flush=True)


def _report(study_id: str, statistic: str, *, as_csv: bool) -> int:
    """Print each point's statistic with its interval, as text or CSV."""
    study = groups.get_study(study_id)
    statuses = {status.index: status for status in sweep_point_statuses(study)}
    results = {result.index: result for result in sweep_point_results(study)}
    keys = _axis_keys(study)
    header = [*keys, "state", "replicates", statistic, "low", "high"]
    rows: list[list[str]] = []
    for index, status in sorted(statuses.items()):
        result = results.get(index)
        stat = result.statistics.get(statistic) if result is not None else None
        if result is not None and stat is None:
            raise ValueError(f"no statistic {statistic!r} in this sweep's results")
        rows.append(
            [
                *(_format(status.coordinates.get(key, "")) for key in keys),
                status.state,
                str(result.n_replicates) if result is not None else "",
                _format(stat["mean"]) if stat else "",
                _format(stat["low"]) if stat and stat["low"] is not None else "",
                _format(stat["high"]) if stat and stat["high"] is not None else "",
            ]
        )
    if as_csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    else:
        print("  ".join(header))
        for row in rows:
            print("  ".join(row))
    return 0


def _axis_keys(study: groups.StudyManifest) -> list[str]:
    """Return the swept keys of a sweep study, in axis order."""
    if study.sweep_spec is None:
        raise ValueError(f"study {study.study_id} is not a sweep")
    axes = study.sweep_spec.get("axes")
    if not isinstance(axes, list):
        raise ValueError("the sweep study's stored axes are malformed")
    return [str(axis["key"]) for axis in axes]


def _format(value: object) -> str:
    """Format a value compactly: shortest round-trip form for numbers."""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
