"""Headless functional tests for the unified run view's own `completed`
state, scalar case (design doc §4.3, §6.4; unified-run-view design
§3.2.4, §8 Phase E).

Real DOM-driven proof that a completed scalar run actually reaches
`fim.enterCompletedState` (`webui/screens/run-view-completed.js`) and
renders the run's own summary and scatter — `test/gui/test_app_api.py`
already proves `format_statistic`/`open_output_folder` independently as
plain Python calls; these tests prove the page's own JavaScript displays
what `_drain_run_messages`'s `"done"` payload actually carries.

Drives a small, fast-converging configuration through the real form (the
same values `test/conftest.py`'s `tiny_params` fixture uses, entered as
form fields — `tiny_params` itself is a `SimulationParams`, not a form
values `dict`, so it cannot be handed to `start_run` directly), not the
starter form's own `d: 20`/`max_generations: 10000` defaults, so each
test here completes in well under a second rather than needing to wait
out (or rely on early convergence of) a 10000-generation run.

`test/gui/test_running_screen.py`'s own module docstring records a real,
repeatedly-reproduced investigation into these tests intermittently
hanging when run alongside that file's — not caused by this file's own
(small, fast) configuration, and not fixed by raising `_POLL_ATTEMPTS`
alone, but by `conftest.py`'s own `_POLL_INTERVAL_SECONDS`: two threads
(a test's own poll loop, and `fim.gui.app._drain_run_messages` pushing
progress from its own background thread) both calling `window.
evaluate_js` on the same window raised the odds of a collision the
faster the poll loop hammered it.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 600
# Generous margin over the largest test's own sequential poll stages,
# each individually bounded by `_POLL_ATTEMPTS`.
_DRIVE_TIMEOUT_SECONDS = 3 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Mirrors `test/conftest.py`'s `tiny_params` fixture field for field, so
# the run converges (or hits its own small generation cap) almost
# immediately — see this module's own docstring.
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
setField('convergence_window', '4');
setField('convergence_tolerance', '1.0');
setField('max_generations', '10');
"""


