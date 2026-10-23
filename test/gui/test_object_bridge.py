"""Tests for `fim.gui.object_bridge`: the Saved runs panel's logic."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fim.examples import classes
from fim.gui import object_bridge
from fim.gui.config_form import starter_form_values
from fim.model.params import SimulationParams
from fim.persistence.run_lifecycle import CheckpointReference, LifecycleError, RunState
from fim.persistence.run_objects import read_run_object, write_run_record
from fim.persistence.run_ownership import request_cancel, start_run_object
from fim.run_service import CheckpointCadence, Session, execute_run_object

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _valid(**changes: str) -> dict[str, str]:
    """A tiny valid form, run settings included."""
    values = starter_form_values(overrides={"ploidy": "1"})
    values.update(
        {
            "N": "16",
            "d": "2",
            "n_replicates": "1",
            "max_generations": "10",
            "engine_backend": "lineal",
        }
    )
    values.update(changes)
    return values


def test_an_invalid_form_saves_as_a_draft_with_its_problem(tmp_path: Path) -> None:
    saved, problem = object_bridge.save(
        _valid(N="many"), name="Trial", results=tmp_path
    )

    assert saved.record.state is RunState.DRAFT
    assert problem is not None
    assert saved.labels.name == "Trial"
    assert saved.record.validation_issues


def test_reopening_a_draft_returns_the_values_exactly_as_typed(
    tmp_path: Path,
) -> None:
    typed = _valid(N="many", m_rate="0.1, oops")
    saved, _problem = object_bridge.save(typed, results=tmp_path)

    values, problem = object_bridge.form_values(
        read_run_object(saved.object_id, results=tmp_path)
    )

    assert problem is None
    assert values == typed


def test_a_draft_and_a_planned_run_trade_places_on_save(tmp_path: Path) -> None:
    saved, problem = object_bridge.save(_valid(), results=tmp_path)
    assert (saved.record.state, problem) == (RunState.PLANNED, None)

    broken, problem = object_bridge.save(
        _valid(d="1"), object_id=saved.object_id, results=tmp_path
    )
    assert broken.record.state is RunState.DRAFT
    assert problem is not None

    fixed, problem = object_bridge.save(
        _valid(), object_id=saved.object_id, results=tmp_path
    )
    assert (fixed.record.state, problem) == (RunState.PLANNED, None)


def test_a_form_that_drops_only_composites_still_saves_as_a_draft(
    tmp_path: Path,
) -> None:
    # An unparseable expert setting drops the whole `expert` block, and
    # every expert setting has a default, so the text alone would validate.
    text, problem = object_bridge.configuration_text(_valid(expert_check_growth="fast"))

    assert problem is not None
    assert "_form_issue" in text
    saved, _problem = object_bridge.save(
        _valid(expert_check_growth="fast"), results=tmp_path
    )
    assert saved.record.state is RunState.DRAFT


def test_form_values_fall_back_to_the_configuration_when_stale(
    tmp_path: Path,
) -> None:
    saved, _problem = object_bridge.save(_valid(), results=tmp_path)
    sidecar = saved.directory / object_bridge.GUI_FORM_FILE_NAME
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    document["digest"] = "stale"
    document["values"]["N"] = "999"
    sidecar.write_text(json.dumps(document), encoding="utf-8")

    values, problem = object_bridge.form_values(
        read_run_object(saved.object_id, results=tmp_path)
    )

    assert problem is None
    assert values is not None
    assert values["N"] == "16"


def test_rows_report_state_and_allowed_actions(tmp_path: Path) -> None:
    draft, _problem = object_bridge.save(_valid(N="many"), results=tmp_path)
    planned, _problem = object_bridge.save(_valid(), name="Ok", results=tmp_path)

    listing = object_bridge.list_rows(tmp_path)

    rows = {entry["objectId"]: entry for entry in listing["runs"]}
    assert listing["unreadable"] == []
    assert rows[draft.object_id]["state"] == "draft"
    assert rows[draft.object_id]["actions"]["start"] is False
    assert rows[draft.object_id]["actions"]["edit"] is True
    assert rows[draft.object_id]["actions"]["clone"] is True
    assert rows[draft.object_id]["issues"]
    assert rows[planned.object_id]["name"] == "Ok"
    assert rows[planned.object_id]["actions"]["start"] is True
    assert rows[planned.object_id]["actions"]["clone"] is True
    assert rows[planned.object_id]["actions"]["open"] is False


def test_rows_summarize_a_valid_configuration_but_not_a_draft(
    tmp_path: Path,
) -> None:
    draft, _problem = object_bridge.save(_valid(N="many"), results=tmp_path)
    planned, _problem = object_bridge.save(_valid(), results=tmp_path)
    seen: list[int] = []

    def summarize(params: SimulationParams) -> dict[str, str]:
        seen.append(params.d)
        return {"d": str(params.d)}

    rows = {
        entry["objectId"]: entry
        for entry in object_bridge.list_rows(tmp_path, summarize=summarize)["runs"]
    }

    assert rows[planned.object_id]["configSummary"] == {"d": "2"}
    assert rows[planned.object_id]["scientificRunId"]
    assert rows[draft.object_id]["configSummary"] is None
    assert rows[draft.object_id]["scientificRunId"] is None
    assert seen == [2]
    assert object_bridge.row(planned)["configSummary"] is None


def test_delete_many_removes_every_saved_run_that_exists(tmp_path: Path) -> None:
    draft, _problem = object_bridge.save(_valid(N="many"), results=tmp_path)
    planned, _problem = object_bridge.save(_valid(), results=tmp_path)

    deleted = object_bridge.delete_many(
        [draft.object_id, planned.object_id, "obj-" + "0" * 32, draft.object_id],
        results=tmp_path,
    )

    assert deleted == 2
    assert object_bridge.list_rows(tmp_path)["runs"] == []


def test_delete_many_deletes_nothing_while_one_executes(tmp_path: Path) -> None:
    """An executing run refuses the whole selection, naming that run."""
    idle, _problem = object_bridge.save(_valid(), results=tmp_path)
    busy, _problem = object_bridge.save(_valid(), name="Busy", results=tmp_path)
    started = start_run_object(
        busy.object_id, expected_revision=busy.record.revision, results=tmp_path
    )
    assert started.lease is not None
    try:
        with pytest.raises(LifecycleError, match="Busy"):
            object_bridge.delete_many(
                [idle.object_id, busy.object_id], results=tmp_path
            )
        remaining = {
            entry["objectId"] for entry in object_bridge.list_rows(tmp_path)["runs"]
        }
    finally:
        started.lease.release()

    assert remaining == {idle.object_id, busy.object_id}


def test_only_an_executing_run_offers_kill(tmp_path: Path) -> None:
    """Kill is offered while a live owner executes the run, never before."""
    planned, _problem = object_bridge.save(_valid(), results=tmp_path)
    assert object_bridge.row(planned)["actions"]["kill"] is False

    started = start_run_object(
        planned.object_id, expected_revision=planned.record.revision, results=tmp_path
    )
    assert started.lease is not None
    try:
        [running] = object_bridge.list_rows(tmp_path)["runs"]
        assert running["state"] == "running"
        assert running["actions"]["kill"] is True
        assert running["actions"]["delete"] is False
    finally:
        started.lease.release()


def test_kill_records_a_run_whose_owner_is_gone_without_killing(
    tmp_path: Path,
) -> None:
    """A run that lost its owner is reconciled, then reported as is."""
    planned, _problem = object_bridge.save(_valid(), results=tmp_path)
    started = start_run_object(
        planned.object_id, expected_revision=planned.record.revision, results=tmp_path
    )
    assert started.lease is not None
    started.lease.release()

    assert object_bridge.kill(planned.object_id, results=tmp_path) == "interrupted"
    assert object_bridge.kill(planned.object_id, results=tmp_path) == "interrupted"


def test_cancel_abandons_a_planned_run_and_delete_removes_it(
    tmp_path: Path,
) -> None:
    saved, _problem = object_bridge.save(_valid(), results=tmp_path)

    assert object_bridge.cancel(saved.object_id, results=tmp_path) == "cancelled"
    object_bridge.delete(saved.object_id, results=tmp_path)

    assert object_bridge.list_rows(tmp_path)["runs"] == []


def test_prepare_start_refuses_a_draft(tmp_path: Path) -> None:
    saved, _problem = object_bridge.save(_valid(N="many"), results=tmp_path)

    with pytest.raises(LifecycleError, match="cannot start"):
        object_bridge.prepare_start(saved.object_id, results=tmp_path)


def test_prepare_start_accepts_a_batch(tmp_path: Path) -> None:
    saved, _problem = object_bridge.save(_valid(n_replicates="2"), results=tmp_path)

    _run_object, params = object_bridge.prepare_start(saved.object_id, results=tmp_path)

    assert params.n_replicates == 2


def test_prepare_start_accepts_the_vector_backend(tmp_path: Path) -> None:
    """The GUI's default dense backend is resumable, so it may start."""
    pytest.importorskip("numba")
    saved, _problem = object_bridge.save(
        _valid(engine_backend="generational-vector"), results=tmp_path
    )

    _run_object, params = object_bridge.prepare_start(saved.object_id, results=tmp_path)

    assert params.engine_backend == "generational-vector"


