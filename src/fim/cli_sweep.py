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
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from fim.config.defaults import DEFAULT_SWEEP_MAX_ATTEMPTS
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.sweep import (
    SIZE_CONFIRMATION_THRESHOLD,
    SweepPlan,
    SweepPoint,
    SweepSpec,
    enumerate_points,
    spec_from_config,
)
from fim.sweep_compare import Comparison, SweepRef, compare_sweeps
from fim.sweep_cost import (
    Band,
    EngineProbe,
    SweepCost,
    ThroughputCache,
    describe_cost,
    format_duration,
    order_costliest_first,
    point_cost,
    probed_cost,
    read_actual,
    static_cost,
)
from fim.sweep_run import (
    Concurrency,
    LocalPointRunner,
    SweepEvent,
    create_sweep_study,
    replan_sweep,
    resolve_concurrency,
    run_sweep,
)
from fim.sweep_status import SweepStatus, describe_remaining, read_sweep_status
from fim.sweep_table import ALL_STATISTICS, ResultTable


def add_sweep_subcommands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Wire `fim sweep plan/run/resume/replan/status/export/compare/report`."""
    sweep_parser = subcommands.add_parser(
        "sweep", help="run a configuration over a parameter space as one study"
    )
    sweep_subcommands = sweep_parser.add_subparsers(dest="sweep_command", required=True)
    plan_parser = sweep_subcommands.add_parser(
        "plan", help="list the points a sweep file would run; runs nothing"
    )
    plan_parser.add_argument("file", metavar="FILE", help="a sweep file (YAML)")
    _add_concurrency_arguments(plan_parser, retry=False)
    _add_probe_argument(plan_parser)
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
    _add_concurrency_arguments(run_parser)
    _add_probe_argument(run_parser)
    run_parser.add_argument(
        "--pilot",
        type=int,
        metavar="K",
        help="run the K points that may take longest first, show how long they "
        "took against the estimate, and ask before running the rest "
        "(--yes answers yes)",
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
    resume_parser.add_argument(
        "--rerun",
        action="store_true",
        help="also recompute the points already completed, from the stored "
        "specification, and compare each with its existing run: a bit-for-bit "
        "match replaces it, a difference keeps both and is reported",
    )
    _add_concurrency_arguments(resume_parser)
    status_parser = sweep_subcommands.add_parser(
        "status",
        help="show where a sweep stands: points by state, who runs it, time left",
    )
    status_parser.add_argument("study_id", metavar="STUDY_ID")
    status_parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="show it again every SECONDS until the sweep is finished",
    )
    export_parser = sweep_subcommands.add_parser(
        "export",
        help="write a sweep's results as a tidy CSV or JSON Lines table",
    )
    export_parser.add_argument("study_id", metavar="STUDY_ID")
    export_parser.add_argument("out", metavar="OUT", help="file to write")
    export_parser.add_argument(
        "--format",
        choices=("csv", "jsonl"),
        help="table format (default: jsonl for a .jsonl file name, else csv)",
    )
    export_parser.add_argument(
        "--statistic",
        action="append",
        metavar="NAME",
        help="statistic to write; repeat for several (default: D and G_ST)",
    )
    export_parser.add_argument(
        "--all-statistics", action="store_true", help="write every statistic"
    )
    export_parser.add_argument(
        "--wide",
        action="store_true",
        help="one row per point with a column group per statistic (CSV only)",
    )
    export_parser.add_argument(
        "--with-theory",
        action="store_true",
        help="add the closed-form prediction and the residual (mean minus theory)",
    )
    export_parser.add_argument(
        "--include-unfinished",
        action="store_true",
        help="also write a row for each point with no result, its state filled",
    )
    export_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing table"
    )
    compare_parser = sweep_subcommands.add_parser(
        "compare",
        help="compare one statistic across two or more sweeps on their shared axes",
    )
    compare_parser.add_argument(
        "studies",
        nargs="+",
        metavar="STUDY[=LABEL]",
        help="two or more sweep studies; a label after = names one in the output",
    )
    compare_parser.add_argument(
        "--statistic", default="D", help="statistic to compare (default: D)"
    )
    compare_parser.add_argument(
        "--on",
        action="append",
        metavar="KEY",
        help="axis to join on, repeated for several (default: every shared axis)",
    )
    compare_parser.add_argument(
        "--csv", metavar="OUT", help="also write the comparison to a CSV file"
    )
    compare_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing --csv file"
    )
    replan_parser = sweep_subcommands.add_parser(
        "replan",
        help="re-identify a sweep study's points after an upgrade changed run ids",
    )
    replan_parser.add_argument("study_id", metavar="STUDY_ID")
    replan_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change and write nothing",
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


def _add_probe_argument(parser: argparse.ArgumentParser) -> None:
    """Add `--probe/--no-probe`, which turns the engine measurement on or off."""
    parser.add_argument(
        "--probe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="measure the engine for a few seconds to estimate time and disk "
        "(default: on); --no-probe prints the work range only",
    )


def _add_concurrency_arguments(
    parser: argparse.ArgumentParser, *, retry: bool = True
) -> None:
    """Add the concurrency flags, and the retry flag unless `retry` is false."""
    if retry:
        parser.add_argument(
            "--max-attempts",
            type=int,
            default=DEFAULT_SWEEP_MAX_ATTEMPTS,
            metavar="N",
            help="times to try a point that failed for a transient reason, such "
            f"as a worker process that died (default: {DEFAULT_SWEEP_MAX_ATTEMPTS}); "
            "a failure the engine reports is never tried again",
        )
    parser.add_argument(
        "--points-at-once",
        default="auto",
        metavar="N|auto",
        help="points to run at the same time (default: auto, which fills the "
        "cores: a single run needs one, a batch as many as its replicates)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help="worker processes for each batch point (default: as many as fit)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="one point at a time, each with one worker",
    )


def _concurrency_options(
    arguments: argparse.Namespace,
) -> tuple[int | None, int | None]:
    """Turn the concurrency flags into `(points_at_once, max_workers)`.

    Raises:
        ValueError: A flag is not a positive whole number, or `--sequential`
            is combined with `--points-at-once` or `--workers`.
    """
    text = str(arguments.points_at_once)
    if arguments.sequential:
        if text != "auto" or arguments.workers is not None:
            raise ValueError(
                "--sequential cannot be combined with other concurrency flags"
            )
        return 1, 1
    points: int | None = None
    if text != "auto":
        if not text.isdigit() or int(text) < 1:
            raise ValueError(
                "--points-at-once must be a whole number of at least 1, or auto"
            )
        points = int(text)
    if arguments.workers is not None and arguments.workers < 1:
        raise ValueError("--workers must be at least 1")
    return points, arguments.workers


def command_sweep(
    arguments: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    """Dispatch one `fim sweep` subcommand and return its exit status."""
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "plan": _plan,
        "run": _run,
        "resume": _resume,
        "replan": _replan,
        "export": _export,
        "status": _status,
        "compare": _compare,
        "report": lambda args: _report(args.study_id, args.statistic, as_csv=args.csv),
    }
    handler = handlers.get(arguments.sweep_command)
    if handler is None:
        parser.error("a sweep subcommand is required")
    return handler(arguments)


def _load(path: Path) -> tuple[SweepSpec, str | None]:
    """Read a sweep file into its spec and optional name."""
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("a sweep file's root must be a mapping")
    return spec_from_config(payload)


def _plan(arguments: argparse.Namespace) -> int:
    """Print the plan for a sweep file; run nothing."""
    spec, _ = _load(Path(arguments.file))
    plan = enumerate_points(spec)
    _print_plan(spec, plan, arguments)
    return 0 if plan.points else 2


def _band_text(band: Band) -> str:
    """Write a band as "a to b", or just "a" when its ends are equal."""
    low, high = f"{band.low:,.0f}", f"{band.high:,.0f}"
    return low if low == high else f"{low} to {high}"


def _cost_for(
    spec: SweepSpec,
    plan: SweepPlan,
    arguments: argparse.Namespace,
) -> tuple[SweepCost, Concurrency | None]:
    """Work out the cost bands of a plan, measuring the engine unless told not to.

    Returns:
        The cost, and the concurrency it assumed (`None` for an empty plan).
    """
    if not plan.points:
        return static_cost(spec, plan), None
    points_at_once, max_workers = _concurrency_options(arguments)
    chosen = resolve_concurrency(
        points=len(plan.points),
        params=SimulationParams.from_mapping(plan.points[0].params),
        requested=points_at_once,
        max_workers=max_workers,
    )
    if not getattr(arguments, "probe", True):
        return static_cost(spec, plan, points_at_once=chosen.points_at_once), chosen
    from fim.gui.preferences import preferences_file_path  # noqa: PLC0415

    cache = ThroughputCache(preferences_file_path().with_name("sweep-throughput.json"))
    cost = probed_cost(
        spec,
        plan,
        EngineProbe(),
        points_at_once=chosen.points_at_once,
        workers_per_point=chosen.workers_per_point or 1,
        cache=cache,
    )
    return cost, chosen


def _print_plan(
    spec: SweepSpec, plan: SweepPlan, arguments: argparse.Namespace
) -> None:
    """Print the points, the invalid ones with reasons, and the size and cost."""
    keys = [axis.key for axis in spec.axes]
    print("  ".join(["point", *keys, "run_id"]))
    for point in plan.points:
        values = [_format(point.coordinates[key]) for key in keys]
        print("  ".join([f"{point.index:>5}", *values, point.run_id]))
    for invalid in plan.invalid:
        values = [_format(invalid.coordinates[key]) for key in keys]
        print("  ".join([f"{invalid.index:>5}", *values, f"INVALID: {invalid.reason}"]))
    cost, chosen = _cost_for(spec, plan, arguments)
    print(
        f"{len(plan.points)} valid point(s), {len(plan.invalid)} invalid, "
        f"{plan.collapsed} duplicate(s) of {plan.grid_size} grid position(s)"
    )
    print(
        f"work   {_band_text(cost.replicate_band())} replicate(s) each, "
        f"{_band_text(cost.generation_band())} generations each: "
        f"{_band_text(cost.work)} replicate-generations in all "
        "(a range, because nobody knows in advance how soon a run settles)"
    )
    if cost.wall_seconds is not None and cost.disk_bytes is not None:
        _print_probed(cost, chosen)
    else:
        print("time   not estimated (run without --no-probe to measure the engine)")
    if plan.needs_confirmation:
        print(
            f"This is {SIZE_CONFIRMATION_THRESHOLD} or more points; "
            "'fim sweep run' will need --yes."
        )


def _print_probed(cost: SweepCost, chosen: Concurrency | None) -> None:
    """Print the time, disk and probe lines of a measured cost."""
    lines = describe_cost(cost, cores=chosen.cores if chosen is not None else None)
    assert lines is not None
    print(f"time   {lines.time}")
    print(f"disk   {lines.disk}")
    print(f"probe  {lines.probe}")


def _run(arguments: argparse.Namespace) -> int:
    """Create the sweep's study and run it."""
    path = Path(arguments.file)
    spec, file_name = _load(path)
    plan = enumerate_points(spec)
    _print_plan(spec, plan, arguments)
    if not plan.points:
        print("fim: error: the sweep has no valid points to run", file=sys.stderr)
        return 2
    if plan.needs_confirmation and not arguments.yes:
        print("fim: error: pass --yes to run a sweep this large", file=sys.stderr)
        return 2
    name = arguments.study or file_name or path.stem
    study = create_sweep_study(spec, plan, name, experiment_id=arguments.experiment)
    print(f"Created sweep study {study.study_id}: {study.name}")
    if arguments.pilot is not None:
        return _pilot(study.study_id, plan, arguments)
    return _execute(study.study_id, arguments)


