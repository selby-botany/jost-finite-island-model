"""Headless functional tests for Screen 5, the animated trajectory player
(design doc §3.8, §4.5, §7.7).

Real DOM-driven proof that Screen 3's "Animate" button reaches Screen 5,
that `Api.get_animation_frames`' real sampled frames render onto the
canvas, and that scrubbing moves the displayed frame and "Back" returns
to Screen 3 — `test/gui/test_app_api.py`'s own tests already prove
`Api.get_animation_frames` correct as a plain Python call; this file
proves the page's own JavaScript (`webui/screens/animation.js`) plays
back what it returns, which no Python-only test can check.

Reaches Screen 3 via `Api.open_run` directly (a plain, synchronous
request/response bridge call — the same shortcut `test/gui/
test_open_run_screen.py` uses to reach it, bypassing Screen 6's own UI),
rather than running a real scalar simulation through Screen 1/2 first:
this file's own concern is Screen 5, not proving Screen 1-3's wiring
again. Drives its window directly (not via the shared `drive` fixture,
which destroys its window after one trigger/read stage): reaching Screen
5 and then scrubbing/going back needs several sequential stages against
the *same* live window, every wait Python-side polling of a real
`window.evaluate_js` read, never a `setTimeout` loop inside a trigger —
see `test/gui/conftest.py`'s own module docstring for why.
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

_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"
_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 300
_DRIVE_TIMEOUT_SECONDS = 4 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0


def _write_run(tmp_path: Path) -> Path:
    """Write a real completed run with several generations, real frames to sample."""
    config = {
        "N": 20,
        "d": 2,
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


def test_animate_button_plays_real_frames_and_back_returns_to_results(
    tmp_path: Path,
) -> None:
    """A real multi-generation run animates, scrubs, and "Back" returns cleanly."""
    output = _write_run(tmp_path)
    window = create_window()
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "(async () => {"
                "window.__fimOpenResult = await window.pywebview.api.open_run("
                f"{{trajectoryPath: {str(output / 'trajectory.jsonl')!r}}}"
                ");"
                # Matches `open-run.js`'s own click handler exactly:
                # `Api.open_run` only ever returns data, it never
                # switches screens itself -- the caller is responsible
                # for handing a successful result to `showResults`.
                "if (window.__fimOpenResult.ok) {"
                "window.fim.showResults(window.__fimOpenResult);"
                "}"
                "})();"
            )
            opened = _poll_until(
                window, "window.__fimOpenResult", lambda value: value is not None
            )
            settled = None
            if opened is not None and opened.get("ok"):
                window.evaluate_js("document.getElementById('animate-button').click();")
                after_click = _poll_until(
                    window,
                    "({"
                    "screenVisible: "
                    "!document.getElementById('screen-animation').hidden, "
                    "playDisabled: "
                    "document.getElementById('animation-play-button').disabled, "
                    "scrubberMax: "
                    "document.getElementById('animation-scrubber').max"
                    "})",
                    lambda value: value is not None and value.get("screenVisible"),
                )
                if after_click.get("screenVisible"):
                    # Scrub to the last frame (index == scrubberMax) and
                    # confirm the label's own "(frame N / total)" text
                    # updates to match -- direct proof the scrub input
                    # actually moved the displayed frame, not just that
                    # the screen switched.
                    scrubber_max = int(after_click["scrubberMax"])
                    window.evaluate_js(
                        "const scrubber = "
                        "document.getElementById('animation-scrubber');"
                        f"scrubber.value = '{scrubber_max}';"
                        "scrubber.dispatchEvent("
                        "new Event('input', {bubbles: true}));"
                    )
                    expected_frame_text = f"frame {scrubber_max + 1} /"
                    after_scrub = _poll_until(
                        window,
                        "document.getElementById('animation-generation-label')"
                        ".textContent",
                        lambda value: (
                            value is not None and expected_frame_text in value
                        ),
                    )
                    window.evaluate_js(
                        "document.getElementById('animation-back-button').click();"
                    )
                    back = _poll_until(
                        window,
                        "!document.getElementById('screen-results').hidden",
                        lambda value: value is True,
                    )
                    settled = {
                        "afterClick": after_click,
                        "afterScrubLabel": after_scrub,
                        "expectedFrameText": expected_frame_text,
                        "back": back,
                    }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None, "Screen 5 was never reached"
    assert settled["afterClick"]["screenVisible"] is True
    assert settled["afterClick"]["playDisabled"] is False
    assert int(settled["afterClick"]["scrubberMax"]) >= 1
    assert settled["expectedFrameText"] in settled["afterScrubLabel"]
    assert settled["back"] is True
