"""Headless functional tests for a loaded configuration's run settings.

Loading a configuration (here an example, through the real Examples
dialog) applies its run settings -- engine, replicates and the rest of
`config_form.DEFAULT_RUN_SETTING_FIELD_NAMES` -- to that run only, never
to saved Settings. The page then shows a notice listing each run
setting that differs from Settings, with "Make these my Settings"
(`screens/run-settings-notice.js`).

`test_app_api.py` proves the bridge half as plain Python calls; these
tests prove the page wires it: the notice and the parameter strip item,
the values the Run button actually submits, Settings left alone until
the button is pressed, and New configuration going back to Settings.

Every wait is on a ready flag or a settled counter the page sets
(`__fimExampleLoadSettled`, `__fimValidationPending`,
`__fimRunSettingsNoticeSettled`, `__fimRunViewReady`), polled with
`poll_or_fail`, so the outcome does not depend on how fast the machine
is.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import poll_or_fail

from fim.gui import app as app_module
from fim.gui import presets as presets_module
from fim.gui.app import Api, await_bridge_threads
from fim.gui.preferences import GuiPreferences, save_preferences
from fim.model.params import SimulationParams

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_EXAMPLE_ID = "unequal-island-sizes-with-a-migration-hub"
_REFUSED = "captured for this test, not run"

_OPEN_DIALOG = (
    "window.fim.showConfigureScreen().then(() => "
    "document.getElementById('configure-examples-button').click());"
)

# Reads both notice copies, the strip item, and what the form submits.
_READ_STATE = (
    "(function(){"
    "function notice(id){"
    "var element = document.getElementById(id);"
    "return {hidden: element.hidden, "
    "summary: element.querySelector('.run-settings-notice-summary').textContent, "
    "rows: Array.from(element.querySelectorAll('tbody tr')).map(function(row){"
    "return [row.dataset.field].concat(Array.from(row.children).map("
    "function(cell){return cell.textContent;}));})};}"
    "var values = collectFormValues();"
    "return {"
    "configure: notice('configure-run-settings-notice'), "
    "runCard: notice('run-run-settings-notice'), "
    "stripHidden: document.getElementById('parameter-strip-run-settings').hidden, "
    "stripText: document.getElementById("
    "'parameter-strip-run-settings-value').textContent, "
    "engine: values.engine_backend === undefined ? null : values.engine_backend, "
    "replicates: values.n_replicates === undefined ? null : values.n_replicates"
    "};"
    "})()"
)


def _settings_on_disk(preferences_path: Path) -> dict[str, str]:
    """Saved Settings, read from the preferences file as a fresh launch would."""
    return Api(preferences_path=preferences_path).get_default_run_settings()


def _auto_200_settings(preferences_path: Path) -> None:
    """Save Settings of the automatic engine and 200 replicates."""
    save_preferences(
        preferences_path,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={"engine_backend": "auto", "n_replicates": "200"},
        ),
    )


def _drive(window: webview.Window, steps: Callable[[Callable[..., Any]], Any]) -> Any:
    """Run `steps` against a ready window, handing it a `wait(script, what)` helper.

    `wait` polls a plain JS boolean until it is true, failing at the
    completion backstop. The result comes back through a queue, since
    the test's own thread cannot touch the window.
    """
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def wait(script: str, what: str) -> None:
        poll_or_fail(
            lambda: window.evaluate_js(script),
            lambda value: value is True,
            what,
            interval=_POLL_INTERVAL_SECONDS,
        )

    def run() -> None:
        try:
            wait("window.__fimRunViewReady === true", "the run view to be ready")
            outcome.put(steps(wait))
        except AssertionError as error:
            outcome.put(error)
        finally:
            await_bridge_threads()
            window.destroy()

    webview.start(run)
    result = outcome.get(timeout=10.0)
    if isinstance(result, AssertionError):
        raise result
    return result


def _load_example(window: webview.Window, wait: Callable[..., Any]) -> None:
    """Load `_EXAMPLE_ID` through the Examples dialog, and wait for it to settle."""
    window.evaluate_js(_OPEN_DIALOG)
    wait("window.__fimExamplesDialogReady === true", "the Examples dialog")
    example = presets_module.get_example(app_module._webui_directory(), _EXAMPLE_ID)
    assert example is not None
    class_id = example.class_id
    window.evaluate_js(
        "window.__fimExampleLoadSettled = false;"
        "document.querySelector("
        f"'#examples-class-tree [data-class-id=\"{class_id}\"]').click();"
        "document.querySelector("
        f"'#examples-list [data-example-id=\"{_EXAMPLE_ID}\"]').click();"
        "document.getElementById('examples-load-button').click();"
    )
    wait(
        "window.__fimExampleLoadSettled === true "
        "&& (window.__fimValidationPending || 0) === 0",
        "the example to load",
    )


def test_loading_a_lineal_example_runs_it_without_changing_settings(
    _isolate_gui_preferences: Path,
    window: webview.Window,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Notice lists the differences; the run uses the example; Settings stay.

    Settings are the automatic engine with 200 replicates; the example
    is lineal with one replicate. The run itself is intercepted where a
    scalar run would start (`Api._start_scalar_run`), so the test sees
    the exact parameters the Run button produced without running them.
    """
    _auto_200_settings(_isolate_gui_preferences)
    captured: list[SimulationParams] = []

    def capture(
        self: Api, params: SimulationParams, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        """Record the run's parameters and decline to start it."""
        captured.append(params)
        return {"ok": False, "message": _REFUSED}

    monkeypatch.setattr(Api, "_start_scalar_run", capture)

    def steps(wait: Callable[..., Any]) -> dict[str, Any]:
        _load_example(window, wait)
        loaded = window.evaluate_js(_READ_STATE)
        settings_after_load = _settings_on_disk(_isolate_gui_preferences)
        # The Settings dialog shows the saved Settings, not the example's.
        window.evaluate_js("document.getElementById('settings-button').click();")
        wait(
            "document.getElementById('settings-n_replicates').value !== ''",
            "the Settings dialog to fill",
        )
        dialog = window.evaluate_js(
            "({engine: document.getElementById('settings-engine_backend').value, "
            "replicates: document.getElementById('settings-n_replicates').value})"
        )
        window.evaluate_js("document.getElementById('modal-settings').close();")
        # Run: the run card's notice shows before the run starts.
        window.evaluate_js("window.fim.showScreen('screen-run');")
        on_run_card = window.evaluate_js(_READ_STATE)
        window.evaluate_js("document.getElementById('run-button').click();")
        wait(
            f"document.getElementById('run-banner').textContent.includes({_REFUSED!r})",
            "the intercepted run to report back",
        )
        # "Make these my Settings".
        window.evaluate_js(
            "window.__fimRunSettingsNoticeSettled = false;"
            "document.querySelector('#configure-run-settings-notice "
            "[data-run-settings-adopt]').click();"
        )
        wait(
            "window.__fimRunSettingsNoticeSettled === true",
            "Make these my Settings to finish",
        )
        adopted = window.evaluate_js(_READ_STATE)
        return {
            "loaded": loaded,
            "settingsAfterLoad": settings_after_load,
            "dialog": dialog,
            "onRunCard": on_run_card,
            "adopted": adopted,
            "settingsAfterAdopt": _settings_on_disk(_isolate_gui_preferences),
        }

    result = _drive(window, steps)

    loaded = result["loaded"]
    assert loaded["configure"]["hidden"] is False
    assert "your Settings have not changed" in loaded["configure"]["summary"]
    assert loaded["configure"]["rows"] == [
        ["engine_backend", "Execution engine", "lineal", "auto"],
        ["n_replicates", "Number of replicates", "1", "200"],
    ]
    assert loaded["stripHidden"] is False
    assert loaded["stripText"] == "this run's own (2 differ)"
    assert (loaded["engine"], loaded["replicates"]) == ("lineal", "1")
    # Settings untouched by the load, on disk and in the dialog.
    settings = result["settingsAfterLoad"]
    assert (settings["engine_backend"], settings["n_replicates"]) == ("auto", "200")
    assert result["dialog"] == {"engine": "auto", "replicates": "200"}
    assert result["onRunCard"]["runCard"]["hidden"] is False
    assert result["onRunCard"]["runCard"]["rows"] == loaded["configure"]["rows"]
    # The run used the example's own run settings.
    (params,) = captured
    assert (params.engine_backend, params.n_replicates) == ("lineal", 1)
    # The button made them Settings, and the strip item went away.
    adopted = result["adopted"]
    assert adopted["configure"]["hidden"] is False
    assert adopted["configure"]["summary"].startswith("Done.")
    assert adopted["configure"]["rows"] == []
    assert adopted["stripHidden"] is True
    after = result["settingsAfterAdopt"]
    assert (after["engine_backend"], after["n_replicates"]) == ("lineal", "1")


def test_new_configuration_after_a_load_uses_settings_again(
    _isolate_gui_preferences: Path, window: webview.Window
) -> None:
    """New configuration drops the example's run settings and the notice."""
    _auto_200_settings(_isolate_gui_preferences)

    def steps(wait: Callable[..., Any]) -> dict[str, Any]:
        _load_example(window, wait)
        loaded = window.evaluate_js(_READ_STATE)
        window.evaluate_js(
            "window.__fimRunViewReady = false; window.fim.menu.newConfiguration();"
        )
        wait(
            "window.__fimRunViewReady === true "
            "&& (window.__fimValidationPending || 0) === 0",
            "New configuration to settle",
        )
        return {"loaded": loaded, "fresh": window.evaluate_js(_READ_STATE)}

    result = _drive(window, steps)

    assert result["loaded"]["engine"] == "lineal"
    fresh = result["fresh"]
    assert fresh["configure"]["hidden"] is True
    assert fresh["runCard"]["hidden"] is True
    assert fresh["stripHidden"] is True
    # No per-run values: the server fills Settings' own in.
    assert (fresh["engine"], fresh["replicates"]) == (None, None)
    settings = _settings_on_disk(_isolate_gui_preferences)
    assert (settings["engine_backend"], settings["n_replicates"]) == ("auto", "200")


def test_a_dismissed_notice_comes_back_from_the_parameter_strip(
    _isolate_gui_preferences: Path, window: webview.Window
) -> None:
    """Dismiss hides the notice; the strip item stays and brings it back."""
    _auto_200_settings(_isolate_gui_preferences)

    def steps(wait: Callable[..., Any]) -> dict[str, Any]:
        _load_example(window, wait)
        window.evaluate_js(
            "document.querySelector('#configure-run-settings-notice "
            "[data-run-settings-dismiss]').click();"
        )
        dismissed = window.evaluate_js(_READ_STATE)
        window.evaluate_js(
            "document.getElementById('parameter-strip-run-settings').click();"
        )
        return {"dismissed": dismissed, "back": window.evaluate_js(_READ_STATE)}

    result = _drive(window, steps)

    assert result["dismissed"]["configure"]["hidden"] is True
    assert result["dismissed"]["stripHidden"] is False
    # Dismissing changes nothing about the run itself.
    assert result["dismissed"]["engine"] == "lineal"
    assert result["back"]["configure"]["hidden"] is False
