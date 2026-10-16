"""The trajectory panel averages over the run's evidence window.

The convergence monitor judges a run over its evidence window, from the
burn-in to the last generation, and the report records the mean and the
standard error it found there (`window_statistics`). The panel's trailing
mean, cumulative mean and tooltip estimate must reproduce those numbers,
so a number on screen can always be traced to the report. These tests use
the shipped Wright-Takahata example, whose saved history and report are
fixed files, so they are pure functions of the commit.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import _convergence_reference_payload
from fim.model import SimulationParams

_EXAMPLE = (
    Path(__file__).parents[2] / "doc/examples/wright-takahata-finite-deme-correction"
)
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_WATCHED = ("G_ST", "D")


def test_a_live_run_averages_over_the_first_check_until_it_ends(
    tiny_params: SimulationParams,
) -> None:
    """With no generation count, `window` is the first-check span."""
    payload = _convergence_reference_payload(tiny_params)

    assert payload["burnIn"] == tiny_params.convergence_burn_in
    assert payload["tolerance"] == tiny_params.precision
    assert payload["window"] >= tiny_params.expert.first_check_minimum


def test_a_finished_run_averages_over_its_evidence_window(
    tiny_params: SimulationParams,
) -> None:
    """The window is the generations recorded after the burn-in."""
    burn_in = 30
    params = replace(tiny_params, convergence_burn_in=burn_in)

    payload = _convergence_reference_payload(params, burn_in + 1_000)

    assert payload["window"] == 1_000
    assert payload["burnIn"] == burn_in


def test_a_run_that_ended_inside_its_burn_in_keeps_the_first_check_span(
    tiny_params: SimulationParams,
) -> None:
    """With nothing recorded past the burn-in there is no evidence window."""
    params = replace(tiny_params, convergence_burn_in=500)

    live = _convergence_reference_payload(params)
    cut_short = _convergence_reference_payload(params, 500)

    assert cut_short["window"] == live["window"]


def _history(names: tuple[str, ...]) -> dict[str, list[float]]:
    """Read the example's saved per-generation values for `names`."""
    values: dict[str, list[float]] = {name: [] for name in names}
    with gzip.open(_EXAMPLE / "convergence.jsonl.gz.part-0001", "rt") as rows:
        for row in rows:
            record = json.loads(row)
            for name in names:
                values[name].append(record[name])
    return values


@pytest.mark.gui
def test_the_panel_reproduces_the_reports_evidence_window_estimates(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Every averaged number the panel shows equals the report's.

    For each watched statistic: the tooltip estimate, the last point of the
    trailing mean and of the cumulative mean, and where the cumulative mean
    starts, all against `report.json`.
    """
    report = json.loads((_EXAMPLE / "report.json").read_text())
    manifest = json.loads((_EXAMPLE / "manifest.json").read_text())
    burn_in = manifest["parameters"]["convergence_burn_in"]
    recorded = manifest["convergence"]["generation_count"]
    histories = _history(_WATCHED)
    script = f"""
    (() => {{
        const histories = {json.dumps(histories)};
        const windowStatistics = {json.dumps(report["window_statistics"])};
        const generations = Array.from(
            {{length: {recorded}}}, (_, index) => index);
        const convergence = {{
            window: {recorded - burn_in}, burnIn: {burn_in}, tolerance: 0.02}};
        const averaging = {{
            window: convergence.window, burnIn: convergence.burnIn,
            anchors: {{}}}};
        const result = {{}};
        for (const name of {json.dumps(list(_WATCHED))}) {{
            const tauInt = tauIntFor(windowStatistics, name);
            const source = {{
                generations, histories, comparisons: {{}}, convergence,
                anchors: {{}}, sums: {{}},
                tauInt: {{[name]: tauInt}},
            }};
            const trailing = trailingMeanSeries(
                generations, histories[name], convergence.window, tauInt);
            const start = cumulativeStart(generations, name, averaging);
            const cumulative = cumulativeMeanSeries(
                histories[name], start, tauInt);
            result[name] = {{
                note: estimateNote(source, name),
                trailing: trailing.mean[trailing.mean.length - 1],
                cumulative: cumulative.mean[cumulative.mean.length - 1],
                cumulativeStart: start,
            }};
        }}
        return result;
    }})()
    """

    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=script)

    for name in _WATCHED:
        expected = report["window_statistics"][name]
        shown = settled[name]
        assert shown["cumulativeStart"] == burn_in
        assert shown["trailing"] == pytest.approx(expected["mean"], abs=1e-9)
        assert shown["cumulative"] == pytest.approx(expected["mean"], abs=1e-9)
        # Four decimals, as the tooltip shows them.
        assert f"mean {expected['mean']:.4f}" in shown["note"]
        assert f"± {expected['standard_error']:.4f}" in shown["note"]
        assert f"{burn_in:,}\u2013{recorded - 1:,}" in shown["note"]