def _pilot(study_id: str, plan: SweepPlan, arguments: argparse.Namespace) -> int:
    """Run the costliest points first, compare them with the plan, then ask.

    The pilot points are real, stored results, so nothing is wasted whatever
    the answer: continuing runs only the rest, and stopping leaves the
    sweep resumable.
    """
    if arguments.pilot < 1:
        raise ValueError("--pilot must be at least 1")
    chosen = order_costliest_first(plan.points)[: arguments.pilot]
    print(f"Pilot: running the {len(chosen)} costliest of {len(plan.points)} point(s)")
    code = _execute(
        study_id, arguments, only=frozenset(point.run_id for point in chosen)
    )
    if code != 0:
        return code
    status = read_sweep_status(study_id)
    _print_pilot_report(study_id, chosen, status)
    if not arguments.yes and not _confirmed("Run the remaining points? [y/N] "):
        print(f"Stopped after the pilot. Continue with: fim sweep resume {study_id}")
        return 0
    return _execute(study_id, arguments)


def _confirmed(question: str) -> bool:
    """Ask a yes/no question on the terminal; end of input means no."""
    try:
        return input(question).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _print_pilot_report(
    study_id: str, chosen: Sequence[SweepPoint], status: SweepStatus
) -> None:
    """Print the pilot points' actual cost against the plan's, and the ETA."""
    study = groups.get_study(study_id)
    directories = {groups.run_id_of(d): d for d in groups.study_run_directories(study)}
    print("Pilot result (planned = the plan's range; actual = what the point took):")
    for point in chosen:
        planned = point_cost(point.run_id, SimulationParams.from_mapping(point.params))
        directory = directories.get(point.run_id)
        actual = read_actual(point.run_id, directory) if directory else None
        where = ", ".join(f"{k}={_format(v)}" for k, v in point.coordinates.items())
        if actual is None:
            print(f"  point {point.index} ({where}): no result")
            continue
        print(
            f"  point {point.index} ({where}): planned "
            f"{_band_text(planned.work)} replicate-generations, actual "
            f"{actual.work:,.0f} in {format_duration(actual.seconds)}"
        )
    _print_remaining(status)


