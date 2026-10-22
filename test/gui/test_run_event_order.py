"""Pin when a job's pushes land relative to the run claim being released.

A characterization suite, not a feature test: every assertion here already
holds on `dev` before the tabs work begins (`20261019-claude-opus-5-5-fim-
gui-tabs-design.md`, commit A1). Its job is to catch a regression the
later commits could introduce while they move these same mechanisms
behind a job registry (A4) and a single publisher thread (A3): a push
must never be delivered after the claim it belongs to has already been
released, and an annotation -- the reproducibility check a stale-version
run gets -- must always land after the terminal push it annotates and
before the final `finished` event.

Existing coverage this suite does not repeat: `test_one_run_at_a_time.py`
(a second start is refused while one run is in flight, and the claim is
released on every path that starts nothing); `test_close_guard.py`
(`close_requirements`' three booleans, read from the same fields);
`test_running_screen.py::test_correcting_the_run_kind_keeps_live_state_a_
tick_already_delivered` (progress that arrives before `start_run`'s own
reply must not be discarded). This file adds the one thing none of them
pins directly: *where in the push sequence* the claim is still held, by
reading `Api._run_in_flight` from inside a fake window's own
`evaluate_js`, synchronously, on the same thread that is about to release
it -- not by polling after the fact, which could never catch the claim
being released one push early.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import poll_or_fail

from fim.gui import app as app_module
from fim.gui.app import Api
from fim.gui.config_form import DEFAULT_RUN_SETTING_FIELD_NAMES, starter_form_values
from fim.persistence import groups

_IDLE_POLL_SECONDS = 0.05
_PROGRESS_POLL_SECONDS = 0.02

# A run short enough to finish inside the test, so no cancellation or
# pause is ever needed to end it.
_FAST_SETTINGS = {
    "n_replicates": "1",
    "max_generations": "10",
    "engine_backend": "lineal",
}

# A run that cannot reach its own stop condition before the test pauses or
# cancels it on purpose -- the same shape `test_close_guard_work.py`'s own
# `_UNREACHABLE` uses, for the identical reason.
_UNREACHABLE_SETTINGS = {
    "n_replicates": "1",
    "max_generations": "100000000",
    "convergence_burn_in": "90000000",
    "engine_backend": "lineal",
}


class _OrderWindow:
    """Records each push's name alongside whatever `read_claim` answers
    at the exact moment that push is delivered.

    `evaluate_js` runs on the same drain thread that will, immediately
    afterward for a terminal push, release the run claim in its own
    `finally` -- so a direct, synchronous read here, never a poll taken
    later, is the only way to know the claim's state *as of* each push.
    """

    def __init__(
        self,
        read_claim: Callable[[], bool],
        read_object_claim: Callable[[], bool] = lambda: False,
        read_active_jobs: Callable[[], int] = lambda: 0,
    ) -> None:
        self._read_claim = read_claim
        self._read_object_claim = read_object_claim
        self._read_active_jobs = read_active_jobs
        self.observations: list[tuple[str, bool, bool, str]] = []
        self.active_job_observations: list[tuple[str, int]] = []

    def evaluate_js(self, script: str) -> Any:
        name = script.split("(", 1)[0]
        self.observations.append(
            (name, self._read_claim(), self._read_object_claim(), script)
        )
        self.active_job_observations.append((script, self._read_active_jobs()))
        return None

    def names(self) -> list[str]:
        return [name for name, _, _, _ in self.observations]

    def claim_held_when(self, name: str) -> bool:
        """Return the claim state recorded at `name`'s first occurrence."""
        for observed_name, claim_held, _, _ in self.observations:
            if observed_name == name:
                return claim_held
        raise AssertionError(f"{name!r} was never pushed; saw {self.names()}")

    def active_jobs_when(self, event_type: str) -> int:
        """Return the registry size while an addressed event is delivered."""
        for script, count in self.active_job_observations:
            if not script.startswith("fim.onJobEvent("):
                continue
            event = json.loads(script[len("fim.onJobEvent(") : -1])
            if event["type"] == event_type:
                return count
        raise AssertionError(f"{event_type!r} was never pushed")

    def scripts(self) -> list[str]:
        """Return the scripts in delivery order."""
        return [script for _, _, _, script in self.observations]


def _form() -> dict[str, str]:
    """A small valid Configure form, without its run settings."""
    values = {
        key: value
        for key, value in starter_form_values(overrides={"ploidy": "1"}).items()
        if key not in DEFAULT_RUN_SETTING_FIELD_NAMES
    }
    values.update({"N": "16", "d": "2"})
    return values


