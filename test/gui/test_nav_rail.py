"""Headless functional tests for the persistent rail and parameter strip
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
redesign.md` §3.1, §3.2 -- design §16's own delivery-phasing "phase 1"
first slice).

Real DOM-driven proof that `webui/screens/nav-rail.js` actually wires the
rail's own click handlers, keeps `app.js`'s `showScreen` in sync via
`updateRailHighlight`, and keeps the parameter strip current -- none of
which a Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

from .conftest import await_bridge_threads

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def _drive(
    window: webview.Window,
    steps: Callable[[Callable[[str, Callable[[Any], bool]], Any]], Any],
) -> Any:
    """Run `steps` against a real, ready `window` (`test_explore_screen.py`'s pattern).

    `steps` receives a `poll_until(script, predicate)` helper and returns
    whatever it wants the caller to see; `webview.start()` runs `steps` on
    its own driver thread and hands its return value back through a
    queue, since the caller's own thread cannot touch `window` directly.
    """
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _run() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            outcome.put(steps(_poll_until))
        finally:
            # `webview.start()` blocks the calling (real) thread running
            # the native run loop until the window is destroyed -- it
            # does not return merely because this callback function
            # returns (confirmed live: omitting this destroy call hung
            # every test in this file indefinitely, `webview.start()`
            # itself never returning). `await_bridge_threads()` first,
            # same ordering as the `window` fixture's own teardown:
            # confirmed live too -- `test_parameter_strip_updates_live_
            # as_a_field_changes` below dispatches a real `input` event,
            # whose delegated listener fires an un-awaited `validate_
            # form` bridge call; destroying immediately after this
            # function's own poll condition is satisfied (the strip
            # updates synchronously, before that bridge call's own
            # promise settles) stranded that call's non-daemon delivery
            # thread and blocked the whole interpreter's shutdown at the
            # end of the run, not just this one test.
            await_bridge_threads()
            window.destroy()

    webview.start(_run)
    return outcome.get(timeout=10.0)


def test_rail_has_the_six_destinations_plus_help_in_order(
    window: webview.Window,
) -> None:
    """The rail's own seven buttons match design §3.1's own destination list."""
    destinations = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "Array.from(document.querySelectorAll('.rail-item'))"
            ".map(b => b.dataset.destination)"
        ),
    )
    assert destinations == [
        "home",
        "configure",
        "explore",
        "run",
        "results",
        "compare",
        "help",
    ]


def test_run_is_the_default_highlighted_destination(window: webview.Window) -> None:
    """`screen-run` is the default-visible screen; the rail agrees on launch."""
    current = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "document.querySelector('.rail-item[data-destination=\"run\"]')"
            ".getAttribute('aria-current')"
        ),
    )
    assert current == "true"


def test_parameter_strip_shows_the_starter_configuration_on_launch(
    window: webview.Window,
) -> None:
    """The strip is populated before any field is touched, on launch."""
    values = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "({N: document.getElementById('parameter-strip-N').textContent,"
            " d: document.getElementById('parameter-strip-d').textContent,"
            " m: document.getElementById('parameter-strip-m').textContent,"
            " mu: document.getElementById('parameter-strip-mu').textContent})"
        ),
    )
    assert values == {"N": "450", "d": "20", "m": "0.001", "mu": "3e-05"}


def test_clicking_configure_shows_the_landing_screen_and_updates_the_rail(
    window: webview.Window,
) -> None:
    """Rail navigation both switches the screen and moves the highlight."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(
            "document.querySelector('.rail-item[data-destination=\"configure\"]').click()"
        )
        return poll_until(
            "({"
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "configureCurrent: document.querySelector("
            "'.rail-item[data-destination=\"configure\"]')"
            ".getAttribute('aria-current'), "
            "runCurrent: document.querySelector("
            "'.rail-item[data-destination=\"run\"]').getAttribute('aria-current')"
            "})",
            lambda value: value["configureVisible"] is True,
        )

    result = _drive(window, steps)
    assert result == {
        "configureVisible": True,
        "configureCurrent": "true",
        "runCurrent": "false",
    }


def test_configure_shows_both_panels_with_their_own_fields(
    window: webview.Window,
) -> None:
    """Configure's own two panels each show real fields, no modal to open.

    Confirms the two-panel restructuring landed where the rail's own
    Configure button points: `field-m_rate` (FIM parameters, §4.1) and
    `field-mutation_model` (Structure, §4.2) are both directly visible
    the moment Configure is showing, not behind a per-section dialog.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(
            "document.querySelector('.rail-item[data-destination=\"configure\"]').click()"
        )
        return poll_until(
            "({"
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "mRateVisible: document.getElementById('field-m_rate')"
            ".offsetParent !== null, "
            "mutationModelVisible: document.getElementById('field-mutation_model')"
            ".offsetParent !== null"
            "})",
            lambda value: value["configureVisible"] is True,
        )

    result = _drive(window, steps)
    assert result == {
        "configureVisible": True,
        "mRateVisible": True,
        "mutationModelVisible": True,
    }


def test_parameter_strip_click_jumps_to_configure(window: webview.Window) -> None:
    """Design §3.2: clicking any strip value jumps straight to Configure."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(
            "document.querySelector('.parameter-strip-item[data-param=\"d\"]').click()"
        )
        return poll_until(
            "!document.getElementById('screen-configure').hidden",
            lambda value: value is True,
        )

    assert _drive(window, steps) is True


def test_parameter_strip_updates_live_as_a_field_changes(
    window: webview.Window,
) -> None:
    """The strip reflects an in-progress edit, not only a submitted/valid form."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(
            "(function(){"
            "var f = document.getElementById('field-N');"
            "f.value = '999';"
            "f.dispatchEvent(new Event('input', {bubbles: true}));"
            "})();"
        )
        return poll_until(
            "document.getElementById('parameter-strip-N').textContent",
            lambda value: value == "999",
        )

    assert _drive(window, steps) == "999"


def test_clicking_the_brand_mark_opens_the_about_dialog(
    window: webview.Window,
) -> None:
    """The rail's own logo/"FIM" mark is a second, always-visible route to
    the same "About fim" dialog the Help menu already opens
    (`screens/nav-rail.js`'s `wireNavRail`, `screens/config-modals.js`'s
    `showAboutModal`) -- proof the click handler is actually wired, not
    only that the button exists (`test_branding.py`'s own static check)."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('rail-brand-about').click()")
        return poll_until(
            "document.getElementById('modal-about').open",
            lambda value: value is True,
        )

    assert _drive(window, steps) is True
