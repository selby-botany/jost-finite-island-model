"""Headless functional tests for Home's Run/Study/Experiment tree.

Real DOM-driven proof that the three-level Experiment/Study/Run tree
(`20260917-claude-sonnet-5-run-study-experiment-hierarchy-design.md`,
`selby/restricted`, §6) actually works end to end through the page's own
JavaScript (`screens/open-run.js`'s own `buildHomeGroups`/
`createGroup`/`buildGroupActionControls`) -- `test/gui/test_app_api.py`'s
own tests already prove every underlying `Api` bridge method correct as
a plain Python call; this file proves the page wires them together,
which no Python-only test can check.

This codebase has no `window.prompt`/`window.confirm` anywhere: both
were confirmed, live, to block indefinitely under a hidden/headless
pywebview window (`screens/open-run.js`'s own `confirmThenRun`
docstring) -- every interaction here is therefore a plain DOM click/
input against always-visible controls, never a native dialog.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli
from fim import paths as paths_module
from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage
from fim.persistence import groups

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
_DRIVE_TIMEOUT_SECONDS = _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0
_TREE_TEXT = "document.getElementById('open-run-recent-runs-body').textContent"
_EVENT_WAIT_TIMEOUT_SECONDS = 30.0

# Mirrors `test/gui/test_running_screen.py`'s own identically-named
# constant -- a direct parallel, not a shared import, per this project's
# established per-test-file fixture convention.
_SET_TINY_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
"""


