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
            "document.getElementById('scrubber-play-button').disabled, "
            "scrubberParentClass: "
            "document.getElementById('scrubber-controls').parentElement.className, "
            "scrubberPreviousClass: document.getElementById("
            "'scrubber-controls').previousElementSibling.className, "
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
    assert settled["supplementalHidden"] is False
    assert settled["ibdHidden"] is True
    layout = settled["layout"]
    assert layout["rowDisplay"] == "grid"
    assert layout["scatter"]["gridColumnStart"] == "1"
    assert layout["scatter"]["gridRowStart"] == "1"
    assert layout["trajectory"]["gridColumnStart"] == "2"
    assert layout["trajectory"]["gridRowStart"] == "1"
    assert layout["composition"]["gridColumnStart"] == "1"
    assert layout["composition"]["gridRowStart"] == "2"
    assert layout["spectrum"]["gridColumnStart"] == "2"
    assert layout["spectrum"]["gridRowStart"] == "2"
    assert layout["stats"]["gridColumnStart"] == "3"
    assert layout["stats"]["gridRowStart"] == "1"
    assert layout["stats"]["gridRowEnd"] == "4"
    assert layout["trajectory"]["left"] > layout["scatter"]["right"]
    assert layout["composition"]["top"] > layout["scatter"]["bottom"]
    assert layout["spectrum"]["top"] > layout["trajectory"]["bottom"]
    assert abs(layout["composition"]["left"] - layout["scatter"]["left"]) < 5
    assert abs(layout["spectrum"]["left"] - layout["trajectory"]["left"]) < 5
    assert layout["stats"]["left"] > layout["trajectory"]["right"]
    # `tiny_params`-scale runs always persist more than one generation
    # (`convergence_window`'s own minimum of 2 forces at least one step
    # past generation 0 before stability can first be evaluated), so the
    # scrubber (no separate "Animate" button, this is the
    # same time slider `completed` shows directly) is populated and
    # enabled here, not just present.
    assert settled["scrubberHidden"] is False
    assert settled["scrubberPlayDisabled"] is False
    assert settled["scrubberParentClass"] == "run-visual-column"
    # `.run-visual-panels` -- now the shared flex-wrap row holding all
    # four graphs (scatter, trajectory, and the two supplemental graphs
    # folded in alongside them, replacing the old standalone "Literature
    # visualizations" panel/section) -- still sits immediately above the
    # scrubber, unchanged from before that panel existed at all.
    assert settled["scrubberPreviousClass"] == "run-visual-panels"
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
            # `scrubber.js` keeps its frame list private; its range's own
            # `max` is the index of the last frame, so the count is that
            # plus one.
            "frameCount: Number("
            "document.getElementById('scrubber-range').max) + 1, "
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
                "statsTop: statsRect.top, statsBottom: statsRect.bottom"
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
    # Same line as the scatter plot, not wrapped onto a line below it --
    # a small tolerance for sub-pixel layout rounding, matching this
    # project's other bounding-box assertions. Both frames are titled
    # cards now, so the comparable edge is the frame's own top, not the
    # canvas inside it (which sits below its card's `<h4>`).
    assert abs(settled["trajectoryTop"] - settled["scatterTop"]) < 5
    assert abs(settled["statsTop"] - settled["scatterTop"]) < 30
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
    assert settled["alleleCompositionHidden"] is False
    assert settled["frequencySpectrumHidden"] is False
    assert "Generation 0" in settled["scrubberLabel"]
