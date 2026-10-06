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

import json
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
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


def _write_run(
    results: Path,
    folder: str,
    seed: int,
    *,
    study_id: str | None = None,
    **overrides: object,
) -> Path:
    """Write a small, real completed run under `results / folder`.

    `study_id`, when given, attaches the run to that Study directly at
    creation time via `fim run --study` — the CLI's own bare `fim run`
    now attaches to the always-present default Study instead (`20260918-
    claude-sonnet-5-home-tree-reorg-design.md`, `selby/restricted`, §2),
    so an explicit `study_id` is the only way a test can put a run
    somewhere else. `**overrides` layers onto the base config below --
    e.g. `d=4` for a test that needs a Study member disagreeing with
    another on deme count.
    """
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
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
    config.update(overrides)
    config_path = results / f"{folder}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = results / folder
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if study_id is not None:
        arguments += ["--study", study_id]
    assert cli.main(arguments) == 0
    return output_directory


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("replicates", [1, 2])
def test_home_loads_a_runs_editable_configuration_without_linking_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
    replicates: int,
) -> None:
    """Home loads regular/example scalar/batch runs without changing the source."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    source = _write_run(
        results,
        "source-run",
        42,
        _read_only=read_only,
        name="Source parameters",
        description="Editable configuration from Home",
        n_replicates=replicates,
    )
    before = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    memberships = groups.get_study(groups.DEFAULT_STUDY_ID).run_directories
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "window.__fimOpenRunRecentRunsLoaded === true",
                lambda value: value is True,
            )
            _expand_every_group(window)
            clicked = window.evaluate_js(
                "(function () {"
                "const row = Array.from(document.querySelectorAll('.open-run-run-row'))"
                ".find((item) => item.textContent.includes('Source parameters'));"
                "const button = row?.querySelector('.open-run-load-config-button');"
                "if (!button || button.textContent !== 'Clone') return false;"
                "window.__fimHomeRunLoadSettled = false;"
                "button.click(); return true;"
                "})()"
            )
            assert clicked, "Home run has no Clone action"
            settled = _poll_until(
                window,
                "window.__fimHomeRunLoadSettled === true",
                lambda value: value is True,
            )
            assert settled, "Home configuration loading never settled"
            snapshot = window.evaluate_js(
                "({configureVisible:"
                " !document.getElementById('screen-configure').hidden,"
                " name: document.getElementById('run-name-input').value,"
                " description: document.getElementById('run-description-input').value,"
                " values: collectFormValues(),"
                " studyLabels: Array.from(document.getElementById('run-study-select')"
                ".options).map((option) => option.textContent),"
                " selectedStudy: document.getElementById('run-study-select')"
                ".selectedOptions[0].textContent,"
                " buttonOrder: Array.from(document.querySelectorAll("
                "'#screen-configure .actions > button'))"
                ".map((button) => button.id).filter((id) => "
                "['configure-load-button', 'configure-save-button',"
                "'configure-examples-button'].includes(id))})"
            )
            outcome.put(snapshot)
        finally:
            window.destroy()

    webview.start(_drive)
    loaded = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)
    assert loaded["configureVisible"] is True
    assert loaded["name"] == "Source parameters"
    assert loaded["description"] == "Editable configuration from Home"
    assert loaded["values"]["seed"] == "42"
    assert Api().validate_form(loaded["values"])["ok"] is True
    assert not any(key.startswith("_") for key in loaded["values"])
    assert loaded["selectedStudy"] == "Default study"
    assert loaded["studyLabels"].count("Default study") == 1
    assert "No study" not in loaded["studyLabels"]
    assert loaded["buttonOrder"] == [
        "configure-load-button",
        "configure-save-button",
        "configure-examples-button",
    ]
    assert groups.get_study(groups.DEFAULT_STUDY_ID).run_directories == memberships
    assert {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == before


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


def _submit_inline_prompt(window: webview.Window, name: str) -> None:
    """Fill and submit the currently-showing `promptForNameThenRun` inline row."""
    window.evaluate_js(
        f"document.querySelector('.open-run-inline-confirm input').value = {name!r};"
    )
    window.evaluate_js(
        "(function(){"
        "var row = document.querySelector('.open-run-inline-confirm');"
        "var buttons = row.querySelectorAll('button');"
        "for (var button of buttons) {"
        "  if (button.textContent === 'Create') { button.click(); return; }"
        "}"
        "})()"
    )


def _create_experiment(window: webview.Window, name: str) -> None:
    """Submit Home's own page-level "Create experiment…" button with `name`."""
    window.evaluate_js("document.getElementById('home-new-experiment-button').click();")
    _submit_inline_prompt(window, name)
    _poll_until(window, _TREE_TEXT, lambda value: value is not None and name in value)


