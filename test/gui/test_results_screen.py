"""Tests for Screen 3, the results screen.

`test_animate_is_enabled_*` is a plain unit test — no display needed,
collected in the default `pytest` run. Every other test constructs a
real `ResultsScreen` (needs a display, hence the `gui` marker — design
doc §6.2/§6.4) and drives it via `.show()`/`.invoke()`, never
`mainloop()` (design §6.1). `.show()` is exercised against a real
`RunResult` from `tiny_params` run through the real `fim.engine.fim` —
the same structural-assertion style `test/viz/test_plots.py` already
uses (axis labels, not a pixel diff) — rather than a hand-constructed
fake.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from matplotlib import pyplot as plt

from fim.engine import RunResult, fim
from fim.gui.app import Application
from fim.gui.screens.results_screen import (
    ResultsScreen,
    ResultsView,
    _animate_is_enabled,
)
from fim.model.params import SimulationParams


@pytest.fixture
def root() -> Iterator[Application]:
    """Build and tear down one real Tk root per test."""
    application = Application()
    try:
        yield application
    finally:
        application.destroy()


@pytest.fixture
def completed(tiny_params: SimulationParams) -> RunResult:
    """Run `tiny_params` to completion for a real `RunResult` fixture."""
    result = fim(
        tiny_params.N, tiny_params.m, tiny_params.mu, tiny_params.d, params=tiny_params
    )
    assert isinstance(result, RunResult)
    return result


def test_animate_is_enabled_only_for_more_than_one_generation() -> None:
    """The "Animate" enablement predicate is a plain, testable comparison.

    `generation_count` can never actually be 1 for a real validated run
    (see `_animate_is_enabled`'s own docstring), but the predicate is
    still checked directly against every boundary value.
    """
    assert _animate_is_enabled(0) is False
    assert _animate_is_enabled(1) is False
    assert _animate_is_enabled(2) is True
    assert _animate_is_enabled(1518) is True


@pytest.mark.gui
def test_results_screen_shows_run_id_and_outcome(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """The run ID and reason/generation line render from the real report."""
    screen = ResultsScreen(root)

    screen.show(ResultsView.from_run_result(completed), tmp_path)

    assert screen._run_label["text"] == completed.run_id
    report = completed.report
    assert screen._outcome_label["text"] == (
        f"{report['reason'].capitalize()}: generation {report['generation']}"
    )


@pytest.mark.gui
def test_results_screen_shows_all_six_named_statistics(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """Every one of the mock's six statistics is rendered, `H_ST` excluded."""
    screen = ResultsScreen(root)

    screen.show(ResultsView.from_run_result(completed), tmp_path)

    assert set(screen._statistic_labels) == {
        "D",
        "G_ST",
        "E_ST",
        "K_ST",
        "H_S",
        "H_T",
    }
    for name in screen._statistic_labels:
        assert screen._statistic_labels[name]["text"].startswith(f"{name:<9}=")


@pytest.mark.gui
def test_results_screen_embeds_a_figure_with_expected_axes(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """The embedded canvas holds a real, correctly labeled scatter figure.

    Design doc's own named test (§6.4): structural assertions (axis
    labels, point counts), not a pixel diff — `tiny_params` is `d=2`,
    so the direct pairwise scatter names its axes "Deme 1"/"Deme 2".
    """
    screen = ResultsScreen(root)

    screen.show(ResultsView.from_run_result(completed), tmp_path)

    assert screen._canvas is not None
    figure = screen._canvas.figure
    assert len(figure.axes) == 1
    assert figure.axes[0].get_xlabel() == "Deme 1"
    assert figure.axes[0].get_ylabel() == "Deme 2"


@pytest.mark.gui
def test_results_screen_enables_animate_for_a_multi_generation_run(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """A run with more than one persisted generation can be animated."""
    assert completed.manifest.generation_count > 1
    screen = ResultsScreen(root)

    screen.show(ResultsView.from_run_result(completed), tmp_path)

    assert "disabled" not in screen._animate_button.state()


@pytest.mark.gui
def test_results_screen_new_run_invokes_the_callback(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """ "New run" calls `on_new_run` with no arguments."""
    calls: list[None] = []
    screen = ResultsScreen(root, on_new_run=lambda: calls.append(None))
    screen.show(ResultsView.from_run_result(completed), tmp_path)

    screen._on_new_run_clicked()

    assert calls == [None]


@pytest.mark.gui
def test_results_screen_animate_invokes_the_callback_with_result_and_directory(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """ "Animate" hands `on_animate` the shown view and its output directory."""
    received: list[tuple[ResultsView, Path]] = []
    view = ResultsView.from_run_result(completed)
    screen = ResultsScreen(
        root,
        on_animate=lambda shown_view, directory: received.append(
            (shown_view, directory)
        ),
    )
    screen.show(view, tmp_path)

    screen._animate_button.invoke()

    assert received == [(view, tmp_path)]


@pytest.mark.gui
def test_results_screen_open_folder_invokes_the_injected_opener(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """ "Open output folder" calls the injected `open_folder`, never a real one."""
    opened: list[Path] = []
    screen = ResultsScreen(root, open_folder=opened.append)
    screen.show(ResultsView.from_run_result(completed), tmp_path)

    screen._on_open_folder_clicked()

    assert opened == [tmp_path]


@pytest.mark.gui
def test_results_screen_show_does_not_leak_a_figure_across_repeated_runs(
    root: Application,
    tiny_params: SimulationParams,
    tmp_path: Path,
) -> None:
    """`.show()` closes the previously embedded figure before building a new one.

    Regression test for design §3.5's `plt.close` care item: without
    it, `pyplot`'s global figure registry (`plt.get_fignums()`) grows
    by one every time this screen shows another run, for the lifetime
    of a long GUI session.
    """
    screen = ResultsScreen(root)
    baseline = len(plt.get_fignums())

    for seed in (1, 2, 3):
        result = fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=replace(tiny_params, seed=seed),
        )
        assert isinstance(result, RunResult)
        screen.show(ResultsView.from_run_result(result), tmp_path)
        assert len(plt.get_fignums()) == baseline + 1


@pytest.mark.gui
def test_results_screen_new_run_closes_the_embedded_figure(
    root: Application,
    completed: RunResult,
    tmp_path: Path,
) -> None:
    """ "New run" (navigating away) closes the currently embedded figure too."""
    screen = ResultsScreen(root)
    baseline = len(plt.get_fignums())
    screen.show(ResultsView.from_run_result(completed), tmp_path)
    assert len(plt.get_fignums()) == baseline + 1

    screen._on_new_run_clicked()

    assert len(plt.get_fignums()) == baseline
