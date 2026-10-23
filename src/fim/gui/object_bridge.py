"""GUI operations for creating and managing saved Run objects.

The bridge delegates lifecycle operations to `fim.run_service` and
`fim.persistence.run_objects`. Drafts retain entered form values in
`gui-form.json` so configurations that are not yet valid can be reopened.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import yaml

from fim.batch_session import check_batch_session_support
from fim.execution import check_session_support
from fim.gui.config_form import (
    form_values_to_draft_payload,
    form_values_to_payload,
    params_to_form_values,
    payload_to_yaml_text,
)
from fim.gui.presets import split_configuration
from fim.model.params import SimulationParams
from fim.paths import write_text_atomically
from fim.persistence import groups
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    RESUMABLE_STATES,
    LifecycleError,
    RunState,
    capabilities,
)
from fim.persistence.run_objects import (
    RunObject,
    RunObjectNotFoundError,
    clone_run_object,
    create_run_object,
    delete_run_object,
    list_run_objects,
    save_run_object,
    validate_configuration_text,
)
from fim.persistence.run_ownership import (
    kill_run_object,
    reconcile_run_object,
    reconcile_run_objects,
    record_worker_failure,
    request_pause,
)
from fim.run_service import cancel_run_object

GUI_FORM_FILE_NAME: Final = "gui-form.json"

# A key `SimulationParams.from_mapping` always rejects; added only when a
# form that does not parse would otherwise produce a valid configuration
# (every unparseable composite was left out), so the Draft stays a Draft.
_FORM_ISSUE_KEY: Final = "_form_issue"

logger = logging.getLogger(__name__)


def cancel(object_id: str, *, results: Path | None = None) -> str:
    """Cancel a saved run, or ask its running worker to stop.

    Args:
        object_id: The saved run.
        results: Results directory (default: `results_directory()`).

    Returns:
        The state the run is in afterward (`cancelling` or `cancelled`).
    """
    current = reconcile_run_object(object_id, results=results)
    outcome = cancel_run_object(
        object_id, expected_revision=current.record.revision, results=results
    )
    return outcome.run_object.record.state.value


def configuration_text(
    values: Mapping[str, str], *, name: str = "", description: str = ""
) -> tuple[str, str | None]:
    """Turn form values into configuration text, whether or not they are valid.

    Args:
        values: The form values, with run settings already merged in.
        name: Optional run name label.
        description: Optional run description label.

    Returns:
        The configuration text, and why the form is invalid (`None` when
        the form parses).
    """
    labels = {
        key: text.strip()
        for key, text in (("name", name), ("description", description))
        if text.strip()
    }
    try:
        payload = form_values_to_payload(values)
        SimulationParams.from_mapping(payload)
    except ValueError as error:
        problem: str | None = str(error)
        payload = form_values_to_draft_payload(values)
    else:
        problem = None
    text = payload_to_yaml_text({**labels, **payload})
    if problem is not None and validate_configuration_text(text)[0].is_valid:
        text = payload_to_yaml_text({**labels, **payload, _FORM_ISSUE_KEY: problem})
    return text, problem


def clone(object_id: str, *, results: Path | None = None) -> RunObject:
    """Create a new saved run from another one's configuration.

    Args:
        object_id: The saved run to clone.
        results: Results directory (default: `results_directory()`).

    Returns:
        The new RunObject.
    """
    reconciled = reconcile_run_object(object_id, results=results)
    cloned = clone_run_object(reconciled.object_id, results=results)
    sidecar = _read_sidecar(reconciled.directory)
    if sidecar is not None and sidecar.get("digest") == _digest(reconciled.config_text):
        values = sidecar.get("values")
        if isinstance(values, Mapping):
            _write_sidecar(
                cloned.directory,
                cloned.config_text,
                {str(k): str(v) for k, v in values.items()},
            )
    logger.debug("cloned run object %s as %s", object_id, cloned.object_id)
    return cloned


def delete(object_id: str, *, results: Path | None = None) -> None:
    """Delete a saved run that is not executing.

    Args:
        object_id: The saved run.
        results: Results directory (default: `results_directory()`).
    """
    current = reconcile_run_object(object_id, results=results)
    delete_run_object(
        object_id, expected_revision=current.record.revision, results=results
    )


def delete_many(object_ids: Sequence[str], *, results: Path | None = None) -> int:
    """Delete several saved runs, all or none of those that still exist.

    Every one is checked first, so one that is still executing leaves the
    others in place too. One already gone is not an error.

    Args:
        object_ids: The saved runs.
        results: Results directory (default: `results_directory()`).

    Returns:
        How many were deleted.

    Raises:
        LifecycleError: One of them cannot be deleted now; the message
            names every such run.
    """
    present: list[RunObject] = []
    for object_id in dict.fromkeys(object_ids):
        try:
            present.append(reconcile_run_object(object_id, results=results))
        except RunObjectNotFoundError:
            continue
    blocked = [
        run_object.labels.name or run_object.object_id
        for run_object in present
        if not row(run_object)["actions"]["delete"]
    ]
    if blocked:
        names = ", ".join(blocked)
        raise LifecycleError(
            f"these saved runs cannot be deleted while they execute: {names}"
        )
    deleted = 0
    for run_object in present:
        try:
            delete_run_object(
                run_object.object_id,
                expected_revision=run_object.record.revision,
                results=results,
            )
        except RunObjectNotFoundError:
            continue
        deleted += 1
    return deleted


def form_values(
    run_object: RunObject,
) -> tuple[dict[str, str] | None, str | None]:
    """Return a saved run's configuration as form values.

    Args:
        run_object: The saved run.

    Returns:
        The values, or `None` and why they cannot be recovered.
    """
    saved = _read_sidecar(run_object.directory)
    if saved is not None and saved.get("digest") == _digest(run_object.config_text):
        values = saved.get("values")
        if isinstance(values, Mapping):
            return {str(key): str(value) for key, value in values.items()}, None
    try:
        document = yaml.safe_load(run_object.config_text)
        if not isinstance(document, Mapping):
            return None, "the configuration is not a mapping of settings"
        model, _labels = split_configuration(document)
        params = SimulationParams.from_mapping(model)
    except (ValueError, TypeError, yaml.YAMLError) as error:
        return None, str(error)
    return params_to_form_values(params), None


def find_completed_run(
    run_id: str,
    software_version: str,
    *,
    results: Path | None = None,
) -> RunObject | None:
    """Find a completed Run object for this scientific run and version.

    Args:
        run_id: Content-derived scientific run identity.
        software_version: Version that must match the completed artifacts.
        results: Results directory (default: `results_directory()`).

    Returns:
        The matching completed object, or `None`.
    """
    for run_object in list_run_objects(results).objects:
        if (
            run_object.record.state is RunState.COMPLETED
            and run_object.record.scientific_run_id == run_id
            and groups.run_identity_of(run_object.directory)
            == (run_id, software_version)
        ):
            return run_object
    return None


def study_id_for(object_id: str, *, results: Path | None = None) -> str | None:
    """Return the Study selected when this Run object was started, if any."""
    run_object = reconcile_run_object(object_id, results=results)
    saved = _read_sidecar(run_object.directory)
    study_id = None if saved is None else saved.get("study_id")
    return study_id if isinstance(study_id, str) and study_id else None


def kill(object_id: str, *, results: Path | None = None) -> str:
    """Kill a saved run's worker at once, recording the run as failed.

    Args:
        object_id: The saved run.
        results: Results directory (default: `results_directory()`).

    Returns:
        The state the run is in afterward, normally `failed`.
    """
    current = reconcile_run_object(object_id, results=results)
    if current.record.state not in ACTIVE_STATES:
        return current.record.state.value
    return kill_run_object(object_id, results=results).record.state.value


def list_rows(
    results: Path | None = None,
    *,
    summarize: Callable[[SimulationParams], dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return one row per saved run, after recording any lost worker.

    Args:
        results: Results directory (default: `results_directory()`).
        summarize: Summarizes a valid configuration for each row's
            `configSummary`; `None` leaves that `None`.

    Returns:
        `{"runs": [...], "unreadable": [...]}`; each run row carries its
        state, progress, issues, and the actions now allowed.
    """
    reconcile_run_objects(results)
    listing = list_run_objects(results)
    return {
        "runs": [
            row(run_object, summarize=summarize) for run_object in listing.objects
        ],
        "unreadable": [
            f"{diagnostic.directory}: {diagnostic.message}"
            for diagnostic in listing.diagnostics
        ],
    }


