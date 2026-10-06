"""Headless functional tests for read-only examples in the GUI.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), section 3: a read-only
Experiment, Study, or Run shows a lock badge wherever its name appears
(Home's tree, the details dialog, the Run card's title), and every edit
control on it is disabled with a tooltip saying why, while viewing and
copying stay available. `test_read_only_bridge.py` proves the bridge
refuses the edits; this file proves the page never offers them.

Every wait polls a real completion signal (a ready flag, a pending-call
counter, or the DOM state the step produces), never a fixed sleep.
"""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli
from fim import paths as paths_module
from fim.gui.app import create_window
from fim.persistence import groups

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
_DRIVE_TIMEOUT_SECONDS = 4 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0
_FIXED_CLOCK = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

_SNAPSHOT_HOME = """
(function () {
    const header = (label) => Array.from(
        document.querySelectorAll('.open-run-group-header')
    ).find((row) => row.querySelector('.open-run-group-toggle')
        .textContent.includes(label));
    const describe = (row) => ({
        badge: row.querySelector('.read-only-badge') !== null,
        checkboxDisabled: row.querySelector('.open-run-select-checkbox').disabled,
        buttons: Object.fromEntries(Array.from(
            row.querySelectorAll('.open-run-group-action-button')
        ).map((button) => [button.textContent, {
            disabled: button.disabled, title: button.title,
        }])),
    });
    const exampleRow = Array.from(document.querySelectorAll('.open-run-run-row'))
        .find((row) => row.textContent.includes('Shipped example'));
    return JSON.stringify({
        experiment: describe(header('Examples (')),
        study: describe(header('Getting started (')),
        run: {
            badge: exampleRow.querySelector('.read-only-badge') !== null,
            badgeTitle: exampleRow.querySelector('.read-only-badge').title,
            checkboxDisabled:
                exampleRow.querySelector('.open-run-select-checkbox').disabled,
            loadButtonText: exampleRow.querySelector(
                '.open-run-load-config-button').textContent,
            loadButtonDisabled: exampleRow.querySelector(
                '.open-run-load-config-button').disabled,
        },
    });
})()
"""

_SNAPSHOT_DETAILS = """
JSON.stringify({
    open: document.getElementById('modal-details').open,
    badge: document.querySelector('#details-title .read-only-badge') !== null,
    saveDisabled: document.getElementById('details-save-button').disabled,
    nameReadOnly: document.getElementById('details-name').readOnly,
    noteHidden: document.getElementById('details-read-only-note').hidden,
    note: document.getElementById('details-read-only-note').textContent,
})
"""


def _write_run(results: Path, folder: str, **overrides: object) -> Path:
    """Write a small, real completed run under `results / folder`."""
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 3,
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
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def _seed_read_only_examples(results: Path) -> Path:
    """One read-only example run, in a read-only Study, in the Examples Experiment.

    The run is real (`fim run` of a configuration carrying `_read_only:
    true` and a name), so it can also be opened; it also lands in the
    default Study, as every bare `fim run` does, beside an ordinary run.
    """
    example = _write_run(
        results, "run-example", _read_only=True, name="Shipped example", seed=4
    )
    _write_run(results, "run-mine")
    groups.write_read_only_study(
        "study-examples-getting-started",
        name="Getting started",
        run_directories=[example],
        results=results,
        clock=lambda: _FIXED_CLOCK,
    )
    groups.write_read_only_experiment(
        "experiment-examples",
        name="Examples",
        study_ids=["study-examples-getting-started"],
        results=results,
        clock=lambda: _FIXED_CLOCK,
    )
    return example


