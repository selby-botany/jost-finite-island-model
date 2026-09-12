"""Headless functional tests for the unified run view's own `completed`
state, batch case (`doc/fim-gui-design.md` §5.2).

Real DOM-driven proof that a completed batch actually reaches
`fim.enterCompletedState(payload, true)` (`webui/screens/run-view-
completed.js`) and renders the batch's own pooled scatter,
confidence-interval bars, and per-replicate table — `test/gui/
test_app_api.py`'s own `_batch_done_payload` tests already prove the
payload's own content is correct as plain Python calls;
`test/gui/test_batch_running.py` already proves the bridge dispatches a
real batch and pushes its `"done"` message correctly. This file proves
the third link: that the page's own JavaScript, given that real payload,
renders it — which no Python-only test can check.

The scalar counterpart is `test/gui/test_results_screen.py`; the two
files share the same element ids (`results-run-id`, `run-canvas`,
`run-deme-pair-selector`, `open-folder-button`, ...) below `completed`,
since `enterCompletedState` is the one shared entry point for both
kinds of run -- one state model, not two (`doc/fim-gui-design.md`
§5.2) -- branching internally on `isBatch` only for the
statistics/table fields that actually differ.

Drives a real, small (two-replicate) batch through the actual UI, the
same tiny-scale `_SET_TINY_BATCH_FIELDS` `test/gui/test_batch_running.py`
uses, and waits on `Api`'s own `on_message` hook rather than polling the
DOM for "the run view is showing `completed`" — see `test/gui/
test_running_screen.py`'s own module docstring for why a DOM-polling
test-driving loop, run concurrently with a real background thread's own
`evaluate_js` pushes, is the wrong tool here. Once `done_event` fires,
`_drain_batch_messages`'s thread has already returned (the terminal
message is the last thing it processes), so the one verification
`evaluate_js` call each test below makes is never concurrent with
anything.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

pytestmark = pytest.mark.gui

_READY_POLL_INTERVAL_SECONDS = 0.05
_READY_POLL_ATTEMPTS = 200
_EVENT_WAIT_TIMEOUT_SECONDS = 30.0
_OUTCOME_TIMEOUT_SECONDS = 40.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Mirrors `test/gui/test_batch_running.py`'s own `_SET_TINY_BATCH_FIELDS`.
_SET_TINY_BATCH_FIELDS = """
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
setField('n_replicates', '2');
setField('max_workers', '2');
"""

# A 5-replicate batch whose own replicates stop at staggered generations
# (`[3, 5, 6, 12, 15]`, confirmed live for this exact configuration) --
# the same values `test/engine/test_engine.py`'s own `test_pooled_
# convergence_histories_shrinks_as_replicates_stop` uses, needed here so
# the completed batch trajectory panel's own pooled band actually
# exercises real, uneven replicate coverage across generations, not
# just the every-replicate-stops-together case `_SET_TINY_BATCH_FIELDS`
# happens to produce.
_SET_STAGGERED_BATCH_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '42');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
setField('convergence_window', '4');
setField('convergence_tolerance', '0.02');
setField('max_generations', '30');
setField('n_replicates', '5');
setField('max_workers', '3');
"""


