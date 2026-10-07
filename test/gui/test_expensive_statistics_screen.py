"""Headless functional tests: Settings' "Statistics shown" decides whether a
run computes the expensive statistics every generation.

Configure's old "track E_ST/K_ST/A_CGD/δG/I for display" checkbox is
gone. `Api._merge_default_run_settings` now sets
`track_expensive_statistics` from what Settings shows
(`fim.statistics.catalog.expensive_statistics_requested`). These tests
start a real, small run from the page and check both sides of the rule:

- showing `E_ST` makes the run record it every generation (the scrubbed
  statistics row shows a real value at generation 0) and shows its row,
  and the run's manifest records `track_expensive_statistics: true`;
- the fresh-install choice (every expensive statistic hidden) records
  none of them (the scrubbed row reads "not known at this generation")
  and the manifest records `false`.

Every wait is on a page flag (`__fimRunViewReady`, the run-view state,
`__fimScrubberPending`), bounded only by the completion backstop.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import poll_or_fail

from fim.gui.app import await_bridge_threads, create_window
from fim.gui.preferences import load_preferences, save_preferences
from fim.statistics.catalog import default_shown_keys

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.05
_DRIVE_TIMEOUT_SECONDS = 300.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# A small, fast run; `fast_scalar_run_settings` supplies the Settings-side
# fields (one replicate, a 10-generation cap, a short window).
_SET_TINY_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
"""

_COMPLETED = (
    "window.fim.getRunViewState() === 'completed' && window.__fimScrubberPending === 0"
)

_E_ST_ROW = (
    "({"
    "title: document.getElementById('stat-E_ST').title, "
    "hidden: document.getElementById('stat-E_ST').hidden, "
    "label: document.getElementById('scrubber-label').textContent"
    "})"
)


def _show(preferences_path: Path, keys: tuple[str, ...]) -> None:
    """Save `keys` as Settings' "Statistics shown", keeping every other preference."""
    preferences, _warning = load_preferences(preferences_path)
    save_preferences(preferences_path, preferences.with_shown_statistics(keys))


def _run_and_scrub_to_generation_0(window: webview.Window) -> dict[str, Any]:
    """Run the tiny configuration, scrub to generation 0 and read the E_ST row."""
    outcome: queue.Queue[dict[str, Any] | AssertionError] = queue.Queue(maxsize=1)

    def poll(script: str, what: str) -> Any:
        return poll_or_fail(
            lambda: window.evaluate_js(script),
            bool,
            what,
            interval=_POLL_INTERVAL_SECONDS,
        )

    def _drive() -> None:
        try:
            poll(_INPUT_SCREEN_READY, "the run view to be ready")
            window.evaluate_js(
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            poll(_COMPLETED, "the run to complete")
            directory = window.evaluate_js("window.fim.getCompletedOutputDirectory()")
            window.evaluate_js(
                "document.getElementById('scrubber-range').value = '0';"
                "document.getElementById('scrubber-range').dispatchEvent("
                "new Event('input', {bubbles: true}));"
            )
            row = poll_or_fail(
                lambda: window.evaluate_js(_E_ST_ROW),
                lambda value: value["label"] == "Generation 0",
                "the scrubber to reach generation 0",
                interval=_POLL_INTERVAL_SECONDS,
            )
            outcome.put({"directory": directory, **row})
        except AssertionError as error:
            outcome.put(error)
        finally:
            await_bridge_threads()
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)
    if isinstance(result, AssertionError):
        raise result
    return result


def _tracked_in_manifest(directory: str) -> bool:
    """Return the run's recorded `track_expensive_statistics`."""
    manifest = json.loads((Path(directory) / "manifest.json").read_text("utf-8"))
    tracked = manifest["parameters"]["track_expensive_statistics"]
    assert isinstance(tracked, bool)
    return tracked


def test_showing_an_expensive_statistic_computes_and_shows_it_every_generation(
    fast_scalar_run_settings: Path,
) -> None:
    """Shown in Settings: E_ST has a real value at generation 0, on screen."""
    _show(fast_scalar_run_settings, (*default_shown_keys(), "E_ST"))

    settled = _run_and_scrub_to_generation_0(create_window(hidden=True))

    assert settled["title"].startswith("E_ST = "), settled["title"]
    assert settled["hidden"] is False
    assert _tracked_in_manifest(settled["directory"]) is True


def test_hiding_every_expensive_statistic_skips_computing_them(
    fast_scalar_run_settings: Path,
) -> None:
    """The fresh-install choice: nothing expensive is recorded per generation."""

    settled = _run_and_scrub_to_generation_0(create_window(hidden=True))

    assert settled["title"].startswith("not known at this generation")
    assert settled["hidden"] is True
    assert _tracked_in_manifest(settled["directory"]) is False
