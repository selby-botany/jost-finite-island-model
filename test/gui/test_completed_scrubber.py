"""Headless functional tests for the unified run view's `completed`-state
scrubber updating the stats table and trajectory panel, not only the
scatter (botanist GUI design doc §6.2/§6.3, `20260907-claude-sonnet-5-
botanist-gui-redesign.md`, `selby/restricted`).

Before this, dragging `#scrubber-range` on a just-finished (or reopened)
scalar run redrew only the scatter canvas (`wireCompletedScrubber`'s own
`drawFrame` callback, `screens/run-view-completed.js`) -- the six-row
stats table and the trajectory panel stayed frozen at the run's final
values no matter where the scrubber sat, a real, confirmed-live gap (not
a hypothesis) design §6.3's own "a scrubber... letting a user drag back
through already-computed history" already calls for. This module proves
the fix: scrubbing away from the final frame (1) updates the watched
convergence statistic's own row, and `D`/`G_ST`/`H_S`/`H_T`'s own rows
regardless of whether they are actually watched (`fim.engine._ALWAYS_
TRACKED_STATISTICS` -- landed after this module was first written,
folded in here rather than tracked as a second, separate change), to
that generation's real recorded value, (2) marks `E_ST`/`K_ST` "not
known at this generation" rather than a possibly-misleading final value
whenever neither is watched nor `track_expensive_statistics` opted in
(`ConvergenceMonitor.record` only ever records the always-tracked four
plus whichever of `E_ST`/`K_ST` were actually requested -- with neither
requested here, those two alone have no per-generation history to
show), (3) draws a moving vertical marker on the trajectory canvas at
the scrubbed generation, and (4) restores the real, authoritative final
statistics and removes the marker exactly at the scrubber's own last
frame.

Uses the same small, fast-converging configuration `test_results_screen.
py`'s own module docstring documents choosing for this exact reason
(completes in well under a second) -- `convergence_window`'s own minimum
of 2 forces at least one recorded generation past 0 before stability can
first be evaluated, so every run here always has more than one persisted
generation (and so a populated, enabled scrubber) to actually scrub
through.
"""

from __future__ import annotations

