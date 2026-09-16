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

from fim.gui import app as app_module
from fim.gui import presets as presets_module
from fim.gui.app import await_bridge_threads

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


def test_rail_has_the_five_destinations_plus_help_in_order(
    window: webview.Window,
) -> None:
    """The rail's own six buttons match design §3.1's own destination list.

    Run and Results were two separate buttons here until they were
    collapsed into one "Run" destination on a real, reported request
    (`screens/nav-rail.js`'s own top comment has the full account).
    """
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
        "compare",
        "help",
    ]


def test_card_navigation_buttons_have_directional_icons(
    window: webview.Window,
) -> None:
    """Back/forward card-navigation controls carry explicit arrow icons."""
    icons = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "({"
            "backButtons: ["
            "'history-back-button', "
            "'results-history-back-button', "
            "'results-back-button', "
            "'open-run-back-button', "
            "'help-back-button', "
            "'explore-back-button', "
            "'compare-back-button', "
            "'configure-back-button'"
            "].map(id => document.getElementById(id)"
            ".querySelector('use')?.getAttribute('href')), "
            "forwardButtons: ["
            "'history-forward-button', "
            "'results-history-forward-button', "
            "'home-new-run-button'"
            "].map(id => document.getElementById(id)"
            ".querySelector('use')?.getAttribute('href'))"
            "})"
        ),
    )
    assert icons == {
        "backButtons": [
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
            "icons/fim-icons.svg#icon-back",
        ],
        "forwardButtons": [
            "icons/fim-icons.svg#icon-forward",
            "icons/fim-icons.svg#icon-forward",
            "icons/fim-icons.svg#icon-forward",
        ],
    }


def test_home_is_the_default_highlighted_destination(window: webview.Window) -> None:
    """`screen-open-run` (Home) is the default-visible screen on launch.

    Botanist GUI design doc §9: "Home replaces the current 'Open a run'
    screen with a richer landing destination" -- a fresh launch shows
    Home, not Run, and the rail agrees.
    """
    current = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "({"
            "homeCurrent: document.querySelector("
            "'.rail-item[data-destination=\"home\"]').getAttribute('aria-current'), "
            "runCurrent: document.querySelector("
            "'.rail-item[data-destination=\"run\"]').getAttribute('aria-current'), "
            "homeVisible: !document.getElementById('screen-open-run').hidden, "
            "runVisible: !document.getElementById('screen-run').hidden"
            "})"
        ),
    )
    assert current == {
        "homeCurrent": "true",
        "runCurrent": "false",
        "homeVisible": True,
        "runVisible": False,
    }


def test_home_back_button_is_disabled_on_launch(window: webview.Window) -> None:
    """Home is the startup screen, so its Back button has no destination yet."""
    disabled = _drive(
        window,
        lambda _poll_until: window.evaluate_js(
            "({"
            "homeBack: document.getElementById('open-run-back-button').disabled, "
            "stripBack: document.getElementById('history-back-button').disabled, "
            "stripForward: document.getElementById('history-forward-button').disabled"
            "})"
        ),
    )
    assert disabled == {"homeBack": True, "stripBack": True, "stripForward": True}


