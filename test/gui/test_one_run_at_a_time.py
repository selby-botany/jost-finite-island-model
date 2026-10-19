"""One run at a time: a second start is refused while a run is in flight.

`Api.start_run` used to refuse only while a sweep ran, and the page kept
"Run simulation" enabled during a run, so a second click -- or the Run
menu, or Configure's own Run button -- started a second plain run. The
second run took over the first's cancel event, worker and comparison
target, and the first run's drain thread then cleared `_run_in_flight`
while the second still ran, so closing the window could report nothing
running. Both halves of the fix are pinned here: Python claims the one
run slot under a lock (`Api._claim_run`), shared by `start_run` and
`start_run_object`, and the page asks `Api.can_start_run` before it
changes anything on screen (`startRunExclusively`).

No run here simulates anything: `fim.gui.runner.start_run` is replaced
by `_HeldRuns.start_run`, whose run waits until the test releases it and
then reports a cancellation. A run therefore stays in flight for exactly
as long as a test needs, whatever the machine, and its drain thread
pushes nothing to the page until it is released.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import poll_or_fail, wait_or_fail

from fim.gui import app as app_module
from fim.gui import runner
from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage
from fim.model.params import SimulationParams

from .test_sweep_api import (  # noqa: F401
    _IDLE_POLL_SECONDS,
    _FakeWindow,
    _form,
    api,
    results,
    window,
)
from .test_sweep_screen import Poll, _drive

_IN_PROGRESS = "a run is in progress; wait for it to finish or cancel it first"
_SWEEP_RUNNING = "a sweep is running; wait for it or cancel it first"

# True once the page has finished deciding its latest run start
# (`startRunExclusively`), whether it started a run or refused one.
_START_SETTLED = "window.__fimRunStartSettled === true"

_RUN_CARD_STATE = """({
    view: window.fim.getRunViewState(),
    screenRunHidden: document.getElementById('screen-run').hidden,
    banner: document.getElementById('run-banner').textContent,
    cancelDisabled: document.getElementById('cancel-run-button').disabled
})"""


class _HeldRuns:
    """Stands in for `fim.gui.runner.start_run`: each run waits to be released.

    A released run reports `("cancelled", 0)`, the one terminal message
    that needs no result, so its drain thread pushes `fim.onRunCancelled`
    and then releases the run claim (`Api._drain_then_release`).
    """

    def __init__(self) -> None:
        self.started: list[Path] = []
        self._releases: list[threading.Event] = []

    def start_run(
        self,
        params: SimulationParams,
        output_directory: Path,
        message_queue: queue.Queue[RunMessage],
        cancel_event: threading.Event,
        **_options: Any,
    ) -> threading.Thread:
        """Start a worker thread that waits for `release_all`.

        Args:
            params: Unused; the run never simulates.
            output_directory: Recorded, so a test can count starts.
            message_queue: Where the run's terminal message goes.
            cancel_event: Unused; only the test ends a held run.
            **_options: The real runner's keyword options, unused.

        Returns:
            The started, not yet released, worker thread.
        """
        del params, cancel_event
        release = threading.Event()
        self._releases.append(release)
        self.started.append(output_directory)

        def work() -> None:
            release.wait()
            message_queue.put(("cancelled", 0))

        worker = threading.Thread(target=work, name="held-run", daemon=True)
        worker.start()
        return worker

    def release_all(self) -> None:
        """Let every held run report its cancellation."""
        for release in self._releases:
            release.set()


@pytest.fixture
def held_runs(monkeypatch: pytest.MonkeyPatch) -> Iterator[_HeldRuns]:
    """Replace the scalar runner with held runs, and release them all at the end."""
    held = _HeldRuns()
    monkeypatch.setattr(runner, "start_run", held.start_run)
    try:
        yield held
    finally:
        held.release_all()


def _wait_until_no_run(instance: Api) -> None:
    """Wait for the drain thread to release the run claim.

    `_drain_then_release` releases it after its terminal push, so this
    returns once the run has ended however it ended; the completion-signal
    backstop fails a run that never ends.
    """
    poll_or_fail(
        lambda: instance._run_in_flight,
        lambda in_flight: not in_flight,
        "run claim release (_run_in_flight)",
        interval=_IDLE_POLL_SECONDS,
    )


def test_a_second_run_is_refused_while_the_first_is_in_flight(
    api: Api,  # noqa: F811
    window: _FakeWindow,  # noqa: F811
    held_runs: _HeldRuns,
) -> None:
    """A second start is refused until the first run ends, then allowed again."""
    del window
    first = api.start_run(_form())
    assert first["ok"] is True, first.get("message")

    second = api.start_run(_form())

    assert second == {"ok": False, "message": _IN_PROGRESS}
    assert api.can_start_run() == second
    assert len(held_runs.started) == 1

    held_runs.release_all()
    _wait_until_no_run(api)
    assert api.can_start_run() == {"ok": True}
    third = api.start_run(_form())
    assert third["ok"] is True, third.get("message")
    assert len(held_runs.started) == 2
    held_runs.release_all()
    _wait_until_no_run(api)


def test_a_refused_start_leaves_the_running_saved_run_alone(
    api: Api,  # noqa: F811
) -> None:
    """Refusing a plain run must not release the claim the saved run holds."""
    api._run_in_flight = True
    api._active_object_id = "saved-run"

    refused = api.start_run(_form())

    assert refused == {"ok": False, "message": _IN_PROGRESS}
    assert (api._run_in_flight, api._active_object_id) == (True, "saved-run")


def test_a_saved_run_cannot_start_while_a_run_is_in_flight(
    api: Api,  # noqa: F811
) -> None:
    """`start_run_object` gives the same refusal, before reading the object."""
    api._run_in_flight = True

    refused = api.start_run_object("never-read")

    assert refused == {"ok": False, "message": _IN_PROGRESS}
    assert api._run_in_flight is True


def test_a_sweep_in_flight_is_named_by_every_run_start(
    api: Api,  # noqa: F811
) -> None:
    """The page's check and both run starts agree while a sweep runs."""
    api._sweep_cancel_event = threading.Event()

    assert api.can_start_run() == {"ok": False, "message": _SWEEP_RUNNING}
    assert api.start_run(_form()) == {"ok": False, "message": _SWEEP_RUNNING}
    assert api.start_run_object("never-read") == {
        "ok": False,
        "message": _SWEEP_RUNNING,
    }
    assert api._run_in_flight is False