import queue
import time
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 600
_DRIVE_TIMEOUT_SECONDS = 3 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Mirrors `test_results_screen.py`'s own `_SET_TINY_FIELDS` field for
# field -- see that module's docstring for why these particular values
# (a small, fast-converging run, not the starter form's own
# `d: 20`/`max_generations: 10000` defaults).
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
setField('n_replicates', '1');
"""


def _poll_until(
    window: webview.Window,
    script: str,
    predicate: Any,
    poll_attempts: int = _POLL_ATTEMPTS,
) -> Any:
    value = None
    for _ in range(poll_attempts):
        value = window.evaluate_js(script)
        if predicate(value):
            return value
        time.sleep(_POLL_INTERVAL_SECONDS)
    return value


def _scrub_to(window: webview.Window, index: int) -> None:
    """Drag `#scrubber-range` to `index` and fire the same `"input"` event
    a real drag dispatches (`scrubber.js`'s own listener)."""
    window.evaluate_js(
        f"document.getElementById('scrubber-range').value = '{index}';"
        "document.getElementById('scrubber-range').dispatchEvent("
        "new Event('input', {bubbles: true}));"
    )


def test_scrubbing_to_an_earlier_generation_updates_the_stats_table_and_marker(
    window: webview.Window,
) -> None:
    """Scrubbing away from the final frame shows the watched statistic's
    own real, per-generation value (different at two different scrubbed
    generations -- proof it is a real lookup, not a static copy), marks
    the other five "not known at this generation" rather than a stale
    final value, and moves the trajectory canvas's own marker (a
    different pixel snapshot than the just-completed, unscrubbed
    panel)."""
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
            _poll_until(window, "window.__fimScrubberPending", lambda value: value == 0)
            final_snapshot = window.evaluate_js(
                "document.getElementById('run-trajectory-canvas').toDataURL()"
            )

            def _read() -> Any:
                return window.evaluate_js(
                    "({"
                    "label: document.getElementById('scrubber-label').textContent, "
                    "statDTitle: document.getElementById('stat-D').title, "
                    "statGSTTitle: document.getElementById('stat-G_ST').title, "
                    "statESTTitle: document.getElementById('stat-E_ST').title, "
                    "statESTValue: document.getElementById('stat-E_ST')"
                    ".querySelector('.stat-value').textContent, "
                    "trajectorySnapshot: "
                    "document.getElementById('run-trajectory-canvas').toDataURL()"
                    "})"
                )

            # Frame 0 is always generation 0 (`select_sample_generations`'s
            # own "always includes the lowest ... generation number") --
            # never the run's own final frame for any run that persisted
            # more than one generation, exactly the case this whole module
            # exists to drive. Frame 1 (generation 1) is a second, distinct
            # non-final generation, read the same way, to prove `stat-D`'s
            # own value really is a per-generation lookup rather than a
            # value fixed once and reused for every scrubbed frame.
            _scrub_to(window, 0)
            at_generation_0 = _read()
            _scrub_to(window, 1)
            at_generation_1 = _read()
            outcome.put(
                {
                    "finalSnapshot": final_snapshot,
                    "atGeneration0": at_generation_0,
                    "atGeneration1": at_generation_1,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    at0 = settled["atGeneration0"]
    at1 = settled["atGeneration1"]
    assert at0["label"].startswith("Generation 0 ")
    assert at1["label"].startswith("Generation 1 ")
    # `D` is the only *watched* statistic here (the form's own default
    # `cs_D` checkbox, untouched by `_SET_TINY_FIELDS`) -- both rows keep
    # showing a real value, each generation's own recorded value, not the
    # run's final one held over unchanged.
    assert at0["statDTitle"].startswith("D = ")
    assert at1["statDTitle"].startswith("D = ")
    assert at0["statDTitle"] != at1["statDTitle"]
    # `G_ST` is never watched here either, but it is one of the four
    # always-tracked statistics (`fim.engine._ALWAYS_TRACKED_STATISTICS`)
    # -- it shows a real, per-generation value too, the same shape `D`
    # does, not "not known."
    assert at0["statGSTTitle"].startswith("GST = ")
    assert at1["statGSTTitle"].startswith("GST = ")
    assert at0["statGSTTitle"] != at1["statGSTTitle"]
    # `E_ST` is neither watched nor opted into via `track_expensive_
    # statistics` (`_SET_TINY_FIELDS` never sets it) -- nothing was ever
    # recorded for it at either generation, so its row shows the same
    # "not known" placeholder `buildOmittedMeter` already establishes for
    # a batch summary statistic with no defined interval, reused verbatim
    # here.
    assert at0["statESTTitle"] == "not known at this generation"
    assert at0["statESTValue"] == "—"
    assert at1["statESTTitle"] == "not known at this generation"
    # The trajectory canvas's own pixels differ once scrubbed away from
    # the run's final state -- proof the marker (or the whole panel)
    # actually redrew, not just the scatter -- and differ again between
    # the two distinct scrubbed generations, proof the marker actually
    # moved rather than landing in the same place either time.
    assert at0["trajectorySnapshot"] != settled["finalSnapshot"]
    assert at1["trajectorySnapshot"] != settled["finalSnapshot"]
    assert at0["trajectorySnapshot"] != at1["trajectorySnapshot"]


def test_scrubbing_back_to_the_final_frame_restores_the_real_statistics_and_marker(
    window: webview.Window,
) -> None:
    """Scrubbing away and then back to the scrubber's own last frame
    restores the exact statistics table and trajectory canvas the run
    first completed with -- the marker is specific to a non-final
    scrub position, not a permanent addition to the panel."""
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
            _poll_until(window, "window.__fimScrubberPending", lambda value: value == 0)
            final_index = window.evaluate_js(
                "Number(document.getElementById('scrubber-range').max)"
            )
            final_snapshot = window.evaluate_js(
                "document.getElementById('run-trajectory-canvas').toDataURL()"
            )
            final_stat_g_title = window.evaluate_js(
                "document.getElementById('stat-G_ST').title"
            )
            _scrub_to(window, 0)
            # Confirm the scrub actually took effect before scrubbing
            # back -- otherwise a no-op "back to final" would trivially
            # "restore" a state that never changed. `E_ST` (neither
            # watched nor `track_expensive_statistics`-opted-in here) is
            # the one used for this check, not `G_ST` -- `G_ST` is one of
            # the four always-tracked statistics
            # (`fim.engine._ALWAYS_TRACKED_STATISTICS`) and so never
            # reads "not known" at any generation any more, which would
            # otherwise spin this poll out to its own full timeout.
            _poll_until(
                window,
                "document.getElementById('stat-E_ST').title",
                lambda value: value == "not known at this generation",
            )
            _scrub_to(window, final_index)
            restored = window.evaluate_js(
                "({"
                "statGTitle: document.getElementById('stat-G_ST').title, "
                "trajectorySnapshot: "
                "document.getElementById('run-trajectory-canvas').toDataURL()"
                "})"
            )
            outcome.put(
                {
                    "finalSnapshot": final_snapshot,
                    "finalStatGTitle": final_stat_g_title,
                    **restored,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert settled["statGTitle"] == settled["finalStatGTitle"]
    assert settled["statGTitle"].startswith("GST = ")
    assert settled["trajectorySnapshot"] == settled["finalSnapshot"]