def _api_with_settings(tmp_path: Path, settings: dict[str, str]) -> Api:
    """An `Api` whose run settings are `settings`, on an isolated preferences file."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    defaults = {
        key: value
        for key, value in starter_form_values().items()
        if key in api.get_default_run_settings()
    }
    defaults.update(settings)
    assert api.set_default_run_settings(defaults)["ok"] is True
    api.set_default_ploidy("1")
    return api


def _wait_until_idle(api: Api) -> None:
    """Wait for the drain thread to release the run claim, with no timing budget."""
    poll_or_fail(
        lambda: api._run_in_flight,
        lambda in_flight: not in_flight,
        "run drain thread (run claim release)",
        interval=_IDLE_POLL_SECONDS,
    )


def _wait_for_a_push(window: _OrderWindow, name: str) -> None:
    """Wait until `window` has recorded at least one push named `name`."""
    poll_or_fail(
        lambda: name in window.names(),
        bool,
        f"the first {name!r} push",
        interval=_PROGRESS_POLL_SECONDS,
    )


def test_a_stale_version_annotation_and_finished_follow_the_done_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The annotation push lands after "done", with the claim still held at both."""
    api = _api_with_settings(tmp_path, _FAST_SETTINGS)
    window = _OrderWindow(
        lambda: api._run_in_flight,
        lambda: api._active_object_id is not None,
        lambda: len(api._jobs.active_jobs()),
    )
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    study = groups.create_study("Runs")

    first = api.start_run(_form(), study.study_id)
    assert first["ok"] is True
    _wait_until_idle(api)
    assert "fim.onRunDone" in window.names()
    window.observations.clear()
    window.active_job_observations.clear()

    # The same configuration, under a different reported software
    # version: `start_run` recomputes it and compares the two, exactly
    # as `test_run_reuse.py`'s own stale-version tests already prove by
    # inspecting the finished `reproducibility.json` -- this test instead
    # watches the two pushes land, and in what order.
    monkeypatch.setattr(app_module, "fim_version", "9.9.9")
    second = api.start_run(_form(), study.study_id)
    assert second["ok"] is True
    assert "reused" not in second

    _wait_until_idle(api)

    names = window.names()
    assert "fim.onRunDone" in names
    event_observations = [
        (
            index,
            json.loads(script[len("fim.onJobEvent(") : -1]),
        )
        for index, (_, _, _, script) in enumerate(window.observations)
        if script.startswith("fim.onJobEvent(")
    ]
    events = [event for _, event in event_observations]
    assert [event["type"] for event in events][-3:] == [
        "terminal",
        "annotation",
        "finished",
    ]
    terminal_index = next(
        index for index, event in event_observations if event["type"] == "terminal"
    )
    finished_index = next(
        index for index, event in event_observations if event["type"] == "finished"
    )
    assert names.index("fim.onRunDone") < terminal_index
    assert window.claim_held_when("fim.onRunDone") is True
    assert window.observations[finished_index][1] is True
    assert window.observations[finished_index][2] is True
    assert window.active_jobs_when("finished") == 1
    assert api._run_in_flight is False
    assert api._active_object_id is None
    assert api._jobs.active_jobs() == ()
    assert api.get_job_snapshot(events[-1]["jobId"])["snapshot"]["type"] == "finished"


def test_a_saved_runs_pause_push_lands_before_the_claim_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pausing a saved run: the push lands while the claim is still held."""
    api = _api_with_settings(tmp_path, _UNREACHABLE_SETTINGS)
    window = _OrderWindow(lambda: api._run_in_flight)
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    saved = api.save_run_object(_form())
    assert saved["state"] == "planned"

    started = api.start_run_object(saved["objectId"])
    assert started["ok"] is True, started.get("message")
    _wait_for_a_push(window, "fim.onRunProgress")

    assert api.pause_run() == {"ok": True}
    _wait_until_idle(api)

    assert "fim.onRunPaused" in window.names()
    assert window.claim_held_when("fim.onRunPaused") is True
    assert api._run_in_flight is False


def test_a_saved_runs_cancel_push_lands_before_the_claim_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a saved run: the push lands while the claim is still held."""
    api = _api_with_settings(tmp_path, _UNREACHABLE_SETTINGS)
    window = _OrderWindow(lambda: api._run_in_flight)
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    saved = api.save_run_object(_form())
    assert saved["state"] == "planned"

    started = api.start_run_object(saved["objectId"])
    assert started["ok"] is True, started.get("message")
    _wait_for_a_push(window, "fim.onRunProgress")

    api.cancel_run()
    _wait_until_idle(api)

    assert "fim.onRunCancelled" in window.names()
    assert window.claim_held_when("fim.onRunCancelled") is True
    assert api._run_in_flight is False
