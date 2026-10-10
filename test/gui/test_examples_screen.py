"""Headless functional tests for the seeded Examples experiment.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), sections 4.2 and 4.3: Home
seeds the bundled examples when the Examples experiment is missing and
always lists it last.

The bundle is a fixture built from one real, tiny `fim run` (laid out
as `dev/bin/build-examples-catalog` lays out `webui/examples/`), so these
tests do not change whenever the shipped examples do. Every wait polls a
real completion signal (a ready flag, a pending-call counter, or the DOM
state the step produces), never a fixed sleep.
"""

from __future__ import annotations

import json
import queue
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli
from fim import paths as paths_module
from fim.examples import seed
from fim.examples.artifacts import copy_outputs
from fim.gui import app as app_module
from fim.gui.app import create_window
from fim.persistence import groups

from .conftest import poll_page

pytestmark = pytest.mark.gui

# How long to wait for a drive callback's result once `webview.start`
# returns. Every callback hands its result over before destroying the
# window, so this only bounds one that failed without a result.
_DRIVE_TIMEOUT_SECONDS = 10.0
_EARLY_CLOCK = datetime(2020, 1, 1, tzinfo=UTC)

_CONFIG: dict[str, Any] = {
    "name": "Shipped example",
    "description": "A tiny example run.",
    "_read_only": True,
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 5,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "stop_batch_early": False,
}

_TOP_LEVEL_GROUPS = """
JSON.stringify(Array.from(document.querySelectorAll(
    '.open-run-group-header:not(.open-run-group-header-nested)'
    + ' .open-run-group-toggle'
)).map((toggle) => toggle.textContent.replace(/^[▸▾] /, '')))
"""


def _build_bundle(root: Path) -> Path:
    """Write a one-example fixture bundle (saved `manifest.json`, `report.json`).

    The run is made with `-o` into `root`, outside any results folder
    the window reads, so the only copy Home can list is the seeded one.
    """
    config_text = yaml.safe_dump(_CONFIG, sort_keys=False)
    config_path = root / "tiny-example.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    run = root / "tiny-run"
    assert cli.main(["run", str(config_path), "-o", str(run), "--quiet"]) == 0
    bundle = root / "bundle"
    (bundle / "tiny-example").mkdir(parents=True)
    for name in ("manifest.json", "report.json"):
        (bundle / "tiny-example" / name).write_bytes((run / name).read_bytes())
    catalog = {
        "schema_version": 1,
        "generator": "dev/bin/build-examples-catalog",
        "classes": [
            {
                "id": "getting-started",
                "title": "Getting started",
                "description": "One-screen runs.",
                "children": [],
            }
        ],
        "examples": [
            {
                "id": "tiny-example",
                "name": "Shipped example",
                "description": "A tiny example run.",
                "class": "getting-started",
                "readme": "# Shipped example\n",
                "readme_excerpt": "A tiny example run.",
                "config_yaml": config_text,
                "outputs": ["manifest.json", "report.json"],
            },
            {
                "id": "not-run-yet",
                "name": "Not run yet",
                "description": "No saved result.",
                "class": "getting-started",
                "readme": "# Not run yet\n",
                "readme_excerpt": "",
                "config_yaml": config_text.replace("seed: 5", "seed: 6"),
                "outputs": [],
            },
        ],
    }
    (bundle / "catalog.json").write_text(json.dumps(catalog, indent=2), "utf-8")
    return bundle


