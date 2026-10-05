"""Headless functional tests for the window estimate and the trailing-mean display.

A statistic's tooltip leads with its mean over the convergence window (or,
at a finished run's end, over the monitor's grown evidence window), that
mean's standard error, and how many standard errors it lies from the
prediction. The trajectory graph can draw each statistic's trailing mean
with a standard-error band instead of its value at every generation. The
page computes both with its own copy of
`fim.convergence.window_statistics`, held equal to the Python here.
"""

from __future__ import annotations

import json
import queue
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.convergence.window_statistics import window_statistics
from fim.gui.preferences import GuiPreferences, save_preferences

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# The tooltip uses a true minus sign (U+2212), not a hyphen.
_MINUS = "\u2212"

# Generation ranges are written with an en dash (U+2013).
_EN_DASH = "\u2013"


def _series() -> dict[str, list[float]]:
    """Seeded test series: correlated, flat, alternating, and drifting."""
    generator = random.Random(20261005)
    correlated = [0.5]
    for _ in range(299):
        correlated.append(
            0.5 + 0.9 * (correlated[-1] - 0.5) + generator.gauss(0.0, 0.05)
        )
    return {
        "correlated": correlated,
        "flat": [0.25] * 40,
        "alternating": [0.1 if index % 2 else 0.3 for index in range(40)],
        "drifting": [
            0.001 * index + generator.gauss(0.0, 0.01) for index in range(200)
        ],
    }


# (series, first index, last index), inclusive.
_WINDOWS = [
    ("correlated", 0, 299),
    ("correlated", 100, 107),
    ("correlated", 37, 251),
    ("flat", 5, 30),
    ("alternating", 0, 39),
    ("alternating", 3, 18),
    ("drifting", 0, 199),
    ("drifting", 150, 199),
]


def test_the_page_estimator_matches_window_statistics(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Mean and standard error agree with Python's for every test window."""
    series = _series()
    script = (
        "(() => {"
        f"const series = {json.dumps(series)};"
        f"const windows = {json.dumps(_WINDOWS)};"
        "const results = windows.map(([name, start, end]) =>"
        "    trailingWindowEstimate(windowSums(series[name]), start, end));"
        "results.push(trailingWindowEstimate(windowSums(series.flat), 0, 6));"
        "results.push(windowSums([0.1, NaN, 0.2]));"
        "return results;"
        "})()"
    )
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=script)

    for (name, start, end), page in zip(_WINDOWS, settled, strict=False):
        expected = window_statistics(series[name][start : end + 1])
        assert page["count"] == end - start + 1, (name, start, end)
        assert page["mean"] == pytest.approx(expected.mean, abs=1e-12)
        assert page["standardError"] == pytest.approx(
            expected.standard_error, rel=1e-9, abs=1e-15
        ), (name, start, end)
    # Seven points are too few to estimate; a non-finite value has no sums.
    assert settled[-2] is None
    assert settled[-1] is None


_WINDOWING = """
(() => {
    const generations = [0, 5, 10, 11, 12, 20];
    const values = Array.from({length: 12}, (_, index) => 0.1 * (index % 3));
    const series = trailingMeanSeries(
        Array.from({length: 12}, (_, index) => index), values, 10
    );
    return {
        start: trailingWindowStart(generations, 5, 10),
        startAll: trailingWindowStart(generations, 2, 100),
        leadingGaps: series.mean.slice(0, 7).every(Number.isNaN),
        firstMean: series.mean[7],
        bandBelow: series.low[11] < series.mean[11],
        bandAbove: series.high[11] > series.mean[11],
        anchors: evidenceWindowAnchors(
            {D: {window: 30}, G_ST: {window: 10}, H_S: {window: 99}}, 40, 10
        ),
    };
})()
"""