def _print_remaining(status: SweepStatus) -> None:
    """Print the time left, from the calibrated estimate."""
    print(f"time left   {describe_remaining(status)}")


def _status(arguments: argparse.Namespace) -> int:
    """Show a sweep's status, once or on a fixed interval."""
    try:
        while True:
            status = read_sweep_status(arguments.study_id)
            _print_status(status)
            if arguments.watch is None or status.finished:
                return 0
            time.sleep(arguments.watch)
            print()
    except KeyboardInterrupt:
        print()
        return 0


def _print_status(status: SweepStatus) -> None:
    """Print one snapshot of a sweep's status."""
    done = status.counts.get("done", 0)
    print(
        f"Sweep {status.name} ({status.study_id}): {done} of {status.total} points done"
    )
    states = ", ".join(f"{n} {state}" for state, n in sorted(status.counts.items()))
    print(f"  {states}")
    print(
        "  a process is running this sweep now"
        if status.locked
        else "  no process is running this sweep"
    )
    for point in status.running:
        where = ", ".join(f"{k}={_format(v)}" for k, v in point.coordinates.items())
        print(f"  running: point {point.index} ({where})")
    print(f"  last point finished: {status.last_finished or 'none yet'}")
    _print_remaining(status)
    for failure in status.failures:
        where = ", ".join(f"{k}={_format(v)}" for k, v in failure.coordinates.items())
        tries = f", {failure.attempts} attempt(s)" if failure.attempts else ""
        print(f"  failed: point {failure.index} ({where}): {failure.reason}{tries}")
    if status.failures:
        print(f"  retry them with: fim sweep resume {status.study_id} --retry-failed")


