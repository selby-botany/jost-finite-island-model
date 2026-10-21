"""Tests for the close guard on `fim.gui.app.Api` (no real window).

Closing the window must not lose unsaved edits or leave running work behind.
`Api.on_window_closing` decides, `finish_close` carries out the answer, and
`stop_active_work` is the backstop after the window is gone. Everything here
is driven through plain calls and events: nothing waits on a clock for its
outcome, and a wait on a helper thread is bounded only by
`conftest.COMPLETION_BACKSTOP_SECONDS`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import COMPLETION_BACKSTOP_SECONDS

from fim.gui import app as app_module
from fim.gui import object_bridge
from fim.gui.app import Api, await_bridge_threads
from fim.persistence.run_lifecycle import LifecycleError


class _FakeWindow:
    """Answers the page-readiness probe and signals what the guard does."""

    def __init__(self, *, page_ready: bool = True) -> None:
        self.page_ready = page_ready
        self.scripts: list[str] = []
        self.asked = threading.Event()
        self.destroyed = threading.Event()

    def evaluate_js(self, script: str) -> Any:
        self.scripts.append(script)
        if "typeof window.fim.confirmClose" in script:
            return self.page_ready
        if "confirmClose()" in script:
            self.asked.set()
        return None

    def destroy(self) -> None:
        self.destroyed.set()


def _make_bridge_thread(release: threading.Event) -> threading.Thread:
    """Build a non-daemon thread shaped like pywebview's bridge delivery.

    The matcher (`in_flight_bridge_threads`) keys on the target's qualified
    name, so the target is nested exactly as in `webview.util`.
    """

    def js_bridge_call() -> Callable[[], None]:
        def _call() -> None:
            release.wait()

        return _call

    return threading.Thread(target=js_bridge_call())


@pytest.fixture
def api(tmp_path: Path) -> Api:
    """A bare `Api` with its own preferences file."""
    return Api(preferences_path=tmp_path / "preferences.json")


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> _FakeWindow:
    """Provide a fake active window."""
    fake = _FakeWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: fake)
    return fake


def _wait(event: threading.Event, what: str) -> None:
    """Fail, rather than hang, if a helper thread never signals `event`."""
    assert event.wait(COMPLETION_BACKSTOP_SECONDS), f"timed out waiting for {what}"


def test_a_quiet_close_goes_straight_through(api: Api, window: _FakeWindow) -> None:
    assert api.close_needs_confirmation() is False

    assert api.on_window_closing(window) is True

    assert api._close_gate.closing is True
    assert window.scripts == []


def test_unsaved_edits_veto_the_close_and_ask_the_page(
    api: Api, window: _FakeWindow
) -> None:
    api.report_unsaved_edits(True)

    assert api.on_window_closing(window) is False

    _wait(window.asked, "the page to be asked")
    assert api._close_gate.closing is False
    assert api.close_requirements() == {
        "ok": True,
        "unsavedEdits": True,
        "savedRun": False,
        "run": False,
        "sweep": False,
        "dirtyDocuments": [],
        "activeJob": None,
    }


def test_close_requirements_include_dirty_documents_and_the_active_job(
    api: Api,
) -> None:
    assert api.report_unsaved_edits(
        True,
        [
            {"tabId": "document-1", "label": "New configuration (draft)"},
            {"tabId": "document-2", "label": "Saved run (planned)"},
        ],
    ) == {"ok": True}
    api._run_in_flight = True
    api._job_id = "job-123"

    requirements = api.close_requirements()

    assert requirements["dirtyDocuments"] == [
        {"tabId": "document-1", "label": "New configuration (draft)"},
        {"tabId": "document-2", "label": "Saved run (planned)"},
    ]
    assert requirements["activeJob"] == {"kind": "run", "id": "job-123"}


@pytest.mark.parametrize(
    "dirty_documents",
    [
        "document-1",
        [{"tabId": "document-1"}],
        [{"tabId": "document-1", "label": "Draft"}] * 2,
    ],
)
def test_report_unsaved_edits_rejects_invalid_document_summaries(
    api: Api, dirty_documents: Any
) -> None:
    api.report_unsaved_edits(True, [{"tabId": "prior", "label": "Prior draft"}])

    result = api.report_unsaved_edits(True, dirty_documents)

    assert result["ok"] is False
    assert api.close_requirements()["dirtyDocuments"] == [
        {"tabId": "prior", "label": "Prior draft"}
    ]
    assert api.close_requirements()["unsavedEdits"] is True


def test_saving_the_edits_clears_the_question(api: Api, window: _FakeWindow) -> None:
    api.report_unsaved_edits(True)
    api.report_unsaved_edits(False)

    assert api.on_window_closing(window) is True


@pytest.mark.parametrize(
    ("attribute", "value", "key"),
    [
        ("_active_object_id", "abc", "savedRun"),
        ("_run_in_flight", True, "run"),
        ("_sweep_cancel_event", threading.Event(), "sweep"),
    ],
)
def test_running_work_vetoes_the_close(
    api: Api, window: _FakeWindow, attribute: str, value: Any, key: str
) -> None:
    setattr(api, attribute, value)

    assert api.on_window_closing(window) is False

    assert api.close_requirements()[key] is True
    _wait(window.asked, "the page to be asked")


def test_a_saved_run_is_not_also_reported_as_a_plain_run(api: Api) -> None:
    api._active_object_id = "abc"
    api._run_in_flight = True

    requirements = api.close_requirements()

    assert requirements["savedRun"] is True
    assert requirements["run"] is False


def test_a_page_that_cannot_ask_does_not_trap_the_window(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _FakeWindow(page_ready=False)
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    api.report_unsaved_edits(True)

    assert api.on_window_closing(window) is False

    _wait(window.destroyed, "the window to close anyway")
    assert window.asked.is_set() is False


def test_the_quit_menu_asks_the_way_the_close_button_does(
    api: Api, window: _FakeWindow
) -> None:
    api.report_unsaved_edits(True)

    api.request_close()

    _wait(window.asked, "the page to be asked")
    assert window.destroyed.is_set() is False


def test_the_quit_menu_closes_at_once_when_nothing_is_at_stake(
    api: Api, window: _FakeWindow
) -> None:
    api.request_close()

    assert window.destroyed.is_set() is True
    assert window.asked.is_set() is False


def test_finishing_the_close_pauses_a_saved_run_and_closes(
    api: Api, window: _FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused: list[str] = []
    monkeypatch.setattr(object_bridge, "pause", paused.append)
    api._active_object_id = "abc"

    assert api.finish_close() == {"ok": True}

    _wait(window.destroyed, "the window to close")
    assert paused == ["abc"]


def test_finishing_the_close_cancels_a_plain_run(api: Api, window: _FakeWindow) -> None:
    cancel = threading.Event()
    api._cancel_event = cancel
    api._run_in_flight = True

    api.finish_close()

    _wait(window.destroyed, "the window to close")
    assert cancel.is_set()


def test_finishing_the_close_cancels_a_sweep(api: Api, window: _FakeWindow) -> None:
    cancel = threading.Event()
    api._sweep_cancel_event = cancel

    api.finish_close()

    _wait(window.destroyed, "the window to close")
    assert cancel.is_set()


def test_a_pause_that_is_already_underway_is_not_an_error(
    api: Api, window: _FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(object_id: str) -> None:
        raise LifecycleError(f"{object_id} is already pausing")

    monkeypatch.setattr(object_bridge, "pause", refuse)
    api._active_object_id = "abc"

    assert api.finish_close() == {"ok": True}

    _wait(window.destroyed, "the window to close")


def test_closing_with_no_window_reports_it(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "_active_window", lambda: None)

    assert api.finish_close()["ok"] is False


def test_stopping_work_waits_for_each_worker(api: Api) -> None:
    cancel = threading.Event()
    sweep_cancel = threading.Event()
    api._cancel_event = cancel
    api._sweep_cancel_event = sweep_cancel
    api._run_in_flight = True
    # Each worker finishes only when asked, like the real ones.
    api._run_worker = threading.Thread(target=cancel.wait)
    api._sweep_thread = threading.Thread(target=sweep_cancel.wait)
    api._run_worker.start()
    api._sweep_thread.start()

    api.stop_active_work(COMPLETION_BACKSTOP_SECONDS)

    assert not api._run_worker.is_alive()
    assert not api._sweep_thread.is_alive()


def test_stopping_work_names_a_worker_that_misses_the_bound(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    release = threading.Event()  # never asked to stop by anything but the test
    api._run_worker = threading.Thread(target=release.wait)
    api._run_worker.start()
    try:
        with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
            api.stop_active_work(0.0)
        assert "run did not stop before exit" in caplog.text
    finally:
        release.set()
        api._run_worker.join(COMPLETION_BACKSTOP_SECONDS)


def test_a_stop_and_a_settle_fit_inside_the_deadman() -> None:
    # Closing's two bounded waits are spent in sequence, both before the
    # deadman fires, so together they must leave it room to spare.
    spent = (
        app_module._STOP_ON_CLOSE_SECONDS + app_module._BRIDGE_SETTLE_TIMEOUT_SECONDS
    )
    assert spent < app_module._SHUTDOWN_DEADMAN_SECONDS


def test_bridge_calls_are_declined_once_the_window_is_closing(api: Api) -> None:
    assert api.list_run_objects() is not None

    api._close_gate.closing = True

    assert api.list_run_objects() is None


def test_a_bridge_call_is_listed_while_it_runs(api: Api) -> None:
    seen: dict[int, tuple[str, float]] = {}

    @app_module._log_bridge_call
    def probe(self: Api) -> None:
        del self
        seen.update(app_module._IN_FLIGHT_CALLS)

    probe(api)

    assert [name for name, _ in seen.values()] == ["probe"]
    assert threading.get_ident() not in app_module._IN_FLIGHT_CALLS


def test_a_settle_that_gives_up_names_the_call_it_waited_for(
    caplog: pytest.LogCaptureFixture,
) -> None:
    release = threading.Event()
    blocker = _make_bridge_thread(release)
    blocker.start()
    assert blocker.ident is not None
    with app_module._IN_FLIGHT_LOCK:
        app_module._IN_FLIGHT_CALLS[blocker.ident] = ("start_run", 0.0)
    try:
        with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
            settled = await_bridge_threads(timeout=0.2)
        assert settled is False
        assert "1 bridge call(s) still in flight" in caplog.text
        assert "start_run" in caplog.text
    finally:
        release.set()
        blocker.join(COMPLETION_BACKSTOP_SECONDS)
        with app_module._IN_FLIGHT_LOCK:
            app_module._IN_FLIGHT_CALLS.pop(blocker.ident, None)


def test_a_settle_with_nothing_in_flight_says_so() -> None:
    assert await_bridge_threads(timeout=0.2) is True


def test_main_stops_running_work_after_the_window_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` asks running work to stop once `webview.start` returns.

    The stubs keep `main` from building a real window or arming a real
    process-killing deadman (`test_app_api._stub_main_past_logging_config`
    has the full reasoning). The order matters: stopping before the window
    closed would pull work out from under a user who chose "Keep working".
    """
    events: list[str] = []
    menus: list[Any] = []
    monkeypatch.setattr(app_module, "create_window", lambda **_kwargs: object())

    def record_menu(_window: object, quit_action: Any = None) -> list[Any]:
        menus.append(quit_action)
        return []

    monkeypatch.setattr(app_module, "_build_menu", record_menu)
    monkeypatch.setattr(app_module, "_start_shutdown_deadman", lambda _timeout: None)
    monkeypatch.setattr(
        app_module, "_install_shutdown_signal_handlers", lambda _api, _timeout: None
    )
    monkeypatch.setattr(webview, "start", lambda **_kwargs: events.append("closed"))
    monkeypatch.setattr(
        Api,
        "stop_active_work",
        lambda _self, timeout, *, wait_for_saved_run: events.append(
            f"stop:{timeout:g}:{wait_for_saved_run}"
        ),
    )

    assert app_module.main([]) == 0

    assert events == [
        "closed",
        f"stop:{app_module._STOP_ON_CLOSE_SECONDS:g}:False",
    ]
    # The Quit menu item asks the way the close button does.
    assert [action.__name__ for action in menus] == ["request_close"]
