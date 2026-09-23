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


def test_a_fresh_form_makes_the_botanist_choose_a_ploidy(
    tmp_path: Path, drive: Callable[..., Any]
) -> None:
    """With no default ploidy, Run is blocked until one is chosen.

    Botanist feedback on the first real beta run: ask for ploidy and then
    individuals, rather than gene copies. The choice is never guessed, so
    a fresh form (no saved default in Settings) opens on "choose..." with
    "Run simulation" disabled; picking a ploidy is what enables it.
    """
    preferences_path = tmp_path / "preferences.json"
    save_preferences(preferences_path, GuiPreferences(welcome_dismissed=True))
    window = create_window(api=Api(preferences_path=preferences_path), hidden=True)

    settled = drive(
        window,
        trigger=(
            "window.__fimBefore = {"
            "ploidy: document.getElementById('field-ploidy').value, "
            "disabled: document.getElementById('run-button').disabled}; "
            "var select = document.getElementById('field-ploidy'); "
            "select.value = '2'; "
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
        ),
        read=(
            "({before: window.__fimBefore, "
            "disabled: document.getElementById('run-button').disabled, "
            "strip: document.getElementById('parameter-strip-N').textContent})"
        ),
        ready=_INPUT_SCREEN_READY,
        is_ready=lambda value: (
            value is not None
            and value["before"] is not None
            and value["strip"] == "225 diploid"
        ),
    )

    assert settled["before"]["ploidy"] == ""
    assert settled["before"]["disabled"] is True
    assert settled["disabled"] is False


