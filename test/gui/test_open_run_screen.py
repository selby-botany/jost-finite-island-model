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

import json
import queue
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
# Generous margin over the raw-driven test's own three sequential poll
# stages, each individually bounded by `_POLL_ATTEMPTS`.
_DRIVE_TIMEOUT_SECONDS = 3 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0
# A real, selectable run row, excluding a date-bucket group's own header
# row (`open-run.js`'s own `buildGroupHeaderRow`) -- shared so every
# "count/select an actual run row" query below stays under this
# project's own 88-column line limit.
_REAL_ROW_SELECTOR = (
    "'#open-run-recent-runs-body "
    "tr:not(.open-run-group-header):not(.open-run-column-header-row)'"
)


def _write_batch_run(tmp_path: Path, *, study_id: str | None = None) -> Path:
    """Write a small, real completed batch (3 replicates) and return its directory.

    `study_id`: see `_write_run`'s own identical parameter.
    """
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
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if study_id is not None:
        arguments += ["--study", study_id]
    assert cli.main(arguments) == 0
    return output_directory


def _write_run(tmp_path: Path, *, study_id: str | None = None) -> Path:
    """Write a small, real completed run under `tmp_path` and return its directory.

    `study_id`, when given, attaches the run to that Study directly at
    creation time via `fim run --study` -- the CLI's own bare `fim run`
    now attaches to the always-present default Study instead of leaving
    the run unattached (`20260918-claude-sonnet-5-home-tree-reorg-
    design.md`, `selby/restricted`, §2), so an explicit `study_id` is
    the only way a test can put a run somewhere else.
    """
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
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if study_id is not None:
        arguments += ["--study", study_id]
    assert cli.main(arguments) == 0
    return output_directory


def _write_run_with_sigma_band(tmp_path: Path) -> Path:
    """Write a small, real completed run requesting a sigma band, return its directory.

    Sigma-band GUI design doc `20260910-claude-sonnet-5-gui-sigma-band-
    design.md` (`selby/restricted`) slice 4 — a longer `max_generations`
    than `_write_run`'s own (so there is real room for the extension
    window after convergence) and an explicit `sigma_band_window`
    short enough to keep this test fast.
    """
    config = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 30,
        "n_replicates": 1,
        "replicate_tolerance": None,
        "sigma_band_multiplier": 3.0,
        "sigma_band_window": 5,
    }
    config_path = tmp_path / "sigma-run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "results" / "sigma-run-output"
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