def test_top_strip_back_and_forward_walk_screen_history(
    window: webview.Window,
) -> None:
    """The shared Back/Forward controls follow browser-style screen history."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.showConfigureScreen();")
        poll_until(
            "!document.getElementById('screen-configure').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("window.fim.showExplore();")
        poll_until(
            "!document.getElementById('screen-explore').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("document.getElementById('history-back-button').click();")
        poll_until(
            "!document.getElementById('screen-configure').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("document.getElementById('history-forward-button').click();")
        return poll_until(
            "({"
            "exploreVisible: !document.getElementById('screen-explore').hidden, "
            "backDisabled: document.getElementById('history-back-button').disabled, "
            "forwardDisabled: "
            "document.getElementById('history-forward-button').disabled"
            "})",
            lambda value: value is not None and value["exploreVisible"] is True,
        )

    result = _drive(window, steps)
    assert result == {
        "exploreVisible": True,
        "backDisabled": False,
        "forwardDisabled": True,
    }


def test_settings_dialog_controls_startup_behavior(
    window: webview.Window,
) -> None:
    """The top-strip settings button persists the startup behavior choice."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js(
            "window.__fimStartupBehaviorAfterChange = null;"
            "document.getElementById('settings-startup-behavior').value = 'restart';"
            "document.getElementById('settings-startup-behavior')"
            ".dispatchEvent(new Event('change', {bubbles: true}));"
            "(async () => {"
            "await new Promise((resolve) => setTimeout(resolve, 50));"
            "window.__fimStartupBehaviorAfterChange = "
            "await window.pywebview.api.get_startup_behavior();"
            "})();"
        )
        return poll_until(
            "window.__fimStartupBehaviorAfterChange",
            lambda value: value == "restart",
        )

    assert _drive(window, steps) == "restart"


