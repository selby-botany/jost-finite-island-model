"""Headless functional tests for the unified run view's own scrubber,
reached by opening a persisted run (design doc §3.8, §4.5, §7.7;
unified-run-view design §3.2.4, §8 Phase E).

The separate "Animate" button and its own screen (`webui/screens/
animation.js`) are retired this phase: there is no longer anything to
navigate to. Opening a run (`Api.open_run`, the same call `test/gui/
test_open_run_screen.py` drives) reaches `completed` directly
(`window.fim.enterCompletedState`), and `wireCompletedScrubber` (`webui/
screens/run-view-completed.js`) auto-populates the scrubber in the
background for any scalar run with more than one persisted generation —
no second click needed. `test/gui/test_results_screen.py`'s own `test_a_
completed_run_renders_the_run_view` already proves this for a *live*
run's own completion; this file proves the same scrubber for a
*re-opened* one, and that scrubbing actually moves the displayed frame
(real sampled frames, not just that the controls become enabled) —
`test/gui/test_app_api.py`'s own tests already prove `Api.get_animation_
frames` correct as a plain Python call, so this file's own job is
proving the page's own JavaScript plays back what it returns, which no
Python-only test can check.

The old animation screen's own deme-pair selector swapped the *entire*
animated frame set (`Api.get_animation_deme_pair_frames`, one call for
every sampled frame at once) — that capability has no reachable UI path
this phase (`run-view-completed.js`'s own comment: "deliberately not
pair-aware for this phase... this phase's own 'no new capabilities'
scope does not need to answer yet"); `get_animation_deme_pair_frames`
itself still exists and is still covered by `test_app_api.py` as a plain
Python call, just unreachable from any button today. The *static*-frame
deme-pair selector this phase actually ships (`run-x-deme`/`run-y-deme`,
affecting only the currently-drawn frame, never future scrubbing) is
already covered by `test/gui/test_results_screen.py`'s own
`test_deme_pair_selector_switches_a_chosen_pair_and_back` — nothing here
duplicates it.

Reaches `completed` via `Api.open_run` directly (a plain, synchronous
request/response bridge call — the same shortcut `test/gui/
test_open_run_screen.py` uses), rather than running a real scalar
simulation first: this file's own concern is the scrubber, not proving
the run-view's own wiring again. Drives its window directly (not via the
shared `drive` fixture, which destroys its window after one trigger/read
stage): scrubbing needs several sequential stages against the *same*
live window, every wait Python-side polling of a real `window.
evaluate_js` read, never a `setTimeout` loop inside a trigger — see
`test/gui/conftest.py`'s own module docstring for why.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli
from fim.gui.app import create_window

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
_DRIVE_TIMEOUT_SECONDS = 4 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0


def _write_run(tmp_path: Path, *, d: int = 2) -> Path:
    """Write a real completed run with several generations, real frames to sample."""
    config = {
        "N": 20,
        "d": d,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 8,
        "convergence_tolerance": 1e-6,
        "max_generations": 12,
    }
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def _poll_until(window: webview.Window, script: str, is_ready: Any) -> Any:
    """Evaluate `script` repeatedly, sleeping between tries, until `is_ready` accepts.

    Never waits inside JavaScript itself — see `test/gui/conftest.py`'s
    own module docstring.
    """
    value: Any = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if is_ready(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def _open_run(window: webview.Window, trajectory_path: Path) -> Any:
    """Open a persisted run through the real bridge and enter `completed`.

    Mirrors `open-run.js`'s own click handler exactly: `Api.open_run`
    only ever returns data, it never enters `completed` itself — the
    caller is responsible for handing a successful result to `window.
    fim.enterCompletedState`.
    """
    window.evaluate_js(
        "(async () => {"
        "window.__fimOpenResult = await window.pywebview.api.open_run("
        f"{{trajectoryPath: {str(trajectory_path)!r}}}"
        ");"
        "if (window.__fimOpenResult.ok) {"
        "window.fim.enterCompletedState(window.__fimOpenResult, false);"
        "}"
        "})();"
    )
    return _poll_until(
        window, "window.__fimOpenResult", lambda value: value is not None
    )


def test_opening_a_run_populates_the_scrubber_and_scrubbing_moves_the_frame(
    tmp_path: Path,
) -> None:
    """Re-opening a multi-generation run auto-populates and scrubs the scrubber."""
    output = _write_run(tmp_path)
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            opened = _open_run(window, output / "trajectory.jsonl")
            settled = None
            if opened is not None and opened.get("ok"):
                # `wireCompletedScrubber`'s own fetch is async and not
                # awaited by `enterCompletedState` (`scrubber.js`'s own
                # fix: loading frames must not repaint the canvas) --
                # `window.__fimScrubberPending` is its settled signal.
                after_load = _poll_until(
                    window,
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "scrubberPending: window.__fimScrubberPending, "
                    "scrubberHidden: "
                    "document.getElementById('scrubber-controls').hidden, "
                    "playDisabled: "
                    "document.getElementById('scrubber-play-button')"
                    ".disabled, "
                    "scrubberMax: "
                    "document.getElementById('scrubber-range').max"
                    "})",
                    lambda value: (
                        value is not None
                        and value.get("runViewState") == "completed"
                        and value.get("scrubberPending") == 0
                    ),
                )
                if not after_load["scrubberHidden"]:
                    # Scrub to the last frame (index == scrubberMax) and
                    # confirm the label's own "(frame N / total)" text
                    # updates to match -- direct proof the scrub input
                    # actually moved the displayed frame, not just that
                    # the controls became enabled.
                    scrubber_max = int(after_load["scrubberMax"])
                    window.evaluate_js(
                        "const scrubber = "
                        "document.getElementById('scrubber-range');"
                        f"scrubber.value = '{scrubber_max}';"
                        "scrubber.dispatchEvent("
                        "new Event('input', {bubbles: true}));"
                    )
                    expected_frame_text = f"frame {scrubber_max + 1} /"
                    after_scrub = _poll_until(
                        window,
                        "document.getElementById('scrubber-label').textContent",
                        lambda value: (
                            value is not None and expected_frame_text in value
                        ),
                    )
                    settled = {
                        "afterLoad": after_load,
                        "afterScrubLabel": after_scrub,
                        "expectedFrameText": expected_frame_text,
                    }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None, "the scrubber was never populated"
    assert settled["afterLoad"]["scrubberHidden"] is False
    assert settled["afterLoad"]["playDisabled"] is False
    assert int(settled["afterLoad"]["scrubberMax"]) >= 1
    assert settled["expectedFrameText"] in settled["afterScrubLabel"]