def _expand_all_recent_run_groups(window: webview.Window) -> None:
    """Click every date-bucket group's own toggle so its rows render.

    Every group starts collapsed by default (item 2: "closed submenus by
    default", `ensureGroupDefaults`) — most tests here care about the
    rows themselves, not the grouping UI, so they call this once right
    after the screen's own recent-runs fetch settles rather than
    re-deriving "expand everything" inline at each call site.

    Clicks in several rounds, not one: `"Earlier"` is itself a parent
    group (item 3) whose own per-date sub-groups only appear in the DOM
    -- collapsed by their own separate default -- once `"Earlier"`
    itself has already been expanded, so a single pass over the
    toggles visible at the start would miss them entirely.

    Waits for `window.__fimOpenRunRecentRunsLoaded === true`, not only
    for a toggle to exist: the app's own launch sequence now shows Home
    by default and pre-populates it (`run-view-initial.js`'s own
    `initializeRunView`), so a toggle from that earlier, unrelated fetch
    can already be sitting in the DOM the moment a caller's own trigger
    (`menu.openRun()`/`showOpenRunScreen()`) fires a fresh one -- a
    toggle-only check would then proceed immediately against that stale
    render, only for the fresh fetch to replace it out from under this
    function moments later (confirmed live: a real `TypeError: null is
    not an object` clicking a row this function had just expanded,
    because `refreshRecentRuns()`'s own `recentRunsBody.replaceChildren
    ()` had already rebuilt the table again in between). The loaded
    flag is reset to `false` synchronously by `refreshRecentRuns()`'s
    own first statement, before any of its own `await`s ever suspend it
    -- by the time a caller's own triggering `evaluate_js` call returns
    to Python, a fresh fetch has therefore already flipped it, so
    waiting for `true` again here can only mean *this* fetch's own
    render, never a stale one.
    """
    _poll_until(
        window,
        "({"
        "loaded: window.__fimOpenRunRecentRunsLoaded === true, "
        "toggleCount: document.querySelectorAll('.open-run-group-toggle').length"
        "})",
        lambda value: (
            value is not None and value["loaded"] is True and value["toggleCount"] > 0
        ),
    )
    for _ in range(6):
        window.evaluate_js(
            "Array.from(document.querySelectorAll("
            "'.open-run-group-toggle[aria-expanded=\"false\"]'"
            ")).forEach((b) => b.click());"
        )
        # A Study group's own toggle click is async (`buildGroupHeaderRow`'s
        # own handler awaits `get_study_run_summary` before its own rows,
        # and any further-nested date-bucket toggles inside it, ever reach
        # the DOM) -- every run now belongs to some real Study, the
        # always-present default one at worst (`20260918-claude-sonnet-5-
        # home-tree-reorg-design.md`, `selby/restricted`, §1/§2), so this
        # function's own toggle-expansion loop hits that await on every
        # single call now, not only for a test that happens to create a
        # Study. Without this sleep, five successive `evaluate_js` round
        # trips easily outrace one bridge call, leaving `remaining` non-
        # zero forever and this loop's own 5-round budget exhausted before
        # any run row ever renders -- confirmed live as the exact cause of
        # a real `rowCount == 0` failure across most of this file's own
        # tests when the "Unsorted" bucket (synchronous, no bridge call at
        # all) was removed in favor of the default Study.
        time.sleep(0.15)
        remaining = window.evaluate_js(
            "document.querySelectorAll("
            "'.open-run-group-toggle[aria-expanded=\"false\"]').length"
        )
        if remaining == 0:
            break


