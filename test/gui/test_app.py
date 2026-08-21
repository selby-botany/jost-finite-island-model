"""Headless functional tests for the Tk application shell.

Every test here constructs a real `tk.Tk` root (needs a display, hence the
`gui` marker — design doc §6.2/§6.4) and drives it synchronously; per the
determinism contract (design doc §6.1), none ever calls `mainloop()`.
`test_check_for_updates_*` call `_check_for_updates()` directly rather
than through the "Help" menu's own command binding — a real
`messagebox.showinfo`/`showerror` call would otherwise open a real,
test-blocking dialog, so both `fim.update` and `fim.gui.app.messagebox`
are always replaced with recording stubs; the menu wiring itself is
standard Tk API with nothing project-specific left to verify once the
command it calls is covered directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from tkinter import ttk

import pytest

from fim import __version__
from fim.gui.app import Application, _check_for_updates

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


def test_check_for_updates_reports_a_newer_release(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer tag than the running version shows its name and URL.

    Requirement G9/design §3.9: the identical `fim.update` calls
    `cli._command_update` uses, rendered as a dialog instead of stdout
    lines.
    """
    monkeypatch.setattr(
        "fim.gui.app.update.latest_release",
        lambda: ("v99.0.0", "https://example.invalid/releases/v99.0.0"),
    )
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _check_for_updates()

    assert len(shown) == 1
    title, message = shown[0]
    assert title == "Check for updates"
    assert "v99.0.0" in message
    assert "https://example.invalid/releases/v99.0.0" in message


def test_check_for_updates_reports_the_current_version(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tag matching the running version reports "current", not a release."""
    monkeypatch.setattr(
        "fim.gui.app.update.latest_release",
        lambda: (f"v{__version__}", "https://example.invalid"),
    )
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _check_for_updates()

    assert len(shown) == 1
    assert "is current" in shown[0][1]


def test_check_for_updates_reports_a_newer_running_version(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running version newer than the latest tag is reported, not hidden."""
    monkeypatch.setattr(
        "fim.gui.app.update.latest_release",
        lambda: ("v0.0.1", "https://example.invalid"),
    )
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fim.gui.app.messagebox.showinfo",
        lambda title, message: shown.append((title, message)),
    )

    _check_for_updates()

    assert len(shown) == 1
    assert "newer than the latest release" in shown[0][1]


def test_check_for_updates_shows_a_failure_as_an_error_dialog(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed release check shows the error verbatim, never `showinfo`."""

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

    _check_for_updates()

    assert shown_errors == [("Check for updates", "update check failed: timed out")]
