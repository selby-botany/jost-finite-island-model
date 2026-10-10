"""Headless functional tests for the Settings dialog's own execution-
default fields and significant-digits field (botanist GUI design doc
`20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.2/§11.2/§12,
extended on a real, reported request to also hold execution engine,
`n_replicates`, `max_generations`, the convergence-loop timing pair,
`confidence`, `jit`, `auto_vector_min_d`, `auto_vector_max_
capacity`, `max_workers`, and `max_concurrent_replicates` as global
defaults -- `index.html`'s own comment above `#modal-settings` has the
full account). `convergence_statistic`/`convergence_combinator`
deliberately have no Settings-side copy at all -- experimental, per-run
choices judged to have no sensible system-wide default -- so this file
carries no coverage for either.

Real DOM-driven proof that `webui/screens/settings.js` actually seeds,
collects, and saves these fields through the real `Api.get_default_run_
settings`/`set_default_run_settings` bridge methods -- `test/gui/
test_app_api.py`/`test_preferences.py`/`test_config_form.py` already
prove the Python side (storage, overlay, validation) is correct as
plain calls; these tests prove the page's own JavaScript actually wires
them at the right moments, which no Python-only test can check.

Dark-mode-override's own "applies the theme live" coverage stays in
`test_dark_mode_screen.py` (a genuinely different concern -- this file
only proves seed/collect/save/validate, not the immediate-apply side
effect); "at startup" stays covered by `test_nav_rail.py`'s own
pre-existing `test_settings_dialog_controls_startup_behavior`, unaffected
by this dialog's growth. `test_field_help.py` covers every field's own
tooltip presence statically; this file does not re-check that
DOM-side.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import create_window
from fim.gui.config_form import starter_form_values
from fim.gui.preferences import GuiPreferences, save_preferences

from .conftest import AWAIT_SETTINGS_SAVES, poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def _drive(
    window: webview.Window,
    steps: Callable[[Callable[[str, Callable[[Any], bool]], Any]], Any],
) -> Any:
    """Run `steps` against a real, ready `window` (`test_nav_rail.py`'s own pattern)."""
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            outcome.put(steps(partial(poll_page, window)))
        finally:
            window.destroy()

    webview.start(_run)
    return outcome.get(timeout=10.0)


def test_significant_digits_field_loads_and_changes_the_real_value(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The Settings field round-trips through the real bridge.

    Not a `SimulationParams` field (`settings-significant_digits`
    carries no `name`/`form="input-form"`), so its own coverage lives
    here rather than in `config_form`'s tests: `wireSignificantDigits
    Field` (`screens/settings.js`) seeds the select from `Api.get_
    significant_digits` on load -- wired at module load, not gated
    behind the dialog opening, so this needs no `settings-button` click
    first -- and a `change` event calls `fim.menu.setSignificantDigits`,
    confirmed by reading the value back through a second `Api` call on
    the very same window.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.__fimSignificantDigitsResult = null; "
            "(async () => { "
            "document.getElementById('settings-significant_digits').value = '6'; "
            "document.getElementById('settings-significant_digits')"
            ".dispatchEvent(new Event('change', {bubbles: true})); "
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSignificantDigitsResult = "
            "await window.pywebview.api.get_significant_digits(); "
            "})();"
        ),
        read="window.__fimSignificantDigitsResult",
        is_ready=lambda value: value is not None,
    )

    assert settled == 6


def test_settings_dialog_seeds_execution_and_convergence_defaults_from_starter(
    window: webview.Window,
) -> None:
    """With nothing saved, opening Settings shows the true starter values."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "({"
            "engineBackend: document.getElementById('settings-engine_backend').value, "
            "nReplicates: document.getElementById('settings-n_replicates').value, "
            "maxGenerations: document.getElementById('settings-max_generations')"
            ".value, "
            "replicateConfidence: document.getElementById("
            "'settings-confidence').value, "
            "jit: document.getElementById('settings-jit').value, "
            "autoVectorMinD: document.getElementById('settings-auto_vector_min_d')"
            ".value"
            "})",
            lambda value: value is not None and value["nReplicates"] != "",
        )

    result = _drive(window, steps)

    starter = starter_form_values()
    assert result["engineBackend"] == starter["engine_backend"]
    assert result["nReplicates"] == starter["n_replicates"]
    assert result["maxGenerations"] == starter["max_generations"]
    assert result["replicateConfidence"] == starter["confidence"]
    assert result["jit"] == starter["jit"]
    assert result["autoVectorMinD"] == starter["auto_vector_min_d"]


def test_settings_dialog_seeds_execution_and_convergence_defaults_from_saved(
    _isolate_gui_preferences: Path,
) -> None:
    """A saved default is shown instead of the true starter values on open."""
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            default_run_settings={
                "engine_backend": "generational",
                "n_replicates": "16",
                "precision": "0.02",
            }
        ),
    )
    own_window = create_window(hidden=True)

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        own_window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "({"
            "engineBackend: document.getElementById('settings-engine_backend').value, "
            "nReplicates: document.getElementById('settings-n_replicates').value"
            "})",
            lambda value: value is not None and value["nReplicates"] != "",
        )

    result = _drive(own_window, steps)

    assert result["engineBackend"] == "generational"
    assert result["nReplicates"] == "16"


def test_settings_save_button_persists_execution_and_convergence_defaults(
    window: webview.Window,
) -> None:
    """Changing a field and clicking Save is reflected back by the bridge itself.

    The trigger reads back only once the save has landed
    (`AWAIT_SETTINGS_SAVES`, on `window.__fimSettingsSavesPending`):
    `Save`'s own `click` handler is `async` (collects the 11 fields,
    awaits a real `set_default_run_settings` bridge round trip, then
    updates the banner and closes the dialog), so a single fixed sleep
    before reading back raced that round trip under real parallel-test
    load and failed intermittently. A bounded retry of the read-back
    (2.5 s) replaced it next, which still gave up silently on a slower
    machine; waiting on the save's own completion signal does not
    depend on how long the bridge call takes.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js(
            "document.getElementById('settings-engine_backend').value "
            "= 'generational';"
            "document.getElementById('settings-n_replicates').value = '16';"
            "window.__fimSettingsSaveResult = null;"
            "document.getElementById('settings-save-button').click();"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSettingsSaveResult = "
            "await window.pywebview.api.get_default_run_settings();"
            "})();"
        )
        return poll_until(
            "window.__fimSettingsSaveResult",
            lambda value: value is not None,
        )

    result = _drive(window, steps)

    assert result["engine_backend"] == "generational"
    assert result["n_replicates"] == "16"


def test_default_ploidy_select_persists_immediately_and_reloads_on_open(
    window: webview.Window,
) -> None:
    """Changing "Default ploidy" saves at once (no Save button) and reopens as set.

    Like the startup-behavior and re-run seed selects beside it, and
    unlike the execution defaults, it persists on change: it seeds only a
    fresh configuration's ploidy, so there is no batch of fields to
    validate together.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js(
            "const select = document.getElementById('settings-default-ploidy');"
            "select.value = '3';"
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
            "window.__fimSaved = null;"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSaved = await window.pywebview.api.get_default_ploidy();"
            "})();"
        )
        return poll_until("window.__fimSaved", lambda value: value is not None)

    assert _drive(window, steps) == "3"


def test_run_card_columns_and_scatter_style_persist_on_change_and_apply_live(
    window: webview.Window,
) -> None:
    """The Run card's columns and scatter style save at once and take effect now.

    Like the other one-value selects in Settings (no Save button): each
    saves on change through its own bridge call and applies to the card
    immediately, so the botanist sees the effect without reopening
    anything.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js(
            "for (const [id, value] of [['settings-run-graph-columns', '3'], "
            "['settings-scatter-style', 'density']]) {"
            "const select = document.getElementById(id);"
            "select.value = value;"
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
            "}"
            "window.__fimSaved = null;"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSaved = await window.pywebview.api.get_run_card_layout();"
            "})();"
        )
        return poll_until("window.__fimSaved", lambda value: value is not None)

    saved = _drive(window, steps)

    assert saved["columns"] == 3
    assert saved["scatterStyle"] == "density"


def test_settings_save_button_shows_the_banner_on_an_invalid_value(
    window: webview.Window,
) -> None:
    """An unparseable value is rejected, surfaced in the banner, not silently saved."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js(
            "document.getElementById('settings-n_replicates').value = 'not a number';"
            "document.getElementById('settings-save-button').click();"
        )
        banner = poll_until(
            "({"
            "hidden: document.getElementById('settings-banner').hidden, "
            "text: document.getElementById('settings-banner').textContent"
            "})",
            lambda value: value is not None and value["hidden"] is False,
        )
        # An invalid value must not also dismiss the dialog -- only a
        # successful save does (`test_settings_save_button_closes_the_
        # dialog_on_success`, below); an invalid save leaving the dialog
        # open is what lets the user see and fix the banner's own
        # message. Read before `_drive`'s own teardown destroys the
        # window, not after.
        banner["dialogOpen"] = window.evaluate_js(
            "document.getElementById('modal-settings').open"
        )
        return banner

    result = _drive(window, steps)

    assert result["hidden"] is False
    assert "n_replicates" in result["text"]
    assert result["dialogOpen"] is True


def test_settings_save_button_closes_the_dialog_on_success(
    window: webview.Window,
) -> None:
    """A successful Save dismisses the dialog -- a real, reported bug, now fixed.

    Save used to persist the change but leave the dialog open,
    indistinguishable at a glance from a save that silently failed.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        window.evaluate_js("document.getElementById('settings-save-button').click();")
        return poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is False,
        )

    result = _drive(window, steps)

    assert result is False


def test_settings_engine_backend_visibility_reveals_jit_and_auto_vector_fields(
    window: webview.Window,
) -> None:
    """`jit`/`auto_vector_*` show only for the one execution engine each tunes.

    "Where apropos" (a real, reported request): `jit` is a real,
    user-facing choice only under `generational` (`lineal` never accepts
    anything but off; `generational-vector` always uses numba
    regardless); `auto_vector_min_d`/`auto_vector_max_capacity` only
    affect the `auto` engine's own threshold, so both are shown or
    hidden together as one group.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )

        def _set_backend(value: str) -> Any:
            window.evaluate_js(
                f"document.getElementById('settings-engine_backend').value "
                f"= '{value}';"
                "document.getElementById('settings-engine_backend')"
                ".dispatchEvent(new Event('change', {bubbles: true}));"
            )
            return poll_until(
                "({"
                "jitHidden: document.getElementById('settings-jit-field').hidden, "
                "autoVectorHidden: document.getElementById("
                "'settings-auto-vector-fields').hidden"
                "})",
                lambda value: value is not None,
            )

        return {
            "generational": _set_backend("generational"),
            "auto": _set_backend("auto"),
            "lineal": _set_backend("lineal"),
        }

    result = _drive(window, steps)

    assert result["generational"] == {"jitHidden": False, "autoVectorHidden": True}
    assert result["auto"] == {"jitHidden": True, "autoVectorHidden": False}
    assert result["lineal"] == {"jitHidden": True, "autoVectorHidden": True}


def test_engine_backend_selector_lists_all_four_options_recommendation_first(
    window: webview.Window,
) -> None:
    """The execution-engine `<select>` shows four options, `auto` labeled recommended.

    Relocated from `test_input_screen.py` (`2026-09-16` revision moved
    this field into Settings entirely -- Configure's own former
    `#field-engine_backend` copy no longer exists). Approach B3's own
    shape, proven against the real rendered DOM rather than the markup
    source: all four legal `SimulationParams.engine_backend` values are
    present so none can ever be silently downgraded on save, but
    `lineal` and `auto` come first and `auto` carries the "recommended"
    wording -- the two-real-choices emphasis the design asked for,
    expressed through order and labeling rather than by withholding
    values.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "Array.from("
            "document.querySelectorAll('#settings-engine_backend option')"
            ").map(function (option) "
            "{ return [option.value, option.textContent]; })",
            lambda value: bool(value) and len(value) == 4,
        )

    settled = _drive(window, steps)

    assert [value for value, _ in settled] == [
        "lineal",
        "auto",
        "generational",
        "generational-vector",
    ]
    assert "reference" in settled[0][1]
    assert "recommended" in settled[1][1]


def test_engine_backend_selector_accepts_every_legal_value(
    window: webview.Window,
) -> None:
    """Each of the four values can actually be set on the live `<select>`.

    Relocated from `test_input_screen.py` (`2026-09-16` revision). The
    browser-level half of `test_config_form.py`'s own round-trip test:
    assigning a value with no matching `<option>` leaves a `<select>`
    reading back the empty string rather than raising, so a missing
    option is exactly the silent, unobservable downgrade approach B1
    was rejected over. Reading each assignment straight back out of the
    real DOM is what makes that observable.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "['lineal', 'auto', 'generational', 'generational-vector']"
            ".map(function (candidate) {"
            "var select = document.getElementById('settings-engine_backend');"
            "select.value = candidate;"
            "return select.value;"
            "})",
            lambda value: bool(value) and len(value) == 4,
        )

    settled = _drive(window, steps)

    assert settled == ["lineal", "auto", "generational", "generational-vector"]


