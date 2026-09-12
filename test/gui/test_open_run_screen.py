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
from fim.gui import app as app_module
from fim.gui import presets as presets_module
from fim.gui.app import create_window

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
_REAL_ROW_SELECTOR = "'#open-run-recent-runs-body tr:not(.open-run-group-header)'"


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
                f"document.querySelectorAll({_REAL_ROW_SELECTOR}).length",
                lambda value: value is not None and value > 0,
            )
            settled = None
            if row_count == 1:
                window.evaluate_js(
                    f"document.querySelector({_REAL_ROW_SELECTOR}).click();"
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
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)'); "
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
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)'); "
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


def test_home_example_select_lists_only_built_in_examples(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`home-example-select` lists the built-in worked examples only.

    Populated by `refreshHomeExampleOptions()` from `Api.list_presets`'s
    own `builtin` entries, filtering out any user-saved preset — the
    full combined list stays reachable only from the existing
    `modal-presets` picker (`fim.menu.loadExample`). The option order
    and titles must match `fim.gui.presets.list_presets` directly (not
    a hand-copied count), the same "read the real module, don't
    re-derive a snapshot" precedent `test_presets.py`'s own
    `_REAL_PRESETS` sets — a real gap this test would have caught: an
    earlier draft asserted a bare option count, which would not have
    noticed the dropdown silently including a user-saved preset instead
    of a missing built-in one.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.showOpenRunScreen();",
        read=(
            "({"
            "ready: window.__fimHomeExampleOptionsReady === true, "
            "labels: Array.from("
            "document.getElementById('home-example-select').options"
            ").map((option) => option.textContent), "
            "placeholderSelected: "
            "document.getElementById('home-example-select').value === ''"
            "})"
        ),
        is_ready=lambda value: value is not None and value.get("ready"),
    )

    expected_titles = [
        preset.title
        for preset in presets_module.list_presets(app_module._webui_directory())
    ]
    assert settled["labels"] == ["Try a worked example…", *expected_titles]
    assert settled["placeholderSelected"] is True


def test_choosing_a_home_example_applies_it_and_opens_configure(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Picking an example applies its values, opens Configure, then resets.

    A plain, immediately-acting pulldown (no separate confirm step): the
    `change` event alone drives it, matching how a real user's own
    pulldown selection fires it. Selects the "Stepping-stone (spatial)
    migration" example specifically (option index 2 — index 0 is the
    placeholder, index 1 is "Unequal island sizes with a migration
    hub") since its own d=6 ring matrix is distinct from the starter
    form's own defaults, the identical "a changed field is real proof
    the click did something" reasoning `test_presets_screen.py`'s own
    equivalent test already uses for the same preset. Reuses
    `presets.js`'s own `applyPreset` via `window.fim.applyPreset` —
    genuinely the same apply path the File-menu picker uses, not a
    second, independent one.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.showOpenRunScreen(); "
            "setTimeout(async () => { "
            "await new Promise((resolve) => { "
            "const check = () => window.__fimHomeExampleOptionsReady "
            "? resolve() : setTimeout(check, 20); "
            "check(); "
            "}); "
            "const select = document.getElementById('home-example-select'); "
            "select.selectedIndex = 2; "
            "select.dispatchEvent(new Event('change')); "
            "}, 0);"
        ),
        read=(
            "({"
            "configureVisible: "
            "!document.getElementById('screen-configure').hidden, "
            "mMode: document.querySelector("
            "'input[name=\"m_mode\"]:checked')?.value, "
            "nValue: document.getElementById('field-N').value, "
            "selectValue: "
            "document.getElementById('home-example-select').value"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("configureVisible") is True
        ),
        poll_attempts=500,
    )

    assert settled["configureVisible"] is True
    assert settled["mMode"] == "matrix"
    assert settled["nValue"] == "150"
    # Reset to its own placeholder afterward — the control always reads
    # as an action, never as "currently showing example X."
    assert settled["selectValue"] == ""


