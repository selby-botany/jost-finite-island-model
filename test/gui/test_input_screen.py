"""Headless functional tests for Screen 1 (design doc §4.1, §6.4).

Real DOM-driven proof that `webui/screens/input.js` actually wires the
page correctly — `test/gui/test_app_api.py` already proves the bridge
methods themselves are correct as plain Python calls; these tests prove
the page's own JavaScript calls them at the right moments and updates
the right elements, which no Python-only test can check.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

from fim.gui.config_form import starter_form_values

pytestmark = pytest.mark.gui

# input.js's own `initializeInputScreen` sets this once every awaited
# bridge call, DOM population, and `wireEvents()` have all completed.
# Every test below that fires a synthetic DOM event passes this as
# `drive`'s `ready` argument, polled with plain (non-async) `evaluate_js`
# calls before `trigger` ever fires -- `conftest.py`'s own `drive_and_
# read` docstring records why an async, `setTimeout`-polling trigger
# hangs the driver thread indefinitely instead.
_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"


def test_input_screen_loads_starter_values(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The N field shows `starter_form_values()`'s value once the page initializes."""
    expected = starter_form_values()["N"]

    value = drive(
        window,
        trigger="null",
        read="document.getElementById('field-N').value",
        is_ready=lambda value: value == expected,
        poll_attempts=500,
    )

    assert value == expected


def test_input_screen_run_button_enabled_for_the_valid_starter_form(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The starter form validates on load, so "Run simulation" starts enabled.

    Polls `window.__fimInputScreenReady`, not the field's own value:
    `applyFormValues` (which sets the field) runs *before* `wireEvents`/
    `revalidate` (which resolves the button's `disabled` state) inside
    `initializeInputScreen`, so polling the field alone risks reading
    `disabled` before `revalidate` has ever run once.
    """
    expected_n = starter_form_values()["N"]

    settled = drive(
        window,
        trigger="null",
        read=(
            "window.__fimInputScreenReady ? ({"
            "n: document.getElementById('field-N').value, "
            "disabled: document.getElementById('run-button').disabled"
            "}) : null"
        ),
        is_ready=lambda value: value is not None,
        poll_attempts=500,
    )

    assert settled["n"] == expected_n
    assert settled["disabled"] is False


def test_input_screen_invalid_value_disables_the_run_button(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """An invalid value disables "Run simulation" and explains why."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-N').value = 'not-a-number'; "
            "document.getElementById('field-N')"
            ".dispatchEvent(new Event('input', {bubbles: true}));"
        ),
        read=(
            "({"
            "reason: document.getElementById('run-reason').textContent, "
            "disabled: document.getElementById('run-button').disabled"
            "})"
        ),
        is_ready=lambda value: (
            "N must be an integer"
            in (value.get("reason") or "" if value is not None else "")
        ),
        poll_attempts=500,
    )

    assert "N must be an integer" in settled["reason"]
    assert settled["disabled"] is True


def test_input_screen_switches_to_the_tab_with_an_invalid_field(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Clicking "Run simulation" with an invalid Migration field switches to that tab.

    Direct regression test for design §4.0 #2 ("every tab with an
    invalid field shows a small error dot... the disabled Run button
    always shows a one-line reason") — the tab-switch specifically, since
    `test_app_api.py` already proves the bridge's own `tab`/`field`
    values are correct. No `input` event needs dispatching first:
    `onRunClicked` calls `revalidate()` itself, which reads the field's
    *current* value straight off the live DOM via `FormData` — it does
    not depend on an `input` event ever having fired.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-m_rate').value = 'not-a-number'; "
            "document.getElementById('run-button').click();"
        ),
        read=(
            "({"
            "checked: document.getElementById('tab-migration').checked, "
            "dotHidden: document.getElementById('dot-migration').hidden"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("checked") is True,
        poll_attempts=500,
    )

    assert settled["checked"] is True
    assert settled["dotHidden"] is False