def test_engine_backend_options_are_relabeled_without_numba(
    window: webview.Window,
) -> None:
    """Without numba, `auto`/`generational-vector` say so; the other two are untouched.

    Relocated from `test_input_screen.py` (`2026-09-16` revision).
    Approach A3, driven through the real page: `applyEngineBackend
    Availability` is re-run against a stubbed bridge reporting no numba,
    rather than uninstalling the dependency, and the labels are read
    back out of the live DOM. Values are deliberately left alone -- every
    legal value must stay selectable for approach B3's own round trip,
    so the honesty lives in the label, not in a disabled or removed
    option.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(
            "window.pywebview.api.get_engine_backend_availability = "
            "function () { return Promise.resolve({numba: false}); };"
            "window.fim.applyEngineBackendAvailability();"
        )
        return poll_until(
            "Array.from("
            "document.querySelectorAll('#settings-engine_backend option')"
            ").map(function (option) "
            "{ return [option.value, option.textContent]; })",
            lambda value: bool(value) and "numba" in value[1][1],
        )

    settled = _drive(window, steps)

    labels = dict(settled)
    assert labels["auto"].endswith(" — needs numba; install fim[jit]")
    assert labels["generational-vector"].endswith(" — needs numba; install fim[jit]")
    assert "numba" not in labels["lineal"]
    assert "numba" not in labels["generational"]


def test_engine_backend_selector_defaults_to_auto(window: webview.Window) -> None:
    """An untouched selector sits on `auto`, this dialog's own recommended choice.

    Relocated from `test_input_screen.py` (`2026-09-16` revision). The
    `selected` attribute is what a user who never opens this field would
    see for the instant before `loadSettingsDialog` applies a real,
    saved (or starter) value over the markup -- checked here in
    isolation (resetting `selectedIndex` back to `defaultSelected`
    first) precisely because `starter_form_values()`'s own value could
    in principle drift from it, the same class of markup/value
    inconsistency `test_input_screen.py`'s own history already found
    once for Configure's former copy of this field.
    """

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "document.getElementById('settings-engine_backend').selectedIndex "
            "= -1; "
            "document.getElementById('settings-engine_backend').selectedIndex = "
            "Array.from("
            "document.querySelectorAll('#settings-engine_backend option')"
            ").findIndex(function (option) { return option.defaultSelected; }); "
            "document.getElementById('settings-engine_backend').value",
            bool,
        )

    selected = _drive(window, steps)

    assert selected == "auto"


# Every Settings section (`<fieldset>`) whose explanatory hint is followed
# by more content, with the measured gap, in CSS pixels, between that hint's
# bottom and the top of what follows it; plus the reference gap the dialog's
# top section leaves between "At startup"'s hint and "Default ploidy".
_SETTINGS_SPACING = """
(() => {
    const dialog = document.getElementById('modal-settings');
    if (!dialog.open) { return null; }
    const startupHint = document.getElementById('settings-startup-behavior')
        .closest('.field').querySelector('.hint');
    const ploidyField = document.getElementById('settings-default-ploidy')
        .closest('.field');
    const sections = Array.from(dialog.querySelectorAll('fieldset'))
        .map((fieldset) => {
            const hint = fieldset.querySelector(':scope > .hint');
            // The first following element actually laid out: a field
            // hidden for the current engine (`JIT`) takes no space.
            let next = hint ? hint.nextElementSibling : null;
            while (next && next.getClientRects().length === 0) {
                next = next.nextElementSibling;
            }
            if (!hint || !next) { return null; }
            return {
                title: fieldset.querySelector('legend').textContent,
                gap: next.getBoundingClientRect().top
                    - hint.getBoundingClientRect().bottom,
                marginBottom: getComputedStyle(hint).marginBottom,
            };
        })
        .filter((section) => section !== null);
    return {
        reference: ploidyField.getBoundingClientRect().top
            - startupHint.getBoundingClientRect().bottom,
        fieldMarginBottom: getComputedStyle(ploidyField).marginBottom,
        sections,
    };
})()
"""


def test_every_settings_section_separates_its_hint_from_its_first_field(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Each section's explanatory text sits as far above its first label as
    the top section's hint sits above "Default ploidy".

    One CSS rule (`#modal-settings fieldset > .hint`, sharing
    `--fim-field-spacing` with `.field`) gives every section the gap;
    before it, the hint ran straight into the first label.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.openStatisticsSettings();",
        read=_SETTINGS_SPACING,
    )

    assert settled["reference"] > 0
    titles = {section["title"] for section in settled["sections"]}
    assert {
        "Statistics shown",
        "Execution defaults",
        "Convergence",
        "Expert: engine tuning",
    } <= titles
    for section in settled["sections"]:
        assert section["marginBottom"] == settled["fieldMarginBottom"], section
        assert section["gap"] == pytest.approx(settled["reference"], abs=0.5), section


def test_the_convergence_section_is_titled_convergence(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The section holding the convergence burn-in and precision reads "Convergence"."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.openStatisticsSettings();",
        read=(
            "document.getElementById('modal-settings').open ? "
            "document.getElementById('settings-convergence_burn_in')"
            ".closest('fieldset').querySelector('legend').textContent : null"
        ),
    )

    assert settled == "Convergence"


_OPEN_SETTINGS = "document.getElementById('settings-button').click();"
_EXPERT_ROWS = (
    "document.querySelectorAll('#settings-expert-fields .expert-setting').length"
)


def test_the_expert_section_lists_every_setting_collapsed_with_its_default(
    window: webview.Window,
) -> None:
    """Settings builds a row per Expert Setting from the bridge, collapsed."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        return poll_until(
            "({"
            f"rows: {_EXPERT_ROWS}, "
            "open: document.getElementById('settings-expert-details').open, "
            "groups: Array.from(document.querySelectorAll("
            "'#settings-expert-fields h4'), (h) => h.textContent), "
            "batchWidth: document.getElementById("
            "'settings-expert_batch_width')?.value, "
            "note: document.getElementById("
            "'settings-expert-note_batch_width')?.textContent"
            "})",
            lambda value: value is not None and value["rows"] > 0,
        )

    result = _drive(window, steps)

    assert result["rows"] == 23
    assert result["open"] is False
    assert result["groups"] == ["Convergence", "Batches", "Statistics", "Storage"]
    assert result["batchWidth"] == "8"
    assert "default 8" in result["note"]
    assert "at least 1" in result["note"]