def test_prepare_start_returns_the_parameters_of_a_planned_run(
    tmp_path: Path,
) -> None:
    saved, _problem = object_bridge.save(_valid(), results=tmp_path)

    run_object, params = object_bridge.prepare_start(saved.object_id, results=tmp_path)

    assert run_object.object_id == saved.object_id
    assert params.gene_copies == 16


def test_clone_creates_a_new_planned_object_and_preserves_sidecar(
    tmp_path: Path,
) -> None:
    saved, _problem = object_bridge.save(_valid(name="Base Run"), results=tmp_path)

    cloned = object_bridge.clone(saved.object_id, results=tmp_path)

    assert cloned.object_id != saved.object_id
    assert cloned.record.source_object_id == saved.object_id
    assert cloned.record.state.value == "planned"

    loaded_values, problem = object_bridge.form_values(cloned)
    assert problem is None
    assert loaded_values is not None
    assert loaded_values["name"] == "Base Run"
    assert loaded_values["d"] == "2"


def test_clone_draft_object_creates_draft_and_preserves_typed_values(
    tmp_path: Path,
) -> None:
    draft, problem = object_bridge.save(_valid(N="many"), results=tmp_path)
    assert problem is not None

    cloned = object_bridge.clone(draft.object_id, results=tmp_path)

    assert cloned.object_id != draft.object_id
    assert cloned.record.source_object_id == draft.object_id
    assert cloned.record.state.value == "draft"

    loaded_values, reason = object_bridge.form_values(cloned)
    assert reason is None
    assert loaded_values is not None
    assert loaded_values["N"] == "many"


