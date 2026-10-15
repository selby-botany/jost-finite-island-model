"""`fim object`: save, inspect, and control persisted Run objects.

A Run object (`fim.persistence.run_objects`) holds a saved configuration of
any validity and its lifecycle: `draft` while the configuration is invalid
or incomplete, `planned` once it is valid, then executing, paused, or
finished. Every command here calls `fim.run_service` or
`fim.persistence.run_ownership`; none applies lifecycle rules itself.

`start` and `resume` execute in the foreground. From another terminal,
`pause` asks the running command to publish a checkpoint and stop, and
`cancel` asks it to stop and keep its partial trajectory. SIGTERM pauses
the command the same way; a second SIGTERM ends it at once. A command that
is killed leaves the object `running` until any later command notices that
its owner is gone and records it as `interrupted`; `resume` then continues
from the latest checkpoint. A command killed after it published every
artifact is recorded as `completed` instead, once its completion receipt
verifies. A run stopped before its first checkpoint cannot resume; `clone`
saves its configuration as a new object that starts from generation zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fim.persistence.run_lifecycle import (
    RESUMABLE_STATES,
    LifecycleError,
    RunLifecycleRecord,
    RunState,
)
from fim.persistence.run_objects import (
    RunObject,
    clone_run_object,
    create_run_object,
    delete_run_object,
    list_run_objects,
    save_run_object,
)
from fim.persistence.run_ownership import (
    reconcile_run_object,
    reconcile_run_objects,
    request_pause,
)
from fim.persistence.staging import reclaim_orphan_staging
from fim.run_service import (
    CheckpointCadence,
    DraftRunError,
    cadence_for_params,
    cancel_run_object,
    completion_was_recovered,
    diagnose_run_object,
    execute_run_object,
    load_params,
)
from fim.termination import sigterm_requests_stop


def add_object_subcommands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """Wire `fim object` and its subcommands, `create` through `reclaim`."""
    object_parser = subcommands.add_parser(
        "object", help="save, inspect, and control resumable run objects"
    )
    commands = object_parser.add_subparsers(dest="object_command", required=True)
    create_parser = commands.add_parser(
        "create", help="save a configuration as a run object (draft if invalid)"
    )
    create_parser.add_argument("config", metavar="CONFIG", help="a configuration file")
    edit_parser = commands.add_parser(
        "edit", help="replace the configuration of a draft or planned object"
    )
    edit_parser.add_argument("object_id", metavar="OBJECT_ID")
    edit_parser.add_argument("config", metavar="CONFIG", help="a configuration file")
    commands.add_parser("list", help="list every run object and its state")
    status_parser = commands.add_parser("status", help="show one object's lifecycle")
    status_parser.add_argument("object_id", metavar="OBJECT_ID")
    status_parser.add_argument(
        "--json", action="store_true", help="print the full lifecycle record"
    )
    for name, text in (
        ("start", "execute a planned object in the foreground"),
        ("resume", "continue a paused, interrupted, or failed object"),
    ):
        execute_parser = commands.add_parser(name, help=text)
        execute_parser.add_argument("object_id", metavar="OBJECT_ID")
        execute_parser.add_argument(
            "--quiet", action="store_true", help="print only errors"
        )
        execute_parser.add_argument(
            "--checkpoint-generations",
            type=int,
            metavar="N",
            default=None,
            help="generations between periodic checkpoints",
        )
        execute_parser.add_argument(
            "--checkpoint-seconds",
            type=float,
            metavar="SECONDS",
            default=None,
            help="seconds between periodic checkpoints",
        )
    for name, text in (
        ("pause", "ask a running object to checkpoint and pause"),
        ("cancel", "stop an object for good, keeping its partial data"),
        ("delete", "delete an object that is not executing"),
    ):
        control_parser = commands.add_parser(name, help=text)
        control_parser.add_argument("object_id", metavar="OBJECT_ID")
    clone_parser = commands.add_parser(
        "clone", help="save an object's configuration as a new object"
    )
    clone_parser.add_argument("object_id", metavar="OBJECT_ID")
    reclaim_parser = commands.add_parser(
        "reclaim", help="remove staging directories that killed commands left"
    )
    reclaim_parser.add_argument(
        "--dry-run", action="store_true", help="only report what would be removed"
    )


def command_object(
    arguments: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    """Dispatch one `fim object` invocation.

    Args:
        arguments: The parsed command line.
        parser: The top-level parser, for its usage error.

    Returns:
        0 on success; 2 when the lifecycle refuses the request; 130 when
        execution was interrupted from the keyboard.
    """
    handlers: dict[str, Callable[[], int]] = {
        "clone": lambda: _clone(arguments.object_id),
        "create": lambda: _create(Path(arguments.config)),
        "edit": lambda: _edit(arguments.object_id, Path(arguments.config)),
        "list": _list,
        "status": lambda: _status(arguments.object_id, as_json=arguments.json),
        "start": lambda: _execute(
            arguments.object_id,
            quiet=arguments.quiet,
            resume=False,
            checkpoint_generations=arguments.checkpoint_generations,
            checkpoint_seconds=arguments.checkpoint_seconds,
        ),
        "resume": lambda: _execute(
            arguments.object_id,
            quiet=arguments.quiet,
            resume=True,
            checkpoint_generations=arguments.checkpoint_generations,
            checkpoint_seconds=arguments.checkpoint_seconds,
        ),
        "pause": lambda: _pause(arguments.object_id),
        "cancel": lambda: _cancel(arguments.object_id),
        "delete": lambda: _delete(arguments.object_id),
        "reclaim": lambda: _reclaim(dry_run=arguments.dry_run),
    }
    handler = handlers.get(arguments.object_command)
    if handler is None:
        parser.error("an object subcommand is required")
    try:
        return handler()
    except DraftRunError as error:
        print(
            f"fim: error: {error.object_id} is a draft and cannot start:",
            file=sys.stderr,
        )
        _print_issues(error.issues, stream=sys.stderr)
        return 2
    except LifecycleError as error:
        print(f"fim: error: {error}", file=sys.stderr)
        return 2


def _cancel(object_id: str) -> int:
    """Cancel an object, or ask its running command to stop."""
    current = reconcile_run_object(object_id)
    outcome = cancel_run_object(object_id, expected_revision=current.record.revision)
    state = outcome.run_object.record.state
    if state is RunState.CANCELLING:
        print(f"Cancel requested for {object_id}; it stops at its next safe point")
    else:
        print(f"Cancelled {object_id}")
    return 0


def _clone(object_id: str) -> int:
    """Save an object's configuration as a new object linked to it."""
    cloned = clone_run_object(object_id)
    _print_saved("Created", cloned)
    print(f"Cloned from {object_id}")
    return 0


