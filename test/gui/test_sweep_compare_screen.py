"""Headless functional tests for comparing sweeps (`sweep-compare.js`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import webview

from fim.persistence import groups
from fim.sweep import expand_axis

from .test_sweep_results_screen import _set_select, _study_with_results
from .test_sweep_screen import Poll, _drive

pytestmark = pytest.mark.gui

_M = expand_axis("m", [0.05, 0.1, 0.2])

_HOME_STATE = """({
    studies: allStudies.map((study) => study.studyId),
    compare: compareSelectedButton.textContent.trim(),
    compareDisabled: compareSelectedButton.disabled,
})"""

_COMPARE_STATE = """({
    ready: window.__fimSweepCompareReady,
    visible: !document.getElementById('screen-sweep-compare').hidden,
    title: document.getElementById('sweep-compare-title').textContent,
    curves: sweepCompareLastSpec === null ? null : sweepCompareLastSpec.curves.map(
        (curve) => ({label: curve.label, color: curve.color, shape: curve.shape,
                     points: curve.marks.length})),
    notes: Array.from(document.querySelectorAll('#sweep-compare-notes li'))
        .map((item) => item.textContent),
    differenceShown:
        !document.getElementById('sweep-compare-difference-panel').hidden,
    differenceTitle: document.getElementById('sweep-compare-difference-title')
        .textContent,
    header: Array.from(document.querySelectorAll('#sweep-compare-head th'))
        .map((cell) => cell.textContent),
    rows: document.querySelectorAll('#sweep-compare-body tr').length,
    inked: (() => {
        const canvas = document.getElementById('sweep-compare-canvas');
        const data = canvas.getContext('2d')
            .getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 3; i < data.length; i += 4) { if (data[i] !== 0) { return true; } }
        return false;
    })(),
    differenceInked: (() => {
        const canvas = document.getElementById('sweep-compare-difference-canvas');
        const data = canvas.getContext('2d')
            .getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 3; i < data.length; i += 4) { if (data[i] !== 0) { return true; } }
        return false;
    })(),
    statistic: document.getElementById('sweep-compare-statistic').value,
    held: document.querySelectorAll('#sweep-compare-held select').length,
})"""


def _load_home(window: webview.Window, poll_until: Poll, *study_ids: str) -> None:
    """Refresh Home and wait until it lists the given Studies."""
    poll_until("document.readyState", lambda state: state == "complete")
    window.evaluate_js("refreshRecentRuns();")
    poll_until(
        _HOME_STATE,
        lambda state: all(study_id in state["studies"] for study_id in study_ids),
    )


def _select(window: webview.Window, *study_ids: str) -> None:
    """Select Home Studies as the checkboxes would."""
    for study_id in study_ids:
        window.evaluate_js(f"selectedStudyIds.add('{study_id}');")
    window.evaluate_js("updateSelectionToolbar();")


def _compare_visible(state: dict[str, Any]) -> bool:
    return bool(state["ready"] is True and state["visible"] is True)


def test_selected_sweeps_open_the_compare_screen_with_a_difference_for_two(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    small = _study_with_results("Small", (_M,), replicates=3)
    large = _study_with_results("Large", (_M,), replicates=3)
    plain = groups.create_study("By hand").study_id

    def steps(poll_until: Poll) -> Any:
        _load_home(window, poll_until, small, large, plain)
        _select(window, plain, small)
        before = window.evaluate_js(_HOME_STATE)
        _select(window, large)
        after = poll_until(_HOME_STATE, lambda s: s["compareDisabled"] is False)
        window.evaluate_js(
            "document.getElementById('open-run-compare-selected-button').click();"
        )
        compared = poll_until(_COMPARE_STATE, _compare_visible)
        return before, after, compared

    before, after, compared = _drive(window, steps)

    # A plain Study is not a sweep, so one sweep alone is not enough.
    assert before["compareDisabled"] is True
    assert after["compare"] == "Compare selected sweeps (2)…"
    assert compared["title"] == "Compare Small and Large"
    assert [curve["label"] for curve in compared["curves"]] == ["Small", "Large"]
    assert [curve["points"] for curve in compared["curves"]] == [3, 3]
    assert len({curve["color"] for curve in compared["curves"]}) == 2
    assert len({curve["shape"] for curve in compared["curves"]}) == 2
    assert compared["differenceShown"] is True
    assert compared["differenceTitle"].startswith("Difference: Small minus Large")
    assert compared["inked"] is True and compared["differenceInked"] is True
    assert compared["header"][0] == "m"
    assert "difference" in compared["header"]
    assert compared["rows"] == 3
    assert any(note.startswith("Seed policy:") for note in compared["notes"])
    assert any("same seeds" in note for note in compared["notes"])


def test_three_sweeps_overlay_with_no_difference_panel(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    ids = [_study_with_results(name, (_M,)) for name in ("One", "Two", "Three")]

    def steps(poll_until: Poll) -> Any:
        _load_home(window, poll_until, *ids)
        _select(window, *ids)
        poll_until(_HOME_STATE, lambda s: s["compareDisabled"] is False)
        window.evaluate_js(
            "document.getElementById('open-run-compare-selected-button').click();"
        )
        return poll_until(_COMPARE_STATE, _compare_visible)

    compared = _drive(window, steps)

    assert [curve["label"] for curve in compared["curves"]] == ["One", "Two", "Three"]
    assert compared["differenceShown"] is False
    assert "difference" not in compared["header"]


def test_an_experiment_row_compares_the_sweeps_inside_it(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    first = _study_with_results("First", (_M,))
    second = _study_with_results("Second", (_M,))
    lone = _study_with_results("Lone", (_M,))
    experiment = groups.create_experiment("Pair")
    groups.add_study_to_experiment(experiment.experiment_id, first)
    groups.add_study_to_experiment(experiment.experiment_id, second)
    alone = groups.create_experiment("Alone")
    groups.add_study_to_experiment(alone.experiment_id, lone)
    buttons = (
        "Array.from(document.querySelectorAll('.open-run-group-actions button'))"
        ".filter((button) => button.textContent.trim() === 'Compare sweeps…')"
        ".map((button) => button.disabled)"
    )

    def steps(poll_until: Poll) -> Any:
        _load_home(window, poll_until, first, second, lone)
        states = poll_until(buttons, lambda found: len(found) == 1)
        window.evaluate_js(
            "Array.from(document.querySelectorAll('.open-run-group-actions button'))"
            ".filter((button) => button.textContent.trim() === 'Compare sweeps…'"
            " && !button.disabled)[0].click();"
        )
        return states, poll_until(_COMPARE_STATE, _compare_visible)

    states, compared = _drive(window, steps)

    # Only the experiment with two sweeps offers it; the lone sweep has none.
    assert states == [False]
    assert compared["title"] == "Compare First and Second"


def test_sweeps_that_cannot_be_compared_say_why_on_home(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    on_m = _study_with_results("On m", (_M,))
    on_d = _study_with_results("On d", (expand_axis("d", [2, 3]),))

    def steps(poll_until: Poll) -> Any:
        _load_home(window, poll_until, on_m, on_d)
        _select(window, on_m, on_d)
        poll_until(_HOME_STATE, lambda s: s["compareDisabled"] is False)
        window.evaluate_js(
            "document.getElementById('open-run-compare-selected-button').click();"
        )
        return poll_until(
            "({banner: document.getElementById('open-run-banner').textContent,"
            " shown: !document.getElementById('screen-sweep-compare').hidden})",
            lambda s: s["banner"] != "",
        )

    state = _drive(window, steps)

    assert "share no axis key" in state["banner"]
    assert state["shown"] is False


def test_the_compare_controls_redraw_and_theory_adds_a_curve_per_sweep(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    ids = [_study_with_results(name, (_M,)) for name in ("A", "B")]

    def steps(poll_until: Poll) -> Any:
        _load_home(window, poll_until, *ids)
        window.evaluate_js(f"window.fim.showSweepCompare({ids!r});")
        poll_until(_COMPARE_STATE, _compare_visible)
        _set_select(window, "sweep-compare-statistic", "G_ST")
        other = poll_until(
            _COMPARE_STATE, lambda s: s["ready"] is True and s["statistic"] == "G_ST"
        )
        window.evaluate_js(
            "const box = document.getElementById('sweep-compare-theory');"
            "box.checked = true;"
            "box.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        poll_until(_COMPARE_STATE, lambda s: s["ready"] is True)
        theory = window.evaluate_js(
            "sweepCompareLastSpec.curves.map((curve) => curve.theory.length)"
        )
        return other, theory

    other, theory = _drive(window, steps)

    assert other["inked"] is True
    assert len(theory) == 2 and all(length > 0 for length in theory)
