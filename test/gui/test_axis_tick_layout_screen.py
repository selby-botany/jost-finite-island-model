"""Axis tick labels never print over one another or over a corner label.

The trajectory graph draws exact corner labels ("gen 0", "gen N") and
interior tick labels ("5000", "10000", ...). A run of 21,910 generations put
"20000" on top of "gen 21910". `drawAxisTickMarks` now leaves off a label
that would come within a few pixels of another, and the trajectory axis
tells it where its corner labels are. These tests drive the real drawing
function with a recording stand-in for the canvas context (text is 6 pixels
per character), so the result is a pure function of the code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Draws one trajectory axis block on a recording context and returns every
# label with the pixel span it occupies, plus the corner-label spans.
_LAYOUT = """
(() => {
    function recorder() {
        const calls = [];
        const context = {
            textAlign: "left", font: "", lineWidth: 1,
            save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {},
            stroke() {},
            measureText(text) { return {width: String(text).length * 6}; },
            fillText(text, x, y) {
                calls.push({text: String(text), x, y, align: this.textAlign});
            },
        };
        return {context, calls};
    }
    function spanOf(call) {
        const width = call.text.length * 6;
        if (call.align === "center") {
            return {start: call.x - width / 2, end: call.x + width / 2};
        }
        return call.align === "right"
            ? {start: call.x - width, end: call.x}
            : {start: call.x, end: call.x + width};
    }
    function layout(maxGeneration, plotWidth) {
        const {context, calls} = recorder();
        const plotLeft = 40;
        const plotRight = plotLeft + plotWidth;
        const xToPixel = (g) => plotLeft + (g / maxGeneration) * plotWidth;
        const yToPixel = (v) => 150 - v * 140;
        drawTrajectoryAxisTicks(
            context, plotLeft, 150, 0, maxGeneration, 0, 1, xToPixel, yToPixel);
        const corners = [
            {start: plotLeft, end: plotLeft + `gen 0`.length * 6},
            {start: plotRight - `gen ${maxGeneration}`.length * 6, end: plotRight},
        ];
        const xLabels = calls
            .filter((call) => call.align === "center")
            .map((call) => ({text: call.text, ...spanOf(call)}));
        return {xLabels, corners};
    }
    function overlaps(first, second) {
        return first.start < second.end && second.start < first.end;
    }
    const worst = [];
    for (const maxGeneration of [10, 67, 10000, 21910, 200000, 2000000]) {
        for (const width of [150, 300, 500, 1000, 2000]) {
            const {xLabels, corners} = layout(maxGeneration, width);
            let crowded = 0;
            for (let i = 0; i < xLabels.length; i += 1) {
                for (const corner of corners) {
                    if (overlaps(xLabels[i], corner)) { crowded += 1; }
                }
                for (let j = i + 1; j < xLabels.length; j += 1) {
                    if (overlaps(xLabels[i], xLabels[j])) { crowded += 1; }
                }
            }
            worst.push({maxGeneration, width, crowded, count: xLabels.length});
        }
    }
    return {
        cases: worst,
        reported: layout(21910, 500).xLabels.map((label) => label.text),
        wide: layout(21910, 2000).xLabels.map((label) => label.text),
        long: layout(2000000, 1000).xLabels.map((label) => label.text),
    };
})()
"""


def test_axis_labels_never_overlap_at_any_run_length_or_width(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """No two labels, and no label and corner, overlap, for any size."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_LAYOUT)

    assert settled["cases"]
    assert [case for case in settled["cases"] if case["crowded"]] == []


def test_the_reported_collision_leaves_off_20000_not_the_corner(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """21,910 generations in a 500-pixel plot: "20000" goes, "gen N" stays."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_LAYOUT)

    assert "20000" not in settled["reported"]
    assert "10000" in settled["reported"]
    # A wide plot has room for every interior tick.
    assert "20000" in settled["wide"]


def test_long_runs_label_whole_thousands_compactly(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A two-million-generation axis reads "500k", not "500000"."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_LAYOUT)

    assert settled["long"]
    assert all(label.endswith("k") for label in settled["long"])
