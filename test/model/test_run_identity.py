"""What a run's ID covers: model keys and internal attributes, not labels.

Read-only examples design (2026-10-05), section 1. A run's ID is a hash
of `SimulationParams.to_dict()` (`fim.engine.deterministic_run_id`), so
anything kept out of `to_dict` is kept out of the ID. The label keys
`name`, `description`, and `class` are accepted by `from_mapping` and
dropped there. The internal attribute `_read_only` is kept, and written
by `to_dict` only when true, so it moves the ID only when set.
"""

from __future__ import annotations

import pytest

from fim.engine import deterministic_run_id
from fim.model.params import SimulationParams


def _config(**updates: object) -> dict[str, object]:
    """Return a small complete configuration, with `updates` applied."""
    config: dict[str, object] = {
        "N": 50,
        "d": 4,
        "m": 0.05,
        "mu": 0.0001,
        "seed": 20261005,
        "ploidy": "diploid",
    }
    config.update(updates)
    return config


def _run_id(config: dict[str, object]) -> str:
    """Return the run ID `config` would get."""
    return deterministic_run_id(SimulationParams.from_mapping(config))


@pytest.mark.parametrize(
    "labels",
    [
        {"name": "Ring of four"},
        {"description": "Four demes, one locus."},
        {"class": "getting-started"},
        {"name": "A", "description": "B", "class": "migration"},
        {"name": None, "description": None, "class": None},
    ],
    ids=["name", "description", "class", "all-three", "all-null"],
)
def test_labels_leave_the_run_id_unchanged(labels: dict[str, object]) -> None:
    """Adding any label, or all of them, keeps the run ID."""
    assert _run_id(_config(**labels)) == _run_id(_config())


def test_changing_only_a_label_leaves_the_run_id_unchanged() -> None:
    """Two configurations that differ only in their labels are the same run."""
    first = _config(name="First", description="One.", **{"class": "migration"})
    second = _config(name="Second", description="Two.", **{"class": "literature"})

    assert _run_id(first) == _run_id(second)


def test_labels_are_absent_from_to_dict() -> None:
    """Labels never reach the parameters a manifest records."""
    params = SimulationParams.from_mapping(
        _config(name="Ring", description="Four demes.", **{"class": "migration"})
    )

    serialized = params.to_dict()

    assert not {"name", "description", "class"} & set(serialized)
    assert params == SimulationParams.from_mapping(_config())


@pytest.mark.parametrize("key", ["name", "description", "class"])
@pytest.mark.parametrize("value", [3, ["a"], {"a": 1}, True])
def test_a_label_that_is_not_text_is_rejected(key: str, value: object) -> None:
    """`from_mapping` type-checks a label before dropping it."""
    with pytest.raises(ValueError, match=f"{key} must be text"):
        SimulationParams.from_mapping(_config(**{key: value}))


def test_a_model_key_still_changes_the_run_id() -> None:
    """Control: the ID is not blind, a real model change still moves it."""
    assert _run_id(_config(seed=20261006)) != _run_id(_config())


# `_config()`'s run ID. Pinned so that adding an internal attribute can
# never silently re-key every existing run. It last moved on 2026-10-09,
# deliberately, when `precision`, `stop_batch_early` and `confidence`
# replaced the three tolerance and confidence settings (the parameters a
# run ID hashes changed names); it was `run-9b48f125d7f8c355` before.
GOLDEN_RUN_ID = "run-0388e63993e51794"


def test_a_configuration_without_read_only_keeps_its_golden_run_id() -> None:
    """A plain configuration hashes to the ID it had before `_read_only`."""
    assert _run_id(_config()) == GOLDEN_RUN_ID


def test_read_only_false_is_the_same_run_as_no_read_only() -> None:
    """An explicit `_read_only: false` is the default, so the ID is unchanged."""
    params = SimulationParams.from_mapping(_config(_read_only=False))

    assert not params.read_only
    assert "_read_only" not in params.to_dict()
    assert deterministic_run_id(params) == GOLDEN_RUN_ID


def test_read_only_true_changes_the_run_id() -> None:
    """`_read_only: true` is part of the run, so the run ID moves."""
    params = SimulationParams.from_mapping(_config(_read_only=True))

    assert params.read_only
    assert params.to_dict()["_read_only"] is True
    assert deterministic_run_id(params) != GOLDEN_RUN_ID


def test_read_only_round_trips_through_to_dict() -> None:
    """`from_mapping(to_dict())` keeps `read_only`, as it keeps every field."""
    params = SimulationParams.from_mapping(_config(_read_only=True))

    assert SimulationParams.from_mapping(params.to_dict()) == params


def test_labels_do_not_move_a_read_only_run_id() -> None:
    """Labels stay out of the ID of a read-only run too."""
    plain = _config(_read_only=True)
    labeled = _config(_read_only=True, name="Example", **{"class": "migration"})

    assert _run_id(labeled) == _run_id(plain)


@pytest.mark.parametrize("key", ["_readonly", "_read_only_", "_seed", "_"])
def test_an_unknown_internal_key_is_rejected(key: str) -> None:
    """Only documented `_` keys are accepted; a typo is rejected by name."""
    with pytest.raises(ValueError, match=f"unknown configuration key\\(s\\): {key}"):
        SimulationParams.from_mapping(_config(**{key: True}))


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_read_only_must_be_a_boolean(value: object) -> None:
    """`_read_only` takes a real boolean, never a truthy stand-in."""
    with pytest.raises(ValueError, match="_read_only must be a boolean"):
        SimulationParams.from_mapping(_config(_read_only=value))


def test_read_only_constructed_directly_must_be_a_boolean() -> None:
    """`__post_init__` checks the field for direct construction too."""
    with pytest.raises(ValueError, match="read_only must be a boolean"):
        SimulationParams(
            gene_copies=10,
            m=0.1,
            mu=0.001,
            d=2,
            seed=1,
            read_only="yes",  # type: ignore[arg-type]
        )