def test_home_example_select_excludes_a_user_saved_preset(
    window: webview.Window,
) -> None:
    """A user-saved preset never appears in Home's own example shortcut.

    "One of the examples" (the design ask) means built-in worked
    examples only — a user-saved configuration stays reachable solely
    from the full `modal-presets` picker. Needs its own two-stage,
    manually driven window (save, then reopen Home) rather than the
    shared `drive` fixture, which destroys its window after one stage.
    """
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "window.__testSaveDone = false; "
                "window.pywebview.api.save_current_as_preset("
                "'Test user preset', collectFormValues()"
                ").then(() => { window.__testSaveDone = true; });"
            )
            _poll_until(
                window, "window.__testSaveDone === true", lambda value: value is True
            )
            window.evaluate_js("window.fim.showOpenRunScreen();")
            settled = _poll_until(
                window,
                "({"
                "ready: window.__fimHomeExampleOptionsReady === true, "
                "labels: Array.from("
                "document.getElementById('home-example-select').options"
                ").map((option) => option.textContent)"
                "})",
                lambda value: value is not None and value.get("ready"),
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert "Test user preset" not in settled["labels"]
    expected_titles = [
        preset.title
        for preset in presets_module.list_presets(app_module._webui_directory())
    ]
    assert settled["labels"] == ["Try a worked example…", *expected_titles]


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
    own unset-so-default `convergence_statistic`) — botanist GUI design
    doc §6.2's own predicted-equilibrium overlay draws against that same
    trailing window even with no curve of its own to sit beside
    (`run-view-completed.js`'s own `renderTrajectory`: an equilibrium
    reference line is scoped to whatever the panel is already showing
    something for — a real curve, or, lacking one, the sigma band), so
    the legend is not fully empty either: one dashed entry, not zero.
    """
    _write_run_with_sigma_band(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
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
    # history at all), so there is no "D (simulated)" entry — but `D`'s
    # own predicted-equilibrium overlay still draws against the sigma
    # band's own trailing window (this test's own docstring), so the
    # legend is not empty either. The identity-recovery curve overlay
    # (`_identity_recovery_reference_payload`) draws unconditionally
    # whenever `N`/`m` alone are plain scalars (they are here too, and it
    # is not scoped to the sigma band's own statistics the way the
    # equilibrium entry is), so it appears as a third entry.
    assert settled["legendNames"] == [
        "D (predicted equilibrium)",
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
    _write_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
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


def test_recent_runs_group_by_date_bucket_and_can_be_collapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design proposal for "a fantastically long results scroll": runs
    render grouped into date-bucket sections, each with its own
    collapsible header naming its member count -- collapsing one
    removes its rows from the DOM outright (`open-run.js`'s own
    `renderRecentRuns`/`buildGroupHeaderRow`), the other bucket's own
    rows unaffected.
    """
    _write_run(tmp_path)
    batch_directory = _write_batch_run(tmp_path)
    manifest_path = batch_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ended_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            before = _poll_until(
                window,
                "(function(){"
                "var headers = Array.from(document.querySelectorAll("
                "'.open-run-group-header .open-run-group-toggle'))"
                ".map((b) => b.textContent);"
                "var rows = document.querySelectorAll("
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)')"
                ".length;"
                "return {headers: headers, rowCount: rows};"
                "})()",
                lambda value: value is not None and value.get("rowCount") == 2,
            )
            window.evaluate_js(
                "document.querySelector('.open-run-group-toggle').click();"
            )
            after_collapse = _poll_until(
                window,
                "document.querySelectorAll("
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)').length",
                lambda value: value is not None and value == 1,
            )
            outcome.put({"before": before, "afterCollapse": after_collapse})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["before"]["rowCount"] == 2
    assert any("Today" in header for header in settled["before"]["headers"])
    assert any("Earlier" in header for header in settled["before"]["headers"])
    # Collapsing the first ("Today") group's header removes only its own
    # one row, leaving the "Earlier" batch row still rendered.
    assert settled["afterCollapse"] == 1


def test_recent_runs_filter_narrows_the_visible_rows_and_updates_the_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The filter bar narrows the same rows the table draws from, live
    (`open-run.js`'s own `renderRecentRuns`) -- not a second, separate
    search index that could drift from what actually renders, and the
    count label states how much of the full list is currently visible.
    """
    run_directory = _write_run(tmp_path)
    _write_batch_run(tmp_path)
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")
    run_id = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))[
        "run_id"
    ]

    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js("window.fim.menu.openRun();")
            _poll_until(
                window,
                "document.querySelectorAll("
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)').length",
                lambda value: value is not None and value == 2,
            )
            window.evaluate_js(
                "(function(){"
                "var input = document.getElementById('open-run-filter');"
                f"input.value = {run_id!r};"
                "input.dispatchEvent(new Event('input', {bubbles: true}));"
                "})();"
            )
            settled = _poll_until(
                window,
                "({"
                "rowCount: document.querySelectorAll("
                "'#open-run-recent-runs-body tr:not(.open-run-group-header)').length, "
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
