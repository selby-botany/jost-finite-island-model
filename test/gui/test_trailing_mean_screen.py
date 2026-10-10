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
import math
import queue
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.preferences import GuiPreferences, save_preferences

from .conftest import AWAIT_SETTINGS_SAVES, poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# The tooltip uses a true minus sign (U+2212), not a hyphen.
_MINUS = "\u2212"

# Generation ranges are written with an en dash (U+2013).
_EN_DASH = "\u2013"


@dataclass(frozen=True)
class _Window:
    """The mean and standard error of one window, given the run's `tau_int`."""

    mean: float
    standard_error: float


# The integrated autocorrelation time the tests hand the page, as the report
# would serve it (`window / effective_sample_size` of the evidence window).
_TAU_INT = 3.0


def window_statistics(values: list[float], tau_int: float = _TAU_INT) -> _Window:
    """Reference for the page's estimator: `SD * sqrt(tau_int / n)` (design 6.8).

    The standard error of a window mean uses the run's own integrated
    autocorrelation time, estimated once over its evidence window by Geyer's
    method and served with the report, not a guess from this window's lag-1
    correlation. A flat window is known exactly.
    """
    count = len(values)
    mean = math.fsum(values) / count
    centered = [value - mean for value in values]
    sum_squares = math.fsum(value * value for value in centered)
    deviation = math.sqrt(sum_squares / (count - 1))
    if deviation == 0.0:
        return _Window(mean, 0.0)
    return _Window(mean, deviation * math.sqrt(max(tau_int, 1.0) / count))


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
        "    trailingWindowEstimate("
        f"windowSums(series[name]), start, end, {_TAU_INT}));"
        "results.push(trailingWindowEstimate(windowSums(series.flat), 0, 6, 3.0));"
        "results.push(windowSums([0.1, NaN, 0.2]));"
        "results.push(trailingWindowEstimate("
        "    windowSums(series.correlated), 0, 99, null));"
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
    assert settled[-3] is None
    assert settled[-2] is None
    # Without the run's autocorrelation time (a live run) the mean is
    # known but its standard error is not: `NaN`, which JSON spells `null`.
    unknown = settled[-1]
    assert unknown["mean"] == pytest.approx(
        math.fsum(series["correlated"][:100]) / 100, abs=1e-12
    )
    assert unknown["standardError"] is None


_WINDOWING = """
(() => {
    const generations = [0, 5, 10, 11, 12, 20];
    const values = Array.from({length: 12}, (_, index) => 0.1 * (index % 3));
    const series = trailingMeanSeries(
        Array.from({length: 12}, (_, index) => index), values, 10, 2.0
    );
    const unknown = trailingMeanSeries(
        Array.from({length: 12}, (_, index) => index), values, 10
    );
    return {
        start: trailingWindowStart(generations, 5, 10),
        startAll: trailingWindowStart(generations, 2, 100),
        leadingGaps: series.mean.slice(0, 7).every(Number.isNaN),
        firstMean: series.mean[7],
        bandBelow: series.low[11] < series.mean[11],
        bandAbove: series.high[11] > series.mean[11],
        unknownBand: Number.isNaN(unknown.low[11]) && !Number.isNaN(unknown.mean[11]),
        anchors: evidenceWindowAnchors(
            {D: {window: 30}, G_ST: {window: 10}, H_S: {window: 99}}, 40, 10
        ),
    };
})()
"""

