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
    name: str,
    seed: int,
    *,
    study_id: str | None = None,
    **overrides: object,
) -> Path:
    """Write a small, real completed run under `results / name`.

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
    config_path = results / f"{name}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = results / name
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if study_id is not None:
        arguments += ["--study", study_id]
    assert cli.main(arguments) == 0
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


def _click_group_button(
    window: webview.Window, group_label: str, button_text: str
) -> None:
    """Click the action button reading `button_text` on the group header
    whose toggle names `group_label`."""
    clicked = window.evaluate_js(
        "(function(label, text) {"
        "var headers = document.querySelectorAll('.open-run-group-header');"
        "for (var header of headers) {"
        "  var toggle = header.querySelector('.open-run-group-toggle');"
        "  if (toggle && toggle.textContent.includes(label)) {"
        "    var buttons = header.querySelectorAll('.open-run-group-action-button');"
        "    for (var button of buttons) {"
        "      if (button.textContent === text) { button.click(); return true; }"
        "    }"
        "  }"
        "}"
        "return false; })"
        f"({group_label!r}, {button_text!r})"
    )
    assert clicked is True, (
        f"no group header named {group_label!r} had a {button_text!r} button"
    )


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


def _create_study_on_experiment(
    window: webview.Window, experiment_label: str, name: str
) -> None:
    """Submit an Experiment row's own "Create study…" button with `name`."""
    _click_group_button(window, experiment_label, "Create study…")
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
    """A checkout with nothing yet still shows a clickable default Study row.

    §1's own amendment: `ensure_default_study` stays lazy (never called
    at app launch), but Home's own `refreshRecentRuns` calls it once,
    exactly when a visit's own `list_studies`/`list_experiments` both
    come back empty -- otherwise a botanist with nothing yet has no row
    at all to click "Create run…" on, contradicting the whole point of
    this reorg.
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


def test_creating_an_experiment_and_a_study_on_its_row_nests_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row-level "Create experiment…"/"Create study…" nest one action, not two.

    `20260918-claude-sonnet-5-home-tree-reorg-design.md` (`selby/
    restricted`) §4: creating a Study on an Experiment's own row calls
    `create_study` immediately followed by `add_study_to_experiment`,
    already nested -- no separate "Add to experiment…" step needed for
    a Study created this way (that picker still exists, `test_moving_
    an_existing_study_into_an_experiment_via_the_picker`, just below,
    for a Study that already exists elsewhere).
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
            _create_experiment(window, "Topology")
            _create_study_on_experiment(window, "Topology", "Ring sweep")
            _expand_every_group(window)
            outcome.put(window.evaluate_js(_TREE_TEXT))
        finally:
            window.destroy()

    webview.start(_drive)
    tree_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert tree_text is not None
    assert "Topology (1 study)" in tree_text
    assert "Ring sweep (0 runs)" in tree_text


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


def test_copying_a_study_creates_an_independent_copy_with_no_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy needs no name entry -- it derives "<name> copy" and acts immediately."""
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


def test_home_shows_both_runs_of_a_repeated_configuration_but_blanks_the_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs of the identical configuration share a `run_id`; both
    still render, but only the first (newest) shows the id text.

    Reported live: a Study's own expanded view showed the same `run-
    <hash>` label twice, a run apart in time -- `run_id` is a
    deterministic hash of the configuration itself (`fim.engine.
    deterministic_run_id`), not a per-invocation random id, so two
    genuinely distinct run directories sharing an identical
    configuration also share one `run_id`. Both are real, distinct
    executions worth keeping visible (a botanist re-running the same
    configuration on purpose, say, to confirm reproducibility) -- only
    the repeated *label* is noise, so `renderGroup`'s own `showRunId`
    blanks it on the second (older) row rather than hiding the row
    outright.
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
    # Newest-first: the first row shows the shared run id, the second
    # (the older run, immediately following in the same sorted list)
    # leaves it blank.
    assert older_manifest["run_id"] in run_id_texts[0]
    assert run_id_texts[1] == ""
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


def test_opening_a_study_row_pools_its_own_member_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Study row's own "Open…" pools every member run into the batch
    Results card.

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
            _click_group_button(window, "Ring sweep", "Open…")
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


def test_opening_a_study_with_a_mismatched_parameter_shows_a_note_but_still_pools(
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
            _click_group_button(window, "Ring sweep", "Open…")
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