@pytest.mark.parametrize(
    "outcome",
    [
        {"ok": True, "reused": True, "isBatch": False, "directory": "existing"},
        {"ok": False, "message": "the output directory cannot be allocated"},
    ],
    ids=["reused", "refused"],
)
def test_a_start_that_starts_nothing_releases_the_claim(
    api: Api,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    outcome: dict[str, Any],
) -> None:
    """Reusing a computed run, or failing to start one, leaves no run in flight."""

    def started_nothing(self: Api, *_arguments: Any) -> dict[str, Any]:
        """Return `outcome`, as `start_run` would without starting a worker."""
        del self
        return outcome

    monkeypatch.setattr(Api, "_start_claimed_run", started_nothing)

    assert api.start_run(_form()) == outcome
    assert api.can_start_run() == {"ok": True}


def test_an_invalid_form_releases_the_claim(api: Api) -> None:  # noqa: F811
    """A form that does not validate starts nothing and leaves the slot free."""
    refused = api.start_run({**_form(), "N": "not a number"})

    assert refused["ok"] is False
    assert api.can_start_run() == {"ok": True}


def test_an_error_while_starting_releases_the_claim(
    api: Api,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception cannot leave the slot claimed for good."""

    def fail(self: Api, *_arguments: Any) -> dict[str, Any]:
        """Raise as an unexpected failure partway through a start would."""
        del self
        raise RuntimeError("starting failed")

    monkeypatch.setattr(Api, "_start_claimed_run", fail)
    monkeypatch.setattr(Api, "_start_claimed_run_object", fail)

    with pytest.raises(RuntimeError, match="starting failed"):
        api.start_run(_form())
    with pytest.raises(RuntimeError, match="starting failed"):
        api.start_run_object("never-read")
    assert api.can_start_run() == {"ok": True}
    assert api._active_object_id is None


def test_a_saved_run_start_that_fails_releases_the_claim(
    api: Api,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no window to report to, a saved run starts nothing and frees the slot."""
    monkeypatch.setattr(app_module, "_active_window", lambda: None)

    refused = api.start_run_object("never-read")

    assert refused == {"ok": False, "message": "no active window"}
    assert api.can_start_run() == {"ok": True}


def test_the_check_and_the_claim_happen_under_one_lock(
    api: Api,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two starts that arrive together cannot both see a free slot.

    Timing two real threads cannot prove this deterministically, so the
    test checks the property that makes it true: every check made while
    claiming runs with the claim lock held, and the claim is set before
    that lock is released (the second claim already sees it).
    """
    locked_during_check: list[bool] = []
    check = Api._run_refusal

    def observed(self: Api) -> str | None:
        """Record whether the claim lock is held, then check as before."""
        locked_during_check.append(self._run_claim_lock.locked())
        return check(self)

    monkeypatch.setattr(Api, "_run_refusal", observed)

    assert api._claim_run() is None
    assert api._claim_run() == _IN_PROGRESS
    assert locked_during_check == [True, True]


@pytest.mark.gui
def test_run_clicked_during_a_run_says_so_and_keeps_the_running_view(
    fast_scalar_run_settings: Path,
    held_runs: _HeldRuns,
) -> None:
    """Run and the Run menu, clicked during a run, are refused on the page.

    The refusal comes before anything on screen changes: the run view
    stays in `running`, Cancel stays enabled, and no second run starts.
    Once the first run ends, Run starts a new one. Page reads are safe
    while a run is held: a held run pushes nothing until it is released,
    so the page is never polled while a drain thread is pushing to it.
    """
    del fast_scalar_run_settings
    run_starts = [threading.Event(), threading.Event()]
    cancellations = [threading.Event(), threading.Event()]

    def on_run_started() -> None:
        run_starts[len(held_runs.started) - 1].set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] == "cancelled":
            next(event for event in cancellations if not event.is_set()).set()

    instance = Api(on_run_started=on_run_started, on_message=on_message)
    page = create_window(api=instance, hidden=True)

    def steps(poll_until: Poll) -> dict[str, Any]:
        page.evaluate_js("document.getElementById('run-button').click();")
        wait_or_fail(run_starts[0], "first run start (on_run_started)")
        poll_until(_START_SETTLED, lambda settled: settled is True)

        page.evaluate_js("document.getElementById('run-button').click();")
        poll_until(_START_SETTLED, lambda settled: settled is True)
        after_click = page.evaluate_js(_RUN_CARD_STATE)
        page.evaluate_js("window.fim.menu.runSimulation();")
        poll_until(_START_SETTLED, lambda settled: settled is True)
        after_menu = page.evaluate_js(_RUN_CARD_STATE)
        starts_while_running = len(held_runs.started)

        held_runs.release_all()
        wait_or_fail(cancellations[0], "first run cancelled")
        _wait_until_no_run(instance)
        page.evaluate_js("document.getElementById('run-button').click();")
        wait_or_fail(run_starts[1], "second run start (on_run_started)")
        poll_until(_START_SETTLED, lambda settled: settled is True)
        held_runs.release_all()
        wait_or_fail(cancellations[1], "second run cancelled")
        _wait_until_no_run(instance)
        return {
            "afterClick": after_click,
            "afterMenu": after_menu,
            "startsWhileRunning": starts_while_running,
        }

    seen = _drive(page, steps)

    for state in (seen["afterClick"], seen["afterMenu"]):
        assert state["view"] == "running"
        assert state["screenRunHidden"] is False
        assert state["banner"] == _IN_PROGRESS
        assert state["cancelDisabled"] is False
    assert seen["startsWhileRunning"] == 1
    assert len(held_runs.started) == 2
