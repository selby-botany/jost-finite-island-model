"""Headless functional tests for the first-launch welcome panel (botanist
GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §10).

Real DOM-driven proof that `webui/screens/welcome.js` actually shows
`<dialog id="modal-welcome">` on a genuine first launch, leaves it hidden
once already dismissed, wires both of its own buttons correctly, and
calls `Api.dismiss_welcome()` through the dialog's native `close` event
-- `test/gui/test_preferences.py`/`test_app_api.py` already prove the
`GuiPreferences.welcome_dismissed`/`Api.get_welcome_dismissed`/`Api.
dismiss_welcome` plumbing itself is correct as plain Python calls; these
tests prove the page's own JavaScript actually shows and wires the panel
at the right moments, which no Python-only test can check.

Every test here that needs the panel to actually show builds its own
window rather than using the `window`/`drive` fixtures as-is:
`test/gui/conftest.py`'s own `_isolate_gui_preferences` autouse fixture
pre-seeds `welcome_dismissed=True` at the redirected preferences path
specifically so the panel stays out of every *other* test's way, so
showing it here means first overwriting that same file with `welcome_
dismissed=False` -- the same explicit-override pattern already used for
`dark_mode_override`/`significant_digits` in `test_app_api.py`. Every
test that needs this requests `_isolate_gui_preferences` directly, for
its own return value (the resolved path) -- that fixture's own docstring
explains why this goes through it rather than `fim.gui.app.preferences_
file_path` reflectively.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import create_window
from fim.gui.preferences import GuiPreferences, save_preferences

from .conftest import poll_page, wait_for_run_view_ready

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def _build_window_with_welcome_not_dismissed(preferences_path: Path) -> webview.Window:
    """A fresh window whose own preferences file genuinely has not dismissed it.

    Overwrites the *same* path `_isolate_gui_preferences` already
    redirected `fim.gui.app.preferences_file_path` to -- `create_window`
    itself reads that path (via its own default `Api()`) at the point
    it is called, not lazily, so this only works because the overwrite
    happens before this function calls it.
    """
    save_preferences(preferences_path, GuiPreferences(welcome_dismissed=False))
    return create_window(hidden=True)


def test_welcome_panel_shows_on_a_genuine_first_launch(
    _isolate_gui_preferences: Path, drive: Callable[..., Any]
) -> None:
    """A launch that has never dismissed the panel shows it once ready."""
    own_window = _build_window_with_welcome_not_dismissed(_isolate_gui_preferences)

    settled = drive(
        own_window,
        ready=_INPUT_SCREEN_READY,
        trigger="null",
        read="document.getElementById('modal-welcome').open",
        is_ready=lambda value: value is not None,
    )

    assert settled is True


def test_welcome_panel_does_not_show_once_already_dismissed(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The ambient, already-dismissed default (every other test's own baseline)."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="null",
        read="document.getElementById('modal-welcome').open",
        is_ready=lambda value: value is not None,
    )

    assert settled is False


def test_examples_button_opens_the_examples_dialog_and_dismisses_welcome(
    _isolate_gui_preferences: Path, drive: Callable[..., Any]
) -> None:
    """ "Examples…" hands off to the Examples dialog, closing itself.

    `welcomeDialog.close()` fires synchronously, from inside the button's
    own click handler, before `fim.showExamplesDialog`'s own `await
    list_examples()` resolves and opens `modal-examples` -- `is_ready`
    below waits for *both* dialogs to reach their settled state (and for
    the dialog's own ready flag), not just the welcome panel's own
    closing half of this handoff, so this cannot pass on a lucky read
    caught between the two.
    """
    own_window = _build_window_with_welcome_not_dismissed(_isolate_gui_preferences)

    settled = drive(
        own_window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('welcome-examples-button')"
            ".dispatchEvent(new Event('click'));"
        ),
        read=(
            "({"
            "welcomeOpen: document.getElementById('modal-welcome').open, "
            "examplesOpen: document.getElementById('modal-examples').open, "
            "examplesReady: window.__fimExamplesDialogReady === true, "
            "presetsOpen: document.getElementById('modal-presets').open"
            "})"
        ),
        is_ready=lambda value: value["welcomeOpen"] is False and value["examplesReady"],
    )

    assert settled == {
        "welcomeOpen": False,
        "examplesOpen": True,
        "examplesReady": True,
        "presetsOpen": False,
    }


def test_start_from_scratch_just_closes_the_panel(
    _isolate_gui_preferences: Path, drive: Callable[..., Any]
) -> None:
    """ "Start from scratch" closes the panel and opens nothing else."""
    own_window = _build_window_with_welcome_not_dismissed(_isolate_gui_preferences)

    settled = drive(
        own_window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('welcome-start-scratch-button')"
            ".dispatchEvent(new Event('click'));"
        ),
        read=(
            "({"
            "welcomeOpen: document.getElementById('modal-welcome').open, "
            "presetsOpen: document.getElementById('modal-presets').open"
            "})"
        ),
        is_ready=lambda value: value["welcomeOpen"] is False,
    )

    assert settled == {"welcomeOpen": False, "presetsOpen": False}


@pytest.mark.parametrize(
    "button_id", ["welcome-examples-button", "welcome-start-scratch-button"]
)
def test_dismissing_the_welcome_panel_persists_through_the_bridge(
    button_id: str, _isolate_gui_preferences: Path
) -> None:
    """Either button's own `close` event reaches `Api.dismiss_welcome()` for real.

    Driven manually, not via the `drive` fixture: `welcomeDialog`'s own
    `close` listener has no DOM-visible effect of its own to poll for --
    the dialog is already closed by the time `Api.dismiss_welcome()`
    resolves -- so this waits for that call's settle flag
    (`window.__fimWelcomeDismissSettled`) and then reads the bridge's own
    `get_welcome_dismissed()` back through a second `Api` call on the
    same window. It used to wait a fixed 50 ms instead, which let the
    read race the save it was meant to observe.
    """
    own_window = _build_window_with_welcome_not_dismissed(_isolate_gui_preferences)

    outcome: queue.Queue[bool | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(own_window)
            own_window.evaluate_js(
                f"document.getElementById({button_id!r})"
                ".dispatchEvent(new Event('click'));"
            )
            poll_page(
                own_window,
                "window.__fimWelcomeDismissSettled === true",
                what="dismiss_welcome() (window.__fimWelcomeDismissSettled)",
            )
            own_window.evaluate_js(
                "window.__fimWelcomeDismissedResult = null; "
                "(async () => { "
                "window.__fimWelcomeDismissedResult = "
                "await window.pywebview.api.get_welcome_dismissed(); "
                "})();"
            )
            outcome.put(
                poll_page(
                    own_window,
                    "window.__fimWelcomeDismissedResult",
                    lambda value: value is not None,
                    what="get_welcome_dismissed() bridge call",
                )
            )
        finally:
            own_window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10.0)

    assert settled is True
