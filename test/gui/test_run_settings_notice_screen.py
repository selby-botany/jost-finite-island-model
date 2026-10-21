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

The example catalog is isolated from the shipped teaching examples, whose
engine policy and replicate counts can change independently of this contract.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml
from conftest import poll_or_fail

from fim.gui import app as app_module
from fim.gui import object_bridge
from fim.gui import presets as presets_module
from fim.gui.app import Api, await_bridge_threads
from fim.gui.preferences import GuiPreferences, save_preferences
from fim.model.params import SimulationParams

from .test_sweep_api import results  # noqa: F401

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_EXAMPLE_ID = "lineal-run-settings"
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


@pytest.fixture(autouse=True)
def _lineal_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a lineal, one-replicate example through the real catalog reader."""
    bundle = tmp_path / "examples"
    bundle.mkdir()
    catalog = {
        "schema_version": 1,
        "classes": [
            {
                "id": "getting-started",
                "title": "Getting started",
                "description": "",
                "children": [],
            }
        ],
        "examples": [
            {
                "id": _EXAMPLE_ID,
                "name": "Lineal run settings",
                "description": "",
                "class": "getting-started",
                "readme": "",
                "readme_excerpt": "",
                "config_yaml": (
                    "N: 20\nploidy: haploid\nd: 2\nm: 0.1\nmu: 0.01\n"
                    "seed: 5\nloci:\n  - locus_id: 1\n    length: 200\n"
                    "engine_backend: lineal\nn_replicates: 1\n"
                ),
                "outputs": [],
            }
        ],
    }
    (bundle / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(app_module, "_examples_bundle_directory", lambda: bundle)


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
    example = presets_module.find_example(app_module._examples_catalog(), _EXAMPLE_ID)
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


def _open_run_settings(window: webview.Window, wait: Callable[..., Any]) -> None:
    """Open the per-run editor and wait until effective values are visible."""
    window.evaluate_js(
        "window.__fimSettingsLoaded = false;"
        "document.getElementById('configure-run-settings-button').click();"
    )
    wait(
        "window.__fimSettingsLoaded === true "
        "&& document.getElementById('modal-settings').open",
        "the per-run editor",
    )


def _apply_run_settings(window: webview.Window, wait: Callable[..., Any]) -> None:
    """Apply the editor and wait for validation and rendering to finish."""
    window.evaluate_js(
        "window.__fimRunSettingsNoticeSettled = false;"
        "document.getElementById('settings-save-button').click();"
    )
    wait(
        "window.__fimRunSettingsNoticeSettled === true",
        "the per-run edit to settle",
    )


def test_run_settings_can_be_edited_restored_and_replaced_with_defaults(
    _isolate_gui_preferences: Path,
    window: webview.Window,
    monkeypatch: pytest.MonkeyPatch,
    results: Path,
) -> None:
    """Edits reach Run, cancel is inert, and defaults and originals stay separate."""
    del results
    _auto_200_settings(_isolate_gui_preferences)
    captured: list[SimulationParams] = []

    def capture(self: Api, object_id: str, **_options: Any) -> dict[str, Any]:
        """Record the submitted configuration without starting a simulation."""
        run_object = object_bridge.reconcile_run_object(object_id)
        model, _labels = presets_module.split_configuration(
            yaml.safe_load(run_object.config_text)
        )
        params = SimulationParams.from_mapping(model)
        captured.append(params)
        return {"ok": False, "message": _REFUSED}

    monkeypatch.setattr(Api, "_start_claimed_run_object", capture)

    def steps(wait: Callable[..., Any]) -> dict[str, Any]:
        _load_example(window, wait)
        _open_run_settings(window, wait)
        assert window.evaluate_js(
            "document.getElementById('settings-preferences-fields').hidden"
        )
        window.evaluate_js(
            "document.getElementById('settings-precision').value = '0.1';"
        )
        _apply_run_settings(window, wait)
        assert window.evaluate_js("collectFormValues().precision") == "0.1"
        window.evaluate_js(
            "window.fim.showScreen('screen-run');"
            "document.getElementById('run-button').click();"
        )
        wait(
            f"document.getElementById('run-banner').textContent.includes({_REFUSED!r})",
            "the edited configuration to reach Run",
        )
        window.evaluate_js("window.fim.showConfigureScreen();")
        _open_run_settings(window, wait)
        window.evaluate_js(
            "document.getElementById('settings-precision').value = '0.3';"
            "document.getElementById('modal-settings').close();"
        )
        assert window.evaluate_js("collectFormValues().precision") == "0.1"
        _open_run_settings(window, wait)
        window.evaluate_js(
            "document.getElementById('settings-restore-loaded').click();"
        )
        _apply_run_settings(window, wait)
        restored = window.evaluate_js("collectFormValues()")
        _open_run_settings(window, wait)
        window.evaluate_js("document.getElementById('settings-use-defaults').click();")
        wait("window.__fimSettingsLoaded === true", "saved Settings to fill the editor")
        _apply_run_settings(window, wait)
        defaults = window.evaluate_js("collectFormValues()")
        # The original remains restorable after applying defaults.
        _open_run_settings(window, wait)
        window.evaluate_js(
            "document.getElementById('settings-restore-loaded').click();"
        )
        _apply_run_settings(window, wait)
        return {"restored": restored, "defaults": defaults}

    result = _drive(window, steps)
    (params,) = captured
    assert params.precision == 0.1
    assert params.n_replicates == 1
    assert result["restored"]["engine_backend"] == "lineal"
    assert result["restored"]["n_replicates"] == "1"
    assert result["restored"]["precision"] != "0.1"
    assert result["defaults"]["engine_backend"] == "auto"
    assert result["defaults"]["n_replicates"] == "200"
    assert "max_workers" not in result["defaults"]
    settings = _settings_on_disk(_isolate_gui_preferences)
    assert settings["n_replicates"] == "200"
    assert settings["engine_backend"] == "auto"
    assert settings["precision"] != "0.1"


def test_edited_run_settings_are_saved_in_exported_yaml(
    window: webview.Window, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Save configuration action exports per-run edits, not saved defaults."""
    saved_path = tmp_path / "edited-run.yaml"
    monkeypatch.setattr(
        window, "create_file_dialog", lambda *_args, **_kwargs: (str(saved_path),)
    )

    def steps(wait: Callable[..., Any]) -> None:
        _load_example(window, wait)
        _open_run_settings(window, wait)
        window.evaluate_js(
            "document.getElementById('settings-precision').value = '0.1';"
        )
        _apply_run_settings(window, wait)
        window.evaluate_js(
            "window.__fimEditedRunSaved = false;"
            "window.fim.saveConfiguration().then(() => {"
            "window.__fimEditedRunSaved = true; });"
        )
        wait("window.__fimEditedRunSaved === true", "the edited YAML to be saved")

    _drive(window, steps)
    saved = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
    assert saved["precision"] == 0.1
    assert saved["n_replicates"] == 1


