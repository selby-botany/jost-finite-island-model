"""Headless functional tests for the Settings dialog's own execution/
convergence-selection defaults and significant-digits field (botanist
GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
§4.2/§11.2/§12, extended on a real, reported request to also hold
execution engine/`n_replicates`/the convergence-selection group as
global defaults -- `index.html`'s own comment above `#modal-settings`
has the full account).

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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import create_window
from fim.gui.config_form import starter_form_values
from fim.gui.preferences import GuiPreferences, save_preferences

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1


def _drive(
    window: webview.Window,
    steps: Callable[[Callable[[str, Callable[[Any], bool]], Any]], Any],
) -> Any:
    """Run `steps` against a real, ready `window` (`test_nav_rail.py`'s own pattern)."""
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
            "await new Promise((resolve) => setTimeout(resolve, 50)); "
            "window.__fimSignificantDigitsResult = "
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
            "csD: document.getElementById('settings-cs_D').checked"
            "})",
            lambda value: value is not None and value["nReplicates"] != "",
        )

    result = _drive(window, steps)

    starter = starter_form_values()
    assert result["engineBackend"] == starter["engine_backend"]
    assert result["nReplicates"] == starter["n_replicates"]
    assert result["csD"] is True


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
                "convergence_combinator": "all",
                "convergence_window": "10",
                "convergence_tolerance": "0.02",
                "cs_D": "true",
                "cs_G_ST": "false",
                "cs_E_ST": "false",
                "cs_K_ST": "false",
                "cs_H_S": "false",
                "cs_H_T": "false",
                "cs_A_CGD": "false",
                "cs_Delta": "false",
                "cs_MI": "false",
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

    The trigger script itself retries the readback (bounded, up to 2.5s)
    rather than trusting one fixed delay before reading back: `Save`'s
    own `click` handler is `async` (collects the 14 fields, awaits a
    real `set_default_run_settings` bridge round trip, then updates the
    banner), so a single fixed sleep before reading back raced that
    round trip under real parallel-test load and failed intermittently
    -- exactly the non-deterministic-test defect this project's own
    testing discipline forbids tolerating. Polling until the readback
    actually reflects the just-saved value converges to the same
    correct result regardless of how long the real bridge call takes,
    rather than gambling that a guessed delay was enough.
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
            "for (let i = 0; i < 50; i++) {"
            "await new Promise((resolve) => setTimeout(resolve, 50));"
            "const current = await window.pywebview.api.get_default_run_settings();"
            "if (current.engine_backend === 'generational') {"
            "window.__fimSettingsSaveResult = current;"
            "return;"
            "}"
            "}"
            "})();"
        )
        return poll_until(
            "window.__fimSettingsSaveResult",
            lambda value: value is not None,
        )

    result = _drive(window, steps)

    assert result["engine_backend"] == "generational"
    assert result["n_replicates"] == "16"


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
        return poll_until(
            "({"
            "hidden: document.getElementById('settings-banner').hidden, "
            "text: document.getElementById('settings-banner').textContent"
            "})",
            lambda value: value is not None and value["hidden"] is False,
        )

    result = _drive(window, steps)

    assert result["hidden"] is False
    assert "n_replicates" in result["text"]


def test_settings_checking_a_second_convergence_statistic_reveals_the_combinator(
    window: webview.Window,
) -> None:
    """Settings' own combinator field follows the identical rule Configure's does."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js("document.getElementById('settings-button').click();")
        poll_until(
            "document.getElementById('modal-settings').open",
            lambda value: value is True,
        )
        before = window.evaluate_js(
            "document.getElementById('settings-combinator-field').hidden"
        )
        window.evaluate_js(
            "document.getElementById('settings-cs_G_ST').checked = true;"
            "document.getElementById('settings-cs_G_ST')"
            ".dispatchEvent(new Event('change', {bubbles: true}));"
        )
        after = poll_until(
            "document.getElementById('settings-combinator-field').hidden",
            lambda value: value is False,
        )
        return {"before": before, "after": after}

    result = _drive(window, steps)

    assert result["before"] is True
    assert result["after"] is False