def _compare(arguments: argparse.Namespace) -> int:
    """Compare a statistic across sweeps and print where they line up."""
    refs = []
    for item in arguments.studies:
        study_id, _, label = item.partition("=")
        refs.append(SweepRef(study_id, label))
    comparison = compare_sweeps(refs, statistic=arguments.statistic, on=arguments.on)
    rows = _comparison_rows(comparison)
    _print_comparison(comparison, rows)
    if arguments.csv:
        target = Path(arguments.csv)
        if target.exists() and not arguments.force:
            raise FileExistsError(
                f"{target} already exists; choose another name or pass --force"
            )
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerows(rows)
        print(f"Wrote {target}")
    return 0


def _comparison_rows(comparison: Comparison) -> list[list[str]]:
    """Lay a comparison out as a header and one row per shared coordinate."""
    two = len(comparison.labels) == 2  # noqa: PLR2004
    header = [*comparison.on]
    for label in comparison.labels:
        header += [
            f"{label}: mean",
            f"{label}: low",
            f"{label}: high",
            f"{label}: seed",
        ]
    if two:
        header += ["difference", "diff low", "diff high"]
    rows = [header]
    for point in comparison.points:
        row = [_format(point.coordinates[key]) for key in comparison.on]
        for label in comparison.labels:
            cell = point.cells[label]
            seed = point.seeds[label]
            row += [
                _format(cell.mean) if cell else "",
                _format(cell.low) if cell and cell.low is not None else "",
                _format(cell.high) if cell and cell.high is not None else "",
                "" if seed is None else str(seed),
            ]
        if two:
            diff = point.difference
            row += [
                _format(diff.difference) if diff else "",
                _format(diff.low) if diff and diff.low is not None else "",
                _format(diff.high) if diff and diff.high is not None else "",
            ]
        rows.append(row)
    return rows