def test_a_saved_default_ploidy_starts_the_form_on_it(
    tmp_path: Path, drive: Callable[..., Any]
) -> None:
    """Settings' default ploidy pre-selects a new configuration's ploidy."""
    preferences_path = tmp_path / "preferences.json"
    save_preferences(
        preferences_path,
        GuiPreferences(welcome_dismissed=True, default_ploidy="4"),
    )
    window = create_window(api=Api(preferences_path=preferences_path), hidden=True)

    settled = drive(
        window,
        trigger="null",
        read=(
            "({ploidy: document.getElementById('field-ploidy').value, "
            "disabled: document.getElementById('run-button').disabled})"
        ),
        ready=_INPUT_SCREEN_READY,
    )

    assert settled["ploidy"] == "4"
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

    Polls for a completion flag this test's own trigger sets *after*
    awaiting `newConfiguration()`, not for `window.__fimRunViewReady`
    alone. That flag was already `true` when the trigger fired --
    `newConfiguration` only cycles it false-then-true from inside the
    `setTimeout` callback -- so a first `read` poll landing before that
    callback ran observed a fully "ready" page that had not started
    resetting anything yet, and read back the edited `999999`. Seen
    once for real in a full serial GUI run; the test's own outcome
    depended on whether a Python poll or a JS timer won a race, which
    makes it a function of its scheduler rather than of its commit.
    Awaiting inside the `setTimeout` callback is safe (and is not the
    deadlocking case above): the callback runs on the page's own event
    loop, not inside an `evaluate_js` expression.
    """
    starter_n = starter_form_values()["N"]

    value = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "document.getElementById('field-N').value = '999999'; "
            "window.__fimNewConfigurationDone = false; "
            "setTimeout(async () => { "
            "await window.fim.menu.newConfiguration(); "
            "window.__fimNewConfigurationDone = true; "
            "}, 0);"
        ),
        read=(
            "({"
            "fieldN: document.getElementById('field-N').value, "
            "ready: window.__fimRunViewReady === true, "
            "done: window.__fimNewConfigurationDone === true"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("done") is True
            and value.get("ready") is True
        ),
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
    saved_values = {**starter_form_values(), "ploidy": "2"}
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


def test_choosing_the_torus_topology_reveals_rows_and_columns(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Rows and columns show for the torus only.

    A torus (a grid that wraps in both directions, so no deme is on an
    edge) is the common stepping-stone topology; unlike a ring or a
    linear chain it needs a grid shape, revealed by
    `syncConditionalVisibility` (`config-modals.js`) only while it is
    the selected topology. (Whether a given shape is valid for `d` is the
    model's own check, covered by `test_topology.py`.)
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "const set = (name, value) => { "
            "const field = document.getElementById(`field-${name}`); "
            "field.value = value; "
            "field.dispatchEvent(new Event('change', {bubbles: true})); }; "
            "const radio = document.querySelector("
            '\'input[name="m_mode"][value="topology"]\'); '
            "radio.checked = true; "
            "radio.dispatchEvent(new Event('change', {bubbles: true})); "
            "set('m_topology', 'ring'); "
            "window.__fimRingHidden = "
            "document.getElementById('m-torus-fields').hidden; "
            "set('m_topology_rate', '0.05'); "
            "set('m_topology', 'torus'); "
            "set('m_topology_rows', '4'); "
            "set('m_topology_columns', '5'); "
            "window.__fimTorusTouched = true;"
        ),
        read=(
            "({"
            "touched: !!window.__fimTorusTouched, "
            "ringHidden: window.__fimRingHidden, "
            "torusHidden: document.getElementById('m-torus-fields').hidden"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("touched") is True,
    )

    assert settled["ringHidden"] is True
    assert settled["torusHidden"] is False


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


def test_batch_progress_display_tracks_mean_generation_not_replicate_high_water(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`onBatchProgress` uses generation progress, not replicate high-water.

    Real, reported symptom: a long batch showed a full "200 / 200
    replicates reporting" progress bar almost immediately because every
    replicate had emitted an early sidecar, even though the plots kept
    changing for many more generations. Fired here as two synthetic
    `fim.onBatchProgress` calls, first with every replicate reporting
    at mean generation 4, then with fewer current sidecars at mean
    generation 6. The bar must advance by generation, and the label
    must name the generation the plots are showing -- a replicate count
    is not an answer to "how far along is this run?", which is the
    question a progress bar's own label is read for.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.onBatchProgress("
            "{replicateCount: 10, reportedReplicateCount: 10, "
            "meanReportedGeneration: 4, maxGenerations: 100, panels: []});"
            "window.fim.onBatchProgress("
            "{replicateCount: 10, reportedReplicateCount: 3, "
            "meanReportedGeneration: 6, maxGenerations: 100, panels: []});"
        ),
        read=(
            "({"
            "barValue: document.getElementById('progress-generation').value, "
            "barMax: document.getElementById('progress-generation').max, "
            "labelText: "
            "document.getElementById('progress-generation-label').textContent"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("barValue") != 0,
    )

    assert settled["barValue"] == 6
    assert settled["barMax"] == 100
    assert settled["labelText"] == "mean completed generation 6"
    assert "replicates reporting" not in settled["labelText"]


def test_ci_tooltip_states_its_symmetric_summary_only_when_one_exists(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`buildCiMeter` branches on `sampleStd`, within one summary table.

    Both halves of the sample-standard-deviation tooltip design
    (`20260912-claude-sonnet-5-sample-std-dev-tooltip-design.md`,
    `selby/restricted`) in one push, since the point is precisely that
    the two shapes can differ row by row:

    - An interval carrying `halfWidth`/`sampleStd` states both numbers
      botanist GUI design doc §7.2 asks for, appended after the caption.
    - An interval carrying neither states neither, falling back to
      exactly the `mean [low, high] -- caption` text this project
      already shipped. `fim.gui.app._interval_payload` omits the two keys
      together for an interval built by `fim.engine._bootstrap_interval`,
      whose own `half_width` is "a symmetrized summary kept only for
      display consistency" rather than the authoritative interval shape
      — so stating it would state something its own constructor
      disclaims.

    Driven as one synthetic `fim.onBatchProgress` payload rather than a
    real batch: no production code path produces a bootstrap-built
    interval today (`bootstrap_replicate_summary` has no caller outside
    its own tests), so no real run can put the two shapes in the same
    table at all — the display logic is still what needs proving, the
    same reasoning `test_batch_progress_display_tracks_mean_generation_
    not_replicate_high_water` above applies to its own synthetic calls.
    The positive case is *also* covered against a real batch, end to end, in
    `test_batch_results_screen.py`.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.onBatchProgress({"
            "replicateCount: 2, reportedReplicateCount: 2, panels: [], "
            "meanReportedGeneration: 1, maxGenerations: 10, "
            "demeCount: 2, statistics: {"
            "D: {mean: '0.0588333', low: '0.0156696', high: '0.101997', "
            "sampleCount: 2, halfWidth: '0.0431637', sampleStd: '0.0347684'}, "
            "G_ST: {mean: '0.0549477', low: '0.0323088', high: '0.0845805', "
            "sampleCount: 2}"
            "}, initialStatistics: {}});"
        ),
        # `STATISTIC_NAMES`' own order puts `D` first and `G_ST` second
        # (`webui/screens/run-view-completed.js`), and `renderBatchSummary`
        # appends one row per name in that order.
        read=(
            "Array.from("
            "document.querySelectorAll('#batch-results-summary-body tr')"
            ").slice(0, 2).map(function (row) { return row.title; })"
        ),
        is_ready=lambda value: bool(value) and all(value),
    )

    # Every statistics tooltip, on Results and Explore alike, ends with
    # that statistic's own short gloss -- the *only* difference between
    # the two screens' tooltips is the confidence-interval clause here,
    # which Explore's theoretical predictions have nothing to put in.
    assert settled[0] == (
        "0.0588333 [0.0156696, 0.101997] — "
        "uncertainty across 2 independent replicates; "
        "half-width 0.0431637, equivalent sample standard deviation 0.0347684"
        " — allelic differentiation, weighting alleles by frequency (Jost's D, q=2)"
    )
    assert settled[1] == (
        "0.0549477 [0.0323088, 0.0845805] — uncertainty across 2 independent replicates"
        " — nearness to fixation (Nei's G_ST), not a measure of differentiation"
    )
