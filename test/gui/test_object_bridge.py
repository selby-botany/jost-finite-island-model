"""Tests for `fim.gui.object_bridge`: the Saved runs panel's logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fim.examples import classes
from fim.execution import UnsupportedSessionError
from fim.gui import object_bridge
from fim.gui.config_form import starter_form_values
from fim.persistence.run_lifecycle import LifecycleError, RunState
from fim.persistence.run_objects import read_run_object

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
    assert rows[draft.object_id]["issues"]
    assert rows[planned.object_id]["name"] == "Ok"
    assert rows[planned.object_id]["actions"]["start"] is True
    assert rows[planned.object_id]["actions"]["open"] is False


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


def test_prepare_start_refuses_a_batch(tmp_path: Path) -> None:
    saved, _problem = object_bridge.save(_valid(n_replicates="2"), results=tmp_path)

    with pytest.raises(UnsupportedSessionError):
        object_bridge.prepare_start(saved.object_id, results=tmp_path)


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
