"""Headless functional tests for the unified run view's own `completed`
state, scalar case (`doc/fim-gui-design.md` §5.2).

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
# immediately — see this module's own docstring. `n_replicates`/`max_
# generations`/the convergence-loop timing pair moved out of Configure's
# own `<form>` entirely and into the Settings dialog (`2026-09-16`
# revision) -- `field-n_replicates` etc. no longer exist to set here at
# all, so every test below that uses this constant now also requests
# `fast_scalar_run_settings` (`conftest.py`), which pre-seeds the
# identical values through Settings' own mechanism instead. Regression,
# found directly, that motivated this: with nothing overriding it, a
# fresh form's own `n_replicates` is `SimulationParams`'s real default
# (`200`, `fim.cli.STARTER_CONFIG` never overrides it) — silently
# submitting a real 200-replicate batch run instead of the one fast
# scalar run this whole module exists to drive, blanking `results-
# outcome` (the batch branch of `enterCompletedState` always does) and
# blowing past a fixed wait budget calibrated for one small run, not two
# hundred.
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
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
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
            "return statD ? statD.title : null;"
            "})(), "
            "statGSTLabel: (() => {"
            "const stat = document.getElementById('stat-G_ST');"
            "const label = stat && stat.querySelector('.stat-name');"
            "return label ? label.innerHTML : null;"
            "})(), "
            "statGSTTrackTitle: (() => {"
            "const stat = document.getElementById('stat-G_ST');"
            "return stat ? stat.title : null;"
            "})(), "
            "supplementalHidden: "
            "document.getElementById('frequency-spectrum-card').hidden, "
            "statACgdTitle: (() => {"
            "const stat = document.getElementById('stat-A_CGD');"
            "return stat ? stat.title : null;"
            "})(), "
            "ibdHidden: document.getElementById('ibd-card').hidden, "
            "layout: (() => {"
            "const rect = (selector) => {"
            "const el = document.querySelector(selector);"
            "const box = el.getBoundingClientRect();"
            "const style = getComputedStyle(el);"
            "return {"
            "left: box.left, top: box.top, right: box.right, "
            "bottom: box.bottom, width: box.width, height: box.height, "
            "gridColumnStart: style.gridColumnStart, "
            "gridRowStart: style.gridRowStart, "
            "gridRowEnd: style.gridRowEnd"
            "};"
            "};"
            "return {"
            "rowDisplay: getComputedStyle("
            "document.getElementById('run-plot-row')).display, "
            "scatter: rect('.run-canvas-frame'), "
            "trajectory: rect('#run-trajectory-frame'), "
            "composition: rect('#allele-composition-card'), "
            "spectrum: rect('#frequency-spectrum-card'), "
            "stats: rect('#results-stats')"
            "};"
            "})(), "
            "scrubberHidden: document.getElementById('scrubber-controls').hidden, "
            "scrubberPlayDisabled: "
            "document.getElementById('scrubber-step-forward').disabled, "
            "scrubberParentClass: "
            "document.getElementById('scrubber-controls').parentElement.className, "
            "scrubberIsFirstInGraphBody: document.getElementById("
            "'scrubber-controls') === document.querySelector("
            "'.run-graph-body > *'), "
            "trajectoryParentClass: document.getElementById("
            "'run-trajectory-frame').parentElement.className, "
            "resultsHistoryBackHidden: "
            "document.getElementById('results-history-back-button').hidden, "
            "resultsHistoryForwardHidden: "
            "document.getElementById('results-history-forward-button').hidden, "
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
            and value.get("statACgdTitle") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    assert "generation" in settled["outcome"]
    # The stats-table row places the full-precision value in the row's own
    # `title` attribute (hover tooltip). For `buildPointMeter`, the title
    # is "D = <value>".
    assert settled["statDTitle"].startswith("D = ")
    # `formatStatisticLabel` renders `_`-suffix as a real `<sub>` in the
    # name cell's innerHTML.
    assert "<sub>ST</sub>" in settled["statGSTLabel"]
    # The row title for G_ST keeps the underscore a reader recognizes:
    # a native `title` tooltip cannot render `<sub>`, so
    # `plainStatisticLabel` restores `_ST` rather than flattening the
    # name to a run-together "GST".
    assert settled["statGSTTrackTitle"].startswith("G_ST = ")
    # Both screens' tooltips carry the statistic's own short gloss,
    # which is why it no longer appears in the visible table.
    assert "nearness to fixation" in settled["statGSTTrackTitle"]
    # `A_CGD` -- one of the three expensive, opt-in "bonus" measurements
    # (`SimulationParams.track_expensive_statistics`) -- now renders as an
    # ordinary stats-table row exactly like every other statistic, no
    # longer a separate "Supplemental statistics" table of its own.
    assert settled["statACgdTitle"].startswith("A_CGD = ")
    assert settled["supplementalHidden"] is True
    assert settled["ibdHidden"] is True
    layout = settled["layout"]
    assert layout["rowDisplay"] == "flex"
    assert layout["trajectory"]["width"] > 0
    assert layout["stats"]["width"] > 0
    assert layout["stats"]["left"] > layout["trajectory"]["right"]
    # `tiny_params`-scale runs always persist more than one generation
    # (`convergence_window`'s own minimum of 2 forces at least one step
    # past generation 0 before stability can first be evaluated), so the
    # scrubber (no separate "Animate" button, this is the
    # same time slider `completed` shows directly) is populated and
    # enabled here, not just present.
    assert settled["scrubberHidden"] is False
    assert settled["scrubberPlayDisabled"] is False
    # The scrubber moved out from under the whole graph stack and onto
    # the stage's own left edge, so it stays on screen alongside
    # whichever single graph is showing. Assert that position directly:
    # "not below the fold" is the entire point of the move, and a
    # scrubber that silently drifted back under the graphs would still
    # satisfy every other assertion here.
    assert settled["scrubberParentClass"] == "run-graph-body"
    assert settled["scrubberIsFirstInGraphBody"] is True
    assert settled["trajectoryParentClass"] == "run-visual-panels"
    assert settled["resultsHistoryBackHidden"] is False
    assert settled["resultsHistoryForwardHidden"] is False


def test_differently_scaled_statistics_start_off_the_trajectory_panel(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`A_CGD`, `Delta`, and `MI` start hidden; the rest start plotted.

    The trajectory panel draws every statistic against one shared
    y-axis, so a statistic that is not a `[0, 1]` differentiation
    measure decides the axis for all of them: `A_CGD` is an effective-
    allele *count* and routinely reads above 1, which flattens `D`,
    `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`, and `H_ST` into an unreadable
    band along the bottom of the panel. They are hidden by default, not
    removed -- this test also clicks `A_CGD`'s own row back on to prove
    the legend toggle still reaches it.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollCompleted = () => { "
            + "if (window.fim.getRunViewState() === 'completed') { "
            + "const state = {}; "
            + "for (const row of document.querySelectorAll("
            + "'#results-stats tr[data-trajectory-statistic]')) { "
            + "state[row.dataset.trajectoryStatistic] = "
            + "row.getAttribute('aria-pressed'); "
            + "} "
            + "window.__fimDefaultPressed = state; "
            + "document.getElementById('stat-A_CGD').click(); "
            + "window.__fimReinstated = document.getElementById("
            + "'stat-A_CGD').getAttribute('aria-pressed'); "
            + "return; "
            + "} "
            + "setTimeout(pollCompleted, 50); "
            + "}; "
            + "setTimeout(pollCompleted, 50);"
        ),
        read=(
            "({"
            "pressed: window.__fimDefaultPressed || null, "
            "reinstated: window.__fimReinstated || null, "
            "scrubberPending: window.__fimScrubberPending"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("pressed") is not None
            and value.get("reinstated") is not None
            and value.get("scrubberPending") == 0
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    pressed = settled["pressed"]
    assert pressed["A_CGD"] == "false"
    assert pressed["Delta"] == "false"
    assert pressed["MI"] == "false"
    for name in ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST"):
        assert pressed[name] == "true", f"{name} should start plotted"
    assert settled["reinstated"] == "true"


def test_drawing_a_scatter_sizes_the_canvas_buffer_before_it_paints(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`drawScatter` adopts the canvas' current CSS size in the same synchronous turn.

    A canvas' `width`/`height` attributes are its drawing-buffer
    resolution, independent of the CSS box it is displayed in. Drawing
    into a stale buffer therefore paints at the wrong resolution and
    lets the browser stretch or squeeze the result into the real box.

    `scatter.js` has always had a `ResizeObserver` that corrects the
    buffer, but an observer callback is delivered *asynchronously*, on a
    later frame. Between the draw and that callback the canvas holds a
    wrong-resolution image -- a visible flash on every completed run,
    and, because `toDataURL()` serializes the buffer rather than the
    displayed box, two renderings of the very same panel that do not
    compare equal. That is exactly how `test_batch_results_screen.py`'s
    own deme-pair revert test intermittently failed: its "default"
    snapshot was occasionally captured in that window, at a 403x403
    buffer inside a 218x218 box, and so could never match the reverted
    snapshot drawn a moment later at the corrected 218x218.

    This drives the whole sequence inside one synchronous JS statement,
    so no observer callback, timer, or animation frame can possibly run
    partway through: force a known-wrong buffer size, force the layout
    to a known CSS size, draw, and read the buffer back. Passing means
    the sync happened in `drawScatter` itself, not a frame later.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.showScreen('screen-run');"
            "const c = document.getElementById('run-canvas');"
            "c.style.setProperty('width', '321px', 'important');"
            "c.style.setProperty('height', '321px', 'important');"
            # Read a layout property to flush the pending style change
            # before drawing, so `clientWidth` below is already the new
            # box and the test is measuring `drawScatter`, not layout.
            "void c.offsetWidth;"
            "c.width = 64; c.height = 64;"
            "drawScatter(c, {x_label: 'x', y_label: 'y', points: ["
            "{x: 0.25, y: 0.5, count: 1, common: true}]});"
            "window.__fimCanvasSizeProbe = {"
            "bufferW: c.width, bufferH: c.height,"
            "cssW: c.clientWidth, cssH: c.clientHeight};"
        ),
        read="window.__fimCanvasSizeProbe",
        is_ready=lambda value: value is not None,
    )

    assert settled["cssW"] > 0, (
        "the run canvas must be laid out for this to mean anything"
    )
    assert settled["bufferW"] == settled["cssW"], (
        "drawScatter painted into a stale buffer: "
        f"{settled['bufferW']} wide inside a {settled['cssW']}px box"
    )
    assert settled["bufferH"] == settled["cssH"]
    assert settled["bufferW"] != 64, (
        "the deliberately-wrong buffer size survived the draw"
    )


def test_a_single_replicate_run_gets_a_per_generation_results_table(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A single-replicate run gets the same full-width results table a batch gets.

    Reported as a gap between the two completed views: a batch showed a
    full-width table of its own per-replicate results below the graphs,
    while a single run showed only the point-value stats panel beside
    them. A batch's rows are its replicates and a single run has
    exactly one, so this table's rows are generations instead --
    specifically the scrubber's own sampled generations, so that a row
    and a scrub position always denote the same instant
    (`renderScalarTable`).

    Checks the two rows whose contents are fully determined regardless
    of how many generations this particular run takes: the first
    (generation 0, outcome "initial") and the last (the run's own stop
    reason, the same text `#results-outcome` reports above the plot).
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(_SET_TINY_FIELDS + "document.getElementById('run-button').click();"),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "scrubberPending: window.__fimScrubberPending, "
            "tableHidden: document.getElementById('run-results-table').hidden, "
            "batchTableHidden: "
            "document.getElementById('batch-results-table').hidden, "
            "headers: Array.from(document.querySelectorAll("
            "'#run-results-table thead th')).map((th) => th.textContent), "
            "rowCount: "
            "document.getElementById('run-results-table-body').children.length, "
            # `scrubber.js` keeps its frame list private; this is its
            # only public introspection of how many frames it holds.
            "frameCount: window.fim.getScrubberGenerations().length, "
            "firstRowCells: (() => {"
            "const row = document.getElementById("
            "'run-results-table-body').children[0];"
            "return row ? Array.from(row.children).map((c) => c.textContent) : null;"
            "})(), "
            "lastRowCells: (() => {"
            "const rows = document.getElementById('run-results-table-body').children;"
            "const row = rows[rows.length - 1];"
            "return row ? Array.from(row.children).map((c) => c.textContent) : null;"
            "})(), "
            "outcome: document.getElementById('results-outcome').textContent"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("scrubberPending") == 0
            and value.get("rowCount", 0) > 0
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["tableHidden"] is False
    # The batch table stays hidden -- this is a second table, not the
    # batch one repurposed.
    assert settled["batchTableHidden"] is True
    assert settled["headers"][:2] == ["Generation", "Outcome"]
    # Ten statistics, no "Replicate" column and no "Open" column: a
    # single run has neither a sibling replicate to name nor a separate
    # trajectory to open, since this card is already showing it.
    assert len(settled["headers"]) == 12
    # One row per scrubber frame, exactly -- the alignment that lets a
    # row and a scrub position mean the same generation.
    assert settled["rowCount"] == settled["frameCount"]
    first = settled["firstRowCells"]
    assert first[0] == "0"
    assert first[1] == "initial"
    last = settled["lastRowCells"]
    assert last[1] != ""
    assert settled["outcome"].startswith(last[1])


def test_completed_run_shows_title_above_canvas_and_back_returns_to_initial(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The run title sits above the plot and the Back action returns to p_0."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollCompleted = () => { "
            + "if (window.fim.getRunViewState() === 'completed') { "
            + "const title = document.getElementById('run-plot-title'); "
            + "const canvas = document.getElementById('run-canvas'); "
            + "window.__fimCompletedTitleAboveCanvas = !!("
            + "title.compareDocumentPosition(canvas) & "
            + "Node.DOCUMENT_POSITION_FOLLOWING); "
            + "document.getElementById('results-back-button').click(); "
            + "return; "
            + "} "
            + "setTimeout(pollCompleted, 50); "
            + "}; "
            + "setTimeout(pollCompleted, 50);"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "titleText: document.getElementById('run-plot-title').textContent, "
            "titleAboveCanvas: !!window.__fimCompletedTitleAboveCanvas, "
            "initialHidden: document.getElementById('initial-stats').hidden, "
            "backHidden: document.getElementById('results-back-button').hidden"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "initial"
            and value.get("titleAboveCanvas") is True
            and value.get("titleText") == "FIM simulation — initial conditions (p₀)"
            and value.get("initialHidden") is False
            and value.get("backHidden") is True
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["runViewState"] == "initial"
    assert settled["titleText"] == "FIM simulation — initial conditions (p₀)"
    assert settled["titleAboveCanvas"] is True
    assert settled["initialHidden"] is False
    assert settled["backHidden"] is True


def test_completed_scatter_draws_the_marker_color_legend(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The on-screen plot explains its own marker colors.

    Before this, the canvas drew blue and orange markers and defined
    neither, leaving "why are some dots blue?" answerable only by reading
    the source -- the same ambiguity that made the original "common
    allele" marker a reported defect rather than merely an unclear one.
    The saved `scatter.png` carries a matplotlib legend; this proves the
    GUI carries the equivalent.

    Records the text the canvas actually draws by wrapping `fillText` on
    the live 2D context, rather than asserting on pixels: it proves the
    real render path emitted the real strings, and reports a readable
    mismatch when it does not.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            # Wrap `fillText` before the run starts, so the completed
            # state's own first paint is captured rather than a later
            # incidental redraw.
            "window.__fimCanvasText = []; "
            "const ctx = document.getElementById('run-canvas').getContext('2d'); "
            "const originalFillText = ctx.fillText.bind(ctx); "
            "ctx.fillText = (text, ...rest) => { "
            "window.__fimCanvasText.push(String(text)); "
            "return originalFillText(text, ...rest); "
            "}; " + _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "drawn: window.__fimCanvasText || []"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and any("Other alleles" in text for text in value.get("drawn", []))
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    drawn = settled["drawn"]
    assert "Most frequent allele in either deme (ring; ties: first)" in drawn
    assert "Other alleles" in drawn


def test_deme_pair_selector_switches_to_a_chosen_pair_and_back(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """Selecting a pair, then selecting back to the default, round-trips
    through the real bridge (no "Show overview"
    button any more — the selectors themselves are the only way to change
    which pair is shown).

    `d=3` (one deme past the default's own "Deme 1 vs Deme 2" panel —
    large `d` also defaults to a Deme-1-vs-Deme-2 panel,
    but the selector itself does not care which `d` produced
    the default panel, so this smaller, faster configuration exercises
    the same bridge round trip a `d=20` run would): selecting Deme 1 vs
    Deme 3 redraws the canvas via a real `Api.get_deme_pair_panel` call,
    and selecting back to Deme 1 vs Deme 2 redraws it via another such
    call, reproducing the exact original panel — `canvas.toDataURL()`
    snapshots prove both the change and the exact-match revert, without
    needing to read pixel data or canvas internals directly.

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
                window,
                "document.getElementById('run-canvas').toDataURL()",
                lambda value: value != default_snapshot,
            )
            window.evaluate_js(
                "document.getElementById('run-y-deme').value = '2';"
                "document.getElementById('run-y-deme').dispatchEvent("
                "new Event('change'));"
            )
            reverted_snapshot = _poll_until(
                window,
                "document.getElementById('run-canvas').toDataURL()",
                lambda value: value == default_snapshot,
            )
            outcome.put(
                {
                    "selectorHidden": selector_state["hidden"],
                    "optionCount": selector_state["optionCount"],
                    "pairDiffersFromDefault": pair_snapshot != default_snapshot,
                    "revertedMatchesDefault": reverted_snapshot == default_snapshot,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled["selectorHidden"] is False
    assert settled["optionCount"] == 3
    assert settled["pairDiffersFromDefault"] is True
    assert settled["revertedMatchesDefault"] is True


def test_running_simulation_again_from_completed_starts_a_new_run(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """ "Run simulation," clicked again from `completed`, starts a genuinely new run.

    The unified run view's own `completed → initial → running`
    transition: a fresh "Run simulation" click reuses the current form
    values with no separate "New run"/reset step needed (the shared
    controls are always present, so the same
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
                "window.fim.getRunViewState() === 'completed' && "
                "window.fim.getCompletedOutputDirectory() !== "
                f"{first_output_directory!r} ? "
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


def test_open_folder_button_reaches_the_injected_opener_and_settles(
    fast_scalar_run_settings: Path,
) -> None:
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


def test_a_completed_run_with_a_sigma_band_draws_it_and_shows_the_caption(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A run started with the sigma-band toggle on draws a real band and caption.

    Sigma-band GUI design doc `20260910-claude-sonnet-5-gui-sigma-band-
    design.md` (`selby/restricted`) slice 3, approach C1 — checked via
    the canvas's own alpha channel (every stroke/fill this page draws
    is fully opaque; a canvas starts fully transparent), the identical
    check `test_compare_screen.py`'s own `_canvas_has_nonblank_pixels_
    script` already established for an unrelated canvas, not
    independently reinvented here.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "var cb = document.getElementById('field-sigma_band_enabled'); "
            "cb.checked = true; "
            "cb.dispatchEvent(new Event('change', {bubbles: true})); "
            "document.getElementById('field-sigma_band_window').value = '3'; "
            "document.getElementById('run-button').click();"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "captionHidden: document.getElementById("
            "'run-trajectory-sigma-band-caption').hidden, "
            "captionText: document.getElementById("
            "'run-trajectory-sigma-band-caption').textContent, "
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
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("runViewState") == "completed"
        ),
        poll_attempts=600,
    )

    assert settled["runViewState"] == "completed"
    assert settled["captionHidden"] is False
    # The Greek sigma character trips ruff's own RUF001/RUF003
    # (ambiguous Unicode) inside a plain string or a comment alike --
    # the identical check `config_form.py`'s own RUF002 hit, from a
    # docstring, while this slice was being written; every other Python
    # source file this design has touched spells it out as ASCII
    # "sigma" instead. This one assertion needs the real character to
    # match `renderTrajectory`'s own actual rendered caption text, so
    # it is written using a Unicode escape sequence rather than typed literally --
    # satisfies the linter without changing what the string contains.
    assert "2\u03c3" in settled["captionText"]
    assert "3 generations" in settled["captionText"]
    assert settled["canvasNonBlankPixelCount"] > 0


def test_run_view_fits_the_default_window_without_excess_scrolling(
    fast_scalar_run_settings: Path,
) -> None:
    """A completed scalar run's trajectory panel and stats table are on-screen.

    Real, reported layout bug at the app's own default window size
    (`create_window`'s own `width=900, height=700`): the scatter frame's
    own vh-based width formula (`app.css`) and the trajectory frame's
    fixed 480px width neither shrink to fit a narrower row, so `.run-
    plot-row`'s `flex-wrap` dropped the trajectory panel and stats table
    to a second, stacked line at their full, un-shrunk (tall-window)
    sizes \u2014 taller, together, than the whole 700px-tall window, pushing
    both almost entirely below the fold with no visible hint that
    scrolling would reveal them. Confirmed live before the fix: the
    document needed roughly 640px of scroll past the window to reach
    `#run-trajectory-frame`.

    The fix (`#run-plot-row.run-plot-row-has-trajectory` rules in
    `app.css`, gated by `setTrajectoryFrameHidden` in `run-view-
    completed.js`) shrinks both frames only when a trajectory panel is
    actually competing for the row, so they render side by side on one
    line instead of stacking \u2014 this test checks exactly that: `#run-
    trajectory-frame` and `#results-stats` both sit within the window's
    own viewport, at the same top offset as `#run-canvas` (same line,
    not wrapped below it), rather than merely "somewhere reachable by
    scrolling."
    """
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            window.resize(900, 700)
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState()",
                lambda value: value == "completed",
            )
            settled = _poll_until(
                window,
                "(function() {"
                "var row = document.getElementById('run-plot-row');"
                "var canvas = document.getElementById('run-canvas');"
                "var scatter = document.querySelector('.run-canvas-frame');"
                "var trajectory = document.getElementById('run-trajectory-frame');"
                "var stats = document.getElementById('results-stats');"
                "if (trajectory.hidden || stats.hidden) { return null; }"
                "var canvasRect = canvas.getBoundingClientRect();"
                "var scatterRect = scatter.getBoundingClientRect();"
                "var trajectoryRect = trajectory.getBoundingClientRect();"
                "var statsRect = stats.getBoundingClientRect();"
                "return {"
                "rowHasTrajectoryClass: "
                "row.classList.contains('run-plot-row-has-trajectory'), "
                "innerHeight: window.innerHeight, "
                "canvasTop: canvasRect.top, canvasBottom: canvasRect.bottom, "
                "scatterTop: scatterRect.top, "
                "trajectoryTop: trajectoryRect.top, "
                "trajectoryBottom: trajectoryRect.bottom, "
                "trajectoryRight: trajectoryRect.right, "
                "statsTop: statsRect.top, statsBottom: statsRect.bottom, "
                "statsLeft: statsRect.left"
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
    assert settled["rowHasTrajectoryClass"] is True
    # The graph and the statistics sit side by side, not stacked: the
    # stage occupies the left of the row and the statistics table the
    # right. (This replaced a `trajectoryTop < statsTop` check, which
    # described the older stacked layout and is simply not a property of
    # the side-by-side one -- the stats now start *above* the graph,
    # level with the stage's selector.)
    assert settled["statsLeft"] >= settled["trajectoryRight"]
    # Fully visible within the actual window -- not merely reachable by
    # scrolling -- which is the whole point of the fix.
    assert settled["trajectoryBottom"] <= settled["innerHeight"]
    assert settled["statsBottom"] <= settled["innerHeight"]


def test_run_view_initial_state_canvas_is_unaffected_by_the_trajectory_fix() -> None:
    """The `initial` p_0 view's scatter plot keeps its full, un-shrunk size.

    Companion to `test_run_view_fits_the_default_window_without_excess_
    scrolling`, just above: `app.css`'s own `@media (max-width: 1300px)`
    rules are gated on `#run-plot-row`'s own `run-plot-row-has-
    trajectory` class specifically so a state with no trajectory panel
    to make room for \u2014 the `initial` p_0 scatter, shown before any run
    starts \u2014 never shrinks needlessly. Confirmed live: reverting the
    gate (applying the shrunk widths unconditionally) measurably shrinks
    `#run-canvas` in this exact state even though nothing here ever
    overflowed the window in the first place.
    """
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            window.resize(900, 700)
            _poll_until(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "document.querySelector('[data-destination=\"run\"]').click();"
            )
            _poll_until(
                window,
                "window.fim.getRunViewState()",
                lambda value: value == "initial",
            )
            settled = _poll_until(
                window,
                "(function() {"
                "var row = document.getElementById('run-plot-row');"
                "var canvas = document.getElementById('run-canvas');"
                "return {"
                "rowHasTrajectoryClass: "
                "row.classList.contains('run-plot-row-has-trajectory'), "
                "canvasWidth: canvas.getBoundingClientRect().width"
                "};"
                "})()",
                lambda value: value is not None and value.get("canvasWidth", 0) > 0,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["rowHasTrajectoryClass"] is False
    # The un-shrunk formula (`@media (max-height: 760px)`, still in
    # effect at 900x700) puts the scatter canvas at roughly 387px wide;
    # the (wrongly) shrunk formula this test guards against would put it
    # at roughly 157px. 300px is comfortably between the two.
    assert settled["canvasWidth"] > 300


def test_completed_scrubber_updates_supplemental_panels_on_scrub_ticks(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Stepping the completed scrubber updates allele composition & spectrum."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollScrubber = () => { "
            + "if (window.fim.getRunViewState() === 'completed' && "
            + "window.__fimScrubberPending === 0) { "
            + "const range = document.getElementById('scrubber-range'); "
            + "range.value = '0'; "
            + "range.dispatchEvent(new Event('input', {bubbles: true})); "
            + "window.__fimScrubbedToZero = true; "
            + "return; "
            + "} "
            + "setTimeout(pollScrubber, 50); "
            + "}; "
            + "setTimeout(pollScrubber, 50);"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "scrubberPending: window.__fimScrubberPending, "
            "scrubbedToZero: !!window.__fimScrubbedToZero, "
            "alleleCompositionHidden: "
            "document.getElementById('allele-composition-card').hidden, "
            "frequencySpectrumHidden: "
            "document.getElementById('frequency-spectrum-card').hidden, "
            "scrubberLabel: document.getElementById('scrubber-label').textContent"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("scrubberPending") == 0
            and value.get("scrubbedToZero") is True
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["runViewState"] == "completed"
    assert settled["alleleCompositionHidden"] is True
    assert settled["frequencySpectrumHidden"] is True
    assert "Generation 0" in settled["scrubberLabel"]


def test_graph_stage_shows_one_graph_and_the_selector_switches_it(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The stage shows exactly one graph, and the selector changes which.

    The Run card used to show four graphs at once in a 3x3 quadrant.
    That was reported as unworkable -- each panel too small to read at
    the app's own 900x700 default -- and was replaced by a "graph
    stage": one graph, chosen from a selector where the per-panel title
    used to be.

    "Exactly one" is the part worth guarding. The stage decides pane
    visibility centrally, but the render functions still report *whether*
    each graph has data, and an earlier version of that split let a
    graph un-hide itself behind the stage's back.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollCompleted = () => { "
            + "if (window.fim.getRunViewState() === 'completed') { "
            + "const select = document.getElementById('run-graph-select'); "
            + "window.__fimOptions = Array.from(select.options)"
            + ".map((option) => option.value); "
            + "window.__fimDefault = window.fim.getActiveGraph(); "
            + "window.fim.showGraph('alleleComposition'); "
            + "window.__fimSwitched = window.fim.getActiveGraph(); "
            + "return; "
            + "} "
            + "setTimeout(pollCompleted, 50); "
            + "}; "
            + "setTimeout(pollCompleted, 50);"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "options: window.__fimOptions, "
            "defaultGraph: window.__fimDefault, "
            "switchedGraph: window.__fimSwitched, "
            "visible: ['run-scatter-card', 'run-trajectory-frame', "
            "'allele-composition-card', 'frequency-spectrum-card']"
            ".filter((id) => !document.getElementById(id).hidden)"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("switchedGraph") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    # Four graphs on offer, never the fifth: the IBD payload is still
    # produced for Python callers, but this card does not show it.
    assert settled["options"] == [
        "scatter",
        "trajectory",
        "alleleComposition",
        "frequencySpectrum",
    ]
    # The trajectory is the requested default even though the scatter
    # declares itself available first, during page load, long before any
    # run exists.
    assert settled["defaultGraph"] == "trajectory"
    assert settled["switchedGraph"] == "alleleComposition"
    assert settled["visible"] == ["allele-composition-card"]


def test_graph_zoom_frame_takes_the_pane_and_gives_it_back(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Double-click zoom moves the real pane out and restores it on close.

    The frame moves the pane rather than cloning it, so the legend and
    every wired handler come along and stay live -- and so there is only
    ever one canvas holding the drawing. The risk that buys is losing
    the pane: if it is not put back exactly where it came from, the
    stage is left permanently blank with no error anywhere.

    2026-09-23 follow-up: the scrubber and the active statistics table
    move the same way, for the same reason (full parity with the Run
    card's own graph-plus-scrubber-plus-statistics experience, at no
    cost of a second implementation) -- this pins their own round trip
    too, not only the pane's.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollCompleted = () => { "
            + "if (window.fim.getRunViewState() === 'completed') { "
            + "const pane = document.getElementById('run-trajectory-frame'); "
            + "const scrubber = document.getElementById('scrubber-controls'); "
            + "const stats = document.getElementById('results-stats'); "
            + "window.__fimHomeBefore = {"
            + "pane: pane.parentElement.className, "
            + "scrubber: scrubber.parentElement.className, "
            + "stats: stats.parentElement.id"
            + "}; "
            + "window.fim.openGraphZoom(); "
            + "window.__fimZoom = {"
            + "open: document.getElementById('graph-zoom-modal').open, "
            + "paneParent: pane.parentElement.id, "
            + "scrubberParent: scrubber.parentElement.id, "
            + "statsParent: stats.parentElement.id, "
            + "title: document.getElementById('graph-zoom-title').textContent, "
            + "active: window.fim.getActiveGraph()"
            + "}; "
            + "document.getElementById('graph-zoom-close').click(); "
            + "setTimeout(() => { "
            + "window.__fimRestored = {"
            + "open: document.getElementById('graph-zoom-modal').open, "
            + "paneParent: pane.parentElement.className, "
            + "scrubberParent: scrubber.parentElement.className, "
            + "statsParent: stats.parentElement.id, "
            + "hidden: pane.hidden, "
            + "placeholder: !!document.getElementById('graph-zoom-placeholder'), "
            + "scrubberPlaceholder: "
            + "!!document.getElementById('graph-zoom-scrubber-placeholder'), "
            + "statsPlaceholder: "
            + "!!document.getElementById('graph-zoom-stats-placeholder'), "
            + "graphColumn: !!document.getElementById('graph-zoom-graph-column'), "
            + "inlineWidth: pane.style.width"
            + "}; "
            + "}, 50); "
            + "return; "
            + "} "
            + "setTimeout(pollCompleted, 50); "
            + "}; "
            + "setTimeout(pollCompleted, 50);"
        ),
        read=(
            "({"
            "runViewState: window.fim.getRunViewState(), "
            "homeBefore: window.__fimHomeBefore, "
            "zoom: window.__fimZoom, "
            "restored: window.__fimRestored"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("restored") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["homeBefore"]["pane"] == "run-visual-panels"
    assert settled["homeBefore"]["scrubber"] == "run-graph-body"
    assert settled["homeBefore"]["stats"] == "run-plot-row"
    assert settled["zoom"]["open"] is True
    # All three land inside the frame: the pane and the scrubber inside
    # the graph column `openGraphZoom` builds, the stats table inside
    # its own scrolling wrapper beside that column.
    assert settled["zoom"]["paneParent"] == "graph-zoom-graph-column"
    assert settled["zoom"]["scrubberParent"] == "graph-zoom-graph-column"
    assert settled["zoom"]["statsParent"] == "graph-zoom-stats-column"
    assert settled["zoom"]["title"] == "Statistic trajectories"
    # Zooming does not change which graph the stage considers active, so
    # closing returns to the same one.
    assert settled["zoom"]["active"] == "trajectory"

    assert settled["restored"]["open"] is False
    assert settled["restored"]["paneParent"] == "run-visual-panels"
    assert settled["restored"]["scrubberParent"] == "run-graph-body"
    assert settled["restored"]["statsParent"] == "run-plot-row"
    assert settled["restored"]["hidden"] is False
    # Every placeholder and the graph column itself -- all transient,
    # built or created fresh on the next open -- are cleaned up, and the
    # explicit pixel size the zoom frame set on the pane is cleared;
    # otherwise the pane would keep the frame's dimensions back on the
    # stage.
    assert settled["restored"]["placeholder"] is False
    assert settled["restored"]["scrubberPlaceholder"] is False
    assert settled["restored"]["statsPlaceholder"] is False
    assert settled["restored"]["graphColumn"] is False
    assert settled["restored"]["inlineWidth"] == ""


def test_graph_zoom_sizes_are_a_function_of_the_frame_not_of_the_last_zoom(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Zoom levels scale from the frame, and the stats table stays put.

    Reported from the zoom frame: at 150% the statistics table vanished,
    at 50% the graph drew at about a quarter of the frame while the
    label still said 50%, and Fit did not return to the opening size.
    Two causes, both pinned here. The graph column's flex basis was its
    own content (so zooming in pushed the table out), and the base size
    was measured off the pane itself (so every step compounded on the
    previous one). Also pinned: the table keeps its natural height
    instead of spreading its rows across a stretched box.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const poll = () => { "
            + "if (window.fim.getRunViewState() === 'completed' && "
            + "window.__fimScrubberPending === 0) { "
            + "const pane = document.getElementById('run-trajectory-frame'); "
            + "const stats = document.getElementById('results-stats'); "
            + "const column = () => "
            + "document.getElementById('graph-zoom-graph-column'); "
            + "const click = (id) => document.getElementById(id).click(); "
            + "const read = () => ({"
            + "level: document.getElementById('graph-zoom-level').textContent, "
            + "paneWidth: pane.getBoundingClientRect().width, "
            + "columnWidth: column().getBoundingClientRect().width, "
            + "statsHeight: stats.getBoundingClientRect().height, "
            + "statsVisible: stats.getBoundingClientRect().width > 0"
            + "}); "
            + "window.fim.openGraphZoom(); "
            + "const fit = read(); "
            + "click('graph-zoom-in'); click('graph-zoom-in'); "
            + "const zoomedIn = read(); "
            + "click('graph-zoom-fit'); "
            + "const refit = read(); "
            + "click('graph-zoom-out'); click('graph-zoom-out'); "
            + "const zoomedOut = read(); "
            + "click('graph-zoom-fit'); "
            + "window.__fimZoomSizes = {fit, zoomedIn, refit, zoomedOut, "
            + "final: read()}; "
            + "return; "
            + "} "
            + "setTimeout(poll, 50); "
            + "}; "
            + "setTimeout(poll, 50);"
        ),
        read="window.__fimZoomSizes || null",
        is_ready=lambda value: value is not None,
        poll_attempts=_POLL_ATTEMPTS,
    )

    fit = settled["fit"]
    assert fit["level"] == "100%"
    # The stats table never leaves the frame, at any level.
    for name in ("fit", "zoomedIn", "refit", "zoomedOut", "final"):
        assert settled[name]["statsVisible"] is True, name
        # Natural height: identical at every level, not stretched.
        assert settled[name]["statsHeight"] == fit["statsHeight"], name
        # The column keeps its width: it does not grow with the pane.
        assert settled[name]["columnWidth"] == fit["columnWidth"], name
    assert settled["zoomedIn"]["level"] == "150%"
    assert settled["zoomedIn"]["paneWidth"] > fit["paneWidth"] * 1.4
    assert settled["zoomedOut"]["level"] == "50%"
    assert abs(settled["zoomedOut"]["paneWidth"] - fit["paneWidth"] * 0.5) < 3
    # Fit returns to exactly the opening size, however it got there.
    assert settled["refit"]["paneWidth"] == fit["paneWidth"]
    assert settled["final"]["paneWidth"] == fit["paneWidth"]


def test_deme_pair_selectors_stay_glued_to_the_scatter_axes(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Both deme selectors sit on the axes they label, not adrift.

    They are positioned purely by `grid-column`/`grid-row` inside `.run-
    canvas-frame`'s own grid -- the y selector rotated in the column
    beside the plot, the x selector centered in the row beneath it --
    so they are glued to the canvas only for as long as they remain
    children of that frame.

    Reported once already: the graph-stage rewrite hoisted them into
    the stage toolbar, where those placements resolved against the
    wrong container and both selectors floated away from the plot (the
    y selector landed over the card's own title). Nothing failed; the
    layout was simply wrong. Hence this check on the geometry itself
    rather than on the markup: the selectors must straddle the canvas.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const poll = () => { "
            + "if (window.fim.getRunViewState() === 'completed') { "
            + "window.fim.showGraph('scatter'); "
            + "const box = (selector) => { "
            + "const el = document.querySelector(selector); "
            + "const b = el.getBoundingClientRect(); "
            + "return {left: b.left, top: b.top, right: b.right, "
            + "bottom: b.bottom, cx: (b.left + b.right) / 2, "
            + "cy: (b.top + b.bottom) / 2}; }; "
            + "window.__fimAxes = {canvas: box('#run-canvas'), "
            + "y: box('#run-y-deme'), x: box('#run-x-deme'), "
            + "parent: document.getElementById("
            + "'run-deme-pair-selector').parentElement.id}; "
            + "return; } setTimeout(poll, 50); }; setTimeout(poll, 50);"
        ),
        read=("({runViewState: window.fim.getRunViewState(), axes: window.__fimAxes})"),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("axes") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    axes = settled["axes"]
    assert axes["parent"] == "run-scatter-card"
    # Measured on the `<select>`s themselves, not their labels: the y
    # control is rotated, and `getBoundingClientRect` reports the
    # transformed box -- the strip a reader actually sees -- whereas the
    # label's box is the control's un-rotated width, four times wider
    # and centered on the strip. Reading the label is what made an
    # earlier version of this test pass while the selector visibly
    # floated well clear of the plot.
    #
    # The y selector sits wholly left of the plot and is centered on it
    # vertically; the x selector sits wholly below the plot and is
    # centered on it horizontally. A generous 12px tolerance on the two
    # centerings -- this is asserting "attached to the axis", not
    # pixel-exact placement that font metrics could shift.
    assert axes["y"]["right"] <= axes["canvas"]["left"]
    assert abs(axes["y"]["cy"] - axes["canvas"]["cy"]) <= 12
    assert axes["x"]["top"] >= axes["canvas"]["bottom"]
    assert abs(axes["x"]["cx"] - axes["canvas"]["cx"]) <= 12

    # Both controls stand off the plot by the same distance, which is
    # the frame grid's own gap in each direction. Reported as the left
    # selector sitting far further out than the bottom one (measured
    # then: 38px beside the plot against 8px below it). The 2px
    # tolerance is for sub-pixel rounding of a single `0.35rem` gap, not
    # for slack -- it is deliberately tight enough to fail if the y
    # label's box ever stops hugging its rotated control, which is the
    # thing that actually went wrong.
    gap_beside = axes["canvas"]["left"] - axes["y"]["right"]
    gap_below = axes["x"]["top"] - axes["canvas"]["bottom"]
    assert abs(gap_beside - gap_below) <= 2, (
        f"y selector stands {gap_beside}px from the plot but the x "
        f"selector stands {gap_below}px from it"
    )


def test_the_graph_you_are_watching_survives_the_run_finishing(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Completion leaves the stage on whatever graph the user chose.

    Reported directly: mid-run, the pull-down was set to the scatter;
    when the run finished the stage jumped to the trajectory, which was
    both unasked-for and inconsistent with the pull-down still showing
    the old choice.

    The cause was `resetGraphStage`, called on the way into `completed`
    to drop the previous state's stale availability, also resetting the
    *preference* back to the default. Clearing what has data is right --
    the panes re-declare themselves immediately after; discarding the
    user's choice is not.

    The selection is driven here through a real `change` event on the
    `<select>`, not `showGraph`, because the option list is rebuilt in
    between (the scatter is the only graph with data until the first
    progress message lands) and the point is that the control and the
    stage still agree afterwards.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "window.__fimSwitched = false; "
            + "document.getElementById('run-button').click(); "
            + "const poll = () => { "
            + "const state = window.fim.getRunViewState(); "
            + "const select = document.getElementById('run-graph-select'); "
            + "if (state === 'running' && !window.__fimSwitched) { "
            + "select.value = 'scatter'; "
            + "select.dispatchEvent(new Event('change', {bubbles: true})); "
            + "window.__fimSwitched = window.fim.getActiveGraph() === 'scatter'; } "
            + "if (state === 'completed' && window.__fimSwitched) { "
            + "window.__fimAfter = {active: window.fim.getActiveGraph(), "
            + "value: select.value, "
            + "text: select.selectedOptions[0].textContent, "
            + "options: Array.from(select.options).map((o) => o.value), "
            + "visible: ['run-scatter-card', 'run-trajectory-frame', "
            + "'allele-composition-card', 'frequency-spectrum-card']"
            + ".filter((id) => !document.getElementById(id).hidden)}; "
            + "return; } setTimeout(poll, 20); }; setTimeout(poll, 20);"
        ),
        read=(
            "({runViewState: window.fim.getRunViewState(), "
            "switched: window.__fimSwitched === true, "
            "after: window.__fimAfter})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("runViewState") == "completed"
            and value.get("after") is not None
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    # The switch really happened while the run was still going, so the
    # assertions below are about surviving completion rather than about
    # a selection made after the fact.
    assert settled["switched"] is True

    after = settled["after"]
    assert after["active"] == "scatter"
    assert after["visible"] == ["run-scatter-card"]

    # The control agrees with the stage -- in both its value and the
    # label a reader actually sees, which is the half that was reported
    # as wrong. By this point the option list has grown from one entry
    # to four underneath the selection.
    assert after["options"] == [
        "scatter",
        "trajectory",
        "alleleComposition",
        "frequencySpectrum",
    ]
    assert after["value"] == "scatter"
    assert after["text"] == "Allele frequencies by deme pair"


def test_nice_axis_ticks_round_to_human_steps_at_every_scale(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`niceAxisTicks` returns 1/2/5-times-a-power-of-ten steps at any scale.

    The shared convention behind every Run-card graph's own tick marks
    (design intent, per the project owner: ticks should feel natural
    whether the axis is `[0, 1]` or generations into the thousands).
    Evaluated against the real page's own global, not a Python
    reimplementation that could drift from it.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="void 0;",
        read=(
            "({"
            "probability: niceAxisTicks(0, 1, 6), "
            "shortRun: niceAxisTicks(0, 9, 6), "
            "mediumRun: niceAxisTicks(0, 67, 6), "
            "longRun: niceAxisTicks(0, 10000, 6), "
            "narrow: niceAxisTicks(0.13, 0.47, 6), "
            "degenerate: niceAxisTicks(2, 2, 6)"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("probability") is not None
        ),
    )

    # The reference fifths a `[0, 1]` axis has always shown -- the nice-
    # number convention reproduces them exactly, by construction.
    assert settled["probability"] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    # Whole-generation steps, growing with the run's own length -- the
    # "thousands of generations" case reads as 2000, 4000, ... not as a
    # label every generation or none at all.
    assert settled["shortRun"] == [0, 2, 4, 6, 8]
    assert settled["mediumRun"] == [0, 10, 20, 30, 40, 50, 60]
    assert settled["longRun"] == [0, 2000, 4000, 6000, 8000, 10000]
    # A narrow sub-unit range still gets round steps, at a finer power
    # of ten.
    assert settled["narrow"] == [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45]
    assert settled["degenerate"] == []


def test_every_run_card_graph_draws_tick_marks_on_its_axes(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Every stage graph's axes carry real tick marks, not bare frames.

    Reported directly: "All graphs have axes needing 'ticks'." The
    trajectory drew only corner labels, the allele-composition and
    frequency-spectrum panels drew bare frames -- nothing to read an
    intermediate value against. The scatter already ticked at fifths
    (`PROBABILITY_TICK_VALUES`); it is included here as the control:
    its strip must keep its marks after the shared tick-drawing
    refactor (`drawAxisTickMarks`), while the other three gain marks
    they never had.

    Measured on the canvas pixels themselves, in the strip just outside
    each axis line where only a tick mark can draw: a count of dark
    pixels there is the behavior a reader sees, not a markup detail.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const finish = () => { "
            + "const dark = (id, x0, x1, y0, y1) => { "
            + "const c = document.getElementById(id); "
            + "const ctx = c.getContext('2d'); "
            + "const w = Math.max(1, Math.ceil(x1) - Math.floor(x0)); "
            + "const h = Math.max(1, Math.ceil(y1) - Math.floor(y0)); "
            + "const data = ctx.getImageData("
            + "Math.floor(x0), Math.floor(y0), w, h).data; "
            + "let n = 0; "
            + "for (let i = 3; i < data.length; i += 4) { "
            + "if (data[i] !== 0) { n += 1; } } return n; }; "
            # The trajectory panel's own geometry: plotLeft 42,
            # plotTop 12, plotBottom = height - 22.
            "window.fim.showGraph('trajectory'); "
            "const tC = document.getElementById('run-trajectory-canvas'); "
            "const tLeft = 42, tTop = 12, tBottom = tC.height - 22; "
            "const tY = dark('run-trajectory-canvas', "
            "tLeft - 4, tLeft - 1, tTop + 15, tBottom - 15); "
            "const tX = dark('run-trajectory-canvas', "
            "tLeft + 40, tC.width - 12 - 40, tBottom + 1, tBottom + 3); "
            # The allele-composition panel: plotLeft 36, plotTop 12,
            # plotBottom = height - 28.
            "window.fim.showGraph('alleleComposition'); "
            "const aC = document.getElementById('allele-composition-canvas'); "
            "const aY = dark('allele-composition-canvas', "
            "36 - 4, 36 - 1, 12 + 15, (aC.height - 28) - 15); "
            # The frequency-spectrum panel: same margins; its x ticks
            # sit just below the frame.
            "window.fim.showGraph('frequencySpectrum'); "
            "const sC = document.getElementById('frequency-spectrum-canvas'); "
            "const sX = dark('frequency-spectrum-canvas', "
            "36 + 1, sC.width - 12 - 1, (sC.height - 28) + 1, (sC.height - 28) + 3); "
            # The scatter (control): `drawScatterCell`'s own geometry
            # for a single panel -- adaptive padding, square plot. Tick
            # position 0.5, the one value present at every density
            # level (full fifths and compact thirds alike).
            "window.fim.showGraph('scatter'); "
            "const rC = document.getElementById('run-canvas'); "
            "const side = Math.min(rC.width, rC.height); "
            "const pad = side < 520 ? Math.max(28, Math.floor(side * 0.09)) : 44; "
            "const plotSize = side - 2 * pad; "
            "const sTickX = pad + 0.5 * plotSize; "
            "const sTick = dark('run-canvas', "
            "sTickX - 1, sTickX + 1, (rC.height - pad) + 1, (rC.height - pad) + 3); "
            "window.__fimTickPixels = {"
            "trajectoryY: tY, trajectoryX: tX, compositionY: aY, "
            "spectrumX: sX, scatterControl: sTick}; "
            + "}; "
            + "const pollTicks = () => { "
            + "if (window.fim.getRunViewState() === 'completed' && "
            + "window.__fimScrubberPending === 0) { finish(); return; } "
            + "setTimeout(pollTicks, 50); }; setTimeout(pollTicks, 50);"
        ),
        read="window.__fimTickPixels || null",
        is_ready=lambda value: value is not None,
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled is not None
    # The control: the scatter's own fifths ticks survive the refactor.
    assert settled["scatterControl"] > 0
    # The three graphs that drew no tick marks before: each now leaves
    # real marks in the strip just outside its own axis.
    assert settled["trajectoryY"] > 0
    assert settled["trajectoryX"] > 0
    assert settled["compositionY"] > 0
    assert settled["spectrumX"] > 0


def test_scrubbing_a_completed_scalar_run_moves_every_stats_row(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The scalar stats panel -- including the derived Ne rows -- tracks the scrubber.

    The named-statistic rows have moved with the scrubber since the
    completed-state scrubber first existed (`updateScrubbedTrajectory`);
    the two derived effective-allele rows (`Ne_S`/`Ne_T`, each a
    closed-form `1 / (1 - H)` transform of the same run's own H_S/H_T
    history value at that generation) did not -- the panel again showed
    two different generations at once, the same staleness class the
    batch panel's own version of this fix was reported for. This pins
    both halves plus the exact restore at the final frame, so the
    connection cannot quietly drop again on either one.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "document.getElementById('run-button').click(); "
            + "const pollStats = () => { "
            + "if (window.fim.getRunViewState() === 'completed' && "
            + "window.__fimScrubberPending === 0) { "
            + "const read = () => { "
            + "return {"
            + "d: document.querySelector('#stat-D .stat-value').textContent, "
            + "ne: document.querySelector('#stat-Ne_S .stat-value').textContent, "
            + "label: document.getElementById('scrubber-label').textContent"
            + "}; }; "
            + "window.__fimStatScrub = {final: read()}; "
            + "const range = document.getElementById('scrubber-range'); "
            + "range.value = '0'; "
            + "range.dispatchEvent(new Event('input', {bubbles: true})); "
            + "window.__fimStatScrub.scrubbed = read(); "
            + "range.value = range.max; "
            + "range.dispatchEvent(new Event('input', {bubbles: true})); "
            + "window.__fimStatScrub.restored = read(); "
            + "return; } setTimeout(pollStats, 50); }; setTimeout(pollStats, 50);"
        ),
        read="window.__fimStatScrub || null",
        is_ready=lambda value: value is not None and value.get("restored") is not None,
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled is not None
    # The scrub really moved to the run's own first generation.
    assert settled["scrubbed"]["label"] == "Generation 0"
    # Both the watched statistic and the derived row moved with it.
    assert settled["scrubbed"]["d"] != settled["final"]["d"]
    assert settled["scrubbed"]["ne"] != settled["final"]["ne"]
    assert settled["scrubbed"]["ne"] != "—"
    # Back at the final frame, the authoritative final values restore
    # verbatim, both rows.
    assert settled["restored"]["d"] == settled["final"]["d"]
    assert settled["restored"]["ne"] == settled["final"]["ne"]