def _write_run(results: Path, name: str, seed: int) -> Path:
    """Write a small, real completed run under `results / name`."""
    config = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": seed,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config_path = results / f"{name}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = results / name
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def _poll_until(
    window: webview.Window, script: str, is_ready: Callable[[Any], bool]
) -> Any:
    """Evaluate `script` repeatedly, sleeping between tries, until `is_ready` is
    true."""
    value: Any = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if is_ready(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def _expand_every_group(window: webview.Window) -> None:
    """Click every currently-collapsed group toggle, several passes deep.

    Mirrors `test_open_run_screen.py`'s own `_expand_all_recent_run_
    groups` -- several rounds, not one, since expanding an outer group
    (Experiment, Study, "Unsorted", "Earlier") can reveal further
    collapsed toggles nested inside it. A Study group's own toggle
    triggers a real, awaited bridge call (`get_study_run_summary`)
    before its rows render, so this sleeps briefly between rounds rather
    than firing every click in one synchronous burst.
    """
    for _ in range(6):
        window.evaluate_js(
            "Array.from(document.querySelectorAll("
            "'.open-run-group-toggle[aria-expanded=\"false\"]'"
            ")).forEach((b) => b.click());"
        )
        time.sleep(0.15)


def _create_study(window: webview.Window, name: str) -> None:
    """Fill in and submit the "New study" card's own inline form."""
    window.evaluate_js(
        f"document.getElementById('home-new-study-name').value = {name!r};"
    )
    window.evaluate_js("document.getElementById('home-new-study-button').click();")
    _poll_until(window, _TREE_TEXT, lambda value: value is not None and name in value)


def _create_experiment(window: webview.Window, name: str) -> None:
    """Fill in and submit the "New experiment" card's own inline form."""
    window.evaluate_js(
        f"document.getElementById('home-new-experiment-name').value = {name!r};"
    )
    window.evaluate_js("document.getElementById('home-new-experiment-button').click();")
    _poll_until(window, _TREE_TEXT, lambda value: value is not None and name in value)


def _add_run_to_study(window: webview.Window, run_marker: str, study_name: str) -> None:
    """Pick `study_name` from the "Add to study…" select on `run_marker`'s own row."""
    picked = window.evaluate_js(
        "(function(marker, studyName) {"
        "var rows = document.querySelectorAll("
        "'#open-run-recent-runs-body tr:not(.open-run-group-header)');"
        "for (var row of rows) {"
        "  if (row.textContent.includes(marker)) {"
        "    var select = row.querySelector('.open-run-add-to-select');"
        "    for (var option of select.options) {"
        "      if (option.textContent === studyName) {"
        "        select.value = option.value;"
        "        select.dispatchEvent(new Event('change'));"
        "        return true;"
        "      }"
        "    }"
        "  }"
        "}"
        "return false; })"
        f"({run_marker!r}, {study_name!r})"
    )
    assert picked is True, f"no row matching {run_marker!r} had a {study_name!r} option"


def _click_group_delete(window: webview.Window, group_label: str) -> None:
    """Click the Delete… button on the group header whose toggle names `group_label`."""
    clicked = window.evaluate_js(
        "(function(label) {"
        "var headers = document.querySelectorAll('.open-run-group-header');"
        "for (var header of headers) {"
        "  var toggle = header.querySelector('.open-run-group-toggle');"
        "  if (toggle && toggle.textContent.includes(label)) {"
        "    var buttons = header.querySelectorAll('.open-run-group-action-button');"
        "    for (var button of buttons) {"
        "      if (button.textContent === 'Delete…') { button.click(); return true; }"
        "    }"
        "  }"
        "}"
        "return false; })"
        f"({group_label!r})"
    )
    assert clicked is True, (
        f"no group header named {group_label!r} had a Delete… button"
    )


def _confirm_inline(window: webview.Window) -> None:
    """Click "Confirm" on the currently-showing `confirmThenRun` inline row."""
    window.evaluate_js(
        "(function(){"
        "var row = document.querySelector('.open-run-inline-confirm');"
        "var buttons = row.querySelectorAll('button');"
        "for (var button of buttons) {"
        "  if (button.textContent === 'Confirm') { button.click(); return; }"
        "}"
        "})()"
    )


def test_creating_a_study_shows_it_in_the_tree_and_wraps_unsorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new, empty Study appears; every existing run becomes "Unsorted".

    Before any Study/Experiment exists, Home renders the plain date-
    bucket tree with no "Unsorted" wrapper (design doc §6, "zero
    required migration") -- creating the very first Study is the one
    moment that wrapper is expected to appear.
    """
    results = tmp_path / "results"
    results.mkdir()
    _write_run(results, "run-a", seed=1)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _create_study(window, "Ring sweep")
            outcome.put(window.evaluate_js(_TREE_TEXT))
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Ring sweep (0 runs)" in tree_text
    assert "Unsorted (1)" in tree_text


def test_adding_a_run_to_a_study_moves_it_out_of_unsorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Add to study…" moves a run's own count from Unsorted into the Study."""
    results = tmp_path / "results"
    results.mkdir()
    _write_run(results, "run-a", seed=1)
    _write_run(results, "run-b", seed=2)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _create_study(window, "Ring sweep")
            _expand_every_group(window)
            _add_run_to_study(window, "seed=1", "Ring sweep")
            tree_text = _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep (1 run)" in value,
            )
            outcome.put(tree_text)
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Ring sweep (1 run)" in tree_text
    assert "Unsorted (1)" in tree_text


def test_creating_an_experiment_and_adding_a_study_nests_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Add to experiment…" nests a Study's own row under its Experiment."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _create_study(window, "Ring sweep")
            _create_experiment(window, "Topology")
            picked = window.evaluate_js(
                "(function(){"
                "var selects = document.querySelectorAll('.open-run-add-to-select');"
                "for (var select of selects) {"
                "  for (var option of select.options) {"
                "    if (option.textContent === 'Topology') {"
                "      select.value = option.value;"
                "      select.dispatchEvent(new Event('change'));"
                "      return true;"
                "    }"
                "  }"
                "}"
                "return false; })()"
            )
            _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Topology (1 study)" in value,
            )
            _expand_every_group(window)
            tree_text = window.evaluate_js(_TREE_TEXT)
            outcome.put({"picked": picked, "treeText": tree_text})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    assert result["picked"] is True
    assert "Topology (1 study)" in result["treeText"]
    assert "Ring sweep (0 runs)" in result["treeText"]


