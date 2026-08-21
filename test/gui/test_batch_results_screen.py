"""Tests for Screen 4, the batch results screen.

`test_confidence_interval_figure_*` are plain unit tests — no display
needed, collected in the default `pytest` run, since
`_confidence_interval_figure` builds a `Figure` directly from a
hand-built summary mapping. Every other test constructs a real
`BatchResultsScreen` (needs a display, hence the `gui` marker — design
doc §6.2/§6.4) and drives it via `.show()`/`.invoke()`, never
`mainloop()` (design §6.1), against a real three-replicate batch run
through `fim.gui.batch_runner.start_batch_run` — the same
structural-assertion style `test/viz/test_plots.py` and
`test/gui/test_results_screen.py` already use, plus exact-value
comparisons against the batch's own persisted `summary.json`/
`report.json` for the two tests design doc §6.4 names directly.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from fim.gui import batch_runner
from fim.gui.app import Application
from fim.gui.screens.batch_results_screen import (
    BatchResultsScreen,
    BatchResultsView,
    _confidence_interval_figure,
)
from fim.gui.screens.results_screen import ResultsView, format_statistic
from fim.model.params import SimulationParams
from fim.statistics.interval import ConfidenceInterval


@pytest.fixture
def batch_params(tiny_params: SimulationParams) -> SimulationParams:
    """A small, fast three-replicate batch configuration."""
    return replace(tiny_params, n_replicates=3)


def _interval(mean: float, sample_count: int = 3) -> ConfidenceInterval:
    """Build one minimal, valid `ConfidenceInterval` for a hand-built summary."""
    return {
        "mean": mean,
        "half_width": 0.1,
        "low": mean - 0.1,
        "high": mean + 0.1,
        "sample_count": sample_count,
        "confidence": 0.95,
    }


def test_confidence_interval_figure_plots_one_point_per_present_statistic() -> None:
    """Every statistic present in the summary gets its own y-axis tick and point."""
    summary = {"D": _interval(0.5), "G_ST": _interval(0.1)}

    figure = _confidence_interval_figure(summary)

    axes = figure.axes[0]
    assert [label.get_text() for label in axes.get_yticklabels()] == [
        "D",
        "G_ST",
        "E_ST",
        "K_ST",
        "H_S",
        "H_T",
        "H_ST",
    ]
    assert len(axes.containers) == 2


def test_confidence_interval_figure_annotates_an_omitted_statistic() -> None:
    """A statistic missing from the summary is shown as omitted, not silently blank."""
    summary = {"D": _interval(0.5)}

    figure = _confidence_interval_figure(summary)

    axes = figure.axes[0]
    assert len(axes.containers) == 1
    texts = [text.get_text() for text in axes.texts]
    assert texts.count("omitted") == 6


@pytest.mark.gui
def test_batch_results_screen_matches_summary_json(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """The rendered table and CI panel match the batch's own persisted artifacts.

    Design doc's own named test (§6.4, G10, §4.4).
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]
    persisted_summary = json.loads((output_directory / "summary.json").read_text())

    screen = BatchResultsScreen(root)
    view = BatchResultsView.from_results(results)
    screen.show(view, output_directory)

    assert set(view.summary) == set(persisted_summary)
    for name, interval in persisted_summary.items():
        assert view.summary[name]["mean"] == pytest.approx(interval["mean"])
        assert view.summary[name]["sample_count"] == interval["sample_count"]
    assert screen._header_label["text"] == "Batch: 3 replicate(s)"

    for index, result in enumerate(results, start=1):
        report = json.loads(
            (output_directory / f"replicate-{index:03}" / "report.json").read_text()
        )
        rendered = screen._table.item(result.run_id, "values")
        assert rendered[0] == str(report["reason"]).capitalize()
        assert rendered[1] == str(report["generation"])
        for offset, name in enumerate(
            ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST")
        ):
            assert rendered[2 + offset] == format_statistic(report[name])


@pytest.mark.gui
def test_batch_results_screen_open_replicate_reaches_results_screen(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """Selecting a row and choosing "Open replicate" hands over a real view.

    Design doc's own named test (§6.4, §4.4). `fim.gui.app` wires this
    screen's `on_open_replicate` to raise Screen 3; this confirms the
    callback carries exactly what that screen needs — a `ResultsView`
    built from the selected replicate's own report and trajectory, and
    that replicate's own output subdirectory.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]

    received: list[tuple[ResultsView, Path]] = []
    screen = BatchResultsScreen(
        root,
        on_open_replicate=lambda view, directory: received.append((view, directory)),
    )
    screen.show(BatchResultsView.from_results(results), output_directory)
    second_replicate = results[1]
    screen._table.selection_set(second_replicate.run_id)

    screen._open_replicate_button.invoke()

    assert len(received) == 1
    view, directory = received[0]
    assert directory == output_directory / "replicate-002"
    assert view.run_id == second_replicate.run_id
    assert view.report == second_replicate.report


@pytest.mark.gui
def test_batch_results_screen_open_replicate_is_a_no_op_with_no_selection(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """ "Open replicate" with nothing selected in the table calls nothing."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]

    received: list[None] = []
    screen = BatchResultsScreen(
        root, on_open_replicate=lambda _view, _directory: received.append(None)
    )
    screen.show(BatchResultsView.from_results(results), output_directory)

    screen._open_replicate_button.invoke()

    assert received == []


@pytest.mark.gui
def test_batch_results_screen_open_folder_invokes_the_injected_opener(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """ "Open batch folder" calls the injected `open_folder`, never a real one."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]

    opened: list[Path] = []
    screen = BatchResultsScreen(root, open_folder=opened.append)
    screen.show(BatchResultsView.from_results(results), output_directory)

    screen._on_open_folder_clicked()

    assert opened == [output_directory]


@pytest.mark.gui
def test_batch_results_screen_export_summary_copies_the_file(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """ "Export summary.json" copies the persisted file byte-for-byte."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]
    destination = tmp_path / "exported-summary.json"

    screen = BatchResultsScreen(root, export_dialog=lambda: str(destination))
    screen.show(BatchResultsView.from_results(results), output_directory)

    screen._on_export_summary_clicked()

    assert destination.read_text() == (output_directory / "summary.json").read_text()


@pytest.mark.gui
def test_batch_results_screen_export_summary_is_a_no_op_when_cancelled(
    root: Application,
    batch_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """A cancelled export dialog (empty string) writes nothing."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)
    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    results = done[1]

    screen = BatchResultsScreen(root, export_dialog=lambda: "")
    screen.show(BatchResultsView.from_results(results), output_directory)

    screen._on_export_summary_clicked()

    assert not (tmp_path / "exported-summary.json").exists()


def _drain(
    message_queue: queue.Queue[batch_runner.BatchMessage],
) -> list[batch_runner.BatchMessage]:
    """Return every message currently queued, in order."""
    messages: list[batch_runner.BatchMessage] = []
    while True:
        try:
            messages.append(message_queue.get_nowait())
        except queue.Empty:
            return messages
