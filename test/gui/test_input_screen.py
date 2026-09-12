"""Headless functional tests for the Configure workspace's own fields
and the always-present controls (botanist GUI redesign doc `20260907-
claude-sonnet-5-botanist-gui-redesign.md` §4; `doc/fim-gui-design.md`
§5.2, §6 for the always-present controls, unchanged by that redesign).

Real DOM-driven proof that `webui/screens/config-modals.js`/`run-view-
controls.js`/`run-view-initial.js` actually wire the page correctly —
`test/gui/test_app_api.py` already proves the bridge methods themselves
are correct as plain Python calls; these tests prove the page's own
JavaScript calls them at the right moments and updates the right
elements, which no Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window
from fim.gui.config_form import starter_form_values
from fim.gui.preferences import GuiPreferences, save_preferences

pytestmark = pytest.mark.gui

# input.js's own `initializeInputScreen` sets this once every awaited
# bridge call, DOM population, and `wireEvents()` have all completed.
# Every test below that fires a synthetic DOM event passes this as
# `drive`'s `ready` argument, polled with plain (non-async) `evaluate_js`
# calls before `trigger` ever fires -- `conftest.py`'s own `drive_and_
# read` docstring records why an async, `setTimeout`-polling trigger
# hangs the driver thread indefinitely instead.
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


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


def test_mutation_tab_renders_mu_as_the_greek_letter(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The Mutation tab's own labels show `μ`, not the literal word "mu".

    Static markup, not JS-generated — `field-mu_value`/`field-mu_b_value`'s
    own `<label>`s in `index.html` — but the `field-*`/`name=`/`value=`
    attributes those labels are `for=` (and every field `input.js` reads
    by name) stay plain ASCII `mu`/`mu_b`: only the human-visible text
    changed, not anything `config_form.py`'s own field mapping depends on.
    """
    labels = drive(
        window,
        trigger="null",
        read=(
            "({"
            "mu: document.querySelector('label[for=\"field-mu_value\"]')"
            ".textContent, "
            "muB: document.querySelector('label[for=\"field-mu_b_value\"]')"
            ".innerHTML"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("mu") == "μ",
        poll_attempts=500,
    )

    assert labels["mu"] == "μ"
    assert labels["muB"] == "μ<sub>b</sub>"


def test_input_screen_run_button_enabled_for_the_valid_starter_form(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The starter form validates on load, so "Run simulation" starts enabled.

    Polls `window.__fimRunViewReady`, not the field's own value:
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
            "window.__fimRunViewReady ? ({"
            "n: document.getElementById('field-N').value, "
            "disabled: document.getElementById('run-button').disabled"
            "}) : null"
        ),
        is_ready=lambda value: value is not None,
        poll_attempts=500,
    )

    assert settled["n"] == expected_n
    assert settled["disabled"] is False


