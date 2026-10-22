"""Closing really stops running work, and leaves no worker thread behind.

`test_close_guard.py` proves `stop_active_work` with stand-in workers. These
tests use the real ones: a run that cannot finish before the test ends is
started through the bridge, `stop_active_work` is called as `main()` calls it
after the window closes, and the run must have stopped, a saved run must be
paused at a checkpoint, and no non-daemon thread the run started may be
left alive (a live one is what holds the process open until the deadman).

The run is made unable to finish by a burn-in just under a very large cap
(`unreachable_batch_run_settings` explains why that is structural, not a bet
on speed). The tests wait for the first progress push, so the run is known to
be running, and then ask it to stop; nothing waits on a delay.
"""

from __future__ import annotations

import multiprocessing
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import COMPLETION_BACKSTOP_SECONDS, poll_or_fail

from fim import paths
from fim.examples import classes
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.gui.config_form import DEFAULT_RUN_SETTING_FIELD_NAMES, starter_form_values
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_objects import read_run_object

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)
_POLL_SECONDS = 0.05
_UNREACHABLE = {
    "n_replicates": "1",
    "max_generations": "100000000",
    "convergence_burn_in": "90000000",
    "engine_backend": "lineal",
}


class _FakeWindow:
    """Records `evaluate_js` pushes from the run's drain thread."""

    def __init__(self) -> None:
        self.pushed: list[str] = []

    def evaluate_js(self, script: str) -> Any:
        self.pushed.append(script)
        return None

    def calls(self, name: str) -> list[str]:
        prefix = f"fim.{name}("
        return [script for script in list(self.pushed) if script.startswith(prefix)]


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Isolate the results directory for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> _FakeWindow:
    """Provide a fake active window."""
    fake = _FakeWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: fake)
    return fake


@pytest.fixture
def api(tmp_path: Path, results: Path) -> Api:
    """An `Api` whose defaults make a run that cannot finish."""
    del results
    api = Api(preferences_path=tmp_path / "preferences.json")
    defaults = {
        key: value
        for key, value in starter_form_values().items()
        if key in api.get_default_run_settings()
    }
    defaults.update(_UNREACHABLE)
    assert api.set_default_run_settings(defaults)["ok"] is True
    api.set_default_ploidy("1")
    return api


def _form() -> dict[str, str]:
    """A small valid Configure form, without its run settings."""
    values = {
        key: value
        for key, value in starter_form_values(overrides={"ploidy": "1"}).items()
        if key not in DEFAULT_RUN_SETTING_FIELD_NAMES
    }
    values.update({"N": "16", "d": "2"})
    return values


def _non_daemon_threads() -> set[threading.Thread]:
    """The live non-daemon threads other than the main one."""
    return {
        thread
        for thread in threading.enumerate()
        if thread.is_alive()
        and not thread.daemon
        and thread is not threading.main_thread()
    }


def _wait_for_progress(window: _FakeWindow) -> None:
    """Wait until the run has pushed progress, so it is known to be running."""
    poll_or_fail(
        lambda: window.calls("onRunProgress"),
        bool,
        "the run's first progress push",
        interval=_POLL_SECONDS,
    )


def _wait_until_idle(api: Api) -> None:
    """Wait for the drain thread to release the run guard."""
    poll_or_fail(
        lambda: api._run_in_flight,
        lambda busy: not busy,
        "the run guard to be released",
        interval=_POLL_SECONDS,
    )


def test_closing_pauses_a_running_scalar_run_and_leaves_no_thread(
    api: Api, window: _FakeWindow
) -> None:
    before = _non_daemon_threads()
    started = api.start_run(_form())
    assert started["ok"] is True
    _wait_for_progress(window)
    job = api._jobs.active_job(started["jobId"])
    assert job is not None and job.child is not None
    child = job.child
    assert api.close_requirements()["savedRun"] is True

    api.stop_active_work(COMPLETION_BACKSTOP_SECONDS)
    _wait_until_idle(api)

    assert window.calls("onRunPaused")
    assert read_run_object(started["objectId"]).record.state is RunState.PAUSED
    assert not child.is_alive()
    assert _non_daemon_threads() - before == set()


def test_closing_pauses_a_running_saved_run_at_a_checkpoint(
    api: Api, window: _FakeWindow
) -> None:
    saved = api.save_run_object(_form())
    assert saved["state"] == "planned"
    before = _non_daemon_threads()
    assert api.start_run_object(saved["objectId"])["ok"] is True
    _wait_for_progress(window)
    assert api.close_requirements()["savedRun"] is True

    api.stop_active_work(COMPLETION_BACKSTOP_SECONDS)
    _wait_until_idle(api)

    assert window.calls("onRunPaused")
    assert read_run_object(saved["objectId"]).record.state is RunState.PAUSED
    assert _non_daemon_threads() - before == set()


def test_application_shutdown_does_not_join_the_saved_run_child(api: Api) -> None:
    """Application shutdown closes the lifeline and returns without a join."""
    saved = api.save_run_object(_form())
    assert saved["state"] == "planned"
    child = app_module.jobs.start_child_job(
        saved["objectId"],
        results=None,
        pairwise_max_demes=1,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
    )
    api._active_object_id = saved["objectId"]
    api._object_worker = child
    started_at = time.monotonic()
    try:
        api.stop_active_work(0.1, wait_for_saved_run=False)

        assert time.monotonic() - started_at < 2
        assert child.is_alive()
        assert child.lifeline is None or child.lifeline.closed
    finally:
        child.process.terminate()
        child.join(5)
        child.messages.close()


def test_closing_pauses_a_running_batch_and_leaves_no_worker_process(
    api: Api, window: _FakeWindow
) -> None:
    defaults = {**api.get_default_run_settings(), **_UNREACHABLE}
    defaults.update({"n_replicates": "2", "max_workers": "2"})
    assert api.set_default_run_settings(defaults)["ok"] is True
    before = _non_daemon_threads()
    started = api.start_run(_form())
    assert started["ok"] is True
    poll_or_fail(
        lambda: window.calls("onBatchProgress"),
        bool,
        "the batch's first progress push",
        interval=_POLL_SECONDS,
    )
    job = api._jobs.active_job(started["jobId"])
    assert job is not None and job.child is not None
    child = job.child

    api.stop_active_work(COMPLETION_BACKSTOP_SECONDS)
    _wait_until_idle(api)

    assert window.calls("onRunPaused")
    assert not child.is_alive()
    assert _non_daemon_threads() - before == set()
    # `ProcessPoolExecutor` workers are children of this process; a worker
    # abandoned by an early exit would still be listed.
    assert multiprocessing.active_children() == []
