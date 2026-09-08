"""Headless functional tests for the Compare workspace (botanist GUI
design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §8).

Real DOM-driven proof that the File menu's "Compare runs…" action
reaches the Compare screen, that its recent-runs list is populated from
real completed runs, and that checking two of them and clicking
"Compare" renders the real overlay `Api.compare_runs` returns —
`test/gui/test_app_api.py`'s own `test_compare_runs_*` tests already
prove that bridge method correct as a plain Python call; this file
proves the page's own JavaScript wires it together, which no
Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli
from fim import paths as paths_module
from fim.gui.app import create_window

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300


def _write_run(results_root: Path, name: str, **overrides: object) -> Path:
    """Write a small, real completed run under `results_root / name`.

    Mirrors `test_open_run_screen.py`'s own identically-shaped helper,
    extended with `**overrides` (`test_app_api.py`'s own `_write_run`
    already has the same parameter) so two runs can differ in exactly
    one field, the Compare workspace's own common case.
    """
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(overrides)
    results_root.mkdir(parents=True, exist_ok=True)
    config_path = results_root / f"{name}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = results_root / name
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def _poll_until(
    window: webview.Window, script: str, is_ready: Callable[[Any], bool]
) -> Any:
    """Evaluate `script` repeatedly, sleeping between tries, until it is ready."""
    value: Any = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if is_ready(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def test_compare_runs_menu_action_reaches_the_compare_screen(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Triggering the File menu's "Compare runs…" action shows the Compare screen.

    Polls for `window.__fimCompareRecentRunsLoaded` alongside screen
    visibility, not screen visibility alone — the identical race
    `test_open_run_screen.py`'s own `test_open_run_menu_action_reaches_
    screen_six` already documents for the same "shown synchronously,
    populated asynchronously" shape.
    """
    visible = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.menu.compareRuns();",
        read=(
            "({"
            "screenVisible: "
            "!document.getElementById('screen-compare').hidden, "
            "recentRunsLoaded: "
            "window.__fimCompareRecentRunsLoaded === true"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("screenVisible")
            and value.get("recentRunsLoaded")
        ),
        poll_attempts=500,
    )

    assert visible["screenVisible"] is True
    assert visible["recentRunsLoaded"] is True


def test_compare_button_disabled_until_two_runs_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "Compare" button stays disabled with zero or one run checked."""
    results_root = tmp_path / "results"
    _write_run(results_root, "run-a", seed=1)
    _write_run(results_root, "run-b", seed=2)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results_root)

    window = create_window(hidden=True)
    # Unlike this module's other driver functions, `_drive` below always
    # reaches `outcome.put(...)` with a real dict — no polling wait that
    # could time out first, so there is no `None` case to type for here.
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.compareRuns();")
            _poll_until(
                window,
                "window.__fimCompareRecentRunsLoaded === true",
                lambda value: value is True,
            )
            before = window.evaluate_js(
                "document.getElementById('compare-run-button').disabled"
            )
            window.evaluate_js(
                "document.querySelector("
                "'#compare-recent-runs-body input[type=\"checkbox\"]').click();"
            )
            after_one = window.evaluate_js(
                "document.getElementById('compare-run-button').disabled"
            )
            window.evaluate_js(
                "document.querySelectorAll("
                "'#compare-recent-runs-body input[type=\"checkbox\"]')[1].click();"
            )
            after_two = window.evaluate_js(
                "document.getElementById('compare-run-button').disabled"
            )
            outcome.put(
                {"before": before, "afterOne": after_one, "afterTwo": after_two}
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=30.0)

    assert result["before"] is True
    assert result["afterOne"] is True
    assert result["afterTwo"] is False


def test_comparing_two_runs_renders_panels_and_the_differing_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking two runs differing only in `seed` and comparing renders both panels.

    Same "several sequential trigger-then-poll stages against one live
    window" shape `test_open_run_screen.py`'s own real-run test already
    uses, for the identical reason.
    """
    results_root = tmp_path / "results"
    _write_run(results_root, "run-a", seed=1)
    _write_run(results_root, "run-b", seed=2)
    monkeypatch.setattr(paths_module, "results_directory", lambda: results_root)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.compareRuns();")
            _poll_until(
                window,
                "window.__fimCompareRecentRunsLoaded === true",
                lambda value: value is True,
            )
            window.evaluate_js(
                "for (const checkbox of "
                "document.querySelectorAll("
                "'#compare-recent-runs-body input[type=\"checkbox\"]')) "
                "{ checkbox.click(); }"
            )
            window.evaluate_js("document.getElementById('compare-run-button').click();")
            settled = _poll_until(
                window,
                "({"
                "resultsReady: window.__fimCompareResultsReady === true, "
                "resultsHidden: "
                "document.getElementById('compare-results').hidden, "
                "panelCount: document.querySelectorAll("
                "'.compare-panel canvas').length, "
                "legendText: "
                "document.getElementById('compare-legend').textContent"
                "})",
                lambda value: value is not None and value["resultsReady"] is True,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=30.0)

    assert settled is not None
    assert settled["resultsHidden"] is False
    assert settled["panelCount"] == 2
    assert "seed" in settled["legendText"]


def test_comparing_runs_with_identical_configs_shows_no_differences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two identically-configured runs report no differing field in the legend."""
    results_root = tmp_path / "results"
    _write_run(results_root, "run-a")
    _write_run(results_root, "run-b")
    monkeypatch.setattr(paths_module, "results_directory", lambda: results_root)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.compareRuns();")
            _poll_until(
                window,
                "window.__fimCompareRecentRunsLoaded === true",
                lambda value: value is True,
            )
            window.evaluate_js(
                "for (const checkbox of "
                "document.querySelectorAll("
                "'#compare-recent-runs-body input[type=\"checkbox\"]')) "
                "{ checkbox.click(); }"
            )
            window.evaluate_js("document.getElementById('compare-run-button').click();")
            settled = _poll_until(
                window,
                "({"
                "resultsReady: window.__fimCompareResultsReady === true, "
                "legendText: "
                "document.getElementById('compare-legend').textContent"
                "})",
                lambda value: value is not None and value["resultsReady"] is True,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=30.0)

    assert settled is not None
    assert "No configuration differences" in settled["legendText"]