def _check_group_checkbox(window: webview.Window, group_label: str) -> None:
    """Check the Select checkbox on the group header whose toggle names
    `group_label`."""
    checked = window.evaluate_js(
        "(function(label) {"
        "var headers = document.querySelectorAll('.open-run-group-header');"
        "for (var header of headers) {"
        "  var toggle = header.querySelector('.open-run-group-toggle');"
        "  if (toggle && toggle.textContent.includes(label)) {"
        "    var checkbox = header.querySelector('.open-run-select-checkbox');"
        "    checkbox.click();"
        "    return true;"
        "  }"
        "}"
        "return false; })"
        f"({group_label!r})"
    )
    assert checked is True, (
        f"no group header named {group_label!r} had a select checkbox"
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


def test_a_bare_cli_run_appears_under_the_default_study(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain `fim run` (no `--study`) still shows up in Home's own tree.

    `20260918-claude-sonnet-5-home-tree-reorg-design.md` (`selby/
    restricted`) §2: the CLI and GUI converge on the identical
    `add_run_to_study` call, so a run made from a terminal is exactly
    as visible in Home as one made from the GUI -- never a silent gap
    only discoverable by counting `results/*/manifest.json` files by
    hand, the regression this test would have caught directly (a real
    one, hit live while building this feature: removing the old
    "Unsorted" bucket without this CLI-side change made every bare run
    disappear from the tree entirely).
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    _write_run(results, "run-a", seed=1)

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
            _expand_every_group(window)
            outcome.put(window.evaluate_js(_TREE_TEXT))
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Default study (1 run)" in tree_text
    assert "seed=1" in tree_text


def test_home_run_count_label_does_not_double_count_a_run_in_two_studies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run belonging to more than one Study is still counted once.

    A real, reported bug: `add_run_to_study` only ever appends, never
    detaches from a prior Study, so a run genuinely can end up in more
    than one Study (here: the always-present default Study, plus two
    more added by hand). The bottom-of-table count label used to sum
    each visible Study's own `runCount` across the whole tree, double-
    (or more-)counting any run shared this way -- confirmed live on a
    checkout with heavy manual "Add to study…" use, producing a
    nonsensical "722 of 2 runs" with no filter text even typed. The
    label must count distinct run directories instead.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    output = _write_run(results, "run-a", seed=1)
    first = groups.create_study("First study", results=results)
    second = groups.create_study("Second study", results=results)
    groups.add_run_to_study(first.study_id, output, results=results)
    groups.add_run_to_study(second.study_id, output, results=results)

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            count_text = _poll_until(
                window,
                "document.getElementById('open-run-count').textContent",
                lambda value: value not in (None, ""),
            )
            outcome.put(count_text)
        finally:
            window.destroy()

    webview.start(_drive)
    count_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert count_text == "1 run"


def test_home_materializes_the_default_study_on_a_truly_empty_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout with nothing yet still shows an expandable default Study row.

    §1's own amendment: `ensure_default_study` stays lazy (never called
    at app launch), but Home's own `refreshRecentRuns` calls it once,
    exactly when a visit's own `list_studies`/`list_experiments` both
    come back empty, so the default destination is visible before the
    first run.
    """
    results = tmp_path / "results"
    results.mkdir()
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
            _expand_every_group(window)
            outcome.put(window.evaluate_js(_TREE_TEXT))
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Default experiment (1 study)" in tree_text
    assert "Default study (0 runs)" in tree_text
    assert groups.get_study(groups.DEFAULT_STUDY_ID, results=results).name == (
        "Default study"
    )


def test_creating_an_experiment_from_the_home_toolbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page-level Create experiment action remains available."""
    results = tmp_path / "results"
    results.mkdir()
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
            _create_experiment(window, "Topology")
            _expand_every_group(window)
            outcome.put(window.evaluate_js(_TREE_TEXT))
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Topology (0 studies)" in tree_text


def test_deleting_a_study_cascades_to_its_own_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking a Study's own row and confirming "Delete selected" removes its Runs too.

    The confirmed, deliberate product decision (`fim.persistence.groups.
    delete_study`'s own docstring): deleting a Study is a real,
    data-destroying operation for the runs it references, not merely a
    bookkeeping change -- confirmed here against real files on disk, not
    only against `Api.delete_study` as a plain Python call
    (`test/gui/test_app_api.py`'s own coverage). Deletion is checkbox +
    "Delete selected" only now, not a per-row "Delete…" button
    (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
    restricted`, §5).
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    kept = _write_run(results, "run-a", seed=1)
    deleted = _write_run(results, "run-b", seed=2, study_id=study.study_id)

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
            _check_group_checkbox(window, "Ring sweep")
            window.evaluate_js(
                "document.getElementById('open-run-delete-selected-button').click();"
            )
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
    assert "1 stud" in result["confirmText"]
    assert "Ring sweep" not in result["treeText"]
    assert not deleted.exists()
    assert kept.exists()


def test_home_hierarchy_omits_removed_row_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Experiment, Study, and Run rows omit removed actions and retain Clone."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    _write_run(results, "run-a", seed=1, study_id=study.study_id)
    experiment = groups.create_experiment("Topology", results=results)
    groups.add_study_to_experiment(
        experiment.experiment_id, study.study_id, results=results
    )

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
            _expand_every_group(window)
            _poll_until(
                window,
                _TREE_TEXT,
                lambda value: value is not None and "Ring sweep (1 run)" in value,
            )
            snapshot = window.evaluate_js(
                "({actions: Array.from(document.querySelectorAll("
                "'.open-run-group-header button, .open-run-run-row button'))"
                ".map((button) => button.textContent.trim()),"
                "cloneCount: document.querySelectorAll("
                "'.open-run-load-config-button').length})"
            )
            outcome.put(snapshot)
        finally:
            window.destroy()

    webview.start(_drive)
    snapshot = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert snapshot is not None
    assert snapshot["cloneCount"] == 1
    assert "Clone" in snapshot["actions"]
    assert not {
        "Create study…",
        "Create run…",
        "Open…",
        "Delete runs…",
        "Copy",
        "Load into Configure",
    }.intersection(snapshot["actions"])


def test_bulk_select_all_and_delete_selected_removes_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "Select all"/"Delete selected" idiom removes every loaded item at once.

    The explicit gap this idiom answers: thousands of Unsorted runs
    could not realistically be deleted one at a time through the GUI.
    "Select all" reaches every loaded Run/Study/Experiment even while
    its own group is collapsed -- this test never expands anything. Both
    bare runs land in the always-present default Study/Experiment
    (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
    restricted`, §1/§2), so "Select all" selects 4 items total, not 2 --
    the 2 runs plus that one Study and one Experiment (`20260918-...
    -design.md` §5's own "Select all" generalization) -- and deleting
    them cascades the Experiment away too, which is harmless: nothing
    here treats the default Study/Experiment as undeletable, and
    `ensure_default_study` simply recreates it, empty, the next time
    anything needs it (§5's own explicit "no special-casing" note).
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    first = _write_run(results, "run-a", seed=1)
    second = _write_run(results, "run-b", seed=2)

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
                lambda value: value == "4 selected",
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
    assert result["selectionCount"] == "4 selected"
    assert result["treeText"] == "0 runs"
    assert not first.exists()
    assert not second.exists()
    # Recreated fresh and empty, not gone for good -- Home's own next
    # render sees an otherwise-empty tree and re-materializes the
    # default Study/Experiment exactly as it did on first launch (§1's
    # own amendment), the same "simply recreates it, empty" behavior
    # this test's own docstring already describes.
    remaining_studies = [
        study.study_id for study in groups.list_studies(results=results)
    ]
    assert remaining_studies == [groups.DEFAULT_STUDY_ID]
    remaining_experiments = [
        experiment.experiment_id
        for experiment in groups.list_experiments(results=results)
    ]
    assert remaining_experiments == [groups.DEFAULT_EXPERIMENT_ID]


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
    # No "Abandoned" Study exists -- only the always-present default one,
    # materialized as a side effect of Home's own launch-time pre-
    # population (`run-view-initial.js`), unrelated to anything this
    # test itself did in Configure.
    names = [study.name for study in groups.list_studies(results=results)]
    assert names == ["Default study"]


def test_home_shows_both_runs_of_a_repeated_configuration_by_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs of the identical configuration share a `run_id`, but each
    still shows its own distinct label -- neither blanked.

    Reported live, the other direction from what this test once checked:
    a Study's own expanded view used to blank the second of two same-
    configuration runs' own id *text*, on the theory that a repeated
    `run_id` meant a repeated label. `run_id` is a deterministic hash of
    the configuration itself (`fim.engine.deterministic_run_id`), not a
    per-invocation random id, so two genuinely distinct run directories
    sharing an identical configuration always share one `run_id` -- but
    the label a botanist actually sees is the run's own *directory* name
    (`directoryName`, not `run_id` -- the botanist-facing-run-name fix),
    and no two directories share a name (`_recent_run_from_file`'s own
    `root.glob("*/manifest.json")` scan cannot produce a duplicate).
    Blanking on a shared `run_id` therefore used to hide the one thing
    this list exists to show: which folder is which. `renderGroup`'s own
    `showRunId` now compares the *displayed* label instead, so both rows
    here render their own directory name in full.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    older = _write_run(results, "run-a", seed=1, study_id=study.study_id)
    newer = _write_run(results, "run-b", seed=1, study_id=study.study_id)
    # Both finish within the same instant in practice -- push `newer`'s
    # own `ended_at` forward by hand, rather than a real sleep, so the
    # two are unambiguously ordered without slowing this test down.
    newer_manifest_path = newer / "manifest.json"
    newer_manifest = json.loads(newer_manifest_path.read_text(encoding="utf-8"))
    newer_manifest["ended_at"] = (
        datetime.fromisoformat(newer_manifest["ended_at"]) + timedelta(minutes=5)
    ).isoformat()
    newer_manifest_path.write_text(json.dumps(newer_manifest), encoding="utf-8")

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
            _expand_every_group(window)
            row_count = _poll_until(
                window,
                "document.querySelectorAll("
                "'#open-run-recent-runs-body tr.open-run-run-row').length",
                lambda value: value is not None and value > 0,
            )
            run_id_texts = window.evaluate_js(
                "Array.from(document.querySelectorAll("
                "'#open-run-recent-runs-body tr.open-run-run-row'))"
                ".map((row) => row.querySelector('td').textContent.trim())"
            )
            tree_text = window.evaluate_js(_TREE_TEXT)
            outcome.put(
                {
                    "rowCount": row_count,
                    "runIdTexts": run_id_texts,
                    "treeText": tree_text,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    # Two distinct run directories, both rendered -- not deduped away.
    assert result["rowCount"] == 2
    older_manifest = json.loads((older / "manifest.json").read_text(encoding="utf-8"))
    newer_manifest = json.loads((newer / "manifest.json").read_text(encoding="utf-8"))
    assert older_manifest["run_id"] == newer_manifest["run_id"]
    run_id_texts = result["runIdTexts"]
    assert len(run_id_texts) == 2
    # Newest-first: the first row is "run-b" (the newer directory), the
    # second is "run-a" (the older) -- both their own real names, neither
    # blanked, even though the two share one `run_id`.
    assert run_id_texts[0] == "run-b"
    assert run_id_texts[1] == "run-a"
    # Both timestamps are still on screen -- only the id text is
    # blanked, everything else about the older row (Ended, Outcome,
    # Configuration, Statistics) still renders normally.
    newer_ended_at = re.sub(r"\.\d+(?=Z?$)", "", newer_manifest["ended_at"])
    older_ended_at = re.sub(r"\.\d+(?=Z?$)", "", older_manifest["ended_at"])
    assert newer_ended_at in result["treeText"]
    assert older_ended_at in result["treeText"]


def test_home_select_button_toggles_the_checkbox_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkboxes stay hidden until "Select" is clicked, on every row kind.

    Noise on a screen mostly used to look, not to bulk-delete -- "Select"
    (`open-run-toggle-select-button`) is a pure display toggle
    (`#open-run-table`'s own `open-run-selecting` class), never touching
    the underlying selection state.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    _write_run(results, "run-a", seed=1, study_id=study.study_id)

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
            _expand_every_group(window)
            _poll_until(
                window,
                "document.querySelectorAll('.open-run-select-checkbox').length",
                lambda value: value is not None and value > 0,
            )
            before = window.evaluate_js(
                "getComputedStyle(document.querySelector("
                "'.open-run-select-checkbox')).display"
            )
            window.evaluate_js(
                "document.getElementById('open-run-toggle-select-button').click();"
            )
            after = window.evaluate_js(
                "getComputedStyle(document.querySelector("
                "'.open-run-select-checkbox')).display"
            )
            pressed_after = window.evaluate_js(
                "document.getElementById('open-run-toggle-select-button')"
                ".getAttribute('aria-pressed')"
            )
            window.evaluate_js(
                "document.getElementById('open-run-toggle-select-button').click();"
            )
            after_second_click = window.evaluate_js(
                "getComputedStyle(document.querySelector("
                "'.open-run-select-checkbox')).display"
            )
            outcome.put(
                {
                    "before": before,
                    "after": after,
                    "pressedAfter": pressed_after,
                    "afterSecondClick": after_second_click,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    assert result["before"] == "none"
    assert result["after"] != "none"
    assert result["pressedAfter"] == "true"
    assert result["afterSecondClick"] == "none"


def test_home_checkbox_and_toggle_sit_on_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Study/Experiment row's own checkbox and toggle never wrap onto
    separate lines.

    A real, reported layout bug: the toggle's own former `width: 100%`
    made it an inline-block wider than the space the checkbox left
    beside it, wrapping it onto a line of its own below the checkbox
    (`.open-run-group-header-cell`'s own flex layout, `app.css`, fixes
    this). Confirmed by comparing each element's own vertical position
    rather than reading text, since a line-wrap changes nothing about
    what text is present, only where it renders.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    groups.create_study("Ring sweep", results=results)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            window.evaluate_js(
                "document.getElementById('open-run-toggle-select-button').click();"
            )
            settled = _poll_until(
                window,
                "(function(){"
                "var checkbox = document.querySelector("
                "'.open-run-select-checkbox');"
                "var toggle = document.querySelector('.open-run-group-toggle');"
                "if (!checkbox || !toggle) { return null; }"
                "return {"
                "checkboxTop: checkbox.getBoundingClientRect().top,"
                "toggleTop: toggle.getBoundingClientRect().top"
                "};"
                "})()",
                lambda value: value is not None,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    # A checkbox and a button of different heights, centered together on
    # one flex line, land a few px apart even when correctly inline --
    # a genuine line-wrap (the bug this guards against) is off by a full
    # line height instead, an order of magnitude more.
    assert abs(result["checkboxTop"] - result["toggleTop"]) < 10


def test_home_selection_toolbar_sits_on_the_filter_line(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The Select/Select all/Clear selection/Delete selected group nests
    inside the same row as the filter input, not a separate line below
    it."""
    nested = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.showOpenRunScreen();",
        read=(
            "document.querySelector("
            "'.open-run-list-controls .open-run-selection-toolbar') !== null"
        ),
    )

    assert nested is True


def test_rendering_a_pooled_study_shows_its_member_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Study bridge payload still renders every member on the Results card.

    `20260919-claude-sonnet-5-unified-batch-and-study-results-reopen-
    design.md` (`selby/restricted`), §2/§3.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    _write_run(results, "run-a", seed=1, study_id=study.study_id)
    _write_run(results, "run-b", seed=2, study_id=study.study_id)

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
                "window.pywebview.api.open_study("
                f"{study.study_id!r}).then((result) => {{"
                "window.fim.resetTrajectoryLegendVisibility();"
                "window.fim.enterCompletedState(result, true);})"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "batchTableHidden: "
                "document.getElementById('batch-results-table').hidden, "
                "replicateRowCount: document.querySelectorAll("
                "'#batch-results-table-body tr').length"
                "})",
                lambda value: (
                    value is not None and value.get("runViewState") == "completed"
                ),
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["runViewState"] == "completed"
    assert settled["batchTableHidden"] is False
    # 3, not 2: `renderBatchTable` prepends its own p0 baseline row.
    assert settled["replicateRowCount"] == 3


def test_rendering_a_pooled_study_with_a_mismatched_parameter_shows_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mismatched `d` across a Study's own members still pools, with a
    visible note naming it -- never a refusal."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    _write_run(results, "run-a", seed=1, d=2, study_id=study.study_id)
    _write_run(results, "run-b", seed=2, d=4, study_id=study.study_id)

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
                "window.pywebview.api.open_study("
                f"{study.study_id!r}).then((result) => {{"
                "window.fim.resetTrajectoryLegendVisibility();"
                "window.fim.enterCompletedState(result, true);})"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "outcomeText: "
                "document.getElementById('results-outcome').textContent"
                "})",
                lambda value: (
                    value is not None and value.get("runViewState") == "completed"
                ),
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["runViewState"] == "completed"
    assert "varies across members" in settled["outcomeText"]
    assert "d" in settled["outcomeText"]


def test_home_run_count_label_never_shows_more_visible_than_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count label never claims more runs are visible than exist.

    A real, reported bug, distinct from the "722 of 2" double-counting
    one above: a Study can legitimately reference a run entirely outside
    `results/` (`fim.persistence.groups._run_reference_string`'s own
    absolute-path fallback), which `Api.list_home_runs`'s own flat,
    one-level scan of `results/` never finds and never counts. The label
    used to compare `distinctRunCount` (every group's own reachable
    runs, which *does* see that externally-referenced run) against
    `allRecentRuns.length` (which never can) -- comparing two counts of
    different things, not a subset relationship, so "visible" could
    exceed "total" outright with no filter text even typed. Reported
    directly as a real, live "60 of 14 runs" -- traced, in that specific
    case, to years of stale test-run references rather than a genuine
    external reference, but the label's own comparison was equally
    nonsensical either way.

    Reproduced here with a genuine external reference (a run whose own
    files live under `tmp_path`, entirely outside this test's own
    `results/`), added to a Study by absolute path -- the same shape
    `_run_reference_string` documents as a supported, legitimate case,
    not a corrupted one.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    external_run = _write_run(tmp_path, "external-run", seed=2)
    study = groups.create_study("Alpha", results=results)
    groups.add_run_to_study(study.study_id, external_run, results=results)

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            count_text = _poll_until(
                window,
                "document.getElementById('open-run-count').textContent",
                lambda value: value not in (None, ""),
            )
            outcome.put(count_text)
        finally:
            window.destroy()

    webview.start(_drive)
    count_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert count_text is not None
    # `allRecentRuns` (Home's own flat scan) found zero runs under this
    # test's own empty `results/` -- the old, broken comparison would
    # have rendered "1 of 0 runs"; this must never claim more runs are
    # visible than the tree itself actually has.
    assert count_text == "1 run"


def test_a_study_header_counts_only_the_runs_that_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header and the list agree when a Study still references gone runs.

    A real report: "Default study (339 runs)" above a list of five. The
    manifest keeps every directory a run was ever recorded from, including
    ones later deleted, and the header used the manifest's own count while
    the list and the "N runs" line counted what exists.
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    output = _write_run(results, "run-a", seed=1)
    study = groups.get_study(groups.DEFAULT_STUDY_ID, results=results)
    stale = tuple(str(tmp_path / "gone" / f"run-{index}") for index in range(20))
    groups.write_study_manifest(
        groups.study_manifest_path(study.study_id, results=results),
        replace(study, run_directories=(*study.run_directories, *stale)),
    )
    assert output.is_dir()

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
            # The default Study sits inside the default Experiment.
            _expand_every_group(window)
            headers = _poll_until(
                window,
                "Array.from(document.querySelectorAll("
                "'.open-run-group-toggle')).map((b) => b.textContent)",
                lambda value: (
                    value is not None and any("Default study" in text for text in value)
                ),
            )
            count = window.evaluate_js(
                "document.getElementById('open-run-count').textContent"
            )
            outcome.put({"headers": headers, "count": count})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    study_header = next(text for text in result["headers"] if "Default study" in text)
    assert "(1 run)" in study_header
    assert "21" not in study_header
    assert result["count"] == "1 run"


def test_select_all_also_selects_runs_a_study_holds_from_outside_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run recorded from a folder outside `results/` is selected too.

    A real report: after "Select all", some rows under the Default study
    stayed unchecked. The tree shows every run a Study holds, but "Select
    all" used only the runs found by scanning `results/`, so runs recorded
    from other folders (a `--output` elsewhere) were left out of the
    selection and out of "Delete selected".
    """
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    inside = _write_run(results, "run-a", seed=1)
    outside_folder = tmp_path / "elsewhere"
    outside_folder.mkdir()
    outside = _write_run(outside_folder, "run-b", seed=2)
    assert inside.is_dir() and outside.is_dir()

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
            _expand_every_group(window)
            state = _poll_until(
                window,
                "({checked: document.querySelectorAll("
                "'.open-run-select-checkbox:checked').length, "
                "boxes: document.querySelectorAll("
                "'.open-run-select-checkbox').length, "
                "label: document.getElementById("
                "'open-run-selection-count').textContent})",
                lambda value: value is not None and value["boxes"] >= 4,
            )
            outcome.put(state)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    # The default Experiment, the default Study and both runs.
    assert result["label"] == "4 selected"
    assert result["checked"] == result["boxes"] == 4