def test_initial_view_shows_axis_selectors_for_deme_pair_choice(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The initial p_0 view shows axis selectors when at least two demes exist."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="null",
        read=(
            "({"
            "hidden: document.getElementById('run-deme-pair-selector').hidden, "
            "xCount: document.getElementById('run-x-deme').options.length, "
            "yCount: document.getElementById('run-y-deme').options.length"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("xCount", 0) >= 2,
        poll_attempts=500,
    )

    assert settled["hidden"] is False
    assert settled["xCount"] >= 2
    assert settled["yCount"] == settled["xCount"]


def test_deme_pair_selector_permits_a_self_comparison_and_shows_a_note(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Selecting the same deme in both selectors is permitted, not forced apart.

    P1 item 6 of the 2026-09-06 open-issues doc: an earlier version of
    `wireDemePairSelector` silently bumped one selector back to a
    distinct value whenever the two matched. This proves the current
    one leaves a same-deme selection exactly as chosen, and surfaces it
    with a visible note (`run-deme-pair-self-note`) rather than leaving
    it unlabeled. The note's own text is design §7.3's own "changes the
    plot's own caption to name what is being shown" -- naming the
    specific deme and explaining what a self-comparison plot means, not
    only that one is showing.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('run-y-deme').value = "
            "document.getElementById('run-x-deme').value; "
            "document.getElementById('run-y-deme')"
            ".dispatchEvent(new Event('change'));"
        ),
        read=(
            "({"
            "xValue: document.getElementById('run-x-deme').value, "
            "yValue: document.getElementById('run-y-deme').value, "
            "noteHidden: document.getElementById('run-deme-pair-self-note').hidden, "
            "noteText: document.getElementById('run-deme-pair-self-note').textContent"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("noteHidden") is False,
        poll_attempts=500,
    )

    assert settled["xValue"] == settled["yValue"]
    assert settled["noteHidden"] is False
    assert settled["noteText"].startswith(f"Deme {settled['xValue']} vs. itself")
    assert "sampling noise" in settled["noteText"]


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


def test_run_simulation_with_an_invalid_field_navigates_to_configure_and_marks_it(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Clicking "Run simulation" with an invalid field opens Configure and marks it.

    Replaces the six-modal era's own "opens that field's modal" contract
    (`focusInvalidField`, `config-modals.js`) — every field now lives
    directly on the always-visible Configure screen (design §4), so
    there is no modal left to open; navigating there and marking the
    specific invalid field (`markTabError`'s own `.field.invalid` class,
    unchanged) is the new, more precise equivalent — precise enough to
    name the exact field, not only the section it used to live in. `N`,
    not `m_rate`: `config_form.field_for_error`'s own docstring is
    explicit that a composite sub-field like `m_rate` resolves to no
    single `FormField` at all (its own validation error names the
    parent `m`) — `N` is a genuine top-level field, so this is the
    shape `focusInvalidField` actually gets a real field name for. No
    `input` event needs dispatching first: `onRunClicked` calls
    `revalidate()` itself, which reads the field's *current* value
    straight off the live DOM via `FormData`.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-N').value = 'not-a-number'; "
            "document.getElementById('run-button').click();"
        ),
        read=(
            "({"
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "fieldInvalid: document.getElementById('field-N')"
            ".closest('.field').classList.contains('invalid')"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("configureVisible") is True
        ),
        poll_attempts=500,
    )

    assert settled["configureVisible"] is True
    assert settled["fieldInvalid"] is True