def pause(object_id: str, *, results: Path | None = None) -> None:
    """Ask a running saved run to checkpoint and pause.

    Args:
        object_id: The saved run.
        results: Results directory (default: `results_directory()`).
    """
    current = reconcile_run_object(object_id, results=results)
    request_pause(object_id, expected_revision=current.record.revision, results=results)


def fail_worker(
    object_id: str, message: str, *, results: Path | None = None
) -> RunObject:
    """Record an unexpected worker exit as a failed Run object attempt."""
    return record_worker_failure(object_id, message, results=results)


def prepare_start(
    object_id: str, *, results: Path | None = None
) -> tuple[RunObject, SimulationParams]:
    """Check that a saved run can start or resume now, before any side effect.

    Args:
        object_id: The saved run.
        results: Results directory (default: `results_directory()`).

    Returns:
        The run (`planned` or resumable) and its parameters.

    Raises:
        LifecycleError: The run is a Draft, or is in any other state.
        ValueError: A saved run cannot run this configuration yet (more
            than one replicate, or a backend `fim.engine.simulate_params` would refuse).
    """
    run_object = reconcile_run_object(object_id, results=results)
    record = run_object.record
    if record.state is not RunState.PLANNED and record.state not in RESUMABLE_STATES:
        allowed = capabilities(record, resume_verified=True)
        reason = allowed.reasons.get("can_start", f"it is {record.state.value}")
        raise LifecycleError(f"this saved run cannot start: {reason}")
    model, _labels = split_configuration(yaml.safe_load(run_object.config_text))
    params = SimulationParams.from_mapping(model)
    if params.n_replicates > 1:
        check_batch_session_support(params)
    else:
        check_session_support(params)
    return run_object, params