@pytest.mark.parametrize("changed", ["copy", "source", "deleted", "draft", "started"])
def test_identical_source_tracks_current_digest_and_state(
    tmp_path: Path, changed: str
) -> None:
    """Only planned copies with a readable, currently identical source warn."""
    source, _problem = object_bridge.save(_valid(), name="Original", results=tmp_path)
    copy = object_bridge.clone(source.object_id, results=tmp_path)

    def summarize(params: SimulationParams) -> dict[str, str]:
        """Give the source the same display information as an ordinary row."""
        return {"N": str(params.gene_copies)}

    assert object_bridge.row(source)["identicalToSource"] is None
    source_info = object_bridge.row(copy, summarize=summarize)["identicalToSource"]
    assert source_info == {
        "objectId": source.object_id,
        "name": "Original",
        "configSummary": {"N": "16"},
        "state": "planned",
        "hasCheckpoint": False,
    }
    assert object_bridge.identical_source(copy, values=_valid()) is not None
    assert object_bridge.identical_source(copy, values=_valid(seed="99")) is None
    assert object_bridge.identical_source(copy, values=_valid(N="invalid")) is None
    lease = None
    try:
        if changed in ("copy", "draft"):
            copy, _problem = object_bridge.save(
                _valid(N="invalid" if changed == "draft" else "20"),
                object_id=copy.object_id,
                results=tmp_path,
            )
        elif changed == "source":
            object_bridge.save(
                _valid(seed="99"), object_id=source.object_id, results=tmp_path
            )
        elif changed == "deleted":
            object_bridge.delete_many([source.object_id], results=tmp_path)
        else:
            started = start_run_object(
                copy.object_id, expected_revision=copy.record.revision, results=tmp_path
            )
            lease = started.lease
            copy = read_run_object(copy.object_id, results=tmp_path)
        assert object_bridge.row(copy)["identicalToSource"] is None
    finally:
        if lease is not None:
            lease.release()