def test_deleting_a_study_cascades_to_its_own_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Study's own inline Delete confirmation removes it and its member Runs.

    The confirmed, deliberate product decision (`fim.persistence.groups.
    delete_study`'s own docstring): deleting a Study is a real,
    data-destroying operation for the runs it references, not merely a
    bookkeeping change -- confirmed here against real files on disk, not
    only against `Api.delete_study` as a plain Python call
    (`test/gui/test_app_api.py`'s own coverage).
    """
    results = tmp_path / "results"
    results.mkdir()
    kept = _write_run(results, "run-a", seed=1)
    deleted = _write_run(results, "run-b", seed=2)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _create_study(window, "Ring sweep")
            _expand_every_group(window)
            _add_run_to_study(window, "seed=2", "Ring sweep")
            _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep (1 run)" in value,
            )
            _click_group_delete(window, "Ring sweep")
            confirm_text = window.evaluate_js(
                "document.querySelector('.open-run-inline-confirm').textContent"
            )
            _confirm_inline(window)
            tree_text = _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep" not in value,
            )
            outcome.put({"confirmText": confirm_text, "treeText": tree_text})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    assert "1 run" in result["confirmText"]
    assert "Ring sweep" not in result["treeText"]
    assert not deleted.exists()
    assert kept.exists()


def test_copying_a_study_creates_an_independent_copy_with_no_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy needs no name entry -- it derives "<name> copy" and acts immediately."""
    results = tmp_path / "results"
    results.mkdir()
    _write_run(results, "run-a", seed=1)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _create_study(window, "Ring sweep")
            _expand_every_group(window)
            _add_run_to_study(window, "seed=1", "Ring sweep")
            _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep (1 run)" in value,
            )
            window.evaluate_js(
                "(function(){"
                "var buttons = document.querySelectorAll("
                "'.open-run-group-action-button');"
                "for (var button of buttons) {"
                "  if (button.textContent === 'Copy') { button.click(); return; }"
                "}"
                "})()"
            )
            tree_text = _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep copy" in value,
            )
            outcome.put(tree_text)
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Ring sweep (1 run)" in tree_text
    assert "Ring sweep copy (1 run)" in tree_text