@pytest.fixture
def results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, examples_seeding: None
) -> Path:
    """An isolated results directory, with a fixture examples bundle installed.

    The bundle's own `fim run` happens first, while the results folder
    is still a different, throwaway one, so it files nothing here.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: staging)
    bundle = _build_bundle(tmp_path)
    monkeypatch.setattr(app_module, "_examples_bundle_directory", lambda: bundle)
    root = tmp_path / "results"
    root.mkdir()
    monkeypatch.setattr(paths_module, "results_directory", lambda: root)
    return root


def _open_home(window: webview.Window) -> None:
    """Wait for the page, show Home, and wait for its tree to load."""
    poll_page(window, "window.__fimRunViewReady === true", lambda value: value)
    window.evaluate_js("window.fim.menu.openRun();")
    poll_page(
        window, "window.__fimOpenRunRecentRunsLoaded === true", lambda value: value
    )


def _top_level_groups(results: Path) -> list[str]:
    """Open Home in a fresh window and return its top-level group labels."""
    window = create_window(hidden=True)
    outcome: queue.Queue[list[str]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _open_home(window)
            outcome.put(json.loads(window.evaluate_js(_TOP_LEVEL_GROUPS)))
        finally:
            window.destroy()

    webview.start(_drive)
    return outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)


def test_home_seeds_a_missing_examples_experiment_and_lists_it_last(
    results: Path,
) -> None:
    """A fresh results folder: the examples appear, after the default Study."""
    labels = _top_level_groups(results)

    assert labels == ["Default experiment (1 study)", "Examples (1 study)"]
    assert groups.get_experiment("experiment-examples", results=results).read_only
    assert seed.example_run_directory("tiny-example", results=results).is_dir()


def test_the_examples_experiment_is_last_even_when_it_is_the_oldest(
    results: Path,
) -> None:
    """Listed last whatever its creation time; a standalone Study comes before."""
    seed.seed_examples(
        results,
        bundle=app_module._examples_bundle_directory(),
        clock=lambda: _EARLY_CLOCK,
    )
    groups.ensure_default_study(results=results)
    groups.create_experiment("Island sizes", results=results)
    groups.create_study("Loose study", results=results)

    labels = _top_level_groups(results)

    assert labels == [
        "Default experiment (1 study)",
        "Island sizes (0 studies)",
        "Loose study (0 runs)",
        "Examples (1 study)",
    ]


def _expand_group(window: webview.Window, label: str) -> None:
    """Expand the Home group whose toggle names `label`, and wait until it is open."""
    toggle = (
        "Array.from(document.querySelectorAll('.open-run-group-toggle'))"
        f".find((button) => button.textContent.includes({label!r}))"
    )
    window.evaluate_js(
        f"(function () {{ const t = {toggle};"
        " if (t && t.getAttribute('aria-expanded') === 'false') { t.click(); }"
        " })()"
    )
    expanded = poll_page(
        window,
        f"(function () {{ const t = {toggle};"
        " return t ? t.getAttribute('aria-expanded') : null; })()",
        lambda value: value == "true",
    )
    assert expanded == "true", f"Home group {label!r} never expanded"


_SNAPSHOT_SAVED_RESULT = """
JSON.stringify({
    state: window.fim.getRunViewState(),
    noteHidden: document.getElementById('run-saved-result-note').hidden,
    noteText: document.getElementById('run-saved-result-text').textContent,
    graphsHidden: document.getElementById('run-graph-body').hidden,
    scrubberHidden: document.getElementById('scrubber-controls').hidden,
    statsHidden: document.getElementById('results-stats').hidden,
    statD: document.getElementById('stat-D').textContent,
    messages: document.getElementById('run-messages').textContent,
})
"""

_SNAPSHOT_CONFIGURE = """
JSON.stringify({
    configureHidden: document.getElementById('screen-configure').hidden,
    runName: document.getElementById('run-name-input').value,
    runDescription: document.getElementById('run-description-input').value,
    seed: document.getElementById('field-seed').value,
    noteHiddenAfterInitial: null,
})
"""


def test_a_seeded_example_opens_from_its_saved_results_and_run_it_loads_it(
    results: Path,
) -> None:
    """Report-only open (design §4.3): statistics, a note, and "Run it"."""
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
            poll_page(
                window, f"{example_row} !== undefined", lambda value: value is True
            )
            window.evaluate_js(
                f"{example_row}.dispatchEvent(new MouseEvent('dblclick',"
                " {bubbles: true}));"
            )
            poll_page(
                window,
                "window.fim.getRunViewState() === 'completed'"
                " && !document.getElementById('run-saved-result-note').hidden",
                lambda value: value is True,
            )
            saved = json.loads(window.evaluate_js(_SNAPSHOT_SAVED_RESULT))
            window.evaluate_js(
                "window.__fimSavedRunLoadSettled = false;"
                "document.getElementById('run-saved-result-run-button').click();"
            )
            poll_page(
                window, "window.__fimSavedRunLoadSettled === true", lambda value: value
            )
            poll_page(
                window,
                "(window.__fimValidationPending || 0) === 0",
                lambda value: value is True,
            )
            configure = json.loads(window.evaluate_js(_SNAPSHOT_CONFIGURE))
            # Leaving `completed` brings the graphs back.
            window.evaluate_js("window.fim.returnToInitialState();")
            configure["noteHiddenAfterInitial"] = poll_page(
                window,
                "document.getElementById('run-saved-result-note').hidden",
                lambda value: value is True,
            )
            poll_page(
                window,
                "(window.__fimScrubberPending || 0) === 0"
                " && (window.__fimValidationPending || 0) === 0",
                lambda value: value is True,
            )
            outcome.put({"saved": saved, "configure": configure})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    saved = result["saved"]
    assert saved["state"] == "completed"
    assert saved["noteHidden"] is False
    assert saved["noteText"] == "Run this example to see its trajectories"
    assert saved["graphsHidden"] is True
    assert saved["scrubberHidden"] is True
    # The statistics and the messages come from the saved report.
    assert saved["statsHidden"] is False
    assert saved["statD"].strip() != ""
    assert "generation" in saved["messages"]
    configure = result["configure"]
    assert configure["configureHidden"] is False
    assert configure["runName"] == "Shipped example"
    assert configure["runDescription"] == "A tiny example run."
    assert configure["seed"] == "5"
    assert configure["noteHiddenAfterInitial"] is True


_SELECT_EXAMPLE = """
(function (id) {
    const item = document.querySelector(`#examples-list [data-example-id='${id}']`);
    item.click();
    const button = document.getElementById('examples-open-saved-button');
    return JSON.stringify({disabled: button.disabled, title: button.title});
})
"""


def test_open_saved_result_opens_the_seeded_run_from_the_examples_dialog(
    results: Path,
) -> None:
    """The dialog's second action opens the saved result; disabled without one."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, "window.__fimRunViewReady === true", lambda value: value)
            window.evaluate_js("window.fim.showExamplesDialog();")
            poll_page(
                window, "window.__fimExamplesDialogReady === true", lambda value: value
            )
            without = json.loads(
                window.evaluate_js(f"{_SELECT_EXAMPLE}('not-run-yet')")
            )
            with_saved = json.loads(
                window.evaluate_js(f"{_SELECT_EXAMPLE}('tiny-example')")
            )
            window.evaluate_js(
                "window.__fimExampleOpenSettled = false;"
                "document.getElementById('examples-open-saved-button').click();"
            )
            poll_page(
                window, "window.__fimExampleOpenSettled === true", lambda value: value
            )
            opened = json.loads(
                poll_page(
                    window,
                    "JSON.stringify({"
                    " state: window.fim.getRunViewState(),"
                    " dialogOpen: document.getElementById('modal-examples').open,"
                    " noteHidden:"
                    "  document.getElementById('run-saved-result-note').hidden,"
                    " directory: window.fim.getCompletedOutputDirectory(),"
                    "})",
                    lambda value: value is not None and '"completed"' in value,
                )
            )
            poll_page(
                window,
                "(window.__fimScrubberPending || 0) === 0",
                lambda value: value is True,
            )
            outcome.put({"without": without, "with": with_saved, "opened": opened})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result["without"]["disabled"] is True
    assert "no saved result yet" in result["without"]["title"]
    assert result["with"]["disabled"] is False
    opened = result["opened"]
    assert opened["state"] == "completed"
    assert opened["dialogOpen"] is False
    assert opened["noteHidden"] is False
    assert opened["directory"] == str(
        seed.example_run_directory("tiny-example", results=results)
    )