@pytest.mark.parametrize(
    "state", [RunState.CANCELLED, RunState.FAILED, RunState.PAUSED]
)
def test_identical_source_reports_stopped_checkpoint_progress(
    tmp_path: Path, state: RunState
) -> None:
    """The warning can distinguish an original whose progress can be retained."""
    source, _problem = object_bridge.save(_valid(), results=tmp_path)
    write_run_record(
        source.directory,
        replace(
            source.record,
            state=state,
            committed_generation=7,
            latest_checkpoint=CheckpointReference("checkpoint-7", "a" * 64, 7),
        ),
    )
    copy = object_bridge.clone(source.object_id, results=tmp_path)
    info = object_bridge.row(copy)["identicalToSource"]
    assert info["hasCheckpoint"] is True
    assert info["state"] == state.value


@pytest.mark.parametrize("checkpoint", ["valid", "corrupt", "missing", "absent"])
def test_cancelled_row_offers_resurrect_only_for_a_verified_checkpoint(
    tmp_path: Path, checkpoint: str
) -> None:
    """Rows and admission reject unavailable checkpoints without a new attempt."""
    saved, _problem = object_bridge.save(_valid(seed="123"), results=tmp_path)
    if checkpoint == "absent":
        object_bridge.cancel(saved.object_id, results=tmp_path)
        cancelled = read_run_object(saved.object_id, results=tmp_path)
    else:

        def cancel(session: Session) -> None:
            """Request cancellation between checkpoints, at generation five."""
            if session.generation == 5:
                current = read_run_object(saved.object_id, results=tmp_path)
                request_cancel(
                    saved.object_id,
                    expected_revision=current.record.revision,
                    results=tmp_path,
                )

        cancelled = execute_run_object(
            saved.object_id,
            results=tmp_path,
            cadence=CheckpointCadence(2, 1e9, 1),
            monotonic=lambda: 0.0,
            observer=cancel,
        )
        if checkpoint in {"corrupt", "missing"}:
            files = list((cancelled.directory / "checkpoints").rglob("*.json"))
            assert files
            for path in files:
                if checkpoint == "missing":
                    path.unlink()
                else:
                    path.write_text("corrupt checkpoint")
    entry = object_bridge.row(cancelled)
    assert entry["state"] == "cancelled"
    assert entry["actions"]["resurrect"] is (checkpoint == "valid")
    assert not entry["actions"]["resume"]
    assert entry["actions"]["clone"] and entry["actions"]["delete"]
    if checkpoint == "valid":
        prepared, _params = object_bridge.prepare_start(
            saved.object_id, results=tmp_path
        )
        assert prepared.record == cancelled.record
    else:
        assert "can_resurrect" in entry["reasons"]
        with pytest.raises(LifecycleError, match="checkpoint"):
            object_bridge.prepare_start(saved.object_id, results=tmp_path)
        assert (
            read_run_object(saved.object_id, results=tmp_path).record
            == cancelled.record
        )
