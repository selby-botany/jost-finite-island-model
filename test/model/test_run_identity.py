"""What a run's ID covers: model keys yes, labels no.

Read-only examples design (2026-10-05), section 1. A run's ID is a hash
of `SimulationParams.to_dict()` (`fim.engine.deterministic_run_id`), so
anything kept out of `to_dict` is kept out of the ID. The label keys
`name`, `description`, and `class` are accepted by `from_mapping` and
dropped there.
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
