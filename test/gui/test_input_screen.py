"""Headless functional tests for the unified run view's own configuration
side -- the Configure menu's modals/value-selectors and the always-
present controls (design doc §4.1, §6.4; unified-run-view design §3.1,
§8 Phase E).

Real DOM-driven proof that `webui/screens/config-modals.js`/`run-view-
controls.js`/`run-view-initial.js` actually wire the page correctly —
`test/gui/test_app_api.py` already proves the bridge methods themselves
are correct as plain Python calls; these tests prove the page's own
JavaScript calls them at the right moments and updates the right
elements, which no Python-only test can check.
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
    """Clicking "Run simulation" with an invalid Migration field opens that modal.

    Direct regression test for design §4.0 #2 ("every tab with an
    invalid field shows a small error dot... the disabled Run button
    always shows a one-line reason") — the modal-opening specifically
    (design §3.1/§8 Phase B: Migration is now a `<dialog>`, not a
    tab-panel), since `test_app_api.py` already proves the bridge's own
    `tab`/`field` values are correct. No `input` event needs dispatching
    first: `onRunClicked` calls `revalidate()` itself, which reads the
    field's *current* value straight off the live DOM via `FormData` —
    it does not depend on an `input` event ever having fired.
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
            "modalOpen: document.getElementById('modal-migration').open, "
            "dotHidden: document.getElementById('dot-migration').hidden"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("modalOpen") is True,
        poll_attempts=500,
    )

    assert settled["modalOpen"] is True
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


def test_menu_configure_tab_switches_tabs_without_resetting_the_form(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.configureTab` (the native Configure menu) opens a modal, no reset.

    Every section is now a `<dialog>`, not a tab-panel (design §3.1,
    §8 Phase A/B) — `test_configure_population_opens_a_modal_without_
    navigating_away` already proves the modal opens without navigating
    away; this test's own remaining job is the one behavioral contract
    that distinguishes `configureTab` from `newConfiguration`: an edited
    field survives the call, unlike a real reset.

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
            "modalOpen: document.getElementById('modal-migration').open"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("modalOpen") is True,
    )

    assert value["fieldN"] == "999999"


def test_every_configure_section_has_its_own_modal(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """All six sections open their own `modal-<name>` dialog (design §8 Phase B).

    Population and Migration each already have their own dedicated test
    above; this one instead sweeps all six in a single `drive()` call
    (native `<dialog>`s stack -- opening one does not close another),
    proving every `configureTab` name resolves to a real, distinct modal
    rather than checking only the two that happen to have other tests.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "["
            "'population', 'migration', 'mutation', "
            "'initial_conditions', 'convergence', 'batch'"
            "].forEach((name) => window.fim.menu.configureTab(name)); "
            "window.__fimAllModalsOpened = ["
            "'population', 'migration', 'mutation', "
            "'initial_conditions', 'convergence', 'batch'"
            "].every((name) => document.getElementById(`modal-${name}`).open);"
        ),
        read="window.__fimAllModalsOpened",
        is_ready=lambda value: value is not None,
    )

    assert settled is True


def test_menu_set_deme_weighting_updates_the_field_without_a_modal(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.setDemeWeighting` sets the field directly (design §3.1.3)."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.menu.setDemeWeighting('equal');",
        read=(
            "({"
            "value: document.getElementById('field-deme_weighting').value, "
            "modalOpen: document.getElementById('modal-population').open"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("value") == "equal",
    )

    assert settled["value"] == "equal"
    assert settled["modalOpen"] is False


def test_menu_set_mutation_model_updates_the_field_without_a_modal(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.setMutationModel` sets the field directly (design §3.1.3)."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.menu.setMutationModel('finite_alleles');",
        read="document.getElementById('field-mutation_model').value",
        is_ready=lambda value: value == "finite_alleles",
    )

    assert settled == "finite_alleles"


def test_menu_toggle_convergence_statistic_adds_to_the_set(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.toggleConvergenceStatistic` adds a statistic, not replaces it.

    The starter form has only `cs_D` checked. Toggling `cs_G_ST` on must
    leave `cs_D` checked too — an exclusive pick here would silently
    discard whatever combination was already configured (design §3.1.3,
    `app.py`'s own `_build_menu` docstring has the full reasoning) — and
    checking two statistics is exactly what makes the combinator field
    appear, proving `syncConditionalVisibility` ran as a side effect too.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.menu.toggleConvergenceStatistic('cs_G_ST');",
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


def test_configure_population_opens_a_modal_without_navigating_away(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Configure > Population floats a modal over the run view (design §3.1/§8 Phase A).

    The Phase A proof-of-concept this test exists for: Population is the
    first (of eventually six, §8 Phase B) tab-panel converted to a native
    `<dialog>`. Asserted against `runViewState` staying untouched, not
    just `screen-run` staying visible -- the bug this whole redesign
    responds to was the old `configureTab` calling `showScreen(
    "screen-input")` first, discarding whatever the user was looking at
    (a live run, a completed result); the merged run view (design §8
    Phase E) makes "which screen is visible" trivially true on its own
    (there is only one to navigate away from), so the state itself is
    the assertion that still has teeth.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "setTimeout(() => { window.fim.menu.configureTab('population'); }, 0);"
        ),
        read=(
            "({"
            "modalOpen: document.getElementById('modal-population').open, "
            "runViewHidden: document.getElementById('screen-run').hidden, "
            "runViewState: window.fim.getRunViewState()"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("modalOpen") is True,
    )

    assert settled["runViewHidden"] is False
    assert settled["runViewState"] == "initial"


def test_configure_population_modal_close_button_closes_it(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The modal's own close button closes it (design §3.1.1's backdrop/close wiring).

    Escape and backdrop-click are the browser's own native `<dialog>`
    behavior (not exercised here — a synthetic, untrusted `keydown` does
    not reliably trigger a real close-watcher in every engine); the
    explicit close button is this app's own code (`fim.wireModal`), and
    is what this test actually proves.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.menu.configureTab('population'); "
            "window.__fimModalWasOpened = "
            "document.getElementById('modal-population').open; "
            "setTimeout(() => { "
            "document.querySelector('#modal-population [data-modal-close]').click(); "
            "window.__fimModalCloseClicked = true; "
            "}, 50);"
        ),
        read=(
            "({"
            "opened: window.__fimModalWasOpened === true, "
            "closed: document.getElementById('modal-population').open === false, "
            "done: window.__fimModalCloseClicked === true"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("done") is True,
    )

    assert settled["opened"] is True
    assert settled["closed"] is True


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
