"""Headless functional tests for Screen 2, the running/progress screen.

Every test constructs a real `ProgressScreen` (needs a display, hence the
`gui` marker — design doc §6.2/§6.4) and drives it synchronously by
calling `._handle_message()` directly or invoking widgets, never
`mainloop()` (design §6.1). `start_run` is always injected with a fake so
no test in this file spawns a real thread or touches the filesystem —
`fim.gui.runner`'s own real background-thread behavior is covered by
`test/gui/test_runner.py`.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from fim.engine import RunResult, fim
from fim.gui.app import Application
from fim.gui.runner import RunMessage
from fim.gui.screens.progress_screen import ProgressScreen
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams

pytestmark = pytest.mark.gui

_PARAMS = SimulationParams(
    N=20, m=0.1, mu=0.01, d=2, seed=1, loci=(LocusSpec(1, 200),), max_generations=100
)


@pytest.fixture
def root() -> Iterator[Application]:
    """Build and tear down one real Tk root per test."""
    application = Application()
    try:
        yield application
    finally:
        application.destroy()


def _noop_start_run(
    params: SimulationParams,
    output_directory: Path,
    message_queue: queue.Queue[RunMessage],
    cancel_event: threading.Event,
) -> threading.Thread:
    """A `start_run` fake that spawns no thread and posts no messages."""
    return threading.Thread(target=lambda: None)


def test_progress_screen_cancel_button_sets_cancel_event(root: Application) -> None:
    """`.invoke()` on Cancel sets the shared event; no real simulation runs.

    Design doc's own named test (§6.4).
    """
    screen = ProgressScreen(root, start_run=_noop_start_run)
    screen.start(_PARAMS, Path("/nonexistent/does-not-matter"))

    screen._cancel_button.invoke()

    assert screen._cancel_event.is_set()


def test_progress_screen_shows_generation_and_progress_bar(root: Application) -> None:
    """A "progress" message updates the generation label and progress bar."""
    screen = ProgressScreen(root, start_run=_noop_start_run)
    screen.start(_PARAMS, Path("/nonexistent"))

    screen._handle_message(("progress", 42))

    assert screen._generation_label["text"] == "Generation 42 / 100"
    assert screen._progress_bar["value"] == 42


def test_progress_screen_done_message_invokes_on_done(
    root: Application,
    tiny_params: SimulationParams,
) -> None:
    """A "done" message updates the generation label and calls `on_done`."""
    received: list[RunResult] = []
    screen = ProgressScreen(root, on_done=received.append, start_run=_noop_start_run)
    screen.start(tiny_params, Path("/nonexistent"))
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
    )
    assert isinstance(result, RunResult)

    terminal = screen._handle_message(("done", result))

    assert terminal is True
    assert received == [result]
    assert (
        screen._generation_label["text"]
        == f"Generation {result.report['generation']} / {tiny_params.max_generations}"
    )


def test_progress_screen_cancelled_message_invokes_on_cancelled(
    root: Application,
) -> None:
    """A "cancelled" message calls `on_cancelled` with the stopping generation."""
    received: list[int] = []
    screen = ProgressScreen(
        root, on_cancelled=received.append, start_run=_noop_start_run
    )
    screen.start(_PARAMS, Path("/nonexistent"))

    terminal = screen._handle_message(("cancelled", 7))

    assert terminal is True
    assert received == [7]


def test_progress_screen_error_message_invokes_on_error(root: Application) -> None:
    """An "error" message calls `on_error` with the message text."""
    received: list[str] = []
    screen = ProgressScreen(root, on_error=received.append, start_run=_noop_start_run)
    screen.start(_PARAMS, Path("/nonexistent"))

    terminal = screen._handle_message(("error", "disk is full"))

    assert terminal is True
    assert received == ["disk is full"]


def test_progress_screen_reports_a_pre_existing_directory_as_an_error(
    root: Application,
    tmp_path: Path,
) -> None:
    """`start()` routes a synchronous `FileExistsError` guard to `on_error`.

    `runner.start_run`'s pre-existing-output-directory guard raises
    before any thread starts (§7.4's runner commit); this confirms the
    screen surfaces that failure the same way it surfaces a worker-
    posted `("error", ...)` message, rather than letting the exception
    propagate out of a button click.
    """
    received: list[str] = []

    def failing_start_run(
        params: SimulationParams,
        output_directory: Path,
        message_queue: queue.Queue[RunMessage],
        cancel_event: threading.Event,
    ) -> threading.Thread:
        raise FileExistsError(f"output directory already exists: {output_directory}")

    screen = ProgressScreen(root, on_error=received.append, start_run=failing_start_run)

    screen.start(_PARAMS, tmp_path / "existing")

    assert len(received) == 1
    assert "already exists" in received[0]