def _create(config: Path) -> int:
    """Save a configuration file as a new object, whatever its validity."""
    created = create_run_object(config.read_text(encoding="utf-8"))
    _print_saved("Created", created)
    return 0


def _delete(object_id: str) -> int:
    """Delete an object that is not executing."""
    current = reconcile_run_object(object_id)
    delete_run_object(object_id, expected_revision=current.record.revision)
    print(f"Deleted {object_id}")
    return 0


def _edit(object_id: str, config: Path) -> int:
    """Replace the saved configuration of a draft or planned object."""
    current = reconcile_run_object(object_id)
    saved = save_run_object(
        object_id,
        config.read_text(encoding="utf-8"),
        expected_revision=current.record.revision,
    )
    _print_saved("Saved", saved)
    return 0


def _execute(
    object_id: str,
    *,
    quiet: bool,
    resume: bool,
    checkpoint_generations: int | None = None,
    checkpoint_seconds: float | None = None,
) -> int:
    """Start or resume an object and run it in the foreground until it stops."""
    # `start` and `resume` name the user's intent; refuse the other one
    # rather than silently doing it.
    reconciled = reconcile_run_object(object_id)
    state = reconciled.record.state
    if completion_was_recovered(reconciled.record):
        if not quiet:
            print(f"{object_id} had already finished; it is completed")
            print(f"Artifacts: {reconciled.directory}")
        return 0
    if resume and state not in RESUMABLE_STATES:
        raise LifecycleError(
            f"{object_id} is {state.value}; only a paused, interrupted, or "
            "failed object can resume"
        )
    if state in RESUMABLE_STATES and reconciled.record.latest_checkpoint is None:
        raise LifecycleError(
            f"{object_id} is {state.value} and stopped before its first "
            f"checkpoint, so it cannot resume; `fim object clone {object_id}` "
            "saves its configuration as a new run that starts from generation 0"
        )
    if not resume and state in RESUMABLE_STATES:
        raise LifecycleError(
            f"{object_id} is {state.value}; use `fim object resume {object_id}`"
        )
    cadence: CheckpointCadence | None = None
    if checkpoint_generations is not None or checkpoint_seconds is not None:
        params = load_params(reconciled)
        cadence = cadence_for_params(
            params,
            generations=checkpoint_generations,
            seconds=checkpoint_seconds,
        )
    if not quiet:
        print(f"{'Resuming' if resume else 'Starting'} {object_id}")
    execution_kwargs: dict[str, Any] = {}
    if cadence is not None:
        execution_kwargs["cadence"] = cadence
    try:
        with sigterm_requests_stop() as stop_requested:
            finished = execute_run_object(
                object_id,
                stop_requested=stop_requested,
                **execution_kwargs,
            )
    except KeyboardInterrupt:
        print(
            f"Interrupted; continue with `fim object resume {object_id}`",
            file=sys.stderr,
        )
        return 130
    if not quiet:
        record = finished.record
        print(
            f"{object_id} is {record.state.value} at generation "
            f"{record.committed_generation}"
        )
        if record.state is RunState.COMPLETED:
            print(f"Artifacts: {finished.directory}")
        elif record.state is RunState.PAUSED:
            print(f"Continue with `fim object resume {object_id}`")
    return 0