def _poll_until(
    window: webview.Window, script: str, predicate: Callable[[Any], bool]
) -> Any:
    value = None
    for _ in range(_POLL_ATTEMPTS):
        value = window.evaluate_js(script)
        if predicate(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def test_a_completed_run_renders_the_run_view(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A finished run shows its run id, outcome, all six statistics, and a scatter."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(_SET_TINY_FIELDS + "document.getElementById('run-button').click();"),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "runId: document.getElementById('results-run-id').textContent, "
            "outcome: document.getElementById('results-outcome').textContent, "
            "statDTitle: (() => {"
            "const statD = document.getElementById('stat-D');"
            "const track = statD && statD.querySelector('.ci-bar-track');"
            "return track ? track.title : null;"
            "})(), "
            "statGSTLabel: (() => {"
            "const stat = document.getElementById('stat-G_ST');"
            "const label = stat && stat.querySelector('.ci-bar-label');"
            "return label ? label.innerHTML : null;"
            "})(), "
            "statGSTTrackTitle: (() => {"
            "const stat = document.getElementById('stat-G_ST');"
            "const track = stat && stat.querySelector('.ci-bar-track');"
            "return track ? track.title : null;"
            "})(), "
            "scrubberHidden: document.getElementById('scrubber-controls').hidden, "
            "scrubberPlayDisabled: "
            "document.getElementById('scrubber-play-button').disabled, "
            "scrubberPending: window.__fimScrubberPending"
            "})"
        ),
        # `runViewState` flips to "completed" synchronously, but the
        # scrubber's own frames load via a separate, later-resolving
        # `Api.get_animation_frames` bridge call (`wireCompletedScrubber`,
        # `screens/run-view-completed.js`) that `enterCompletedState`
        # kicks off and does not wait on -- deliberately, so the run's
        # own final view never blocks on it (`scrubber.js`'s own fix for
        # the reverse hazard: loading frames must not repaint the
        # canvas). `window.__fimScrubberPending` is that call's own
        # settled signal (0 = nothing in flight); waiting on it too, not
        # just `runViewState`, is what actually proves the scrubber has
        # finished loading before the assertions below read it -- and
        # before `drive`'s own teardown destroys the window out from
        # under a still-in-flight bridge call.
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("scrubberPending") == 0
            and value.get("statDTitle") is not None
            and value.get("statGSTLabel") is not None
            and value.get("statGSTTrackTitle") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    assert "generation" in settled["outcome"]
    # The new compact meter places the value in the track's `title` attribute
    # (hover tooltip). For `buildPointMeter`, the title is "D = <value>".
    assert settled["statDTitle"].startswith("D = ")
    # `formatStatisticLabel` renders `_`-suffix as a real `<sub>` in the
    # label element's innerHTML.
    assert "<sub>ST</sub>" in settled["statGSTLabel"]
    # The track title for G_ST is "GST = <value>" (strip-tag form used in
    # `buildPointMeter`'s own title construction).
    assert settled["statGSTTrackTitle"].startswith("GST = ")
    # `tiny_params`-scale runs always persist more than one generation
    # (`convergence_window`'s own minimum of 2 forces at least one step
    # past generation 0 before stability can first be evaluated), so the
    # scrubber (design §3.2.4: no separate "Animate" button, this is the
    # same time slider `completed` shows directly) is populated and
    # enabled here, not just present.
    assert settled["scrubberHidden"] is False
    assert settled["scrubberPlayDisabled"] is False


def test_deme_pair_selector_switches_to_a_chosen_pair_and_back(
    window: webview.Window,
) -> None:
    """ "Show pair"/"Show overview" round-trip through the real bridge and back.

    `d=3` (one deme past the overview's own default "Deme 1 vs Deme 2"
    pairwise panel — `scatter.PAIRWISE_MAX_DEMES`'s own small-`d`
    dispatch; large `d` also defaults to a Deme-1-vs-Deme-2 panel now
    (unified-run-view design §3.6), but the selector itself does not
    care which layout produced the overview panel, so this smaller,
    faster configuration exercises the same bridge round trip a `d=20`
    run would): "Show pair"
    (Deme 1 vs Deme 3) redraws the canvas via a real `Api.get_deme_
    pair_panel` call, and "Show overview" redraws it back to the exact
    original panel with no further bridge call — `canvas.toDataURL()`
    snapshots prove both the change and the exact-match revert,
    without needing to read pixel data or canvas internals directly.

    Drives the window manually (not via the `drive` fixture, the same
    reason `test_running_simulation_again_from_completed_starts_a_new_
    run` does): three sequential trigger-then-poll stages against one
    live window.
    """
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    set_fields = _SET_TINY_FIELDS.replace("setField('d', '2');", "setField('d', '3');")

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState()",
                lambda value: value == "completed",
            )
            # `enterCompletedState` kicks off `wireCompletedScrubber`'s
            # own `get_animation_frames` fetch without waiting on it
            # (`scrubber.js`'s own fix: loading frames must not repaint
            # the canvas) -- wait for it to settle before this test does
            # its *own* canvas snapshotting below, and before `finally`
            # destroys the window out from under a still-in-flight
            # bridge call.
            _poll_until(
                window,
                "window.__fimScrubberPending",
                lambda value: value == 0,
            )
            selector_state = window.evaluate_js(
                "({"
                "hidden: document.getElementById('run-deme-pair-selector').hidden, "
                "optionCount: document.getElementById('run-x-deme').options.length"
                "})"
            )
            overview_snapshot = window.evaluate_js(
                "document.getElementById('run-canvas').toDataURL()"
            )
            window.evaluate_js(
                "document.getElementById('run-x-deme').value = '1';"
                "document.getElementById('run-y-deme').value = '3';"
                "document.getElementById('run-show-pair-button').click();"
            )
            pair_snapshot = _poll_until(
                window,
                "document.getElementById('run-canvas').toDataURL()",
                lambda value: value != overview_snapshot,
            )
            window.evaluate_js(
                "document.getElementById('run-show-overview-button').click();"
            )
            reverted_snapshot = _poll_until(
                window,
                "document.getElementById('run-canvas').toDataURL()",
                lambda value: value == overview_snapshot,
            )
            outcome.put(
                {
                    "selectorHidden": selector_state["hidden"],
                    "optionCount": selector_state["optionCount"],
                    "pairDiffersFromOverview": pair_snapshot != overview_snapshot,
                    "revertedMatchesOverview": reverted_snapshot == overview_snapshot,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled["selectorHidden"] is False
    assert settled["optionCount"] == 3
    assert settled["pairDiffersFromOverview"] is True
    assert settled["revertedMatchesOverview"] is True


def test_running_simulation_again_from_completed_starts_a_new_run(
    window: webview.Window,
) -> None:
    """ "Run simulation," clicked again from `completed`, starts a genuinely new run.

    Design §3.2.1's own `completed → initial → running` transition: a
    fresh "Run simulation" click reuses the current form values with no
    separate "New run"/reset step needed (retired this phase, design
    §8 Phase E — the shared controls are always present, so the same
    button that started the first run is already right there). Proven
    by the *output directory* changing between the two completed views,
    not just by `completed` being reached again — a stale DOM left over
    from the first run would otherwise look identical to a real second
    one. The run id itself is `deterministic_run_id(params)` (see
    `src/fim/engine.py`) — deliberately the *same* string for two runs
    of identical form values, so it cannot serve as this test's proof;
    the output directory embeds a wall-clock timestamp and so genuinely
    differs between the two invocations even though the run id does
    not.

    Drives the window directly (not via the `drive` fixture): this test
    needs two sequential trigger-then-poll stages against the *same*
    live window (finish a run, only then click "Run simulation" again)
    — `conftest.py`'s `drive_and_read` destroys the window in its own
    `finally` block after one such stage, the same reason
    `test/gui/test_running_screen.py`'s own cancel test drives directly.
    """
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState()",
                lambda value: value == "completed",
            )
            first_run_id = window.evaluate_js(
                "document.getElementById('results-run-id').textContent"
            )
            first_output_directory = window.evaluate_js(
                "window.fim.getCompletedOutputDirectory()"
            )
            # A fresh click reuses whatever the form already has -- no
            # field needs re-setting, and no "New run"/reset step comes
            # first.
            window.evaluate_js("document.getElementById('run-button').click();")
            second_run_id = _poll_until(
                window,
                "window.fim.getRunViewState() === 'completed' ? "
                "document.getElementById('results-run-id').textContent : null",
                lambda value: value is not None,
            )
            second_output_directory = window.evaluate_js(
                "window.fim.getCompletedOutputDirectory()"
            )
            # Both this run's own `wireCompletedScrubber` fetch and (if
            # it was somehow still running) the first run's own can be
            # in flight here -- `window.__fimScrubberPending` is a
            # counter for exactly this reason (see that function's own
            # comment). Wait for it to reach zero before `finally`
            # destroys the window out from under a still-in-flight
            # bridge call.
            _poll_until(
                window,
                "window.__fimScrubberPending",
                lambda value: value == 0,
            )
            outcome.put(
                {
                    "firstRunId": first_run_id,
                    "secondRunId": second_run_id,
                    "firstOutputDirectory": first_output_directory,
                    "secondOutputDirectory": second_output_directory,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled["firstRunId"].startswith("run-")
    assert settled["secondRunId"].startswith("run-")
    # Same form values -> the deterministic run id is legitimately
    # identical both times; the output directory (a wall-clock
    # timestamp embedded in the path) is what actually distinguishes
    # a genuine second run from a stale first-run DOM.
    assert settled["firstOutputDirectory"]
    assert settled["secondOutputDirectory"]
    assert settled["secondOutputDirectory"] != settled["firstOutputDirectory"]


def test_open_folder_button_reaches_the_injected_opener_and_settles() -> None:
    """ "Open output folder" reaches the injected opener and settles before teardown.

    Builds its own window (not the shared `window` fixture, which
    always uses a bare `Api()`) with `open_folder` injected, the same
    hook `test_app_api.py`'s own `test_open_output_folder_calls_the_
    injected_opener` uses as a plain Python call — here driven through
    a real click instead, so a real Finder/Explorer window never opens
    during this test.

    Also proves the fix for a real, once-reproduced hang: `open-
    FolderButton`'s click handler calls `window.pywebview.api.open_
    output_folder(...)` without anything downstream awaiting it, so
    nothing before `run-view-controls.js`'s own `window.__fimOpenFolder
    Settled` flag existed tied a test's own teardown to that call having
    actually finished — the same shape `test_running_screen.py`'s own
    `_wait_for_cancel_run_settled` closes for `cancel_run()`, traced
    there via `sample <pid>` on a `git push`'s own hung pre-push
    `pytest` run. Polling this flag before letting `window.destroy()`
    run is the actual regression proof; the injected opener's own
    recorded path is the icing.
    """
    opened: list[Path] = []
    window = create_window(api=Api(open_folder=opened.append), hidden=True)
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState()",
                lambda value: value == "completed",
            )
            # `enterCompletedState` also kicks off `wireCompletedScrubber`'s
            # own un-awaited `get_animation_frames` fetch (see that
            # function's own comment on `window.__fimScrubberPending`) --
            # wait for it too, not just `__fimOpenFolderSettled` below,
            # before `finally` destroys the window.
            _poll_until(
                window,
                "window.__fimScrubberPending",
                lambda value: value == 0,
            )
            window.evaluate_js("document.getElementById('open-folder-button').click();")
            settled = _poll_until(
                window,
                "window.__fimOpenFolderSettled === true",
                lambda value: value is True,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is True
    assert len(opened) == 1