def _wait_for_input_screen_ready(window: webview.Window) -> None:
    """Poll until the run view's own async initialization has finished.

    Safe to poll here: no background thread exists yet, so this loop is
    never a second concurrent `evaluate_js` caller.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js(_INPUT_SCREEN_READY):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"the run view was not ready within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def test_batch_trajectory_domain_excludes_a_thin_samples_own_band(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A `sampleCount`-2 point's own wild band never widens the plotted axis.

    Confirmed live: a real, staggered-stopping batch's own `D` band
    reached `low: -2.98, high: 3.54` at its own tail (only 2 replicates
    still contributing -- Student's-t with 1 degree of freedom has an
    enormous critical value) for a statistic that never otherwise
    leaves roughly `[0, 1]`, squashing every earlier, better-supported
    generation into an unreadable sliver once the axis stretched to
    include it. `computeBatchTrajectoryValueDomain` is tested directly
    against a hand-built payload here (no real batch needed) rather
    than only indirectly through rendered canvas pixels, since a pure
    function's own return value is a far more direct assertion than
    reading pixel coverage back out of a canvas.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.__testDomain = computeBatchTrajectoryValueDomain({"
            "D: ["
            "{generation: 0, mean: '0.2', low: '0.1', high: '0.3', sampleCount: 5},"
            "{generation: 5, mean: '0.4', low: '-3', high: '4', sampleCount: 2}"
            "]"
            "});"
        ),
        read="window.__testDomain",
        is_ready=lambda value: value is not None,
    )

    # The thin-sample point's own wild `low`/`high` (-3/4) are excluded
    # entirely -- only its `mean` (0.4), the well-supported point's own
    # real `[0.1, 0.3]` band, and the always-included `[0, 1]` floor/
    # ceiling bound the domain. Confirms the exclusion actually fires
    # (not merely that these particular numbers stay inside `[0, 1]`
    # regardless): -3/4 would otherwise widen `minValue`/`maxValue`
    # well past this assertion.
    assert settled["minValue"] == 0
    assert settled["maxValue"] == 1


def test_batch_trajectory_domain_keeps_a_uniformly_small_samples_own_band(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A *uniformly* small `sampleCount` (every point, not just a thin tail)
    still counts toward the domain -- the threshold is relative to the
    largest `sampleCount` seen, not an absolute cutoff.

    A real regression this test guards against: `fim.engine.pooled_
    convergence_histories` now holds each replicate's own final value
    constant once it stops (carry-forward), which means a completed
    batch's own `sampleCount` is constant across every generation of
    one statistic's own history -- there is no more "thin tail" for an
    absolute cutoff to distinguish from a "well-supported" earlier
    point, since there is no longer any variation within one statistic
    to compare against at all. An earlier version of this threshold
    was an absolute `sampleCount >= 4`; for a real but small (2-3
    replicate) completed batch, every one of its points would have
    fallen below that absolute floor, incorrectly excluding its own
    real, stable band from the domain entirely, not just an unstable
    outlier -- confirmed live to reproduce before this test was
    written to guard against a return of that regression.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.__testDomain = computeBatchTrajectoryValueDomain({"
            "D: ["
            "{generation: 0, mean: '0.2', low: '-3', high: '4', sampleCount: 2},"
            "{generation: 5, mean: '0.4', low: '-3', high: '4', sampleCount: 2}"
            "]"
            "});"
        ),
        read="window.__testDomain",
        is_ready=lambda value: value is not None,
    )

    # Both points share the same `sampleCount` (2) -- the largest seen
    # anywhere in this view is also 2, so the relative threshold (half
    # of the largest) is satisfied by both, and their own real `[-3, 4]`
    # band correctly widens the domain rather than being excluded.
    assert settled["minValue"] == -3
    assert settled["maxValue"] == 4


def test_a_completed_batch_renders_the_run_view() -> None:
    """A finished two-replicate batch shows a run id, two table rows, and six stat rows.

    Every one of the six named statistics gets a `.stats-table` row with
    a confidence interval in its hover tooltip (`buildCiMeter`/
    `buildOmittedMeter`: a statistic omitted from
    `summary.json` still renders as explicitly omitted, not blank), so
    `#batch-results-summary-body` always has exactly six `<tr>` children
    regardless of which, if any, statistics `replicate_summary` actually
    defined for this particular run.
    """
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "runId: "
                    "document.getElementById('results-run-id').textContent, "
                    "rowCount: "
                    "document.getElementById('batch-results-table-body')"
                    ".children.length, "
                    "ciBarCount: "
                    "document.getElementById('batch-results-summary-body')"
                    ".children.length, "
                    "firstRowCells: Array.from("
                    "document.getElementById('batch-results-table-body')"
                    ".children[0].children"
                    ").map((cell) => cell.textContent), "
                    "secondRowCells: Array.from("
                    "document.getElementById('batch-results-table-body')"
                    ".children[1].children"
                    ").map((cell) => cell.textContent), "
                    "trajectoryFrameHidden: "
                    "document.getElementById('run-trajectory-frame').hidden"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s "
        f"(messages received: {messages!r})"
    )
    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    # Row 0 is the p_0 baseline; rows 1 and 2 are the two replicates.
    assert settled["rowCount"] == 3
    assert settled["ciBarCount"] == 6
    # p_0 row: generation=0, outcome="initial".
    first_row = settled["firstRowCells"]
    assert first_row[0] == "0"
    # Column order: Generation | Replicate | Outcome | ...
    assert first_row[2] == "initial"
    # Second row is the first replicate. Columns: Generation | Replicate | Outcome | ...
    # "Converged", not "Converged (statistic converged)" -- `replicate.
    # reason` is redundant with `converged` in the true case (`StopReason`
    # only ever pairs them one way), so the parenthetical said nothing a
    # reader did not already know.
    second_row = settled["secondRowCells"]
    assert second_row[2] == "Converged"
    # A batch's own `completed` view now draws a pooled trajectory too
    # (batch trajectory panel design `20260912-claude-sonnet-5-batch-
    # trajectory-panel-design.md`, `selby/restricted`, commit 2) --
    # `test_a_completed_batchs_own_pooled_trajectory_shrinks_as_
    # replicates_stop`, below, is the dedicated test for its own
    # content; this one only needs to confirm the panel is no longer
    # unconditionally hidden the way it used to be (`git blame` this
    # line for the pre-commit-2 assertion, `True`).
    assert settled["trajectoryFrameHidden"] is False


def test_a_completed_batch_hides_the_reanalyze_controls() -> None:
    """A batch's own `completed` view hides item 6's re-analysis controls.

    A batch manifest has no single trajectory of its own to re-analyze
    (the same "no single trajectory" boundary `open-run.js`'s own
    single-click row handler already draws for a batch row on Home) --
    `enterCompletedState`'s own `resultsReanalyzeControls.hidden = isBatch`
    is what enforces this; the scalar counterpart (hidden is `False`) is
    `test/gui/test_running_screen.py`'s own `test_a_live_runs_own_done_
    payload_enables_the_reanalyze_controls`.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "reanalyzeHidden: "
                    "document.getElementById('results-reanalyze-controls').hidden, "
                    "trajectoryPath: window.fim.getCompletedTrajectoryPath()"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["runViewState"] == "completed"
    assert settled["reanalyzeHidden"] is True
    assert settled["trajectoryPath"] is None


def test_a_completed_batchs_own_pooled_trajectory_renders() -> None:
    """The completed batch trajectory panel (batch trajectory panel
    design `20260912-claude-sonnet-5-batch-trajectory-panel-design.md`,
    `selby/restricted`, commit 2) actually renders, given a real batch
    whose replicates stop at genuinely staggered generations.

    `test/engine/test_engine.py`'s own `test_pooled_convergence_
    histories_shrinks_as_replicates_stop` already proves the underlying
    aggregation math is correct as a plain Python call, for this exact
    same configuration; this test proves the page's own JavaScript
    (`renderBatchTrajectory`/`drawBatchTrajectoryCurve`) actually draws
    the payload it is given, which no Python-only test can check.
    Checks that *something* real was drawn (a legend entry per tracked
    statistic, real non-transparent canvas pixels), not the exact
    pooled numbers themselves -- re-deriving those independently here
    would only re-implement the aggregation this test is not the one
    responsible for verifying.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_STAGGERED_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "frameHidden: "
                    "document.getElementById('run-trajectory-frame').hidden, "
                    "legendTexts: Array.from(document.querySelectorAll("
                    "'#run-trajectory-legend .legend-item')"
                    ").map((el) => el.textContent), "
                    "canvasNonBlank: (function() {"
                    "  var c = document.getElementById('run-trajectory-canvas');"
                    "  var ctx = c.getContext('2d');"
                    "  var data = ctx.getImageData(0, 0, c.width, c.height).data;"
                    "  var count = 0;"
                    "  for (var i = 3; i < data.length; i += 4) {"
                    "    if (data[i] > 0) { count += 1; }"
                    "  }"
                    "  return count;"
                    "})()"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, "batch never reached done within the wait budget"
    assert settled["runViewState"] == "completed"
    assert settled["frameHidden"] is False
    # The always-tracked four (`track_expensive_statistics` is not set
    # by `_SET_STAGGERED_BATCH_FIELDS`, so `E_ST`/`K_ST` never get a
    # per-generation history at all -- `pooled_convergence_histories`'s
    # own docstring, and `test_pooled_convergence_histories_shrinks_
    # as_replicates_stop`'s identical assertion for this same case).
    assert sorted(text.split(" ")[0] for text in settled["legendTexts"]) == [
        "D",
        "G_ST",
        "H_S",
        "H_T",
    ]
    assert settled["canvasNonBlank"] > 0


