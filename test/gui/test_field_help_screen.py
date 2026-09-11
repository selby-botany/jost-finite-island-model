"""Headless functional tests for the inline field tooltips (botanist GUI
design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.6) on
Configure, plus the Home/open-run screen's own two re-analysis controls
that share the same mechanism.

Real DOM-driven proof that `webui/field-help.js` actually shows and hides
the tooltip bubble on hover and on keyboard focus alike --
`test/gui/test_field_help.py`'s own static checks already prove every
field/group on either screen has a real `FIELD_HELP` entry; these tests
prove the page's own JavaScript actually shows it, which no static-
analysis test can check.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def test_hovering_a_field_label_shows_its_tooltip_and_leaving_hides_it(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A field's own `<label>` shows `FIELD_HELP[name]` on `mouseenter`, hides on leave.

    `text`/`matchesFieldHelp` are both read inside the one driven
    `read` expression, not via a second `window.evaluate_js` call after
    `drive` returns -- `drive_and_read`'s own teardown destroys the
    window as soon as it settles, so anything read afterward, on the
    already-destroyed window, is a bug in the test, not in the page
    (confirmed live: an earlier version of this file did exactly that
    and read back `None` for `window.FIM_FIELD_HELP.N`).
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.querySelector('label[for=\"field-N\"]')"
            ".dispatchEvent(new Event('mouseenter'));"
        ),
        read=(
            "({"
            "hidden: document.querySelector('.field-tooltip').hidden, "
            "matchesFieldHelp: document.querySelector('.field-tooltip')"
            ".textContent === window.FIM_FIELD_HELP.N"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("hidden") is False,
    )

    assert settled["hidden"] is False
    assert settled["matchesFieldHelp"] is True


def test_leaving_a_field_label_hides_its_tooltip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`mouseleave` hides the bubble the preceding `mouseenter` showed."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "(function(){"
            "var label = document.querySelector('label[for=\"field-N\"]');"
            "label.dispatchEvent(new Event('mouseenter'));"
            "label.dispatchEvent(new Event('mouseleave'));"
            "})();"
        ),
        read="document.querySelector('.field-tooltip').hidden",
        is_ready=lambda value: value is not None,
    )

    assert settled is True


def test_focusing_a_field_shows_its_tooltip_for_a_keyboard_only_user(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The field itself (not only its label) shows the tooltip on `focus`.

    Design §4.6: "hover/focus tooltip" -- a keyboard-only user tabs to
    the input, never hovers its label, so the trigger has to live on the
    field too, not only on the label text.

    Navigates to Configure first, unlike the `dispatchEvent`-driven
    tests above: `.focus()` is a real DOM API that respects visibility
    (a `hidden`-ancestor element cannot become the focused element at
    all, confirmed live -- an earlier version of this test called it
    with Configure not yet showing and the tooltip never appeared), not
    a synthetic event fired straight at a listener regardless of
    display state the way `dispatchEvent` is.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.showScreen('screen-configure'); "
            "document.getElementById('field-seed').focus();"
        ),
        read=(
            "({"
            "hidden: document.querySelector('.field-tooltip').hidden, "
            "matchesFieldHelp: document.querySelector('.field-tooltip')"
            ".textContent === window.FIM_FIELD_HELP.seed"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("hidden") is False,
    )

    assert settled["hidden"] is False
    assert settled["matchesFieldHelp"] is True


def test_a_group_legend_is_keyboard_focusable_and_shows_its_own_tooltip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A mode-selector group's own `<legend>` is a real, focusable tooltip trigger.

    `<legend>` is not natively focusable -- `wireGroupTooltip`'s own
    `tabIndex = 0` is what makes this reachable at all for a keyboard-
    only user, checked directly here, not merely assumed from reading
    the source.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.querySelector('legend[data-field-help=\"m_mode\"]')"
            ".dispatchEvent(new Event('mouseenter'));"
        ),
        read=(
            "({"
            "tabIndex: document.querySelector("
            "'legend[data-field-help=\"m_mode\"]').tabIndex, "
            "hidden: document.querySelector('.field-tooltip').hidden, "
            "matchesFieldHelp: document.querySelector('.field-tooltip')"
            ".textContent === window.FIM_FIELD_HELP.m_mode"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("hidden") is False,
    )

    assert settled["tabIndex"] == 0
    assert settled["hidden"] is False
    assert settled["matchesFieldHelp"] is True


def test_the_open_run_screens_differentiation_orders_label_shows_its_tooltip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The open-run screen's own `data-field-help` label -- not the
    `field-<key>` id convention `wireFieldTooltip`'s callers elsewhere all
    use -- still resolves to the right `FIELD_HELP` entry.

    Exercises the code path `wireAllFieldTooltips` added for this screen:
    the key comes from `label.dataset.fieldHelp` directly, not from
    slicing a `field-` prefix off `label.htmlFor` (this label's own `for`
    is `open-run-differentiation-orders`, which has no such prefix).
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.querySelector("
            "'label[for=\"open-run-differentiation-orders\"]')"
            ".dispatchEvent(new Event('mouseenter'));"
        ),
        read=(
            "({"
            "hidden: document.querySelector('.field-tooltip').hidden, "
            "matchesFieldHelp: document.querySelector('.field-tooltip')"
            ".textContent === window.FIM_FIELD_HELP"
            ".open_run_differentiation_orders"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("hidden") is False,
    )

    assert settled["hidden"] is False
    assert settled["matchesFieldHelp"] is True


def test_the_open_run_screens_generation_legend_shows_its_own_tooltip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The open-run screen's own "Generation" mode-selector group works
    the same way `m_mode`'s Configure-side legend already does, confirming
    the widened `wireAllFieldTooltips` selector actually reaches it."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.querySelector("
            "'legend[data-field-help=\"open_run_generation_mode\"]')"
            ".dispatchEvent(new Event('mouseenter'));"
        ),
        read=(
            "({"
            "tabIndex: document.querySelector("
            "'legend[data-field-help=\"open_run_generation_mode\"]')"
            ".tabIndex, "
            "hidden: document.querySelector('.field-tooltip').hidden, "
            "matchesFieldHelp: document.querySelector('.field-tooltip')"
            ".textContent === window.FIM_FIELD_HELP.open_run_generation_mode"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("hidden") is False,
    )

    assert settled["tabIndex"] == 0
    assert settled["hidden"] is False
    assert settled["matchesFieldHelp"] is True