def test_menu_new_configuration_resets_an_edited_field(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.newConfiguration` resets the form to true starter values.

    The one behavioral difference from the existing "New run" buttons:
    those only navigate back to Screen 1, leaving whatever was already
    in the form; the menu's own "New configuration" genuinely resets it
    — this test exists specifically to keep that distinction honest.
    No longer the same as a fresh app launch's own `initializeRunView`
    (P1 item 4: a launch now prefers a saved form over starter values,
    `loadInitialForm` vs. this menu item's own unconditional
    `resetInputForm`) — see `test_initial_launch_prefers_a_saved_form_
    over_starter_values`, below, for that distinction's own coverage.

    The trigger wraps the call in `setTimeout(..., 0)`, matching
    `fim.gui.app._build_menu`'s own real dispatcher exactly (not a test
    convenience): calling an `async` `fim.menu.*` method directly as an
    `evaluate_js` expression deadlocks — confirmed live building this
    test — the same pywebview behavior `conftest.py`'s own `drive_and_
    read` docstring already documents for its `ready`-polling case.

    Polls for `window.__fimRunViewReady` alongside the field's own
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
            "ready: window.__fimRunViewReady === true"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("ready") is True,
        poll_attempts=500,
    )

    assert value["fieldN"] == starter_n


def test_initial_launch_prefers_a_saved_form_over_starter_values(
    tmp_path: Path, drive: Callable[..., Any]
) -> None:
    """A fresh launch's own Input screen shows a saved form, not starter values.

    Builds its own window rather than using the shared `window` fixture:
    the preferences file has to exist on disk *before* `Api.__init__`
    (and therefore `initializeRunView`'s own `loadInitialForm`) ever
    runs, and the shared fixture's own window is already built by the
    time a test body gets to execute at all. `drive` (the fixture) still
    handles this window exactly like the shared one -- `drive_and_read`
    takes any `target_window`, not only the fixture's own.
    """
    preferences_path = tmp_path / "preferences.json"
    saved_values = dict(starter_form_values())
    saved_values["N"] = "424242"
    save_preferences(preferences_path, GuiPreferences(form_values=saved_values))
    window = create_window(api=Api(preferences_path=preferences_path), hidden=True)

    field_n = drive(
        window,
        trigger="null",
        read="document.getElementById('field-N').value",
        ready=_INPUT_SCREEN_READY,
    )

    assert field_n == "424242"


def test_initial_launch_falls_back_to_starter_values_for_an_invalid_saved_form(
    tmp_path: Path, drive: Callable[..., Any]
) -> None:
    """A saved form that no longer validates is discarded, never applied partially.

    `Api.get_initial_form` re-validates through the exact same path
    `start_run` itself uses (`fim.gui.preferences`'s own module
    docstring) -- a hand-edited or stale file that fails it falls all
    the way back to `starter_form_values()`, the same as a first-ever
    launch with nothing saved at all.
    """
    preferences_path = tmp_path / "preferences.json"
    saved_values = dict(starter_form_values())
    saved_values["N"] = "not-a-number"
    save_preferences(preferences_path, GuiPreferences(form_values=saved_values))
    window = create_window(api=Api(preferences_path=preferences_path), hidden=True)

    field_n = drive(
        window,
        trigger="null",
        read="document.getElementById('field-N').value",
        ready=_INPUT_SCREEN_READY,
    )

    assert field_n == starter_form_values()["N"]


def test_significant_digits_field_loads_and_changes_the_real_value(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The Configure field (design §4.2) round-trips through the real bridge.

    Not a `SimulationParams` field (`field-significant_digits` carries
    no `name`/`form="input-form"`), so its own coverage lives here
    rather than in `config_form`'s tests: `wireSignificantDigitsField`
    (`config-modals.js`) seeds the select from `Api.get_significant_
    digits` on load, and a `change` event calls `fim.menu.
    setSignificantDigits` — the same method the native View menu's own
    now-removed quick-toggle submenu used to call, confirmed by reading
    the value back through a second `Api` call on the very same window.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        # A plain, synchronous statement that only *starts* the async
        # IIFE and returns immediately -- `evaluate_js` itself never
        # awaits anything (`conftest.py`'s own `drive_and_read` docstring
        # on why an async *trigger* whose own completion `evaluate_js`
        # would have to wait for deadlocks; this is the safe shape that
        # docstring also describes: fire-and-forget, then poll a
        # separate `read`).
        trigger=(
            "window.__fimSignificantDigitsResult = null; "
            "(async () => { "
            "document.getElementById('field-significant_digits').value = '6'; "
            "document.getElementById('field-significant_digits')"
            ".dispatchEvent(new Event('change', {bubbles: true})); "
            "await new Promise((resolve) => setTimeout(resolve, 50)); "
            "window.__fimSignificantDigitsResult = "
            "await window.pywebview.api.get_significant_digits(); "
            "})();"
        ),
        read="window.__fimSignificantDigitsResult",
        is_ready=lambda value: value is not None,
    )

    assert settled == 6


def test_checking_a_second_convergence_statistic_reveals_the_combinator(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Checking a second statistic reveals the combinator field.

    The starter form has only `cs_D` checked; `syncConditionalVisibility`
    (`config-modals.js`) reveals `combinator-field` only once two or more
    are checked. Driven as a direct DOM click on the checkbox itself,
    the field's own real interaction now that the native Configure
    menu's own `toggleConvergenceStatistic` quick-toggle no longer
    exists — every field is reachable the same way regardless of how
    quick a toggle it used to be (design §3.3).
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="document.querySelector('input[name=\"cs_G_ST\"]').click();",
        read=(
            "({"
            "d: document.querySelector('input[name=\"cs_D\"]').checked, "
            "gSt: document.querySelector('input[name=\"cs_G_ST\"]').checked, "
            "combinatorHidden: document.getElementById('combinator-field').hidden"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("gSt") is True,
    )

    assert settled["d"] is True
    assert settled["gSt"] is True
    assert settled["combinatorHidden"] is False


def test_checking_the_sigma_band_toggle_reveals_and_seeds_its_own_fields(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Checking "within-run sigma band" reveals its two fields, window pre-filled `100`.

    `20260910-claude-sonnet-5-gui-sigma-band-design.md` (`selby/
    restricted`) approach A1: "off by default and one toggle away," the
    multiplier defaulting to the `<select>`'s own first `<option>`
    (`2.0`, no JS needed for that half) and the window seeded by
    `config-modals.js`'s own `wireSigmaBandSeedDefault`.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "var cb = document.getElementById('field-sigma_band_enabled'); "
            "cb.checked = true; "
            "cb.dispatchEvent(new Event('change', {bubbles: true}));"
        ),
        read=(
            "({"
            "fieldsHidden: document.getElementById('sigma-band-fields').hidden, "
            "multiplierValue: "
            "document.getElementById('field-sigma_band_multiplier').value, "
            "windowValue: document.getElementById('field-sigma_band_window').value"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("fieldsHidden") is False,
    )

    assert settled["fieldsHidden"] is False
    assert settled["multiplierValue"] == "2.0"
    assert settled["windowValue"] == "100"


def test_unchecking_and_rechecking_the_sigma_band_toggle_keeps_a_typed_window_value(
    window: webview.Window,
) -> None:
    """A window value already typed survives an uncheck/recheck, never reset to `100`.

    Driven manually (`window.fim.showScreen`/`whenApiReady` already
    settled by the time `_INPUT_SCREEN_READY` is true, so a plain
    sequence of synchronous `evaluate_js` calls against one window is
    enough — no background thread involved, matching `test_open_run_
    screen.py`'s own "plain, synchronous request/response" precedent).
    """
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(300):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(0.1)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "var cb = document.getElementById('field-sigma_band_enabled'); "
                "cb.checked = true; "
                "cb.dispatchEvent(new Event('change', {bubbles: true}));"
            )
            window.evaluate_js(
                "document.getElementById('field-sigma_band_window').value = '250';"
            )
            window.evaluate_js(
                "var cb = document.getElementById('field-sigma_band_enabled'); "
                "cb.checked = false; "
                "cb.dispatchEvent(new Event('change', {bubbles: true})); "
                "cb.checked = true; "
                "cb.dispatchEvent(new Event('change', {bubbles: true}));"
            )
            window_value = window.evaluate_js(
                "document.getElementById('field-sigma_band_window').value"
            )
            outcome.put(window_value)
        finally:
            window.destroy()

    webview.start(_drive)
    window_value = outcome.get(timeout=10.0)

    assert window_value == "250"


def test_track_expensive_statistics_checkbox_starts_unchecked(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The E_ST/K_ST display opt-in defaults unchecked, matching `SimulationParams`.

    Unlike the sigma-band toggle above, this is a plain "bool" `FormField`
    with no second, revealed field pair to seed -- this and the test
    below are its own entire DOM-level coverage.
    """
    checked = drive(
        window,
        trigger="null",
        read="document.getElementById('field-track_expensive_statistics').checked",
        ready=_INPUT_SCREEN_READY,
        is_ready=lambda value: value is False,
        poll_attempts=500,
    )

    assert checked is False


def test_checking_track_expensive_statistics_updates_the_checkbox(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Checking the box actually flips its own DOM state, live."""
    checked = drive(
        window,
        trigger=(
            "var cb = document.getElementById('field-track_expensive_statistics'); "
            "cb.checked = true; "
            "cb.dispatchEvent(new Event('change', {bubbles: true}));"
        ),
        read="document.getElementById('field-track_expensive_statistics').checked",
        ready=_INPUT_SCREEN_READY,
        is_ready=lambda value: value is True,
        poll_attempts=500,
    )

    assert checked is True


def test_navigating_to_configure_does_not_reset_run_view_state(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Configure is reachable mid-run-lifecycle without discarding it (design §3.1).

    Asserted against `runViewState` staying untouched, not merely which
    screen is visible — the invariant this project has kept through
    every navigation redesign so far: the six-modal era's own version of
    this test proved a Configure modal floated over the run view without
    resetting it; the rail-based redesign replaces "floats over" with
    "is its own destination," but "reaching Configure never discards a
    live or completed run" is the same contract either way.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.showScreen('screen-configure');",
        read=(
            "({"
            "configureVisible: !document.getElementById('screen-configure').hidden, "
            "runViewState: window.fim.getRunViewState()"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("configureVisible") is True
        ),
    )

    assert settled["runViewState"] == "initial"


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
    this is `screens/run-view-running.js`'s own display logic under
    test, not the batch-execution timing that triggers it. No explicit
    reset call needed first: every test gets a fresh page load of its
    own, so the module-scoped high-water mark this proves already
    starts at its own initial `0` regardless.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
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