def test_a_completed_batchs_own_scrubber_replays_the_pooled_scatter() -> None:
    """The completed batch scrubber (batch trajectory panel design
    `20260912-claude-sonnet-5-batch-trajectory-panel-design.md`,
    `selby/restricted`) shows, has a real generation range, and
    scrubbing to a real frame updates the label without error.

    `test/gui/test_app_api.py`'s own `test_get_batch_animation_frames_
    ships_client_ready_pooled_panels` already proves the underlying
    bridge call returns real, correctly shaped frames as a plain Python
    call; this test proves the page's own JavaScript
    (`wireCompletedBatchScrubber`) actually wires them into the shared
    scrubber UI and that scrubbing itself redraws without throwing,
    which no Python-only test can check. Waits on `window.
    __fimScrubberPending` settling (`wireCompletedScrubber`'s own
    established pattern, extended to its batch counterpart) rather than
    guessing a delay is enough for the un-awaited bridge call to land.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_STAGGERED_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                for _ in range(_READY_POLL_ATTEMPTS):
                    if not window.evaluate_js("window.__fimScrubberPending"):
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
                before = window.evaluate_js(
                    "({"
                    "scrubberHidden: "
                    "document.getElementById('scrubber-controls').hidden, "
                    "scrubberMax: "
                    "document.getElementById('scrubber-range').max"
                    "})"
                )
                window.evaluate_js(
                    "(function(){"
                    "var range = document.getElementById('scrubber-range');"
                    "range.value = Math.floor(Number(range.max) / 2);"
                    "range.dispatchEvent(new Event('input', {bubbles: true}));"
                    "})();"
                )
                after_scrub_label = window.evaluate_js(
                    "document.getElementById('scrubber-label').textContent"
                )
                settled = {"before": before, "afterScrubLabel": after_scrub_label}
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, "batch never reached done within the wait budget"
    assert settled["before"]["scrubberHidden"] is False
    assert int(settled["before"]["scrubberMax"]) > 0
    assert "Generation" in settled["afterScrubLabel"]


def test_the_ci_meter_names_the_replicate_count_in_its_own_tooltip() -> None:
    """`buildCiMeter`'s own tooltip states "uncertainty across N independent
    replicates" (botanist GUI design doc §7.2: re-labeled "everywhere it
    appears... so it is never visually confusable with" the within-run
    sigma band) — `webui/meters.js`'s own `ciCaption`, not a second,
    independently worded phrase.

    It also states the two numbers the rest of that same §7.2 sentence
    asks for — "both the confidence-interval half-width and the
    equivalent sample standard deviation" (sample-standard-deviation
    tooltip design `20260912-claude-sonnet-5-sample-std-dev-tooltip-
    design.md`, `selby/restricted`) — spelled out rather than as a sigma
    glyph, which this GUI reserves for the within-run band the caption
    exists to stay distinguishable from. Checked in the same real-batch
    pass rather than as a second window test, since it is the same one
    tooltip string.
    """
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            tooltip = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                tooltip = window.evaluate_js(
                    "document.querySelector('#batch-results-summary-body tr').title"
                )
            outcome.put(tooltip)
        finally:
            window.destroy()

    webview.start(_drive)
    tooltip = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert tooltip is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s "
        f"(messages received: {messages!r})"
    )
    # `_SET_TINY_BATCH_FIELDS` requests 2 replicates.
    assert "uncertainty across 2 independent replicates" in tooltip
    assert "half-width " in tooltip
    assert "equivalent sample standard deviation " in tooltip
    # The sigma glyph belongs to the within-run band, not to this one
    # (spelled as a code point so this file itself stays plain ASCII,
    # matching every other Python module here, which writes "sigma").
    assert chr(0x03C3) not in tooltip


def test_batch_deme_pair_selector_switches_to_a_chosen_pair_and_back() -> None:
    """Selecting a pair, then selecting back to the default, round-trips
    through the real batch bridge (no "Show overview" button any more).

    The batch counterpart to `test_results_screen.py`'s own identically
    named scalar-run test — `d=3` past the default "Deme 1 vs Deme 2"
    panel, selecting Deme 1 vs Deme 3 redraws the canvas via a real
    `Api.get_batch_deme_pair_panel` call (pooled across both replicates,
    `deme_pair_panel`'s own docstring), and selecting back to Deme 1 vs
    Deme 2 redraws it via another such call, reproducing the exact
    original panel. The selector and canvas are the same shared elements
    the scalar test drives (`run-deme-pair-selector`, `run-x-deme`,
    `run-canvas`, ...) — `run-view-completed.js` dispatches to the
    batch- or scalar-flavored bridge call by `window.fim.
    getRunViewState`'s own `isBatch` bookkeeping, not by a different
    element id.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
    set_fields = _SET_TINY_BATCH_FIELDS.replace(
        "setField('d', '2');", "setField('d', '3');"
    )

    def _poll_until(script: str, predicate: Any) -> Any:
        value = None
        for _ in range(_READY_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_READY_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                selector_state = window.evaluate_js(
                    "({"
                    "hidden: document.getElementById("
                    "'run-deme-pair-selector').hidden, "
                    "optionCount: document.getElementById("
                    "'run-x-deme').options.length"
                    "})"
                )
                default_snapshot = window.evaluate_js(
                    "document.getElementById('run-canvas').toDataURL()"
                )
                window.evaluate_js(
                    "document.getElementById('run-x-deme').value = '1';"
                    "document.getElementById('run-y-deme').value = '3';"
                    "document.getElementById('run-y-deme').dispatchEvent("
                    "new Event('change'));"
                )
                pair_snapshot = _poll_until(
                    "document.getElementById('run-canvas').toDataURL()",
                    lambda value: value != default_snapshot,
                )
                window.evaluate_js(
                    "document.getElementById('run-y-deme').value = '2';"
                    "document.getElementById('run-y-deme').dispatchEvent("
                    "new Event('change'));"
                )
                reverted_snapshot = _poll_until(
                    "document.getElementById('run-canvas').toDataURL()",
                    lambda value: value == default_snapshot,
                )
                settled = {
                    "selectorHidden": selector_state["hidden"],
                    "optionCount": selector_state["optionCount"],
                    "pairDiffersFromDefault": pair_snapshot != default_snapshot,
                    "revertedMatchesDefault": reverted_snapshot == default_snapshot,
                }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s"
    )
    assert settled["selectorHidden"] is False
    assert settled["optionCount"] == 3
    assert settled["pairDiffersFromDefault"] is True
    assert settled["revertedMatchesDefault"] is True


def test_running_a_batch_again_from_completed_starts_a_new_batch() -> None:
    """ "Run simulation," clicked again from a completed batch, starts a new one.

    The batch counterpart to `test_results_screen.py`'s own `test_
    running_simulation_again_from_completed_starts_a_new_run` — no
    separate "New run" button exists any more: the shared controls
    are always present, so the same button that started the first
    batch is already right there.
    Proven by the *output directory* changing between the two completed
    views, the same reason the scalar test gives: `deterministic_run_id
    (params)` is deliberately the same string for two batches of
    identical form values, so it cannot serve as this test's proof.

    Drives the window directly, waiting on two separate `on_message`
    hooks (one per batch) rather than `conftest.py`'s `drive` fixture,
    which destroys the window after one such stage.
    """
    first_done = threading.Event()
    second_done = threading.Event()
    done_count = 0

    def on_message(message: RunMessage | BatchMessage) -> None:
        nonlocal done_count
        if message[0] in ("done", "cancelled", "error"):
            done_count += 1
            if done_count == 1:
                first_done.set()
            elif done_count == 2:
                second_done.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if first_done.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                first_output_directory = window.evaluate_js(
                    "window.fim.getCompletedOutputDirectory()"
                )
                # A fresh click reuses whatever the form already has --
                # no field needs re-setting, and no "New run"/reset step
                # comes first.
                window.evaluate_js("document.getElementById('run-button').click();")
                if second_done.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                    second_output_directory = window.evaluate_js(
                        "window.fim.getCompletedOutputDirectory()"
                    )
                    settled = {
                        "firstOutputDirectory": first_output_directory,
                        "secondOutputDirectory": second_output_directory,
                    }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"both batches did not complete within "
        f"{2 * _EVENT_WAIT_TIMEOUT_SECONDS}s combined"
    )
    assert settled["firstOutputDirectory"]
    assert settled["secondOutputDirectory"]
    assert settled["secondOutputDirectory"] != settled["firstOutputDirectory"]


def test_open_folder_button_reaches_the_injected_opener_and_settles() -> None:
    """ "Open output folder" reaches the injected opener and settles before teardown.

    The batch-results counterpart to `test_results_screen.py`'s own
    identically-named test — same shared `open-folder-button`/`window.
    __fimOpenFolderSettled` flag (one button now, regardless of scalar
    or batch), same injected-`open_folder` hook (so a
    real Finder/Explorer window never opens here either), same real,
    once-reproduced hang this closes: a click handler calling `window.
    pywebview.api.open_output_folder(...)` with nothing downstream
    awaiting it. See `test_running_screen.py`'s own `_wait_for_cancel_
    run_settled` for the full mechanism, traced there via `sample <pid>`
    on a `git push`'s own hung pre-push `pytest` run.
    """
    opened: list[Path] = []
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(
        api=Api(on_message=on_message, open_folder=opened.append), hidden=True
    )
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = False
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                window.evaluate_js(
                    "document.getElementById('open-folder-button').click();"
                )
                for _ in range(_READY_POLL_ATTEMPTS):
                    settled = window.evaluate_js(
                        "window.__fimOpenFolderSettled === true"
                    )
                    if settled:
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is True
    assert len(opened) == 1