def _config_summary(
    run_object: RunObject,
    summarize: Callable[[SimulationParams], dict[str, str]] | None,
) -> dict[str, str] | None:
    """Summarize a saved run's configuration, or `None` for a Draft."""
    if summarize is None or run_object.record.configuration_digest is None:
        return None
    try:
        params = SimulationParams.from_mapping(yaml.safe_load(run_object.config_text))
    except (ValueError, TypeError, yaml.YAMLError):
        return None
    return summarize(params)


def row(
    run_object: RunObject,
    *,
    summarize: Callable[[SimulationParams], dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return the Saved runs panel's row for one saved run.

    Args:
        run_object: The saved run.
        summarize: Summarizes a valid configuration as `configSummary`, so
            a run saved without a name is still told apart by its
            settings; `None` leaves that `None`.

    Returns:
        A JSON-ready mapping.
    """
    record = run_object.record
    allowed = capabilities(
        record,
        pause_supported=True,
        resume_verified=record.latest_checkpoint is not None,
    )
    error = record.last_error
    return {
        "objectId": run_object.object_id,
        "name": run_object.labels.name,
        "description": run_object.labels.description,
        "configSummary": _config_summary(run_object, summarize),
        "scientificRunId": record.scientific_run_id,
        "state": record.state.value,
        "generation": record.committed_generation,
        "updatedAt": record.updated_at,
        "issues": [issue.message for issue in record.validation_issues],
        "error": None if error is None else error.message,
        "directory": str(run_object.directory),
        "actions": {
            "edit": allowed.can_edit,
            "start": allowed.can_start,
            "resume": allowed.can_resume,
            "clone": True,
            "pause": allowed.can_pause,
            "cancel": allowed.can_cancel or allowed.can_abandon,
            "kill": record.state in ACTIVE_STATES,
            "delete": allowed.can_delete,
            "open": record.state is RunState.COMPLETED,
        },
        "reasons": dict(allowed.reasons),
    }


def save(
    values: Mapping[str, str],
    *,
    object_id: str | None = None,
    name: str = "",
    description: str = "",
    study_id: str | None = None,
    results: Path | None = None,
    create: Callable[..., RunObject] = create_run_object,
) -> tuple[RunObject, str | None]:
    """Save the form as a new saved run, or over an existing Draft or Planned one.

    Args:
        values: The form values, with run settings already merged in.
        object_id: The saved run to update; `None` creates one.
        name: Optional run name label.
        description: Optional run description label.
        results: Results directory (default: `results_directory()`).
        create: Injectable object creator.

    Returns:
        The saved run (`draft` or `planned`), and why the form is invalid.

    Raises:
        LifecycleError: The run has started, so its configuration is fixed,
            or it changed since it was read.
    """
    text, problem = configuration_text(values, name=name, description=description)
    if object_id is None:
        saved = create(text, results=results)
    else:
        current = reconcile_run_object(object_id, results=results)
        saved = save_run_object(
            object_id,
            text,
            expected_revision=current.record.revision,
            results=results,
        )
    _write_sidecar(saved.directory, text, values, study_id=study_id)
    logger.debug("saved form as %s (%s)", saved.object_id, saved.record.state.value)
    return saved, problem


def _digest(config_text: str) -> str:
    """Return the digest that ties `gui-form.json` to one configuration text."""
    return hashlib.sha256(config_text.encode("utf-8")).hexdigest()


def _read_sidecar(directory: Path) -> dict[str, Any] | None:
    """Read `gui-form.json`, or `None` if it is missing or unreadable."""
    try:
        document = json.loads((directory / GUI_FORM_FILE_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _write_sidecar(
    directory: Path,
    config_text: str,
    values: Mapping[str, str],
    *,
    study_id: str | None = None,
) -> None:
    """Write the form's values for the configuration text just saved."""
    document = {"digest": _digest(config_text), "values": dict(sorted(values.items()))}
    if study_id is not None:
        document["study_id"] = study_id
    text = json.dumps(document, indent=2, sort_keys=True)
    write_text_atomically(directory / GUI_FORM_FILE_NAME, text + "\n", prefix=".gui.")