def test_a_changed_expert_value_is_marked_saved_and_resettable(
    window: webview.Window,
) -> None:
    """Editing marks the row; Save persists it; Reset restores the default."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        window.evaluate_js(
            "const input = document.getElementById('settings-expert_batch_width');"
            "input.value = '3';"
            "input.dispatchEvent(new Event('input', {bubbles: true}));"
        )
        marked = window.evaluate_js(
            "document.getElementById('settings-expert_batch_width')"
            ".closest('.field').classList.contains('expert-changed')"
        )
        window.evaluate_js(
            "window.__fimSettingsSaveResult = null;"
            "document.getElementById('settings-save-button').click();"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSettingsSaveResult = "
            "await window.pywebview.api.get_default_run_settings();"
            "})();"
        )
        saved = poll_until(
            "window.__fimSettingsSaveResult", lambda value: value is not None
        )
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        window.evaluate_js(
            "document.querySelector("
            "'#settings-expert_batch_width ~ .expert-reset').click();"
        )
        after_reset = window.evaluate_js(
            "({value: document.getElementById('settings-expert_batch_width').value, "
            "changed: document.getElementById('settings-expert_batch_width')"
            ".closest('.field').classList.contains('expert-changed')})"
        )
        return {"marked": marked, "saved": saved, "after_reset": after_reset}

    result = _drive(window, steps)

    assert result["marked"] is True
    assert result["saved"]["expert_batch_width"] == "3"
    assert result["after_reset"] == {"value": "8", "changed": False}


def test_saving_an_invalid_expert_value_shows_the_banner_by_name(
    window: webview.Window,
) -> None:
    """A bad Expert value is refused by name, and nothing is saved."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        window.evaluate_js(
            "document.getElementById('settings-expert_check_growth').value = '1';"
            "document.getElementById('settings-save-button').click();"
        )
        return poll_until(
            "document.getElementById('settings-banner').textContent",
            bool,
        )

    banner = _drive(window, steps)

    assert "check_growth" in banner