def test_home_back_button_returns_to_the_screen_that_opened_home(
    window: webview.Window,
) -> None:
    """Home's Back button is enabled only after Home has a real return target."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.showConfigureScreen();")
        poll_until(
            "!document.getElementById('screen-configure').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("window.fim.showOpenRunScreen();")
        poll_until(
            "!document.getElementById('screen-open-run').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("document.getElementById('open-run-back-button').click();")
        return poll_until(
            "({"
            "backDisabled: document.getElementById('open-run-back-button').disabled, "
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "homeVisible: !document.getElementById('screen-open-run').hidden"
            "})",
            lambda value: value is not None and value["configureVisible"] is True,
        )

    result = _drive(window, steps)
    assert result == {
        "backDisabled": False,
        "configureVisible": True,
        "homeVisible": False,
    }


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


def test_configure_back_button_returns_to_whichever_screen_preceded_it(
    window: webview.Window,
) -> None:
    """`configure-back-button` returns to Explore, not a fixed destination.

    Configure is reachable from nearly everywhere (the rail, the
    parameter strip, Home's own shortcuts, the File menu) -- a fixed
    "Back to Home" would be wrong here, since Explore, not Home, is
    genuinely whichever screen was showing right before Configure opened
    this time. The same `exploreReturnScreen`/"Back" contract `screen-
    help`/`screen-explore`/`screen-compare` already established
    (`screens/explore.js`'s own module docstring), extended to Configure.
    Explore chosen deliberately over Home: Configure's own *default*
    return screen is already `screen-open-run` (Home) before this test
    ever runs, so returning to Home would pass even if this bookkeeping
    never actually updated `configureReturnScreen` at all.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.menu.explore();")
        poll_until(
            "!document.getElementById('screen-explore').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("window.fim.showConfigureScreen();")
        poll_until(
            "!document.getElementById('screen-configure').hidden",
            lambda value: value is True,
        )
        window.evaluate_js("document.getElementById('configure-back-button').click();")
        return poll_until(
            "({"
            "exploreVisible: !document.getElementById('screen-explore').hidden, "
            "configureVisible: !document.getElementById('screen-configure').hidden"
            "})",
            lambda value: value is not None and value["configureVisible"] is False,
        )

    result = _drive(window, steps)
    assert result == {"exploreVisible": True, "configureVisible": False}


def test_configure_example_select_lists_only_built_in_examples(
    window: webview.Window,
) -> None:
    """`configure-example-select` offers the identical shortcut Home's own
    `home-example-select` does — built-in worked examples only, populated
    by the same shared `refreshExampleOptions` (`screens/presets.js`), not
    a second, independently maintained option list that could drift from
    it (`test_open_run_screen.py`'s own `test_home_example_select_lists_
    only_built_in_examples` is the identical test for Home's copy).
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.showConfigureScreen();")
        return poll_until(
            "({"
            "ready: window.__fimConfigureExampleOptionsReady === true, "
            "labels: Array.from("
            "document.getElementById('configure-example-select').options"
            ").map((option) => option.textContent)"
            "})",
            lambda value: value is not None and value.get("ready"),
        )

    result = _drive(window, steps)
    api = app_module.Api()
    expected_titles = [
        preset.title
        if api.get_preset_form_values(preset.preset_id)["ok"]
        else f"{preset.title} (view YAML only)"
        for preset in presets_module.list_presets(app_module._webui_directory())
    ]
    assert result["labels"] == ["Try a worked example…", *expected_titles]


def test_choosing_a_configure_example_applies_it_without_leaving_configure(
    window: webview.Window,
) -> None:
    """Picking an example applies its values in place, then resets — no
    navigation, unlike Home's own identical shortcut: there is nowhere
    else to jump to, since the whole point is loading a different
    example without leaving Configure. Selects "Stepping-stone (spatial)
    migration" (option index 2) specifically, the same real, distinct-
    from-the-starter-defaults choice `test_open_run_screen.py`'s own
    `test_choosing_a_home_example_applies_it_and_opens_configure` and
    `test_presets_screen.py`'s own equivalent test both already use.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.showConfigureScreen();")
        poll_until(
            "window.__fimConfigureExampleOptionsReady === true",
            lambda value: value is True,
        )
        window.evaluate_js(
            "(function(){"
            "var select = document.getElementById('configure-example-select');"
            "select.selectedIndex = 2;"
            "select.dispatchEvent(new Event('change'));"
            "})();"
        )
        return poll_until(
            "({"
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "mMode: document.querySelector("
            "'input[name=\"m_mode\"]:checked')?.value, "
            "nValue: document.getElementById('field-N').value, "
            "selectValue: "
            "document.getElementById('configure-example-select').value"
            "})",
            lambda value: value is not None and value.get("mMode") == "matrix",
        )

    result = _drive(window, steps)
    assert result["configureVisible"] is True
    assert result["mMode"] == "matrix"
    assert result["nValue"] == "150"
    # Reset to its own placeholder afterward — the control always reads
    # as an action, never as "currently showing example X."
    assert result["selectValue"] == ""


def test_choosing_the_non_loadable_configure_example_shows_an_inline_notice(
    window: webview.Window,
) -> None:
    """The one non-loadable example shows Configure's own banner, not an alert.

    Design doc `20260913-claude-sonnet-5-gui-worked-example-loadability-
    design.md` (`selby/restricted`), Option C: `window.alert`'s blocking
    OS chrome replaced with `showExampleLoadNotice`'s own inline,
    non-modal banner. "Per-base mutation rate across unequal locus
    lengths" is the one built-in example
    `test_every_other_builtin_preset_loads_into_form_values`
    (`test_app_api.py`) confirms has no form representation; picked by
    stable preset id rather than index so adding another worked example
    cannot quietly turn this into a loadable-example test. The bare
    title, not the "(view YAML only)" label text, must appear in the
    notice — a real regression found live while writing this test,
    before `refreshExampleOptions`'s own `dataset.presetTitle` existed
    to separate the two.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("window.fim.showConfigureScreen();")
        poll_until(
            "window.__fimConfigureExampleOptionsReady === true",
            lambda value: value is True,
        )
        window.evaluate_js(
            "(function(){"
            "var select = document.getElementById('configure-example-select');"
            "select.value = "
            "'per-base-mutation-rate-across-unequal-locus-lengths';"
            "select.dispatchEvent(new Event('change'));"
            "})();"
        )
        return poll_until(
            "({"
            "bannerHidden: document.getElementById('configure-banner').hidden, "
            "bannerText: document.getElementById('configure-banner').textContent, "
            "configureVisible: !document.getElementById('screen-configure').hidden"
            "})",
            lambda value: value is not None and value["bannerHidden"] is False,
        )

    result = _drive(window, steps)
    assert result["configureVisible"] is True
    assert (
        'Could not load "Per-base mutation rate across unequal locus lengths"'
        in result["bannerText"]
    )
    assert "(view YAML only)" not in result["bannerText"]
    assert "per-locus mu" in result["bannerText"]
