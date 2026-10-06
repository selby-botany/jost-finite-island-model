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
from fim.examples import seed
from fim.gui import app as app_module
from fim.gui.app import create_window
from fim.persistence import groups

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
_DRIVE_TIMEOUT_SECONDS = 4 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0
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
    "convergence_window": 4,
    "convergence_tolerance": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "replicate_tolerance": None,
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
            }
        ],
    }
    (bundle / "catalog.json").write_text(json.dumps(catalog, indent=2), "utf-8")
    return bundle


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def _open_home(window: webview.Window) -> None:
    """Wait for the page, show Home, and wait for its tree to load."""
    _poll_until(window, "window.__fimRunViewReady === true", lambda value: value)
    window.evaluate_js("window.fim.menu.openRun();")
    _poll_until(
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