def test_reset_all_restores_every_default(window: webview.Window) -> None:
    """The section's Reset all puts every input back at its default."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        window.evaluate_js(
            "for (const input of document.querySelectorAll("
            "'#settings-expert-fields input')) { input.value = '7'; }"
            "document.getElementById('settings-expert-reset-all').click();"
        )
        return window.evaluate_js(
            "Array.from(document.querySelectorAll('#settings-expert-fields input'),"
            " (input) => input.value === input.dataset.default).every(Boolean)"
        )

    assert _drive(window, steps) is True


def test_settings_holds_the_estimate_method_and_window_defaults(
    window: webview.Window,
) -> None:
    """Estimate, precision method and replicate window seed, save and reload."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        seeded = window.evaluate_js(
            "({"
            "estimate: document.getElementById('settings-convergence_estimate').value, "
            "method: document.getElementById('settings-precision_method').value, "
            "window: document.getElementById("
            "'settings-replicate_averaging_window').value"
            "})"
        )
        window.evaluate_js(
            "document.getElementById('settings-convergence_estimate').value = 'auto';"
            "document.getElementById('settings-precision_method').value = "
            "'planned_replicates';"
            "document.getElementById('settings-replicate_averaging_window').value = "
            "'1200';"
            "window.__fimSettingsSaveResult = null;"
            "document.getElementById('settings-save-button').click();"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSettingsSaveResult = "
            "await window.pywebview.api.get_default_run_settings();"
            "})();"
        )
        saved = poll_until(
            "window.__fimSettingsSaveResult", lambda value: value is not None
        )
        return {"seeded": seeded, "saved": saved}

    result = _drive(window, steps)

    assert result["seeded"] == {
        "estimate": "mean_of_values",
        "method": "interval",
        "window": "auto",
    }
    assert result["saved"]["convergence_estimate"] == "auto"
    assert result["saved"]["precision_method"] == "planned_replicates"
    assert result["saved"]["replicate_averaging_window"] == "1200"