def test_run_settings_editor_is_available_without_a_loaded_configuration(
    window: webview.Window,
) -> None:
    """A fresh configuration can override defaults and rejects invalid edits."""

    def steps(wait: Callable[..., Any]) -> None:
        window.evaluate_js("window.fim.showConfigureScreen();")
        _open_run_settings(window, wait)
        assert window.evaluate_js(
            "document.getElementById('settings-restore-loaded').disabled"
        )
        before = window.evaluate_js("collectFormValues()")
        window.evaluate_js(
            "document.getElementById('settings-n_replicates').value = '0';"
        )
        _apply_run_settings(window, wait)
        assert window.evaluate_js("document.getElementById('modal-settings').open")
        assert window.evaluate_js(
            "document.getElementById('settings-banner').textContent"
        )
        assert window.evaluate_js("collectFormValues()") == before
        window.evaluate_js(
            "document.getElementById('settings-n_replicates').value = '1';"
            "document.getElementById('settings-max_generations').value = '100';"
        )
        _apply_run_settings(window, wait)
        assert not window.evaluate_js("document.getElementById('modal-settings').open")
        assert window.evaluate_js("collectFormValues().n_replicates") == "1"

    _drive(window, steps)


def test_applying_defaults_reports_an_underivable_cap_without_changing_the_run(
    _isolate_gui_preferences: Path, window: webview.Window
) -> None:
    """A zero-migration, zero-mutation model needs an explicit cap, not auto."""
    _auto_200_settings(_isolate_gui_preferences)

    def steps(wait: Callable[..., Any]) -> None:
        _load_example(window, wait)
        window.evaluate_js(
            "document.getElementById('field-m_rate').value = '0';"
            "document.getElementById('field-mu_value').value = '0';"
        )
        _open_run_settings(window, wait)
        window.evaluate_js(
            "document.getElementById('settings-max_generations').value = '1';"
        )
        _apply_run_settings(window, wait)
        assert window.evaluate_js("collectFormValues().max_generations") == "1"
        _open_run_settings(window, wait)
        window.evaluate_js("document.getElementById('settings-use-defaults').click();")
        wait("window.__fimSettingsLoaded === true", "the default settings snapshot")
        _apply_run_settings(window, wait)
        assert window.evaluate_js("document.getElementById('modal-settings').open")
        message = window.evaluate_js(
            "document.getElementById('settings-banner').textContent"
        )
        assert "cannot derive max_generations" in message
        assert window.evaluate_js("collectFormValues().max_generations") == "1"
        assert window.evaluate_js("collectFormValues().n_replicates") == "1"

    _drive(window, steps)


def test_loading_a_lineal_example_runs_it_without_changing_settings(
    _isolate_gui_preferences: Path,
    window: webview.Window,
    monkeypatch: pytest.MonkeyPatch,
    results: Path,
) -> None:
    """Notice lists the differences; the run uses the example; Settings stay.

    Settings are the automatic engine with 200 replicates; the example
    is lineal with one replicate. The object start is intercepted after
    its saved configuration is resolved, so the test sees the exact
    parameters the Run button produced without running them.
    """
    del results
    _auto_200_settings(_isolate_gui_preferences)
    captured: list[SimulationParams] = []

    def capture(self: Api, object_id: str, **_options: Any) -> dict[str, Any]:
        """Record the run's parameters and decline to start it."""
        run_object = object_bridge.reconcile_run_object(object_id)
        model, _labels = presets_module.split_configuration(
            yaml.safe_load(run_object.config_text)
        )
        params = SimulationParams.from_mapping(model)
        captured.append(params)
        return {"ok": False, "message": _REFUSED}

    monkeypatch.setattr(Api, "_start_claimed_run_object", capture)

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
    assert "Saved Settings are unchanged" in loaded["configure"]["summary"]
    assert loaded["configure"]["rows"] == [
        ["engine_backend", "Execution engine", "lineal", "auto"],
        ["n_replicates", "Number of replicates", "1", "200"],
    ]
    assert loaded["stripHidden"] is False
    assert loaded["stripText"] == "run settings (2 differ)"
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
    assert adopted["configure"]["summary"].startswith("Settings saved.")
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