def _poll_until(
    window: webview.Window, script: str, is_ready: Callable[[Any], bool]
) -> Any:
    """Evaluate `script` until `is_ready` accepts its value, or attempts run out."""
    value: Any = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if is_ready(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def _expand_group(window: webview.Window, label: str) -> None:
    """Expand the Home group whose toggle names `label`, and wait until it is open.

    The toggle re-renders the tree once its Study's runs have arrived
    (`get_study_run_summary`), so the expanded state itself is the
    completion signal.
    """
    toggle = (
        "Array.from(document.querySelectorAll('.open-run-group-toggle'))"
        f".find((button) => button.textContent.includes({label!r}))"
    )
    clicked = window.evaluate_js(
        f"(function () {{ const t = {toggle}; if (!t) {{ return false; }}"
        " if (t.getAttribute('aria-expanded') === 'false') { t.click(); }"
        " return true; })()"
    )
    assert clicked is True, f"no Home group named {label!r}"
    expanded = _poll_until(
        window,
        f"(function () {{ const t = {toggle};"
        " return t ? t.getAttribute('aria-expanded') : null; })()",
        lambda value: value == "true",
    )
    assert expanded == "true", f"Home group {label!r} never expanded"


def _open_home(window: webview.Window) -> None:
    """Wait for the page, show Home, and wait for its tree to load."""
    _poll_until(window, "window.__fimRunViewReady === true", lambda value: value)
    window.evaluate_js("window.fim.menu.openRun();")
    _poll_until(
        window, "window.__fimOpenRunRecentRunsLoaded === true", lambda value: value
    )


def test_home_locks_read_only_items_and_disables_their_edit_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Examples retain locks and Clone without the removed hierarchy actions."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    _seed_read_only_examples(results)
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _open_home(window)
            _expand_group(window, "Examples (")
            _expand_group(window, "Getting started (")
            _poll_until(
                window,
                "document.querySelectorAll('.open-run-run-row .read-only-badge')"
                ".length",
                lambda value: value == 1,
            )
            snapshot = json.loads(window.evaluate_js(_SNAPSHOT_HOME))
            window.evaluate_js(
                "document.getElementById('open-run-select-all-button').click();"
            )
            snapshot["deleteSelected"] = _poll_until(
                window,
                "document.getElementById('open-run-delete-selected-button')"
                ".textContent",
                lambda value: value != "Delete selected",
            )
            outcome.put(snapshot)
        finally:
            window.destroy()

    webview.start(_drive)
    snapshot = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    experiment = snapshot["experiment"]
    assert experiment["badge"] is True
    assert experiment["checkboxDisabled"] is True
    assert experiment["buttons"] == {}
    study = snapshot["study"]
    assert study["badge"] is True
    assert study["checkboxDisabled"] is True
    assert study["buttons"] == {}
    run = snapshot["run"]
    assert run["badge"] is True
    assert "read-only example" in run["badgeTitle"]
    assert run["checkboxDisabled"] is True
    assert run["loadButtonText"] == "Clone"
    assert run["loadButtonDisabled"] is False
    # "Select all": the default Experiment, the default Study, and the two
    # runs it holds, never the example's own Study or Experiment. The
    # example run is counted because the editable default Study holds it
    # (a bare `fim run` files every run there); deleting that Study keeps
    # it, which `test_read_only_bridge.py` proves.
    assert snapshot["deleteSelected"] == "Delete selected (4)"


def test_details_dialog_and_run_title_show_a_read_only_run_as_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The details dialog cannot save, and the opened run's title has the lock."""
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    _seed_read_only_examples(results)
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    example_row = (
        "Array.from(document.querySelectorAll('.open-run-run-row'))"
        ".find((row) => row.textContent.includes('Shipped example'))"
    )

    def _drive() -> None:
        try:
            _open_home(window)
            _expand_group(window, "Examples (")
            _expand_group(window, "Getting started (")
            _poll_until(
                window, f"{example_row} !== undefined", lambda value: value is True
            )
            window.evaluate_js(
                "window.__fimDetailsDialogReady = false;"
                f"{example_row}.querySelector('.details-button').click();"
            )
            _poll_until(
                window, "window.__fimDetailsDialogReady === true", lambda value: value
            )
            details = json.loads(window.evaluate_js(_SNAPSHOT_DETAILS))
            window.evaluate_js("document.getElementById('modal-details').close();")
            window.evaluate_js(
                f"{example_row}.dispatchEvent(new MouseEvent('dblclick',"
                " {bubbles: true}));"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState() === 'completed'"
                " && window.__fimRunTitleReady === true",
                lambda value: value is True,
            )
            title_badges = _poll_until(
                window,
                "document.querySelectorAll('#run-plot-title .read-only-badge').length",
                lambda value: value == 2,
            )
            _poll_until(
                window,
                "(window.__fimScrubberPending || 0) === 0",
                lambda value: value is True,
            )
            outcome.put({"details": details, "titleBadges": title_badges})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    details = result["details"]
    assert details["open"] is True
    assert details["badge"] is True
    assert details["saveDisabled"] is True
    assert details["nameReadOnly"] is True
    assert details["noteHidden"] is False
    assert "Load it into Configure" in details["note"]
    # Two badges: the Examples Experiment (the run's non-default Study
    # names it) and the example Run itself; a Run's title names no Study.
    assert result["titleBadges"] == 2