_CUMULATIVE = """
(() => {
    const generations = Array.from({length: 20}, (_, index) => 5 * index);
    const values = generations.map((_, index) => 0.1 * (index % 4));
    const series = cumulativeMeanSeries(values, 3);
    const sums = windowSums(values);
    const averaging = {window: 30, anchors: {D: 2}};
    return {
        gaps: series.mean.slice(0, 10).every(Number.isNaN),
        first: series.mean[10],
        lastMatches:
            series.mean[19] === trailingWindowEstimate(sums, 3, 19).mean,
        counts: [series.count[9], series.count[10], series.count[19]],
        anchored: cumulativeStart(generations, "D", averaging),
        burnIn: cumulativeStart(generations, "G_ST", averaging),
        tooShort: cumulativeStart(generations.slice(0, 5), "G_ST", averaging),
        shared: averagingWindowMarker(generations, {D: 4, G_ST: 4}, 12),
        apart: averagingWindowMarker(generations, {D: 6, G_ST: 2, H_S: 6}, 12),
        notYet: averagingWindowMarker(generations, {D: 15}, 12),
        trailingLegend: averagedLegendEntries(
            "trailing_mean", 1356, generations, {D: 4}, {end: 60, starts: []}
        ),
        cumulativeLegend: averagedLegendEntries(
            "cumulative_mean", 30, generations, {D: 6, G_ST: 6}, null
        ),
        mixedLegend: averagedLegendEntries(
            "cumulative_mean", 30, generations, {D: 2, G_ST: 6}, {end: 60, starts: []}
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
    # No autocorrelation time (a live run): the mean is drawn, no band.
    assert settled["unknownBand"] is True
    # Only a window that grew past the convergence window, and fits in
    # the recorded history, is anchored.
    assert settled["anchors"] == {"D": 10}


@pytest.mark.parametrize("formatted_window", ["1,356", "1356"])
def test_the_cumulative_mean_its_start_and_the_window_marker(
    window: webview.Window, drive: Callable[..., Any], formatted_window: str
) -> None:
    """Averaging and legends agree with a controlled number formatter."""
    # Browser locale data differs across platforms; control formatting,
    # not the legend implementation, and restore it before the next test.
    script = (
        "(() => {"
        "const original = Number.prototype.toLocaleString;"
        "Number.prototype.toLocaleString = function () {"
        f"return Number(this) === 1356 ? {json.dumps(formatted_window)} : String(this);"
        "};"
        "try {"
        f"return {_CUMULATIVE.strip().removesuffix(';')};"
        "} finally { Number.prototype.toLocaleString = original; }"
        "})()"
    )
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=script)

    # Averaging from index 3 needs eight points: the first mean is at 10.
    assert settled["gaps"] is True
    assert settled["first"] == pytest.approx(0.1 * sum([3, 0, 1, 2, 3, 0, 1, 2]) / 8)
    assert settled["lastMatches"] is True
    assert settled["counts"] == [0, 8, 17]
    # A grown evidence window's anchor wins; otherwise one window of
    # burn-in (generation 30 is index 6); none before the run passes it.
    assert settled["anchored"] == 2
    assert settled["burnIn"] == 6
    assert settled["tooShort"] is None
    # One shared start: drawn in the accent color (no statistic name).
    assert settled["shared"] == {
        "end": 60,
        "starts": [{"generation": 20, "name": None}],
    }
    # Starts apart: one per distinct start, in order; a start only one
    # statistic has takes its color, one several share the accent.
    assert settled["apart"] == {
        "end": 60,
        "starts": [
            {"generation": 10, "name": "G_ST"},
            {"generation": 30, "name": None},
        ],
    }
    # A window that begins after the shown generation draws no marker.
    assert settled["notYet"] is None
    assert settled["trailingLegend"] == [
        ["swatch-band", f"trailing mean ± 2 SE (last {formatted_window} generations)"],
        ["swatch-window-start", "trailing window start"],
    ]
    assert settled["cumulativeLegend"] == [
        ["swatch-band", "cumulative mean ± 2 SE (from generation 30)"],
    ]
    assert settled["mixedLegend"] == [
        ["swatch-band", "cumulative mean ± 2 SE (from where averaging began)"],
        ["swatch-window-start", "averaging start"],
    ]


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
        tauInt: {{D: {_TAU_INT}}},
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
            tauInt: {{D: {_TAU_INT}}},
            sums: null,
        }}, "D"),
        live: estimateNote({{
            generations,
            histories: {{D: values}},
            comparisons,
            convergence: {{window: 8, tolerance: 1.0}},
            anchors: {{}},
            sums: {{}},
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
    # A live run has no autocorrelation time yet: the mean, without an error bar.
    stats = window_statistics(_VALUES[4:])
    assert settled["live"] == (
        f"mean {stats.mean:.4f} over generations 4{_EN_DASH}11 (8 generations; "
        "standard error known once the run has averaged); predicted 0.2500"
    )
    assert settled["noWindow"] == ""
    assert settled["joined"] == "first — second — description"


@pytest.fixture
def estimable_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings for a fast run long enough to estimate a window mean.

    `fast_scalar_run_settings`' window of 4 is shorter than the eight
    points an estimate needs, so this one uses a window of 8, and a
    tolerance no run meets, so the run goes to its 40-generation cap: long
    enough past the one-window burn-in for a cumulative mean.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "1",
                "max_generations": "40",
                "precision": "1e-06",
            },
        ),
    )
    return _isolate_gui_preferences


def test_a_completed_run_leads_with_its_estimate_and_offers_the_averages(
    estimable_run_settings: Path, window: webview.Window
) -> None:
    """D's tooltip leads with its mean ± SE; each display redraws and persists."""
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

    def choose(display: str) -> str:
        return (
            "(() => {"
            "const select = document.getElementById('run-trajectory-display');"
            f"select.value = '{display}';"
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
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            before = poll_page(
                window,
                completed,
                lambda value: value["state"] == "completed" and value["pending"] == 0,
            )
            chosen_displays = {}
            for display, legend in (
                ("trailing_mean", "trailing mean"),
                ("cumulative_mean", "cumulative mean"),
            ):

                def matches_display(
                    value: Any,
                    expected_legend: str = legend,
                    expected_display: str = display,
                ) -> bool:
                    """Match the legend and saved setting for this selection."""
                    return (
                        expected_legend in value["legend"]
                        and value["saved"] == expected_display
                    )

                window.evaluate_js(choose(display))
                chosen_displays[display] = poll_page(
                    window,
                    chosen,
                    matches_display,
                )
            outcome.put({"before": before, **chosen_displays})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)
    before = settled["before"]
    trailing = settled["trailing_mean"]
    cumulative = settled["cumulative_mean"]

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
    assert "trailing mean ± 2 SE (last 50 generations)" in trailing["legend"]
    assert "trailing window start" in trailing["legend"]
    assert trailing["saved"] == "trailing_mean"
    # The run records where the monitor began averaging (its burn-in), so the
    # legend says so instead of naming a generation of its own.
    assert "cumulative mean ± 2 SE (from where averaging began)" in cumulative["legend"]
    assert cumulative["saved"] == "cumulative_mean"