def test_selecting_and_opening_a_recent_run_renders_screen_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real recent run, selected and opened, ends on a populated Screen 3."""
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    output = _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            row_count = _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    f"document.querySelector({_REAL_ROW_SELECTOR}).click();"
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


def test_double_clicking_a_recent_run_row_opens_it_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Double-clicking a run row opens it, without a separate "Open" click.

    Design item 5: the same "final generation, no sweep" shortcut a
    single click plus the "Open" button already gives (the test right
    above this one), reached in one interaction instead of two --
    `open-run.js`'s own `openTrajectory`, shared by both paths.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    output = _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            row_count = _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    f"document.querySelector({_REAL_ROW_SELECTOR})"
                    ".dispatchEvent(new MouseEvent("
                    "'dblclick', {bubbles: true}));"
                )
                settled = _poll_until(
                    window,
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "runId: "
                    "document.getElementById('results-run-id').textContent, "
                    "screenOpenRunHidden: "
                    "document.getElementById('screen-open-run').hidden"
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

    assert settled is not None, "`completed` was never reached after double-clicking"
    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    assert settled["screenOpenRunHidden"] is True
    assert output.exists()


def test_double_clicking_a_batch_row_opens_its_pooled_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Double-clicking a batch row opens the identical batch Results card
    a live batch's own completion already shows.

    `20260919-claude-sonnet-5-unified-batch-and-study-results-reopen-
    design.md` (`selby/restricted`), §3: batch and scalar rows are
    symmetric now -- `open-run.js`'s own `openBatch`, reached the
    identical way `openTrajectory` already is for a scalar row (the
    test right above this one). "Open replicate," reached by expanding
    the row instead, is a separate, still-available way to open one
    specific replicate's own scalar result.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                f"document.querySelector({_REAL_ROW_SELECTOR})"
                ".dispatchEvent(new MouseEvent("
                "'dblclick', {bubbles: true}));"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "runId: "
                "document.getElementById('results-run-id').textContent, "
                "screenOpenRunHidden: "
                "document.getElementById('screen-open-run').hidden, "
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

    assert settled is not None, "`completed` was never reached after double-clicking"
    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    assert settled["screenOpenRunHidden"] is True
    assert settled["batchTableHidden"] is False
    # 4, not 3: `renderBatchTable` prepends a p0 baseline row (the
    # shared initial conditions) ahead of the batch's own 3 replicate
    # rows -- `open_batch` recomputes `p0Statistics` fresh from the
    # batch's own manifest params, exactly like a live batch's own
    # "done" payload already does.
    assert settled["replicateRowCount"] == 4


def test_selecting_and_opening_a_batch_row_via_the_open_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single click selects a batch row and enables "Open," exactly
    like a scalar row -- no more early-return banner (`20260919-claude-
    sonnet-5-unified-batch-and-study-results-reopen-design.md`,
    `selby/restricted`, §3)."""
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(f"document.querySelector({_REAL_ROW_SELECTOR}).click();")
            open_button_disabled_after_select = window.evaluate_js(
                "document.getElementById('open-run-open-button').disabled"
            )
            banner_hidden_after_select = window.evaluate_js(
                "document.getElementById('open-run-banner').hidden"
            )
            window.evaluate_js(
                "document.getElementById('open-run-open-button').click();"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "batchTableHidden: "
                "document.getElementById('batch-results-table').hidden"
                "})",
                lambda value: (
                    value is not None and value.get("runViewState") == "completed"
                ),
            )
            outcome.put(
                {
                    "openButtonDisabledAfterSelect": open_button_disabled_after_select,
                    "bannerHiddenAfterSelect": banner_hidden_after_select,
                    "settled": settled,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert result is not None
    assert result["openButtonDisabledAfterSelect"] is False
    assert result["bannerHiddenAfterSelect"] is True
    assert result["settled"]["runViewState"] == "completed"
    assert result["settled"]["batchTableHidden"] is False


def test_reanalyzing_at_a_chosen_generation_updates_the_outcome_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing "choose" plus a generation re-analyzes at that generation.

    Design item 6's other half (the sweep is the test right below this
    one): `#results-outcome`'s own text states the generation the
    current report is for (`run-view-completed.js`'s own
    `enterCompletedState`), so re-analyzing at a different, explicit
    generation than the one the run opened at (its final one) is
    observable directly from that text, without needing to inspect the
    stats table's own numbers.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            row_count = _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    f"document.querySelector({_REAL_ROW_SELECTOR}).click();"
                )
                window.evaluate_js(
                    "document.getElementById('open-run-open-button').click();"
                )
                opened_outcome = _poll_until(
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
                window.evaluate_js(
                    "document.querySelector("
                    '\'input[name="results_generation_mode"]'
                    '[value="choose"]\').checked = true;'
                    "document.getElementById('results-generation-value')"
                    ".value = '1';"
                    "document.getElementById('results-reanalyze-button').click();"
                )
                reanalyzed_outcome = _poll_until(
                    window,
                    "document.getElementById('results-outcome').textContent",
                    lambda value: value is not None and "generation 1" in value,
                )
                settled = {
                    "openedOutcome": opened_outcome["outcomeText"],
                    "reanalyzedOutcome": reanalyzed_outcome,
                }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    # The run's own final generation is not 1 (`_write_run`'s own
    # `max_generations=10`, `convergence_window=4` converges well
    # before generation 1 could be its own final one) -- confirming the
    # *opened* text differs from the *reanalyzed* one is what proves the
    # click actually changed something, not merely that "generation 1"
    # happens to already be on screen by coincidence.
    assert "generation 1" not in settled["openedOutcome"]
    assert "generation 1" in settled["reanalyzedOutcome"]


def test_reanalyzing_a_run_with_a_differentiation_q_sweep_draws_the_curve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested differentiation-q sweep renders both the lines and the curve.

    The sweep is entered on the Results card itself (design item 6:
    relocated from the old open-run screen, where it had to be chosen
    *before* opening) -- open the run first, at its default final
    generation with no sweep, then set `#results-differentiation-orders`
    and click `#results-reanalyze-button` to re-analyze in place. No
    existing test drove this specific field through the real DOM at all
    before this one (`test/gui/test_app_api.py`'s own `Api.open_run`
    coverage only ever calls it as a plain Python function) — this is
    also the first real proof that `run-view-completed.js`'s own
    `drawDifferentiationQCurve` (botanist GUI design doc `20260907-
    claude-sonnet-5-botanist-gui-redesign.md` §7.7) actually draws
    something, not only that the per-order text lines still render.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            row_count = _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    f"document.querySelector({_REAL_ROW_SELECTOR}).click();"
                )
                window.evaluate_js(
                    "document.getElementById('open-run-open-button').click();"
                )
                _poll_until(
                    window,
                    "window.fim.getRunViewState()",
                    lambda value: value == "completed",
                )
                window.evaluate_js(
                    "document.getElementById('results-differentiation-orders')"
                    ".value = '0, 1, 2';"
                )
                window.evaluate_js(
                    "document.getElementById('results-reanalyze-button').click();"
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
                        value is not None
                        and value.get("runViewState") == "completed"
                        and value.get("lineCount", 0) > 0
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
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            settled = _poll_until(
                window,
                "(function(){"
                f"var row = document.querySelector({_REAL_ROW_SELECTOR}); "
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
    # 6, not 5: the Run/Study/Experiment hierarchy design (`20260917-
    # claude-sonnet-5-run-study-experiment-hierarchy-design.md`, `selby/
    # restricted`, §6/§10) added a trailing "Actions" column (an "Add to
    # study…" pulldown per run row) -- the bulk-selection checkbox lives
    # inside the existing first cell instead, so it added no column of
    # its own.
    assert settled["cellCount"] == 6
    assert "N=20" in settled["configText"]
    assert "mu=0.01" in settled["configText"]
    assert settled["configTitle"] == settled["configText"]
    assert "D=" in settled["statisticsText"]
    assert "G_ST=" in settled["statisticsText"]


def test_recent_runs_row_hides_fractional_seconds_in_the_ended_column(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The "Ended" column reads to the second, not the microsecond.

    A real completed run's own `manifest.json` `ended_at` carries full
    sub-second precision (`Api.list_home_runs`'s own `endedAt` passes it
    through unchanged) -- `open-run.js`'s own `formatEndedAt` strips it
    for *display* only (`buildRunRow`), so a human scanning the table
    never needs microsecond resolution to recognize when a run finished.
    Grouping/filtering (`dateBucketFor`/`matchesRecentRunsFilter`) still
    use the raw, untrimmed value -- unaffected by this display-only
    formatting, and not this test's own concern.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            settled = _poll_until(
                window,
                "(function(){"
                f"var row = document.querySelector({_REAL_ROW_SELECTOR}); "
                "if (!row) { return null; } "
                "return {endedAtCell: row.children[1].textContent};"
                "})()",
                lambda value: value is not None,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    ended_at_cell = settled["endedAtCell"]
    # A real run's own `ended_at` is written with microsecond precision
    # (e.g. `2026-09-12T13:54:36.292444Z`) -- confirming the rendered
    # cell has no `.` at all is proof the fractional part was actually
    # stripped, not merely that this particular run happened to end on
    # an exact second.
    assert "." not in ended_at_cell
    assert ended_at_cell.endswith("Z")


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
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            statistics_text = _poll_until(
                window,
                "(function(){"
                f"var row = document.querySelector({_REAL_ROW_SELECTOR}); "
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
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                "document.querySelector('.open-run-replicate-toggle') !== null",
                lambda value: value is True,
            )
            before_count = window.evaluate_js(
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length"
            )
            window.evaluate_js(
                "document.querySelector('.open-run-replicate-toggle').click();"
            )
            after_expand_count = _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
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
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
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


def test_opening_a_run_with_a_sigma_band_shows_it_with_no_curve_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reopened run's own sigma band renders — the band alone, no curve.

    Sigma-band GUI design doc `20260910-claude-sonnet-5-gui-sigma-band-
    design.md` (`selby/restricted`) slice 4: `Api.open_run` has no
    `convergenceGenerations`/`convergenceHistories` of its own (re-
    analysis recomputes one generation, never a full history) — the
    trajectory panel still shows, axes sized to the band's own trailing
    window alone, with no simulated-curve legend entry (no curve, no
    per-statistic swatch to show for one) and a real, non-blank shaded
    region.

    `_write_run_with_sigma_band`'s own `N`/`m`/`mu` (`20`/`0.1`/`0.01`)
    are all plain scalars, and its sigma band covers `D` (the config's
    own unset-so-default `convergence_statistic`). The separate
    statistic-color key is intentionally absent; only non-statistic
    overlays keep their own caption-style legend entries.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run_with_sigma_band(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(f"document.querySelector({_REAL_ROW_SELECTOR}).click();")
            window.evaluate_js(
                "document.getElementById('open-run-open-button').click();"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "frameHidden: "
                "document.getElementById('run-trajectory-frame').hidden, "
                "captionHidden: document.getElementById("
                "'run-trajectory-sigma-band-caption').hidden, "
                "captionText: document.getElementById("
                "'run-trajectory-sigma-band-caption').textContent, "
                "legendNames: Array.from("
                "document.querySelectorAll('#run-trajectory-legend span'))"
                ".map((span) => span.textContent).filter((text) => text), "
                "canvasNonBlankPixelCount: (() => {"
                "var c = document.getElementById('run-trajectory-canvas');"
                "var ctx = c.getContext('2d');"
                "var data = ctx.getImageData(0, 0, c.width, c.height).data;"
                "var count = 0;"
                "for (var i = 3; i < data.length; i += 4) {"
                "if (data[i] !== 0) { count += 1; }"
                "}"
                "return count;"
                "})()"
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
    assert settled["frameHidden"] is False
    assert settled["captionHidden"] is False
    assert "3\u03c3" in settled["captionText"]
    assert "5 generations" in settled["captionText"]
    # No curve was ever drawn (`Api.open_run` carries no `convergence*`
    # history at all), so there is no statistic-color key. The
    # identity-recovery curve overlay (`_identity_recovery_reference_
    # payload`) draws unconditionally whenever `N`/`m` alone are plain
    # scalars, so it remains as a non-statistic overlay entry.
    assert settled["legendNames"] == [
        "f₀ (identity recovery, theoretical founder event)",
    ]
    assert settled["canvasNonBlankPixelCount"] > 0


def test_opening_a_run_without_a_sigma_band_still_hides_the_trajectory_panel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary reopened run (no sigma band, no curve) keeps the panel hidden.

    Unchanged behavior — the trajectory panel's own pre-existing
    "nothing to show" case, confirmed still correct now that it shares
    a gate with the new sigma-band-alone case above.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(f"document.querySelector({_REAL_ROW_SELECTOR}).click();")
            window.evaluate_js(
                "document.getElementById('open-run-open-button').click();"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "frameHidden: "
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

    assert settled is not None
    assert settled["frameHidden"] is True


def test_expanding_a_study_shows_every_run_directly_with_no_date_subgroups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Study's own expanded runs render as one flat list, not nested
    further into date-bucket sections.

    Every run now belongs to some real Study or Experiment, so the
    date-bucket grouping this tree used to add one level inside each
    Study (Today/Yesterday/Earlier, further split by calendar day) no
    longer separated anything meaningful -- only three more collapsed
    toggles to open before reaching an actual run row. Removed: a Study
    collapsed by default (`ensureGroupDefaults`) shows zero run rows;
    expanding it directly reveals every one of its own runs, with no
    further per-date grouping to expand.

    Both runs here are bare (`--study` unset), so both land in the
    always-present default Study inside its own default Experiment
    (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
    restricted`, §1/§2).
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)
    batch_directory = _write_batch_run(tmp_path)
    manifest_path = batch_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ended_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            collapsed = _poll_until(
                window,
                "(function(){"
                "var headers = Array.from(document.querySelectorAll("
                "'.open-run-group-header .open-run-group-toggle'))"
                ".map((b) => b.textContent);"
                f"var rows = document.querySelectorAll({_REAL_ROW_SELECTOR}).length;"
                "return {headers: headers, rowCount: rows};"
                "})()",
                lambda value: value is not None and len(value.get("headers", [])) == 1,
            )
            # Every group starts collapsed -- expand Default experiment
            # and Default study (only two levels now) to see every row.
            _expand_all_recent_run_groups(window)
            after_expand = _poll_until(
                window,
                "({"
                f"rowCount: document.querySelectorAll({_REAL_ROW_SELECTOR}).length, "
                "headers: Array.from(document.querySelectorAll("
                "'.open-run-group-header .open-run-group-toggle'))"
                ".map((b) => b.textContent), "
                "dateHeaders: Array.from(document.querySelectorAll("
                "'.open-run-group-header .open-run-group-toggle'))"
                ".filter((b) => /Today|Yesterday|Earlier/.test(b.textContent))"
                ".length"
                "})",
                lambda value: value is not None and value.get("rowCount") == 2,
            )
            outcome.put({"collapsed": collapsed, "afterExpand": after_expand})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    # Collapsed by default: one top-level group header ("Default
    # experiment"), zero run rows -- "Default study" is nested, not
    # rendered until it, too, is expanded.
    assert settled["collapsed"]["rowCount"] == 0
    assert any(
        "Default experiment" in header for header in settled["collapsed"]["headers"]
    )
    # Both runs render directly once Default study is expanded -- no
    # "Today"/"Yesterday"/"Earlier" date-bucket header anywhere.
    assert settled["afterExpand"]["rowCount"] == 2
    assert settled["afterExpand"]["dateHeaders"] == 0


def test_recent_runs_filter_narrows_the_visible_rows_and_updates_the_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filter bar narrows by Study/Experiment name, live
    (`open-run.js`'s own `renderRecentRuns`/`nameMatchesFilter`) -- not a
    second, separate search index that could drift from what actually
    renders, and the count label states how much of the full list is
    currently visible.

    Filters by Study *name*, not by a run's own id/label/date the way an
    earlier revision of this test did: the Run-level free-text filter
    (`matchesRecentRunsFilter`) was removed along with the "Unsorted"
    bucket it only ever applied to (`20260918-claude-sonnet-5-home-
    tree-reorg-design.md`, `selby/restricted`, §5's own amendment) --
    every run now belongs to some real Study, and a Study's own expanded
    view was never filtered by free text even before that change.
    """
    results = tmp_path / "results"
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    ring_sweep = groups.create_study("Ring sweep", results=results)
    topology = groups.create_study("Topology", results=results)
    _write_run(tmp_path, study_id=ring_sweep.study_id)
    _write_batch_run(tmp_path, study_id=topology.study_id)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            # Groups start collapsed by default -- expand them all first
            # so the filter's own effect on row count is what's measured.
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value == 2,
            )
            window.evaluate_js(
                "(function(){"
                "var input = document.getElementById('open-run-filter');"
                "input.value = 'Ring sweep';"
                "input.dispatchEvent(new Event('input', {bubbles: true}));"
                "})();"
            )
            settled = _poll_until(
                window,
                "({"
                f"rowCount: document.querySelectorAll({_REAL_ROW_SELECTOR}).length, "
                "countText: document.getElementById('open-run-count').textContent"
                "})",
                lambda value: value is not None and value.get("rowCount") == 1,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["rowCount"] == 1
    assert settled["countText"] == "1 of 2 runs"


def test_a_reopened_batch_still_offers_its_trajectory_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch opened from the Home list keeps the trajectory in the selector.

    Pooled convergence histories are a byproduct of the live
    `ConvergenceMonitor` and are never written to disk, so a reopened
    batch used to arrive with none -- and since a pooled curve is the
    only trajectory a batch has, `renderBatchTrajectory({})` found zero
    statistic names and hid the pane, taking the entry out of the graph
    selector entirely. Reported directly: "the trajectory graph is
    missing from the pull-down."

    `_rebuilt_pooled_histories` now recomputes them from each
    replicate's own persisted states, so this asserts the end of that
    chain -- what the reader can actually choose to look at.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                f"document.querySelector({_REAL_ROW_SELECTOR})"
                ".dispatchEvent(new MouseEvent("
                "'dblclick', {bubbles: true}));"
            )
            settled = _poll_until(
                window,
                "({"
                "runViewState: window.fim.getRunViewState(), "
                "graphKeys: Array.from(document.getElementById("
                "'run-graph-select').options).map((option) => option.value)"
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

    assert settled is not None, "`completed` was never reached after double-clicking"
    assert "trajectory" in settled["graphKeys"]


def test_reopening_shows_a_busy_indicator_while_the_bridge_call_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening a batch puts a spinner up for as long as the work takes.

    Reopening is no longer a lookup: every member replicate's own
    trajectory is re-read and its convergence history recomputed
    (`Api._rebuilt_pooled_histories`), which on a large batch runs for
    seconds with nothing else on screen to say so.

    Observed by standing in for the bridge call itself and recording
    the indicator's own visibility at the moment it is entered -- a
    real reopen of a test-sized batch finishes far too quickly to
    catch by polling, which would make the test a race rather than a
    measurement.
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_batch_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                "window.__fimBusyProbe = null;"
                "window.pywebview.api.open_batch = function () {"
                "  const busy = document.getElementById('open-run-busy');"
                "  window.__fimBusyProbe = {"
                "    hiddenDuring: busy.hidden,"
                "    label: document.getElementById("
                "      'open-run-busy-label').textContent"
                "  };"
                "  return Promise.resolve({ok: false, message: 'stubbed'});"
                "};"
            )
            window.evaluate_js(
                f"document.querySelector({_REAL_ROW_SELECTOR})"
                ".dispatchEvent(new MouseEvent("
                "'dblclick', {bubbles: true}));"
            )
            settled = _poll_until(
                window,
                "(window.__fimBusyProbe === null ? null : {"
                "hiddenDuring: window.__fimBusyProbe.hiddenDuring, "
                "label: window.__fimBusyProbe.label, "
                "hiddenAfter: document.getElementById('open-run-busy').hidden"
                "})",
                lambda value: value is not None and value.get("hiddenAfter") is True,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None, "the reopen bridge call was never reached"
    assert settled["hiddenDuring"] is False
    assert settled["label"] == "Opening batch\u2026"
    # Cleared on the failure path too, so a failed open leaves the
    # screen usable rather than permanently "opening".
    assert settled["hiddenAfter"] is True


def test_clicking_a_column_header_sorts_that_studys_own_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clicking "Ended" toggles that Study's own run rows between
    newest-first and oldest-first (item 4).

    Only one Study is involved, so this also proves sorting is scoped
    per-Study rather than reaching into some other Study's own rows --
    there being only one here is itself part of what is being checked:
    a single click must not need a second Study to prove it never
    touches.
    """
    results = tmp_path / "results"
    monkeypatch.setattr(paths_module, "results_directory", lambda: results)
    study = groups.create_study("Ring sweep", results=results)
    older = _write_run(tmp_path, study_id=study.study_id)
    newer_directory = tmp_path / "results" / "run-output-2"
    config_path = tmp_path / "run2.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "N": 20,
                "d": 2,
                "m": 0.1,
                "mu": 0.01,
                "seed": 2,
                "loci": [{"locus_id": 1, "length": 200}],
                "convergence_window": 4,
                "convergence_tolerance": 1.0,
                "max_generations": 10,
                "n_replicates": 1,
                "replicate_tolerance": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "run",
                str(config_path),
                "-o",
                str(newer_directory),
                "--quiet",
                "--study",
                study.study_id,
            ]
        )
        == 0
    )
    newer_manifest_path = newer_directory / "manifest.json"
    newer_manifest = json.loads(newer_manifest_path.read_text(encoding="utf-8"))
    older_manifest = json.loads((older / "manifest.json").read_text(encoding="utf-8"))
    # Force an unambiguous order rather than trusting the two real
    # `cli.main` calls above to land in different wall-clock seconds --
    # deterministic, not a race.
    newer_manifest["ended_at"] = (
        datetime.fromisoformat(older_manifest["ended_at"]) + timedelta(minutes=5)
    ).isoformat()
    newer_manifest_path.write_text(json.dumps(newer_manifest), encoding="utf-8")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value == 2,
            )
            newest_first = window.evaluate_js(
                "Array.from(document.querySelectorAll("
                f"{_REAL_ROW_SELECTOR})).map((row) => row.cells[1].textContent)"
            )
            window.evaluate_js(
                "document.querySelector("
                "'.open-run-column-sort-button[aria-label=\"Sort by Ended\"]').click();"
            )
            first_click = window.evaluate_js(
                "Array.from(document.querySelectorAll("
                f"{_REAL_ROW_SELECTOR})).map((row) => row.cells[1].textContent)"
            )
            window.evaluate_js(
                "document.querySelector("
                "'.open-run-column-sort-button[aria-label=\"Sort by Ended\"]').click();"
            )
            second_click = window.evaluate_js(
                "Array.from(document.querySelectorAll("
                f"{_REAL_ROW_SELECTOR})).map((row) => row.cells[1].textContent)"
            )
            outcome.put(
                {
                    "newestFirst": newest_first,
                    "firstClick": first_click,
                    "secondClick": second_click,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    # Default (nothing clicked yet): newest-first.
    assert settled["newestFirst"][0] >= settled["newestFirst"][1]
    # First click on "Ended": ascending (oldest-first) -- the reverse.
    assert settled["firstClick"] == list(reversed(settled["newestFirst"]))
    # Second click on the same column: descending again.
    assert settled["secondClick"] == settled["newestFirst"]


def test_dragging_a_column_resize_handle_widens_it_for_the_whole_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dragging one column's own resize handle widens that `<col>` --
    table-wide, since every Study's own column header row and every run
    row share the identical underlying columns (item 4).
    """
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    _write_run(tmp_path)

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _expand_all_recent_run_groups(window)
            _poll_until(
                window,
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value == 1,
            )
            before = window.evaluate_js(
                "document.querySelectorAll('colgroup col')[0].style.width"
            )
            window.evaluate_js(
                "(function(){"
                "var handle = document.querySelector("
                "'.open-run-column-resize-handle');"
                "var rect = handle.getBoundingClientRect();"
                "handle.dispatchEvent(new MouseEvent('mousedown', "
                "{bubbles: true, clientX: rect.left}));"
                "document.dispatchEvent(new MouseEvent('mousemove', "
                "{bubbles: true, clientX: rect.left + 80}));"
                "document.dispatchEvent(new MouseEvent('mouseup', "
                "{bubbles: true}));"
                "})();"
            )
            after = window.evaluate_js(
                "document.querySelectorAll('colgroup col')[0].style.width"
            )
            outcome.put({"before": before, "after": after})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["before"] == ""
    assert settled["after"] != ""
    assert settled["after"].endswith("px")
