"""Unit tests for `fim.gui.app.Api`'s window-free bridge methods.

No display, no Tk import, no `gui` marker: `get_starter_form`,
`validate_form`, and `get_default_max_workers` never touch
`webview.windows[0]` — they call straight into `fim.gui.config_form`/
`fim.gui.batch_runner`, so they are exercised here as plain Python calls,
far cheaper and more direct than driving them through a real window and
`evaluate_js` (design doc §6.2's unit layer, applied to the bridge
itself). `load_yaml`/`save_yaml` do need a real window (a real file
dialog) and are covered instead in `test/gui/test_app.py`, marked `gui`.
"""

from __future__ import annotations

import time as time_module
from pathlib import Path

import pytest

from fim import paths as paths_module
from fim.gui import app as app_module
from fim.gui.app import Api, format_statistic
from fim.gui.batch_runner import default_max_workers
from fim.gui.config_form import starter_form_values


def test_get_starter_form_matches_config_form_directly() -> None:
    """The bridge method adds no logic of its own beyond `starter_form_values`."""
    assert Api().get_starter_form() == starter_form_values()


def test_get_default_max_workers_matches_batch_runner_directly() -> None:
    """The Batch tab's default is `batch_runner.default_max_workers`, not invented."""
    assert Api().get_default_max_workers() == default_max_workers()


def test_validate_form_accepts_the_starter_values() -> None:
    """The starter form is valid on its own — no field left in a rejecting state."""
    result = Api().validate_form(starter_form_values())

    assert result == {"ok": True}


def test_validate_form_rejects_and_locates_an_invalid_population_field() -> None:
    """An invalid `N` is rejected, named, and routed to the Population tab."""
    values = dict(starter_form_values())
    values["N"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "N"
    assert result["tab"] == "population"
    assert "N must be an integer" in result["message"]


def test_validate_form_rejects_and_locates_an_invalid_migration_rate() -> None:
    """An invalid scalar `m` rate routes to Migration via the composite selector."""
    values = dict(starter_form_values())
    values["m_mode"] = "scalar"
    values["m_rate"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["tab"] == "migration"


def test_validate_form_rejects_an_invalid_choice_field() -> None:
    """A "choice"-kind field's own error is located exactly like an "int" field's."""
    values = dict(starter_form_values())
    values["deme_weighting"] = "not-a-real-choice"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "deme_weighting"
    assert result["tab"] == "population"
    assert "deme_weighting" in result["message"]


def test_format_statistic_matches_the_cli_own_format_optional() -> None:
    """`format_statistic` is a direct parallel of `cli._format_optional`."""
    assert format_statistic(None) == "undefined"
    assert format_statistic(0.123456789) == "0.123457"
    assert format_statistic(1.0) == "1"


def test_open_output_folder_calls_the_injected_opener(tmp_path: Path) -> None:
    """`open_output_folder` reaches the injected opener, never a real one.

    Mirrors the Tk-era `ResultsScreen`'s own `open_folder` injection
    point (`test_results_screen_open_folder_invokes_the_injected_
    opener`), carried into `Api.__init__` unchanged in spirit.
    """
    opened: list[Path] = []
    api = Api(open_folder=opened.append)

    api.open_output_folder(str(tmp_path))

    assert opened == [tmp_path]


def test_resolve_available_output_directory_returns_a_free_path_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No collision, no retry: the first candidate is returned as-is."""
    free = tmp_path / "run-free"
    monkeypatch.setattr(paths_module, "default_output_directory", lambda: free)
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == free
    assert sleeps == []


def test_resolve_available_output_directory_retries_past_a_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-second collision (`paths.default_output_directory`'s own
    real, timestamp-based naming — see `_START_RUN_COLLISION_*`'s own
    comment) is retried until a free path is returned, not surfaced as
    a failure on the very first attempt.

    Direct regression coverage for the real, repeatedly-reproduced
    failure this fixed: several of this project's own `gui`-marked
    tests, each starting a real run within the same wall-clock second,
    previously received `{"ok": False, "message": "output directory
    already exists"}` for what was, from each test's perspective, an
    entirely fresh run — see `test/gui/test_running_screen.py`'s own
    module docstring for the full investigation.
    """
    colliding = tmp_path / "run-colliding"
    colliding.mkdir()
    free = tmp_path / "run-free"
    candidates = iter([colliding, colliding, free])
    monkeypatch.setattr(
        paths_module, "default_output_directory", lambda: next(candidates)
    )
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == free
    assert sleeps == [app_module._START_RUN_COLLISION_RETRY_INTERVAL_SECONDS] * 2


def test_resolve_available_output_directory_gives_up_after_the_max_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collision that never clears is returned anyway once the wait budget expires.

    `Api.start_run` is the one that turns a still-colliding directory
    into a real `{"ok": False, ...}` (via `runner.start_run`'s own
    `FileExistsError`) — this function's own job ends at "stop
    retrying," not at deciding what a persistent collision means.
    """
    colliding = tmp_path / "run-colliding"
    colliding.mkdir()
    monkeypatch.setattr(paths_module, "default_output_directory", lambda: colliding)
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == colliding
    expected_retries = round(
        app_module._START_RUN_COLLISION_MAX_WAIT_SECONDS
        / app_module._START_RUN_COLLISION_RETRY_INTERVAL_SECONDS
    )
    assert len(sleeps) == expected_retries