_EVIDENCE = """
(() => {
    const stats = {
        D: {mean: 0.5, standard_deviation: 0.1, window: 400,
            effective_sample_size: 100, window_start: 100, window_end: 499},
        G_ST: {mean: 0.2, standard_deviation: 0.02, window: 400,
            effective_sample_size: 400, window_start: 100, window_end: 499},
        H_S: {mean: NaN, standard_deviation: 0.1, window: 400,
            effective_sample_size: 40, window_start: 100, window_end: 499},
    };
    const one = evidenceSigmaBand(stats, 1);
    const two = evidenceSigmaBand(stats, 2);
    return {
        tau: [tauIntFor(stats, "D"), tauIntFor(stats, "G_ST"),
              tauIntFor(stats, "missing"), tauIntFor(null, "D"),
              tauIntFor({X: {window: 10, effective_sample_size: 0}}, "X"),
              tauIntFor({X: {window: 10, effective_sample_size: 40}}, "X")],
        one: one,
        twoD: two.band.D,
        names: Object.keys(two.band),
        none: evidenceSigmaBand(null, 2),
        empty: evidenceSigmaBand({H_S: stats.H_S}, 2),
        visible: Object.keys(visibleSigmaBand(two, {D: [1]}).band),
        allVisible: Object.keys(visibleSigmaBand(two, null).band),
        noneVisible: visibleSigmaBand(two, {X: [1]}),
        sigmaNote: (() => {
            completedWindowStatistics = stats;
            return [sigmaNote("D"), sigmaNote("missing")];
        })(),
    };
})()
"""


def test_the_sigma_display_is_built_from_the_evidence_window(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Band, tau_int and the sigma tooltip clause come from the report's window."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_EVIDENCE)

    # tau_int is window / effective sample size, at least one; none without a window.
    assert settled["tau"] == [4.0, 1.0, None, None, None, 1.0]
    assert settled["one"]["multiplier"] == 1
    assert settled["one"]["window"] == 399
    assert settled["twoD"] == pytest.approx(
        {"mean": 0.5, "sigma": 0.1, "lower": 0.3, "upper": 0.7}
    )
    # A statistic whose mean is not finite has no band; no window, no display.
    assert settled["names"] == ["D", "G_ST"]
    assert settled["none"] is None
    assert settled["empty"] is None
    assert settled["visible"] == ["D"]
    assert settled["allVisible"] == ["D", "G_ST"]
    assert settled["noneVisible"] is None
    assert settled["sigmaNote"] == ["\u03c3 0.1000 per generation", ""]


def test_the_band_width_selector_applies_and_is_remembered(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Choosing a width updates the page state and the saved layout."""
    trigger = (
        "(async () => {"
        "window.__bandResult = null;"
        "const select = document.getElementById('run-trajectory-band-width');"
        "select.value = '1';"
        "select.dispatchEvent(new Event('change', {bubbles: true}));"
        + AWAIT_SETTINGS_SAVES
        + "window.__bandResult = {"
        "width: trajectoryBandWidth, "
        "select: select.value, "
        "saved: (await window.pywebview.api.get_run_card_layout()).bandWidth};"
        "})();"
    )
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=trigger,
        read="window.__bandResult",
    )

    assert settled == {"width": 1, "select": "1", "saved": 1}