def test_settings_holds_the_storage_defaults(window: webview.Window) -> None:
    """Retention, stride and thinning start seed, save and reload."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_SETTINGS)
        poll_until("window.__fimSettingsLoaded === true", lambda value: value is True)
        seeded = window.evaluate_js(
            "({"
            "retention: document.getElementById("
            "'settings-trajectory_retention').value, "
            "stride: document.getElementById('settings-trajectory_stride').value, "
            "start: document.getElementById("
            "'settings-trajectory_thinning_start').value"
            "})"
        )
        window.evaluate_js(
            "document.getElementById('settings-trajectory_retention').value = "
            "'thinned';"
            "document.getElementById('settings-trajectory_stride').value = '20';"
            "document.getElementById('settings-trajectory_thinning_start').value = "
            "'5000';"
            "window.__fimSettingsSaveResult = null;"
            "document.getElementById('settings-save-button').click();"
            "(async () => {"
            + AWAIT_SETTINGS_SAVES
            + "window.__fimSettingsSaveResult = "
            "await window.pywebview.api.get_default_run_settings();"
            "})();"
        )
        saved = poll_until(
            "window.__fimSettingsSaveResult", lambda value: value is not None
        )
        return {"seeded": seeded, "saved": saved}

    result = _drive(window, steps)

    assert result["seeded"] == {"retention": "full", "stride": "10", "start": "auto"}
    assert result["saved"]["trajectory_retention"] == "thinned"
    assert result["saved"]["trajectory_stride"] == "20"
    assert result["saved"]["trajectory_thinning_start"] == "5000"