def _list() -> int:
    """Print one row per object, after recording any lost owners."""
    reconcile_run_objects()
    listing = list_run_objects()
    if not listing.objects and not listing.diagnostics:
        print("No run objects.")
        return 0
    if listing.objects:
        print(f"{'OBJECT_ID':<38}{'STATE':<13}{'GENERATION':>10}  NAME")
    for run_object in listing.objects:
        record = run_object.record
        generation = (
            "-" if record.committed_generation is None else record.committed_generation
        )
        print(
            f"{run_object.object_id:<38}{record.state.value:<13}{generation!s:>10}  "
            f"{run_object.labels.name or ''}"
        )
    for diagnostic in listing.diagnostics:
        print(f"unreadable: {diagnostic.directory}: {diagnostic.message}")
    return 0


def _pause(object_id: str) -> int:
    """Ask a running object's command to checkpoint and pause."""
    current = reconcile_run_object(object_id)
    request_pause(object_id, expected_revision=current.record.revision)
    print(f"Pause requested for {object_id}; it pauses at its next safe point")
    return 0


def _print_issues(issues: tuple[Any, ...], *, stream: Any = None) -> None:
    """Print one validation issue per line."""
    for issue in issues:
        where = f"{issue.location}: " if issue.location else ""
        print(f"  - {where}{issue.message}", file=stream or sys.stdout)


def _print_record(record: RunLifecycleRecord) -> None:
    """Print the lifecycle fields a person needs to decide what to do next."""
    checkpoint = record.latest_checkpoint
    rows: list[tuple[str, object]] = [
        ("Object", record.object_id),
        ("State", record.state.value),
        ("Revision", record.revision),
        ("Kind", record.kind.value),
        ("Run ID", record.scientific_run_id or "-"),
        ("Phase", record.phase.value if record.phase else "-"),
        (
            "Generation",
            "-" if record.committed_generation is None else record.committed_generation,
        ),
        (
            "Checkpoint",
            "-"
            if checkpoint is None
            else f"{checkpoint.checkpoint_id} "
            f"(generation {checkpoint.committed_generation})",
        ),
        ("Attempt", record.active_attempt_id or "-"),
    ]
    for label, value in rows:
        print(f"{label + ':':<12}{value}")
    if record.last_error is not None:
        print(
            f"{'Error:':<12}[{record.last_error.category}] {record.last_error.message}"
        )
    if record.validation_issues:
        print("Validation issues:")
        _print_issues(record.validation_issues)


def _print_saved(verb: str, run_object: RunObject) -> None:
    """Report a saved object, with the issues that keep a draft from starting."""
    record = run_object.record
    print(f"{verb} {run_object.object_id} ({record.state.value})")
    if record.validation_issues:
        print("It cannot start until these are fixed:")
        _print_issues(record.validation_issues)


def _reclaim(*, dry_run: bool) -> int:
    """Remove, or with `dry_run` list, orphan staging directories."""
    orphans = reclaim_orphan_staging(dry_run=dry_run)
    if not orphans:
        print("No orphan staging directories.")
        return 0
    for orphan in orphans:
        if not orphan.reclaimable:
            verb = "Kept"
        elif dry_run:
            verb = "Would remove"
        else:
            verb = "Removed"
        print(f"{verb} {orphan.kind.value} staging {orphan.path}: {orphan.reason}")
    return 0


def _status(object_id: str, *, as_json: bool) -> int:
    """Show one object's lifecycle, after recording a lost owner."""
    run_object = reconcile_run_object(object_id)
    record = run_object.record
    if as_json:
        print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
        return 0
    _print_record(record)
    problems = diagnose_run_object(run_object)
    if problems:
        print("Problems:")
        for problem in problems:
            print(f"  - {problem}")
    return 0