def _print_comparison(comparison: Comparison, rows: list[list[str]]) -> None:
    """Print what was compared, how the seeds relate, and the table."""
    print(
        f"Comparing {comparison.statistic} across "
        f"{', '.join(comparison.labels)}, joined on {', '.join(comparison.on)}"
    )
    for key, values in comparison.base_differences.items():
        shown = "; ".join(f"{label}: {value}" for label, value in values.items())
        print(f"  base differs in {key}: {shown}")
    policies = ", ".join(f"{k}: {v}" for k, v in comparison.seed_policies.items())
    print(f"  seed policy: {policies}")
    note = {
        "independent": "the runs used different seeds",
        "shared-seeds": (
            "the runs used the same seeds, so they are paired, and the interval "
            "of a difference (which assumes independence) is only approximate"
        ),
        "partly-shared-seeds": "some compared runs used the same seeds",
        "unknown": "no coordinate has a finished point in two sweeps",
    }[comparison.pairing]
    print(f"  seeds: {note}")
    if len(comparison.labels) == 2:  # noqa: PLR2004
        print(
            f"  difference is {comparison.labels[0]} minus {comparison.labels[1]}, "
            "with a Welch interval"
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print(
            "  ".join(
                text.ljust(width) for text, width in zip(row, widths, strict=True)
            )
        )


def _export(arguments: argparse.Namespace) -> int:
    """Write a sweep's results as a tidy table and say where it went."""
    out = Path(arguments.out)
    fmt = arguments.format or ("jsonl" if out.suffix == ".jsonl" else "csv")
    if arguments.wide and fmt != "csv":
        raise ValueError("--wide is available for CSV only")
    statistics = ALL_STATISTICS if arguments.all_statistics else arguments.statistic
    table = ResultTable.read(arguments.study_id)
    options = {
        "statistics": statistics,
        "with_theory": arguments.with_theory,
        "include_unfinished": arguments.include_unfinished,
        "overwrite": arguments.force,
    }
    if fmt == "jsonl":
        sidecar = table.to_jsonl(out, **options)
    else:
        sidecar = table.to_csv(out, wide=arguments.wide, **options)
    print(f"Wrote {out} and {sidecar}")
    return 0


def _replan(arguments: argparse.Namespace) -> int:
    """Re-identify a sweep study's points and print what changed."""
    outcome = replan_sweep(arguments.study_id, dry_run=arguments.dry_run)
    verb = "Would change" if arguments.dry_run else "Changed"
    print(
        f"{verb}: {len(outcome.renamed)} point(s) got a new run id, "
        f"{outcome.unchanged} already matched, {len(outcome.added)} added, "
        f"{len(outcome.removed)} removed"
    )
    for old, new in outcome.renamed:
        print(f"  {old} -> {new}")
    if outcome.failures_moved:
        print(f"{outcome.failures_moved} recorded failure(s) follow their points")
    if outcome.unlinked_objects:
        print(
            f"{len(outcome.unlinked_objects)} run object(s) are no longer "
            "continued by this sweep: " + ", ".join(outcome.unlinked_objects)
        )
    if outcome.orphaned_runs:
        print(
            f"{len(outcome.orphaned_runs)} run(s) in the study match no point "
            "and stay attached as ordinary runs; their points run again:"
        )
        for directory in outcome.orphaned_runs:
            print(f"  {directory}")
    if not arguments.dry_run:
        print(f"Continue with: fim sweep resume {arguments.study_id}")
    return 0


def _resume(arguments: argparse.Namespace) -> int:
    """Run the points of an existing sweep study that are missing."""
    groups.get_study(arguments.study_id)
    return _execute(arguments.study_id, arguments)


def _execute(
    study_id: str,
    arguments: argparse.Namespace,
    *,
    only: frozenset[str] | None = None,
) -> int:
    """Run a sweep study, print a line per point, and return an exit status."""
    points_at_once, max_workers = _concurrency_options(arguments)
    cancel_event = threading.Event()
    try:
        outcome = run_sweep(
            study_id,
            LocalPointRunner(),
            _print_event,
            cancel_event,
            retry_failed=getattr(arguments, "retry_failed", False),
            rerun=getattr(arguments, "rerun", False),
            points_at_once=points_at_once,
            max_workers=max_workers,
            max_attempts=arguments.max_attempts,
            only=only,
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
            f"{outcome.recomputed} point(s) recomputed and matched the "
            f"earlier result bit for bit; {outcome.differing} differed"
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
    prefix = f"[{event.position}/{event.total} finished] point {event.index}"
    if event.kind == "point_started":
        print(f"{prefix}: running", flush=True)
    elif event.kind == "point_done":
        took = ""
        if isinstance(event.detail, dict) and "seconds" in event.detail:
            took = f" in {format_duration(float(event.detail['seconds']))}"
        print(f"{prefix}: done{took}", flush=True)
    elif event.kind == "point_reused":
        print(f"{prefix}: reused an existing run", flush=True)
    elif event.kind == "point_recomputed":
        print(f"{prefix}: recomputed; matches the earlier result", flush=True)
    elif event.kind == "point_differs":
        print(
            f"{prefix}: recomputed; DIFFERS from the earlier result "
            f"({_describe_differences(event.detail)})",
            flush=True,
        )
    elif event.kind == "point_failed":
        print(f"{prefix}: FAILED ({event.detail})", flush=True)


def _report(study_id: str, statistic: str, *, as_csv: bool) -> int:
    """Print each point's statistic with its interval and health, as text or CSV."""
    table = ResultTable.read(study_id)
    keys = list(table.axis_keys)
    header = [*keys, "state", "replicates", statistic, "low", "high", "health"]
    rows: list[list[str]] = []
    for point in table.points:
        result = point.row
        cell = result.statistics.get(statistic) if result is not None else None
        if result is not None and cell is None:
            raise ValueError(f"no statistic {statistic!r} in this sweep's results")
        health = result.health if result is not None else None
        rows.append(
            [
                *(_format(point.coordinates.get(key, "")) for key in keys),
                point.state,
                str(result.n_replicates) if result is not None else "",
                _format(cell.mean) if cell else "",
                _format(cell.low) if cell and cell.low is not None else "",
                _format(cell.high) if cell and cell.high is not None else "",
                health.classify(table.policy) if health is not None else "",
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


def _format(value: object) -> str:
    """Format a value compactly: shortest round-trip form for numbers."""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
