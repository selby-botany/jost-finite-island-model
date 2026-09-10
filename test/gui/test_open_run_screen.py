"""Headless functional tests for Screen 6, opening an existing run
(`doc/fim-gui-design.md` §9).

Real DOM-driven proof that the File menu's "Open run…" action reaches
Screen 6, that Screen 6's recent-runs list is populated from a real, completed
run, and that selecting and opening it re-renders Screen 3 with the real
content `Api.open_run` returns — `test/gui/test_app_api.py`'s own tests
already prove `Api.list_recent_runs`/`open_run` correct as plain Python
calls; this file proves the page's own JavaScript wires them together,
which no Python-only test can check.

Every interaction here is a plain, synchronous request/response bridge
call (`list_recent_runs`, `open_run`) — no background thread ever pushes
anything for these screens, so none of `test/gui/test_running_screen.py`'s
own concurrent-`evaluate_js` concerns apply. `test_selecting_and_opening_
a_recent_run_renders_screen_three` still drives its window directly
(not via the shared `drive` fixture): it needs several sequential
trigger-then-poll stages against the *same* live window (show Screen 6,
wait for the async-populated row, click it, click Open, wait for Screen
3), and `conftest.py`'s `drive_and_read` destroys the window in its own
`finally` block after one such stage. Every wait here is Python-side
polling of a real `window.evaluate_js` read, never a `setTimeout` loop
inside a trigger — `conftest.py`'s own module docstring records why an
async trigger with an internal wait loop hangs the driver thread
indefinitely.
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
# Generous margin over the raw-driven test's own three sequential poll
# stages, each individually bounded by `_POLL_ATTEMPTS`.
_DRIVE_TIMEOUT_SECONDS = 3 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0


def _write_batch_run(tmp_path: Path) -> Path:
    """Write a small, real completed batch (3 replicates) and return its directory."""
    config = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 3,
        "replicate_tolerance": None,
    }
    config_path = tmp_path / "batch.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "results" / "batch-output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def _write_run(tmp_path: Path) -> Path:
    """Write a small, real completed run under `tmp_path` and return its directory."""
    config = {
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
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "results" / "run-output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def test_open_run_menu_action_reaches_screen_six(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Triggering the File menu's "Open run…" action shows Screen 6.

    Polls for `window.__fimOpenRunRecentRunsLoaded` alongside screen
    visibility, not screen visibility alone: `showOpenRunScreen` shows
    the screen synchronously, then fires its own `refreshRecentRuns()`
    (an async `list_recent_runs()` bridge call) without awaiting it
    (`open-run.js`'s own comment on that flag explains why). Screen
    visibility alone was `True` well before that call settled, so
    `drive`'s own `window.destroy()` (via this file's `window` fixture)
    used to tear the window down while `Api.list_recent_runs()`'s
    result was still in flight back to pywebview's own JS bridge on its
    own delivery thread — a real, if rare, trigger for a very slow
    interpreter-shutdown stall (that background thread outliving the
    window it was about to call back into), not merely a stray
    unhandled-thread-exception warning.
    """
    visible = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.menu.openRun();",
        read=(
            "({"
            "screenVisible: "
            "!document.getElementById('screen-open-run').hidden, "
            "recentRunsLoaded: "
            "window.__fimOpenRunRecentRunsLoaded === true"
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


def _poll_until(
    window: webview.Window, script: str, is_ready: Callable[[Any], bool]
) -> Any:
    """Evaluate `script` repeatedly, sleeping between tries, until `is_ready` says stop.

    Never waits inside JavaScript itself — see this module's own
    docstring.
    """
    value: Any = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if is_ready(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def test_selecting_and_opening_a_recent_run_renders_screen_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real recent run, selected and opened, ends on a populated Screen 3."""
    output = _write_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            row_count = _poll_until(
                window,
                "document.getElementById('open-run-recent-runs-body').children.length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    "document.querySelector('#open-run-recent-runs-body tr').click();"
                )
                window.evaluate_js(
                    "document.getElementById('open-run-open-button').click();"
                )
                settled = _poll_until(
                    window,
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "runId: "
                    "document.getElementById('results-run-id').textContent, "
                    "trajectoryFrameHidden: "
                    "document.getElementById('run-trajectory-frame').hidden"
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

    assert settled is not None, "`completed` was never reached after opening the run"
    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    assert output.exists()
    # `Api.open_run`'s own payload carries neither `convergenceGenerations`
    # nor `convergenceHistories` (no live monitor to have recorded a
    # history from a re-analyzed run) — botanist GUI design doc §6.2's
    # trajectory panel is a live-run-only first slice, a named scope
    # boundary, not an oversight.
    assert settled["trajectoryFrameHidden"] is True


def test_opening_a_run_with_a_differentiation_q_sweep_draws_the_curve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested differentiation-q sweep renders both the lines and the curve.

    No existing test drove this specific field through the real DOM at
    all before this one (`test/gui/test_app_api.py`'s own `Api.open_run`
    coverage only ever calls it as a plain Python function) — this is
    also the first real proof that `run-view-completed.js`'s own
    `drawDifferentiationQCurve` (botanist GUI design doc `20260907-
    claude-sonnet-5-botanist-gui-redesign.md` §7.7) actually draws
    something, not only that the per-order text lines still render.
    """
    _write_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            row_count = _poll_until(
                window,
                "document.getElementById('open-run-recent-runs-body').children.length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    "document.querySelector('#open-run-recent-runs-body tr').click();"
                )
                window.evaluate_js(
                    "document.getElementById('open-run-differentiation-orders')"
                    ".value = '0, 1, 2';"
                )
                window.evaluate_js(
                    "document.getElementById('open-run-open-button').click();"
                )
                settled = _poll_until(
                    window,
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "qHidden: "
                    "document.getElementById('results-differentiation-q').hidden, "
                    "lineCount: "
                    "document.getElementById('results-differentiation-q-lines')"
                    ".children.length, "
                    "canvasWidth: "
                    "document.getElementById('results-differentiation-q-canvas')"
                    ".width"
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

    assert settled is not None, "`completed` was never reached after opening the run"
    assert settled["qHidden"] is False
    assert settled["lineCount"] == 3
    assert settled["canvasWidth"] > 0


def test_recent_runs_row_shows_config_summary_and_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home enrichment design doc's own per-row columns actually render.

    `test/gui/test_app_api.py`'s own `test_list_home_runs_*` tests
    already prove `Api.list_home_runs` itself is correct as a plain
    Python call; this proves `open-run.js`'s own `refreshRecentRuns`
    actually calls it (not the older `list_recent_runs`) and renders
    both new columns, including the full text still being reachable via
    each cell's own `title` attribute once the compact text is
    ellipsized.
    """
    _write_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            settled = _poll_until(
                window,
                "(function(){"
                "var row = document.querySelector("
                "'#open-run-recent-runs-body tr'); "
                "if (!row) { return null; } "
                "var cells = row.children; "
                "return {"
                "cellCount: cells.length, "
                "configText: cells[3].textContent, "
                "configTitle: cells[3].title, "
                "statisticsText: cells[4].textContent"
                "};"
                "})()",
                lambda value: value is not None,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["cellCount"] == 5
    assert "N=20" in settled["configText"]
    assert "mu=0.01" in settled["configText"]
    assert settled["configTitle"] == settled["configText"]
    assert "D=" in settled["statisticsText"]
    assert "G_ST=" in settled["statisticsText"]


def test_a_batch_rows_statistics_cell_names_its_own_replicate_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch row's own Statistics cell leads with `ciCaption`'s own text.

    Botanist GUI design doc §7.2: the cross-replicate confidence
    interval is "explicitly re-labeled everywhere it appears... as
    'uncertainty across N independent replicates.'" Stated once per
    row, not once per statistic (`open-run.js`'s own `formatRowStatistics`
    docstring has the full reasoning) — a scalar row's own cell (the
    test above) carries no such caption at all, since a point value has
    no replicate count to name.
    """
    _write_batch_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            statistics_text = _poll_until(
                window,
                "(function(){"
                "var row = document.querySelector("
                "'#open-run-recent-runs-body tr'); "
                "return row ? row.children[4].textContent : null;"
                "})()",
                lambda value: value is not None,
            )
            outcome.put(statistics_text)
        finally:
            window.destroy()

    webview.start(_drive)
    statistics_text = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert statistics_text is not None
    assert statistics_text.startswith("(uncertainty across 3 independent replicates)")


def test_expanding_a_batch_row_shows_its_own_replicate_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home enrichment design doc's own approach B1: expand, then collapse.

    Clicking a batch row's own toggle reveals one row per replicate
    (`Api.get_batch_replicate_summary`, already proven correct as a
    plain Python call in `test_app_api.py`); clicking it again removes
    them. Clicking a replicate row selects its own trajectory for
    "Open ▶", the same selection mechanism a scalar row's own click
    already uses.
    """
    _write_batch_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "document.querySelector('.open-run-replicate-toggle') !== null",
                lambda value: value is True,
            )
            before_count = window.evaluate_js(
                "document.querySelectorAll('#open-run-recent-runs-body tr').length"
            )
            window.evaluate_js(
                "document.querySelector('.open-run-replicate-toggle').click();"
            )
            after_expand_count = _poll_until(
                window,
                "document.querySelectorAll('#open-run-recent-runs-body tr').length",
                lambda value: value is not None and value > 1,
            )
            replicate_texts = window.evaluate_js(
                "Array.from(document.querySelectorAll("
                "'.open-run-replicate-row td:nth-child(3)'))"
                ".map((cell) => cell.textContent)"
            )
            window.evaluate_js(
                "document.querySelector('.open-run-replicate-row').click();"
            )
            open_button_disabled = window.evaluate_js(
                "document.getElementById('open-run-open-button').disabled"
            )
            window.evaluate_js(
                "document.querySelector('.open-run-replicate-toggle').click();"
            )
            after_collapse_count = _poll_until(
                window,
                "document.querySelectorAll('#open-run-recent-runs-body tr').length",
                lambda value: value == before_count,
            )
            outcome.put(
                {
                    "beforeCount": before_count,
                    "afterExpandCount": after_expand_count,
                    "replicateTexts": replicate_texts,
                    "openButtonDisabled": open_button_disabled,
                    "afterCollapseCount": after_collapse_count,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["beforeCount"] == 1
    assert settled["afterExpandCount"] == 4
    assert settled["replicateTexts"] == ["#1", "#2", "#3"]
    assert settled["openButtonDisabled"] is False
    assert settled["afterCollapseCount"] == 1


def test_home_new_run_card_opens_configure(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Home enrichment design doc's own slice 3: "New run" reaches Configure.

    Pure navigation, no new bridge call — `Api.list_home_runs`'s own
    empty-`results/` case is enough here, no real run needs writing.
    `is_ready` checks for `False` specifically, not merely "not `None`"
    — a real race an earlier version of this test hit live: `hidden`'s
    own *starting* value (`True`, before the click has even fired) is
    already non-`None`, so a looser check accepted it on the very first
    poll, before the `setTimeout` callback had a chance to run at all.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.showOpenRunScreen(); "
            "setTimeout(() => { "
            "document.getElementById('home-new-run-button').click(); "
            "}, 0);"
        ),
        read="document.getElementById('screen-configure').hidden",
        is_ready=lambda value: value is False,
    )

    assert settled is False


def test_home_explore_card_opens_explore(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Home enrichment design doc's own slice 3: "Explore" reaches Explore."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.showOpenRunScreen(); "
            "setTimeout(() => { "
            "document.getElementById('home-explore-button').click(); "
            "}, 0);"
        ),
        read="document.getElementById('screen-explore').hidden",
        is_ready=lambda value: value is False,
    )

    assert settled is False
