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
