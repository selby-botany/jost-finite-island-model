"""Headless functional tests for Screen 6, opening an existing run (design
doc §4.6, §7.7).

Real DOM-driven proof that Screen 1's "Open a run…" button reaches Screen
6, that Screen 6's recent-runs list is populated from a real, completed
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
    }
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "results" / "run-output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def test_open_a_run_button_reaches_screen_six(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Clicking "Open a run…" on Screen 1 shows Screen 6.

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
        trigger="document.getElementById('open-run-button').click();",
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
            window.evaluate_js("document.getElementById('open-run-button').click();")
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
                    "document.getElementById('results-run-id').textContent"
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