def test_a_complete_example_opens_with_graphs_and_a_working_scrubber(
    results: Path,
) -> None:
    """A compressed example opens like a completed user run, without a rerun."""
    bundle = app_module._examples_bundle_directory()
    outputs = copy_outputs(results.parent / "tiny-run", bundle / "tiny-example")
    catalog_path = bundle / "catalog.json"
    catalog = json.loads(catalog_path.read_text("utf-8"))
    catalog["examples"][0]["outputs"] = outputs
    catalog_path.write_text(json.dumps(catalog), "utf-8")
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _open_home(window)
            _expand_group(window, "Examples (")
            _expand_group(window, "Getting started (")
            window.evaluate_js(
                "Array.from(document.querySelectorAll('.open-run-run-row'))"
                ".find((row) => row.textContent.includes('Shipped example'))"
                ".dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));"
            )
            ready = poll_page(
                window,
                "window.fim.getRunViewState() === 'completed'"
                " && (window.__fimScrubberPending || 0) === 0"
                " && !document.getElementById('scrubber-controls').hidden",
                lambda value: value is True,
            )
            assert ready, "completed example never showed its scrubber"
            snapshot = json.loads(window.evaluate_js(_SNAPSHOT_SAVED_RESULT))
            snapshot["graphs"] = window.evaluate_js("availableGraphKeys()")
            snapshot["finalGeneration"] = window.evaluate_js(
                "document.getElementById('scrubber-range').value"
            )
            window.evaluate_js(
                "const slider = document.getElementById('scrubber-range');"
                "slider.value = '0';"
                "slider.dispatchEvent(new Event('input', {bubbles: true}));"
            )
            poll_page(
                window,
                "(window.__fimScrubberPending || 0) === 0",
                lambda value: value is True,
            )
            snapshot["firstGeneration"] = window.evaluate_js(
                "document.getElementById('scrubber-range').value"
            )
            outcome.put(snapshot)
        finally:
            window.destroy()

    webview.start(_drive)
    opened = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)
    assert opened["noteHidden"] is True
    assert opened["graphsHidden"] is False
    assert opened["scrubberHidden"] is False
    assert {"scatter", "trajectory", "alleleComposition", "frequencySpectrum"} <= set(
        opened["graphs"]
    )
    assert int(opened["finalGeneration"]) > 0
    assert opened["firstGeneration"] == "0"