def test_bulk_select_all_and_delete_selected_removes_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "Select all"/"Delete selected" idiom removes every loaded run at once.

    The explicit gap this idiom answers: thousands of Unsorted runs
    could not realistically be deleted one at a time through the GUI.
    "Select all" reaches every loaded run even while its own group is
    collapsed -- this test never expands anything.
    """
    results = tmp_path / "results"
    results.mkdir()
    first = _write_run(results, "run-a", seed=1)
    second = _write_run(results, "run-b", seed=2)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            window.evaluate_js(
                "document.getElementById('open-run-select-all-button').click();"
            )
            selection_count = _poll_until(
                window,
                "document.getElementById('open-run-selection-count').textContent",
                lambda value: value == "2 selected",
            )
            window.evaluate_js(
                "document.getElementById('open-run-delete-selected-button').click();"
            )
            _confirm_inline(window)
            tree_text = _poll_until(
                window,
                "document.getElementById('open-run-count').textContent",
                lambda value: value == "0 runs",
            )
            outcome.put({"selectionCount": selection_count, "treeText": tree_text})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    assert result["selectionCount"] == "2 selected"
    assert result["treeText"] == "0 runs"
    assert not first.exists()
    assert not second.exists()


def test_starting_a_run_from_configure_with_a_study_selected_attaches_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_scalar_run_settings: Path,
) -> None:
    """Configure's own Study picker attaches a real run to a real Study once done.

    Run/Study/Experiment workflow-ergonomics design (`20260917-claude-
    sonnet-5-run-study-experiment-workflow-ergonomics.md`, `selby/
    restricted`, item 3): "Configure ... has no link to organization"
    was the reported gap -- this drives the actual Configure screen's
    own "Run" button (`configure-run-button`, which navigates to
    `#screen-run` and clicks `run-button` itself, the identical path a
    real click takes) with a Study chosen in `run-study-select`
    beforehand, and confirms the Study's own manifest lists the real
    run directory afterward, on real disk -- not only that `Api.
    start_run`'s own `study_id` argument is accepted (`test_app_api.py`'s
    own narrower coverage).
    """
    results = tmp_path / "results"
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)

    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            # Waits for `showConfigureScreen()`'s own returned promise to
            # settle, not merely for `run-study-select` to look populated
            # (`options.length > 1` can also turn true from `wireRunView
            # Controls`'s own unrelated launch-time refresh, still racing
            # in the background under heavy parallel test load -- a real,
            # reproduced flake, not a hypothetical one; `run-view-
            # controls.js`'s own `runStudyRefreshToken` guard fixes the
            # production race this exposed, but this test still needs its
            # own explicit "the call I actually triggered is done" signal
            # rather than an ambiguous DOM side effect).
            window.evaluate_js(
                "window.__fimTestConfigureReady = false;"
                "window.fim.showConfigureScreen().then("
                "() => { window.__fimTestConfigureReady = true; }"
                ");"
            )
            _poll_until(
                window,
                "window.__fimTestConfigureReady === true",
                lambda value: value is True,
            )
            window.evaluate_js(
                f"document.getElementById('run-study-select').value ="
                f" {study.study_id!r};"
            )
            selected = window.evaluate_js(
                "document.getElementById('run-study-select').value"
            )
            window.evaluate_js(
                _SET_TINY_FIELDS
                + "document.getElementById('configure-run-button').click();"
            )
            done = done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS)
            outcome.put({"selected": selected, "done": done})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["selected"] == study.study_id, (
        "run-study-select did not accept the chosen study"
    )
    assert settled["done"] is True, "the run never reached a terminal state"
    updated = groups.get_study(study.study_id, results=results)
    assert updated.run_count == 1


def test_run_study_select_new_study_creates_and_selects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configure's own "New study…" entry creates a real Study inline.

    `20260918-claude-sonnet-5-explore-to-study-run-handoff-design.md`
    (`selby/restricted`), §1 Option B: the compact counterpart to
    Home's own "New study" card, reached from `run-study-select`
    itself rather than a navigation away from Configure.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "window.__fimTestConfigureReady = false;"
                "window.fim.showConfigureScreen().then("
                "() => { window.__fimTestConfigureReady = true; }"
                ");"
            )
            _poll_until(
                window,
                "window.__fimTestConfigureReady === true",
                lambda value: value is True,
            )
            row_hidden_before = window.evaluate_js(
                "document.getElementById('run-study-new-row').hidden"
            )
            window.evaluate_js(
                "document.getElementById('run-study-select').value = '__new__';"
                "document.getElementById('run-study-select')"
                ".dispatchEvent(new Event('change'));"
            )
            row_hidden_after_select = window.evaluate_js(
                "document.getElementById('run-study-new-row').hidden"
            )
            window.evaluate_js(
                "document.getElementById('run-study-new-name').value = 'Ring sweep';"
            )
            window.evaluate_js(
                "document.getElementById('run-study-new-create-button').click();"
            )
            selected = _poll_until(
                window,
                "document.getElementById('run-study-select').value",
                lambda value: value not in (None, "", "__new__"),
            )
            row_hidden_after_create = window.evaluate_js(
                "document.getElementById('run-study-new-row').hidden"
            )
            outcome.put(
                {
                    "rowHiddenBefore": row_hidden_before,
                    "rowHiddenAfterSelect": row_hidden_after_select,
                    "selected": selected,
                    "rowHiddenAfterCreate": row_hidden_after_create,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["rowHiddenBefore"] is True
    assert settled["rowHiddenAfterSelect"] is False
    assert settled["rowHiddenAfterCreate"] is True
    study_id = settled["selected"]
    assert study_id != "__new__"
    created = groups.get_study(study_id, results=results)
    assert created.name == "Ring sweep"
    assert created.run_count == 0


def test_run_study_select_new_study_cancel_returns_to_no_study(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the inline "New study…" row abandons it, no Study created."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "window.__fimTestConfigureReady = false;"
                "window.fim.showConfigureScreen().then("
                "() => { window.__fimTestConfigureReady = true; }"
                ");"
            )
            _poll_until(
                window,
                "window.__fimTestConfigureReady === true",
                lambda value: value is True,
            )
            window.evaluate_js(
                "document.getElementById('run-study-select').value = '__new__';"
                "document.getElementById('run-study-select')"
                ".dispatchEvent(new Event('change'));"
            )
            window.evaluate_js(
                "document.getElementById('run-study-new-name').value = 'Abandoned';"
            )
            window.evaluate_js(
                "document.getElementById('run-study-new-cancel-button').click();"
            )
            selected = window.evaluate_js(
                "document.getElementById('run-study-select').value"
            )
            row_hidden = window.evaluate_js(
                "document.getElementById('run-study-new-row').hidden"
            )
            name_cleared = window.evaluate_js(
                "document.getElementById('run-study-new-name').value"
            )
            outcome.put(
                {
                    "selected": selected,
                    "rowHidden": row_hidden,
                    "nameCleared": name_cleared,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["selected"] == ""
    assert settled["rowHidden"] is True
    assert settled["nameCleared"] == ""
    assert groups.list_studies(results=results) == []
