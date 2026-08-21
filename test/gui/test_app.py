"""Headless functional tests for the Tk application shell.

Every test here constructs a real `tk.Tk` root (needs a display, hence the
`gui` marker — design doc §6.2/§6.4) and drives it synchronously; per the
determinism contract (design doc §6.1), none ever calls `mainloop()`.
`test_show_update_result_*` call `_show_update_result()` directly with a
hand-built message — the same "handler function tested directly, no real
thread involved" shape `fim.gui.screens.progress_screen.
_handle_message` is tested with — since it is pure dialog-rendering logic
with no threading of its own. `test_check_for_updates_*` exercise the
real background thread and `root.after` poll instead, since backgrounding
is exactly the behavior under test there; both replace `fim.update` and
`fim.gui.app.messagebox` with recording stubs so no test opens a real
dialog or makes a real network call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from tkinter import ttk

import pytest

from fim import __version__
from fim.gui.app import Application, _check_for_updates, _show_update_result

pytestmark = pytest.mark.gui


@pytest.fixture
def app() -> Iterator[Application]:
    """Build and tear down one real `Application` root per test."""
    application = Application()
    try:
        yield application
    finally:
        application.destroy()


def test_register_screen_grids_it_into_the_shared_container(
    app: Application,
) -> None:
    """A registered screen occupies the container's stacking cell."""
    screen = ttk.Frame(app)
    app.register_screen("only", screen)
    app.update_idletasks()

    assert screen.winfo_manager() == "grid"
    assert screen.grid_info()["row"] == 0
    assert screen.grid_info()["column"] == 0


def test_show_screen_raises_the_named_screen_above_its_sibling(
    app: Application,
) -> None:
    """`show_screen` calls `tkraise()` on exactly the requested screen.

    Tk exposes no public, headless-safe query for which stacked widget is
    currently on top (`winfo_ismapped()` is true for every gridded sibling
    regardless of stacking order), so this spies on `tkraise` itself
    rather than trying to observe its visual effect.
    """
    first = ttk.Frame(app)
    second = ttk.Frame(app)
    app.register_screen("first", first)
    app.register_screen("second", second)
    raised: list[str] = []
    first.tkraise = lambda *_args: raised.append("first")  # type: ignore[method-assign]
    second.tkraise = lambda *_args: raised.append("second")  # type: ignore[method-assign]

    app.show_screen("second")

    assert raised == ["second"]


def test_show_screen_rejects_an_unregistered_name(app: Application) -> None:
    """An unknown screen name is a programming error, not a silent no-op."""
    with pytest.raises(KeyError):
        app.show_screen("never-registered")


def test_show_update_result_reports_a_newer_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer tag than the running version shows its name and URL.

    Requirement G9/design §3.9: the identical `fim.update` calls
    `cli._command_update` uses, rendered as a dialog instead of stdout
    lines.
    """
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _show_update_result(
        ("done", ("v99.0.0", "https://example.invalid/releases/v99.0.0"))
    )

    assert len(shown) == 1
    title, message = shown[0]
    assert title == "Check for updates"
    assert "v99.0.0" in message
    assert "https://example.invalid/releases/v99.0.0" in message


def test_show_update_result_reports_the_current_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tag matching the running version reports "current", not a release."""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _show_update_result(("done", (f"v{__version__}", "https://example.invalid")))

    assert len(shown) == 1
    assert "is current" in shown[0][1]


def test_show_update_result_reports_a_newer_running_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running version newer than the latest tag is reported, not hidden."""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _show_update_result(("done", ("v0.0.1", "https://example.invalid")))

    assert len(shown) == 1
    assert "newer than the latest release" in shown[0][1]


def test_show_update_result_shows_a_failure_as_an_error_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed release check shows the error verbatim, never `showinfo`."""
    shown_errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showerror",
        lambda title, message: shown_errors.append((title, message)),
    )
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda *_args: pytest.fail("showinfo was called"),
    )

    _show_update_result(("error", "update check failed: timed out"))

    assert shown_errors == [("Check for updates", "update check failed: timed out")]


def test_check_for_updates_runs_in_the_background_without_blocking(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_check_for_updates` returns immediately; the network call runs off-thread.

    "Liveliness" matters for a GUI the way it never does for a blocking
    CLI command — proven here by injecting a `latest_release` that
    blocks on an event this test controls: if `_check_for_updates`
    waited for it synchronously, this call would take up to the full
    5-second wait below to return, not the sub-second bound asserted
    here. The menu entry is disabled the instant the call returns,
    before the background thread has had any chance to finish; once the
    real worker thread completes and the `root.after`-scheduled poll
    drains its result (driven here via `app.update()` in a loop, never
    `mainloop()` — design §6.1), the entry re-enables and the dialog
    appears.
    """
    release_event = threading.Event()

    def blocking_latest_release() -> tuple[str, str]:
        release_event.wait(timeout=5)
        return ("v1.1.0", "https://example.invalid")

    monkeypatch.setattr("fim.gui.app.update.latest_release", blocking_latest_release)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    started = time.monotonic()
    _check_for_updates(app, app._help_menu)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "blocked on the network call instead of backgrounding it"
    assert app._help_menu.entrycget(0, "state") == "disabled"

    release_event.set()
    deadline = time.monotonic() + 5
    while app._help_menu.entrycget(0, "state") != "normal":
        if time.monotonic() > deadline:
            pytest.fail("update check never re-enabled the menu entry")
        app.update()

    assert shown == [("Check for updates", "fim 1.1.0 is current")]


def test_check_for_updates_reaches_an_error_dialog_in_the_background(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background failure re-enables the menu and shows the error, not `showinfo`."""

    def fail() -> tuple[str, str]:
        raise RuntimeError("update check failed: timed out")

    monkeypatch.setattr("fim.gui.app.update.latest_release", fail)
    shown_errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showerror",
        lambda title, message: shown_errors.append((title, message)),
    )
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda *_args: pytest.fail("showinfo was called"),
    )

    _check_for_updates(app, app._help_menu)

    deadline = time.monotonic() + 5
    while app._help_menu.entrycget(0, "state") != "normal":
        if time.monotonic() > deadline:
            pytest.fail("update check never re-enabled the menu entry")
        app.update()

    assert shown_errors == [("Check for updates", "update check failed: timed out")]