def test_menu_new_configuration_resets_an_edited_field(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.newConfiguration` resets the form to starter values.

    The one behavioral difference from the existing "New run" buttons
    (in-app help design §4.5): those only navigate back to Screen 1,
    leaving whatever was already in the form; the menu's own "New
    configuration" genuinely resets it, the same way a fresh app
    launch's own `initializeInputScreen` does — this test exists
    specifically to keep that distinction honest.

    The trigger wraps the call in `setTimeout(..., 0)`, matching
    `fim.gui.app._build_menu`'s own real dispatcher exactly (not a test
    convenience): calling an `async` `fim.menu.*` method directly as an
    `evaluate_js` expression deadlocks — confirmed live building this
    test — the same pywebview behavior `conftest.py`'s own `drive_and_
    read` docstring already documents for its `ready`-polling case.

    Polls for `window.__fimInputScreenReady` alongside the field's own
    value, not the field alone: `newConfiguration` cycles that flag
    false-then-true around the whole reset, and `field-N` already shows
    the new value while `resetInputForm` still has two more real bridge
    calls in flight (`get_default_max_workers`, `revalidate`) — reading
    only the field risked `drive`'s own window teardown racing those,
    the same class of failure `test_open_run_screen.py`'s own
    `window.__fimOpenRunRecentRunsLoaded` flag exists to prevent for
    `refreshRecentRuns`, confirmed as a real, reproducible
    `JavascriptException` (not merely theoretical) against a `results/`
    directory large enough for the bridge call it raced to take real time.
    """
    starter_n = starter_form_values()["N"]

    value = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-N').value = '999999'; "
            "setTimeout(() => { window.fim.menu.newConfiguration(); }, 0);"
        ),
        read=(
            "({"
            "fieldN: document.getElementById('field-N').value, "
            "ready: window.__fimInputScreenReady === true"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("ready") is True,
        poll_attempts=500,
    )

    assert value["fieldN"] == starter_n


def test_menu_configure_tab_switches_tabs_without_resetting_the_form(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.configureTab` (the native Configure menu, design §4.5) navigates only.

    The declutter this menu exists for (visualization-and-config-editors
    design): the on-canvas tab bar itself is now hidden (`app.css`'s
    `.tab-bar { display: none; }`), so this is the only way left to
    reach a tab other than an invalid-field auto-jump — checked here
    both for the tab switch itself (the hidden radio still flips, and
    `current-tab-heading` still shows the right name for a user with no
    visible tab bar to read instead) and for the one behavioral contract
    that distinguishes it from `newConfiguration`: an edited field
    survives the switch, unlike a real reset.

    The trigger wraps the call in `setTimeout(..., 0)`, matching
    `fim.gui.app._build_menu`'s own real dispatcher exactly — the same
    reason `test_menu_new_configuration_resets_an_edited_field` above
    does, and for the identical, confirmed-live deadlock this avoids.
    """
    value = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-N').value = '999999'; "
            "setTimeout(() => { window.fim.menu.configureTab('migration'); }, 0);"
        ),
        read=(
            "({"
            "fieldN: document.getElementById('field-N').value, "
            "migrationChecked: document.getElementById('tab-migration').checked, "
            "heading: document.getElementById('current-tab-heading').textContent"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("migrationChecked") is True
        ),
    )

    assert value["fieldN"] == "999999"
    assert value["heading"] == "Migration"


def test_batch_progress_display_never_regresses(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`onBatchProgress` shows a high-water mark, not the raw reported count.

    Real, reported behavior, not a hypothetical one: an adaptive
    `replicate_tolerance` stop is only decided after a whole concurrent
    worker wave completes, so a worker beyond the replicate that
    triggered it can still be mid-run -- and counted by a live poll --
    when the decision lands; once that now-orphaned replicate's
    directory is pruned, the very next poll legitimately reports fewer
    valid replicates than a moment before ("the generation tracking bar
    jumps around during the last ~20%"). Fired here as two synthetic
    `fim.onBatchProgress` calls (5 reporting, then 3) rather than
    orchestrating a real batch that actually overshoots and prunes --
    this is `screens/progress.js`'s own display logic under test, not
    the batch-execution timing that triggers it.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.resetBatchProgress();"
            "window.fim.onBatchProgress("
            "{replicateCount: 10, reportedReplicateCount: 5, panels: []});"
            "window.fim.onBatchProgress("
            "{replicateCount: 10, reportedReplicateCount: 3, panels: []});"
        ),
        read=(
            "({"
            "barValue: document.getElementById('progress-generation').value, "
            "labelText: "
            "document.getElementById('progress-generation-label').textContent"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("barValue") != 0,
    )

    assert settled["barValue"] == 5
    assert settled["labelText"] == "5 / 10 replicates reporting"
