"""Headless functional tests for the Help screen (doc/fim-gui-design.md §11)."""

from __future__ import annotations

import queue
import webbrowser
from typing import Any

import pytest
import webview

from .conftest import poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def test_help_screen_shows_usage_and_back_returns_to_the_prior_screen(
    window: webview.Window,
) -> None:
    """`fim.showHelp` renders `usage.html`; Back returns to the prior screen.

    Two sequential stages against one live window (the same manual-driver
    shape `test_results_screen.py`'s own deme-pair-selector test uses,
    for the same reason: more than one trigger-then-poll round trip on
    a single window needs one `webview.start()` call, not the `drive`
    fixture called twice — its own driver destroys the window in a
    `finally` block after its first, single round trip).
    """
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.showHelp('usage');")
            settled = poll_page(
                window,
                "({"
                "helpVisible: !document.getElementById('screen-help').hidden, "
                "hasContent: "
                "document.getElementById('help-content').textContent.length > 0"
                "})",
                lambda value: value["helpVisible"] is True,
            )
            settled["hasContent"] = poll_page(
                window,
                "window.__fimHelpRendered === 'usage' && "
                "document.getElementById('help-content').textContent.length > 0",
                lambda value: value is True,
            )
            window.evaluate_js("document.getElementById('help-back-button').click();")
            # Home (`screen-open-run`), not Run: `showHelp` was triggered
            # straight from launch, with no navigation in between, and
            # Home is this app's own default landing screen
            # (`test_nav_rail.py`'s own
            # `test_home_is_the_default_highlighted_destination`) --
            # `help.js`'s own `returnScreen` captured whichever screen
            # was actually showing at that moment, not a fixed default.
            back_visible = poll_page(
                window,
                "!document.getElementById('screen-open-run').hidden",
                lambda value: value is True,
            )
            outcome.put(
                {
                    "helpVisible": settled["helpVisible"],
                    "hasContent": settled["hasContent"],
                    "backVisible": back_visible,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["helpVisible"] is True
    assert result["hasContent"] is True
    assert result["backVisible"] is True


def test_external_doc_link_reaches_the_browser_and_settles(
    window: webview.Window, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rendered `data-fim-external` link opens the OS browser and settles.

    Clicks whichever real external link `usage.html`'s own rendered
    fragment happens to contain first (`dev/bin/generate-help-html`'s
    own link rewriting) rather than a synthetic one, the same
    "exercise the real generated content" precedent the rest of this
    file already follows. Monkeypatches `webbrowser.open` — the same
    hook `test_app_api.py`'s own `test_open_external_link_opens_the_
    os_default_browser` uses as a plain Python call — so this never
    opens a real browser.

    Also proves a real, once-reproduced hang is closed:
    `helpContent`'s own click handler called `window.pywebview.api.
    open_external_link(...)` without anything downstream awaiting it,
    so nothing tied a test's own teardown to that call having actually
    finished before `screens/help.js`'s own `window.__fimHelpExternal
    LinkSettled` flag. See `test_running_screen.py`'s own `_wait_for_
    cancel_run_settled` for the full mechanism, traced there via
    `sample <pid>` on a `git push`'s own hung pre-push `pytest` run.
    """
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    outcome: queue.Queue[bool] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.showHelp('usage');")
            poll_page(
                window,
                "document.querySelectorAll('[data-fim-external]').length > 0",
                lambda value: value is True,
            )
            window.evaluate_js("document.querySelector('[data-fim-external]').click();")
            settled = poll_page(
                window,
                "window.__fimHelpExternalLinkSettled === true",
                lambda value: value is True,
            )
            outcome.put(bool(settled))
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled is True
    assert len(opened) == 1
    assert opened[0].startswith("https://")


def test_help_screen_returns_to_results_when_opened_from_there(
    window: webview.Window,
) -> None:
    """Back returns to Results, not a fixed default, when Help was opened from there.

    The one genuinely new interaction Help adds relative to every other
    screen's own fixed-target "Back" button: recording
    *whichever* screen was showing, not always the same one.
    """
    outcome: queue.Queue[bool] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            # Show any other screen first -- Screen 6 (open a run) needs
            # no completed run to reach, unlike Results/Batch results.
            window.evaluate_js("window.fim.showOpenRunScreen();")
            # Waits for `window.__fimOpenRunRecentRunsLoaded`, not only
            # screen visibility -- `showOpenRunScreen` shows the screen
            # synchronously and fires its own `refreshRecentRuns()` (an
            # async `list_recent_runs()` bridge call) without awaiting it
            # (`open-run.js`'s own comment on that flag explains why;
            # `test_open_run_screen.py::test_open_a_run_button_reaches_
            # screen_six` already established this exact pattern). On a
            # `results/` directory with hundreds of real runs, that call
            # takes real time -- proceeding to destroy the window before
            # it settles is a real, reproducible `JavascriptException`
            # (`_returnValuesCallbacks[...] is not a function`) once the
            # background thread tries to deliver its answer to a window
            # already gone, not merely a theoretical race.
            poll_page(
                window,
                "!document.getElementById('screen-open-run').hidden "
                "&& window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            window.evaluate_js("window.fim.showHelp('configuration');")
            poll_page(
                window,
                "!document.getElementById('screen-help').hidden",
                lambda value: value is True,
            )
            window.evaluate_js("document.getElementById('help-back-button').click();")
            back_visible = poll_page(
                window,
                "!document.getElementById('screen-open-run').hidden",
                lambda value: value is True,
            )
            outcome.put(back_visible)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result is True


@pytest.mark.parametrize("label_source", ["row", "column"])
def test_statistic_name_opens_its_definition_without_toggling_the_plot(
    window: webview.Window,
    label_source: str,
) -> None:
    """Statistic help preserves plot visibility and returns to the same screen."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    button = (
        "window.__helpRow.querySelector('button')"
        if label_source == "row"
        else "document.querySelector("
        "'tr[data-statistic-columns] th[data-shown-key=\"D\"] button')"
    )

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "window.fim.showScreen('screen-run');"
                "window.__plotToggles = 0;"
                "window.__helpRow = document.createElement('tr');"
                "window.__helpRow.appendChild(buildStatCells('D', '0.42'));"
                "applyStatRow(window.__helpRow, buildStatCells('G_ST', '0.1'));"
                "applyStatRow(window.__helpRow, buildStatCells('D', '0.42'));"
                "decorateTrajectoryStatisticRow(window.__helpRow, 'D');"
                "window.__plotPressed = window.__helpRow.getAttribute('aria-pressed');"
                "window.__helpRow.addEventListener('click', () => "
                "window.__plotToggles++);"
                "document.querySelector('#initial-stats tbody')"
                ".appendChild(window.__helpRow);"
                "window.__helpRow.querySelector('button').dispatchEvent("
                "new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));"
            )
            window.evaluate_js(f"{button}.click();")
            state = poll_page(
                window,
                "({"
                "helpVisible: !document.getElementById('screen-help').hidden,"
                "hasDefinition: document.querySelector('#help-content #d') !== null,"
                "hasApi: document.querySelector("
                "'#help-content [data-fim-external*=\"API.md#fim.statistics\"]') "
                "!== null,"
                "hasMath: document.querySelector("
                "'#help-content math[display=\"block\"] mfrac') !== null,"
                "hasEquationTag: Array.from(document.querySelectorAll("
                "'#help-content .equation-tag')).some("
                "element => element.textContent === '(S.2)'),"
                "nativeMath: (() => {"
                "const fraction = document.querySelector("
                "'#help-content math[display=\"block\"] mfrac');"
                "if (!fraction) return false;"
                "const numerator = fraction.children[0].getBoundingClientRect();"
                "const denominator = fraction.children[1].getBoundingClientRect();"
                "return numerator.height > 0 && "
                "denominator.top >= numerator.bottom;"
                "})(),"
                "toggles: window.__plotToggles,"
                "plotUnchanged: window.__plotPressed === "
                "window.__helpRow.getAttribute('aria-pressed')"
                "})",
                lambda value: value["helpVisible"] and value["hasDefinition"],
            )
            window.evaluate_js("document.getElementById('help-back-button').click();")
            state["back"] = poll_page(
                window,
                "!document.getElementById('screen-run').hidden",
                lambda value: value is True,
            )
            outcome.put(state)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result == {
        "helpVisible": True,
        "hasDefinition": True,
        "hasApi": True,
        "hasMath": True,
        "hasEquationTag": True,
        "nativeMath": True,
        "toggles": 0,
        "plotUnchanged": True,
        "back": True,
    }


_HELP_STATE = (
    "({"
    "rendered: window.__fimHelpRendered, "
    "helpVisible: !document.getElementById('screen-help').hidden, "
    "breadcrumb: document.getElementById('help-breadcrumb').textContent, "
    "forwardDisabled: document.getElementById('help-forward-button').disabled"
    "})"
)


def _run_steps(window: webview.Window, steps: list[tuple[str, str, Any]]) -> list[Any]:
    """Run trigger/read/predicate steps against one live window.

    Args:
        window: The test window.
        steps: `(trigger, read, settled)` triples; each `read` is polled
            until `settled` accepts it.

    Returns:
        Each step's settled `read` value, in order.
    """
    outcome: queue.Queue[list[Any] | AssertionError] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            values = []
            for trigger, read, settled in steps:
                window.evaluate_js(trigger)
                values.append(poll_page(window, read, settled))
            outcome.put(values)
        except AssertionError as error:
            outcome.put(error)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=30)
    if isinstance(result, AssertionError):
        raise result
    return result


def test_help_home_lists_every_topic_by_group(window: webview.Window) -> None:
    """The rail and Help menu open Help home: grouped topics, no CLI first."""
    (home,) = _run_steps(
        window,
        [
            (
                "window.fim.menu.help();",
                "window.__fimHelpRendered === 'home' && ({"
                "groups: Array.from(document.querySelectorAll("
                "'#help-content .help-home-group h2')).map(h => h.textContent), "
                "topics: document.querySelectorAll("
                "'#help-content .help-topic-list a[data-fim-help]').length, "
                "first: document.querySelector("
                "'#help-content .help-topic-list a').dataset.fimHelp, "
                "breadcrumb: document.getElementById('help-breadcrumb').textContent"
                "})",
                lambda value: value is not False,
            )
        ],
    )

    assert home["groups"][0] == "Desktop app"
    assert home["groups"][-1] == "Command line"
    assert home["first"] == "gui-overview"
    assert home["topics"] >= 19
    assert home["breadcrumb"] == "Help"


def test_help_pages_have_their_own_history_and_return_to_work(
    window: webview.Window,
) -> None:
    """Back and Forward move between pages; Return to work leaves Help."""

    def rendered(topic: str) -> str:
        return f"window.__fimHelpRendered === '{topic}' && {_HELP_STATE}"

    states = _run_steps(
        window,
        [
            (
                "window.fim.showHelp('gui-overview');",
                rendered("gui-overview"),
                lambda value: value is not False,
            ),
            (
                "document.querySelector("
                "'#help-content a[data-fim-help=\"gui-conventions\"]').click();",
                rendered("gui-conventions"),
                lambda value: value is not False and "\u203a" in value["breadcrumb"],
            ),
            (
                "document.getElementById('help-back-button').click();",
                rendered("gui-overview"),
                lambda value: value is not False,
            ),
            (
                "document.getElementById('help-forward-button').click();",
                rendered("gui-conventions"),
                lambda value: value is not False,
            ),
            (
                "document.getElementById('help-return-button').click();",
                "!document.getElementById('screen-open-run').hidden",
                lambda value: value is True,
            ),
        ],
    )

    assert "Desktop app" in states[1]["breadcrumb"]
    assert "Implementation conventions" in states[1]["breadcrumb"]
    assert states[1]["forwardDisabled"] is True
    assert states[2]["forwardDisabled"] is False
    assert states[3]["forwardDisabled"] is True
    assert states[4] is True


def test_help_search_finds_desktop_sections_offline(window: webview.Window) -> None:
    """Search ranks section-level results and opens one at its heading."""
    results, opened = _run_steps(
        window,
        [
            (
                "window.fim.showHelpHome();"
                "const search = document.getElementById('help-search');"
                "search.value = 'migration convention';"
                "search.dispatchEvent(new KeyboardEvent("
                "'keydown', {key: 'Enter', bubbles: true}));",
                "window.__fimHelpRendered === 'search' && Array.from("
                "document.querySelectorAll('#help-content .help-search-results a'))"
                ".map(a => a.dataset.fimHelp)",
                lambda value: value is not False,
            ),
            (
                "document.querySelector('#help-content .help-search-results a')"
                ".click();",
                "window.__fimHelpRendered !== 'search' "
                "&& window.__fimHelpRendered !== null "
                "&& document.getElementById('help-search').value",
                lambda value: value == "",
            ),
        ],
    )

    assert results
    assert results[0].startswith("gui-")
    assert opened == ""


def test_f1_opens_the_focused_fields_reference_and_returns(
    window: webview.Window,
) -> None:
    """F1 in a Configure field opens its configuration key; Return goes back."""
    reference, returned = _run_steps(
        window,
        [
            (
                "window.fim.showConfigureScreen().then(() => {"
                "document.getElementById('field-d').focus();"
                "document.getElementById('field-d').dispatchEvent(new KeyboardEvent("
                "'keydown', {key: 'F1', bubbles: true}));});",
                "window.__fimHelpRendered === 'configuration' && ({"
                "anchor: document.querySelector('#help-content #d') !== null, "
                "screenButton: document.getElementById('help-screen-button')"
                ".textContent})",
                lambda value: value is not False,
            ),
            (
                "document.getElementById('help-return-button').click();",
                "!document.getElementById('screen-configure').hidden",
                lambda value: value is True,
            ),
        ],
    )

    assert reference == {"anchor": True, "screenButton": "Help for Configure"}
    assert returned is True


def test_missing_help_page_offers_recovery(window: webview.Window) -> None:
    """A page that fails to load says so and offers Retry, home, and return."""
    (failure,) = _run_steps(
        window,
        [
            (
                "window.fim.showHelp('no-such-topic');",
                "window.__fimHelpRendered === 'error' && Array.from("
                "document.querySelectorAll('#help-content .help-error button'))"
                ".map(button => button.textContent)",
                lambda value: value is not False,
            )
        ],
    )

    assert failure == ["Try again", "Help home", "Return to work"]
