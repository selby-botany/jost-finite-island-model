"""Headless functional tests for the Tk application shell.

Every test here constructs a real `tk.Tk` root (needs a display, hence the
`gui` marker — design doc §6.2/§6.4) and drives it synchronously; per the
determinism contract (design doc §6.1), none ever calls `mainloop()`.
"""

from __future__ import annotations

from collections.abc import Iterator
from tkinter import ttk

import pytest

from fim.gui.app import Application

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