def test_the_trailing_window_counts_generations_and_starts_with_enough_points(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The window spans generations; the curve begins at eight points."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_WINDOWING)

    # Generation 20's window of 10 holds every generation after 10.
    assert settled["start"] == 3
    assert settled["startAll"] == 0
    assert settled["leadingGaps"] is True
    first_window = [0, 1, 2, 0, 1, 2, 0, 1]
    assert settled["firstMean"] == pytest.approx(0.1 * sum(first_window) / 8)
    assert settled["bandBelow"] is True
    assert settled["bandAbove"] is True
    # Only a window that grew past the convergence window, and fits in
    # the recorded history, is anchored.
    assert settled["anchors"] == {"D": 10}


_VALUES = [0.12, 0.31, 0.22, 0.45, 0.28, 0.37, 0.19, 0.41, 0.33, 0.26, 0.39, 0.30]

_NOTES = f"""
(() => {{
    const values = {json.dumps(_VALUES)};
    const generations = values.map((_, index) => index);
    const comparisons = closedFormComparisons(
        generations, {{D: values}}, null, {{D: "0.25"}}
    );
    const source = (anchors, tolerance) => ({{
        generations,
        histories: {{D: values}},
        comparisons,
        convergence: {{window: 8, tolerance}},
        anchors,
        sums: {{}},
    }});
    const sparse = values.map((_, index) => 10 * index);
    return {{
        trailing: estimateNote(source({{}}, 1.0), "D"),
        anchored: estimateNote(source({{D: 2}}, 1.0), "D"),
        scrubbed: estimateNote(source({{D: 2}}, 1.0), "D", 8),
        tooShort: estimateNote(source({{}}, 1.0), "D", 5),
        inadequate: estimateNote(source({{}}, 1e-6), "D"),
        sparse: estimateNote({{
            generations: sparse,
            histories: {{D: values}},
            comparisons: {{}},
            convergence: {{window: 75, tolerance: 1.0}},
            anchors: {{}},
            sums: null,
        }}, "D"),
        noWindow: estimateNote(source({{}}, 1.0), "K_ST"),
        joined: withNotes(["first", "", "second"], "description"),
    }};
}})()
"""


def _expected_note(start: int, end: int, *, predicted: float | None) -> str:
    """The tooltip clause for `_VALUES[start..end]`, computed in Python."""
    stats = window_statistics(_VALUES[start : end + 1])
    count = end - start + 1
    note = (
        f"mean {stats.mean:.4f} ± {stats.standard_error:.4f} over generations "
        f"{start}{_EN_DASH}{end} ({count} generations; 1 SE)"
    )
    if predicted is not None:
        distance = (stats.mean - predicted) / stats.standard_error
        sign = _MINUS if distance < 0 else "+"
        note += f"; predicted {predicted:.4f}, {sign}{abs(distance):.1f} SE"
    return note


def test_the_estimate_note_text(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Trailing, anchored, scrubbed, sparse and inadequate estimates read right."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_NOTES)

    assert settled["trailing"] == _expected_note(4, 11, predicted=0.25)
    # Inside the grown evidence window: everything from its start.
    assert settled["anchored"] == _expected_note(2, 11, predicted=0.25)
    # Scrubbed back to where the evidence window is shorter than the
    # convergence window: the ordinary trailing window again.
    assert settled["scrubbed"] == _expected_note(1, 8, predicted=0.25)
    assert settled["tooShort"] == ""
    assert "(8 generations; 1 SE, not yet noise-adequate)" in settled["inadequate"]
    # Sparse live ticks: the window spans generations 40-110, eight ticks.
    sparse = window_statistics(_VALUES[4:])
    assert settled["sparse"] == (
        f"mean {sparse.mean:.4f} ± {sparse.standard_error:.4f} over generations "
        f"40{_EN_DASH}110 (71 generations, 8 recorded; 1 SE)"
    )
    assert settled["noWindow"] == ""
    assert settled["joined"] == "first — second — description"


@pytest.fixture
def estimable_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings for a fast run long enough to estimate a window mean.

    `fast_scalar_run_settings`' window of 4 is shorter than the eight
    points an estimate needs, so this one uses a window of 8.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "1",
                "max_generations": "40",
                "convergence_window": "8",
                "convergence_tolerance": "1.0",
            },
        ),
    )
    return _isolate_gui_preferences


def _poll(window: webview.Window, script: str, predicate: Callable[[Any], bool]) -> Any:
    """Evaluate `script` until `predicate` holds (bounded), returning the value."""
    value = None
    for _ in range(600):
        value = window.evaluate_js(script)
        if predicate(value):
            return value
        time.sleep(0.1)
    return value


def test_a_completed_run_leads_with_its_estimate_and_offers_the_trailing_mean(
    estimable_run_settings: Path, window: webview.Window
) -> None:
    """D's tooltip leads with its mean ± SE; the display choice redraws and persists."""
    set_fields = (
        "function setField(name, value) {"
        "const field = document.getElementById(`field-${name}`);"
        "field.value = value;"
        "field.dispatchEvent(new Event('input', {bubbles: true}));"
        "}"
        "setField('N', '20'); setField('d', '2'); setField('seed', '20261005');"
        "setField('m_rate', '0.1'); setField('mu_value', '0.01');"
        "setField('locus_lengths', '200');"
    )
    completed = (
        "({"
        "state: window.fim.getRunViewState(), "
        "pending: window.__fimScrubberPending, "
        "D: document.getElementById('stat-D').title, "
        "chooserHidden: document.getElementById("
        "'run-trajectory-display-control').hidden, "
        "legend: document.getElementById('run-trajectory-legend').textContent"
        "})"
    )
    choose = (
        "(() => {"
        "const select = document.getElementById('run-trajectory-display');"
        "select.value = 'trailing_mean';"
        "select.dispatchEvent(new Event('change', {bubbles: true}));"
        "})()"
    )
    chosen = (
        "(() => {"
        "window.pywebview.api.get_run_card_layout()"
        ".then((layout) => { window.__trajectoryLayout = layout; });"
        "return {"
        "legend: document.getElementById('run-trajectory-legend').textContent, "
        "saved: window.__trajectoryLayout"
        "    ? window.__trajectoryLayout.trajectoryDisplay : null"
        "};"
        "})()"
    )
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            before = _poll(
                window,
                completed,
                lambda value: value["state"] == "completed" and value["pending"] == 0,
            )
            window.evaluate_js(choose)
            after = _poll(
                window,
                chosen,
                lambda value: (
                    "trailing mean" in value["legend"]
                    and value["saved"] == "trailing_mean"
                ),
            )
            outcome.put({"before": before, "after": after})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)
    before = settled["before"]
    after = settled["after"]

    title = before["D"]
    assert " — mean " in title
    assert " over generations " in title
    assert "; predicted " in title
    # The estimate first, then the comparison, then the description.
    assert (
        title.index("mean ")
        < title.index("ΔDₚ")
        < title.index("allelic differentiation")
    )
    assert before["chooserHidden"] is False
    assert "trailing mean" not in before["legend"]
    assert "trailing mean ± 2 SE (last 8 generations)" in after["legend"]
    assert after["saved"] == "trailing_mean"
