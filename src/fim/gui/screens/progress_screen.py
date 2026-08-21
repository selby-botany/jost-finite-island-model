"""Screen 2 — running (design doc §4.2): live progress for one background
scalar simulation, with an always-effective Cancel.

Backed entirely by `fim.gui.runner.start_run` and the `RunMessage` queue
it posts to (design §3.4). The progress bar is determinate against
`max_generations` because that value is always known up front — a run
that instead converges early simply ends the bar short of 100% rather
than reaching it, which is a normal, non-failure outcome (design §4.2).

Scalar-only: a single "generation N / max_generations" bar, one Cancel
button. Milestone G4 (§7.6) adds the outer replicate-count bar and
relabels Cancel to "Cancel batch" for `n_replicates > 1`; this screen's
scalar shape does not change underneath that addition.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from fim.engine import RunResult, deterministic_run_id
from fim.gui import runner
from fim.gui.runner import RunMessage
from fim.model.params import SimulationParams

_SECONDS_PER_MINUTE = 60


class ProgressScreen(ttk.Frame):
    """Screen 2: display one background scalar run's progress, with Cancel."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_done: Callable[[RunResult], None] | None = None,
        on_cancelled: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        start_run: Callable[
            [SimulationParams, Path, queue.Queue[RunMessage], threading.Event],
            threading.Thread,
        ] = runner.start_run,
        poll_interval_ms: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build the progress display; no run is started until `.start()`.

        Args:
            parent: The Tk container this screen is gridded into.
            on_done: Called with the completed `RunResult` once the
                worker posts `("done", result)`. Defaults to a no-op —
                Milestone G3 gives this a real results-screen navigator.
            on_cancelled: Called with the generation the run was
                cancelled at, once the worker posts
                `("cancelled", generation)`. Defaults to a no-op.
            on_error: Called with the message text once the worker
                posts `("error", message)` — an unexpected engine error,
                or a pre-existing output directory the guard in
                `runner.start_run` rejected before any thread started.
                Defaults to a no-op.
            start_run: Builds and starts the background worker thread.
                Defaults to the real `runner.start_run`; injectable so
                tests never spawn a real thread.
            poll_interval_ms: How often the main thread drains the
                message queue (design §3.4's `root.after(100, poll)`).
            clock: Injectable wall clock for the elapsed-time label.
        """
        super().__init__(parent)
        self._on_done = on_done if on_done is not None else (lambda _result: None)
        self._on_cancelled = (
            on_cancelled if on_cancelled is not None else (lambda _generation: None)
        )
        self._on_error = on_error if on_error is not None else (lambda _message: None)
        self._start_run = start_run
        self._poll_interval_ms = poll_interval_ms
        self._clock = clock

        self._cancel_event = threading.Event()
        self._queue: queue.Queue[RunMessage] = queue.Queue()
        self._max_generations = 1
        self._started_at: float | None = None
        self._after_id: str | None = None

        self._run_label = ttk.Label(self)
        self._run_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self._generation_label = ttk.Label(self)
        self._generation_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self._progress_bar = ttk.Progressbar(self, mode="determinate", length=300)
        self._progress_bar.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=4
        )
        self._elapsed_label = ttk.Label(self)
        self._elapsed_label.grid(row=3, column=0, sticky="w", padx=4)
        self._cancel_button = ttk.Button(
            self, text="Cancel", command=self._on_cancel_clicked
        )
        self._cancel_button.grid(row=3, column=1, sticky="e", padx=4)

    def start(self, params: SimulationParams, output_directory: Path) -> None:
        """Start a real background run and begin polling for progress.

        Args:
            params: Already-validated parameters — Screen 1 never hands
                this an unvalidated payload (design §3.6).
            output_directory: The run's target artifact directory.
        """
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        self._cancel_event = threading.Event()
        self._queue = queue.Queue()
        self._max_generations = params.max_generations
        self._started_at = self._clock()

        self._run_label["text"] = f"Running {deterministic_run_id(params)}"
        self._set_generation(0)
        self._progress_bar["maximum"] = params.max_generations
        self._elapsed_label["text"] = "Elapsed: 00:00.0"

        try:
            self._start_run(params, output_directory, self._queue, self._cancel_event)
        except FileExistsError as error:
            self._on_error(str(error))
            return
        self._poll()

    def _on_cancel_clicked(self) -> None:
        """Set the shared cancel event; always effective within one generation."""
        self._cancel_event.set()

    def _handle_message(self, message: RunMessage) -> bool:
        """Apply one queued message; return whether it ended the run.

        A `match` on the message's literal tag (not an `if`-chain on a
        separately bound `kind` variable) is what lets mypy narrow each
        branch's payload type — `message[1]`'s type otherwise stays the
        full `int | RunResult | str` union throughout the function body.
        """
        match message:
            case ("progress", generation):
                self._set_generation(generation)
                return False
            case ("done", result):
                self._set_generation(result.report["generation"])
                self._on_done(result)
                return True
            case ("cancelled", generation):
                self._on_cancelled(generation)
                return True
            case ("error", text):
                self._on_error(text)
                return True

    def _poll(self) -> None:
        """Drain every queued message, then reschedule unless one was terminal."""
        try:
            while True:
                if self._handle_message(self._queue.get_nowait()):
                    return
        except queue.Empty:
            pass
        self._update_elapsed_label()
        self._after_id = self.after(self._poll_interval_ms, self._poll)

    def _set_generation(self, generation: int) -> None:
        """Update the generation label and progress bar together."""
        self._generation_label["text"] = (
            f"Generation {generation} / {self._max_generations}"
        )
        self._progress_bar["value"] = min(generation, self._max_generations)

    def _update_elapsed_label(self) -> None:
        """Refresh the elapsed-time label from the injected clock.

        Not independently tested (design §6.1 does not name it): it is
        cosmetic display text derived from wall-clock time, not a
        correctness-bearing computation the way `ProgressThrottle` is.
        """
        if self._started_at is None:
            return
        elapsed = self._clock() - self._started_at
        minutes, seconds = divmod(elapsed, _SECONDS_PER_MINUTE)
        self._elapsed_label["text"] = f"Elapsed: {int(minutes):02}:{seconds:04.1f}"
