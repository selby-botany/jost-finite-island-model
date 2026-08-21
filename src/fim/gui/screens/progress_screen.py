"""Screen 2 — running (design doc §4.2): live progress for one background
simulation, scalar or batch, with an always-effective Cancel.

Backed by `fim.gui.runner.start_run` (scalar) and
`fim.gui.batch_runner.start_batch_run` (batch), and the `RunMessage`/
`BatchMessage` queues they post to (design §3.4). Every progress bar is
determinate against a value known up front — `max_generations` for the
inner bar, `n_replicates` for the batch's outer bar — because a run or
replicate that instead converges early simply ends its bar short of
100% rather than reaching it, a normal, non-failure outcome (design
§4.2).

Scalar mode (`.start()`) shows a single "generation N / max_generations"
bar and a "Cancel" button. Batch mode (`.start_batch()`) adds an outer
"replicate N / n_replicates" bar above it and relabels the button
"Cancel batch" — cancellation stops the whole batch, never one
replicate, since there is no partial-batch save point (design §4.0 #5,
#6). Both modes share one screen and one `Cancel`/`Cancel batch`
button; only one of the two runs at a time.
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
from fim.gui import batch_runner, runner
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage
from fim.model.params import SimulationParams

_SECONDS_PER_MINUTE = 60


class ProgressScreen(ttk.Frame):
    """Screen 2: display one background run's (scalar or batch) progress."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_done: Callable[[RunResult], None] | None = None,
        on_cancelled: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_batch_done: Callable[[tuple[RunResult, ...]], None] | None = None,
        on_batch_cancelled: Callable[[int, int], None] | None = None,
        start_run: Callable[
            [SimulationParams, Path, queue.Queue[RunMessage], threading.Event],
            threading.Thread,
        ] = runner.start_run,
        start_batch_run: Callable[
            [SimulationParams, Path, queue.Queue[BatchMessage], threading.Event],
            threading.Thread,
        ] = batch_runner.start_batch_run,
        poll_interval_ms: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build the progress display; no run is started until `.start()`.

        Args:
            parent: The Tk container this screen is gridded into.
            on_done: Called with the completed `RunResult` once a
                scalar worker posts `("done", result)`. Defaults to a
                no-op — Milestone G3 gives this a real results-screen
                navigator.
            on_cancelled: Called with the generation a scalar run was
                cancelled at, once the worker posts
                `("cancelled", generation)`. Defaults to a no-op.
            on_error: Called with the message text once either worker
                posts `("error", message)` — an unexpected engine
                error, or a pre-existing output directory the guard in
                `start_run`/`start_batch_run` rejected before any
                thread started. Defaults to a no-op.
            on_batch_done: Called with the completed replicate tuple
                once a batch worker posts `("done", results)`. Defaults
                to a no-op — Milestone G4's own batch-results-screen
                bullet gives this a real navigator.
            on_batch_cancelled: Called with the replicate index and
                generation a batch was cancelled at, once the worker
                posts `("cancelled", replicate_index, generation)`.
                Defaults to a no-op.
            start_run: Builds and starts the scalar background worker
                thread. Defaults to the real `runner.start_run`;
                injectable so tests never spawn a real thread.
            start_batch_run: Builds and starts the batch background
                worker thread. Defaults to the real
                `batch_runner.start_batch_run`; injectable for the same
                reason.
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
        self._on_batch_done = (
            on_batch_done if on_batch_done is not None else (lambda _results: None)
        )
        self._on_batch_cancelled = (
            on_batch_cancelled
            if on_batch_cancelled is not None
            else (lambda _replicate_index, _generation: None)
        )
        self._start_run = start_run
        self._start_batch_run = start_batch_run
        self._poll_interval_ms = poll_interval_ms
        self._clock = clock

        self._cancel_event = threading.Event()
        self._queue: queue.Queue[RunMessage] = queue.Queue()
        self._batch_queue: queue.Queue[BatchMessage] = queue.Queue()
        self._max_generations = 1
        self._max_replicates = 1
        self._started_at: float | None = None
        self._after_id: str | None = None

        self._run_label = ttk.Label(self)
        self._run_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self._replicate_label = ttk.Label(self)
        self._replicate_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self._replicate_bar = ttk.Progressbar(self, mode="determinate", length=300)
        self._replicate_bar.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=4
        )
        self._generation_label = ttk.Label(self)
        self._generation_label.grid(row=3, column=0, columnspan=2, sticky="w", padx=4)
        self._progress_bar = ttk.Progressbar(self, mode="determinate", length=300)
        self._progress_bar.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=4, pady=4
        )
        self._elapsed_label = ttk.Label(self)
        self._elapsed_label.grid(row=5, column=0, sticky="w", padx=4)
        self._cancel_button = ttk.Button(
            self, text="Cancel", command=self._on_cancel_clicked
        )
        self._cancel_button.grid(row=5, column=1, sticky="e", padx=4)
        self._hide_replicate_bar()

    def start(self, params: SimulationParams, output_directory: Path) -> None:
        """Start a real background scalar run and begin polling for progress.

        Args:
            params: Already-validated parameters — Screen 1 never hands
                this an unvalidated payload (design §3.6).
            output_directory: The run's target artifact directory.
        """
        self._cancel_scheduled_poll()
        self._hide_replicate_bar()
        self._cancel_button["text"] = "Cancel"
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

    def start_batch(self, params: SimulationParams, output_directory: Path) -> None:
        """Start a real background batch run and begin polling for progress.

        Args:
            params: Already-validated parameters with `n_replicates > 1`
                — Screen 1 never hands this an unvalidated payload
                (design §3.6); `fim.gui.app.main` routes here instead
                of `.start()` exactly when `n_replicates > 1` (design
                §4.1: "there is no separate 'batch mode' toggle").
            output_directory: The batch's target artifact directory.
        """
        self._cancel_scheduled_poll()
        self._show_replicate_bar()
        self._cancel_button["text"] = "Cancel batch"
        self._cancel_event = threading.Event()
        self._batch_queue = queue.Queue()
        self._max_generations = params.max_generations
        self._max_replicates = params.n_replicates
        self._started_at = self._clock()

        self._run_label["text"] = f"Running batch {deterministic_run_id(params)}"
        self._set_replicate(0)
        self._set_generation(0)
        self._replicate_bar["maximum"] = params.n_replicates
        self._progress_bar["maximum"] = params.max_generations
        self._elapsed_label["text"] = "Elapsed: 00:00.0"

        try:
            self._start_batch_run(
                params, output_directory, self._batch_queue, self._cancel_event
            )
        except FileExistsError as error:
            self._on_error(str(error))
            return
        self._poll_batch()

    def _cancel_scheduled_poll(self) -> None:
        """Cancel whichever poll loop (scalar or batch) is currently scheduled."""
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _handle_batch_message(self, message: BatchMessage) -> bool:
        """Apply one queued batch message; return whether it ended the run.

        See `_handle_message`'s own docstring for why this matches on
        the message's literal tag directly rather than an `if`-chain on
        a separately bound `kind` variable.
        """
        match message:
            case ("replicate", replicate_index, generation):
                self._set_replicate(replicate_index)
                self._set_generation(generation)
                return False
            case ("done", results):
                self._set_replicate(len(results))
                self._on_batch_done(results)
                return True
            case ("cancelled", replicate_index, generation):
                self._on_batch_cancelled(replicate_index, generation)
                return True
            case ("error", text):
                self._on_error(text)
                return True

    def _handle_message(self, message: RunMessage) -> bool:
        """Apply one queued scalar message; return whether it ended the run.

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

    def _hide_replicate_bar(self) -> None:
        """Hide the outer replicate-count row — scalar mode has no batch axis."""
        self._replicate_label.grid_remove()
        self._replicate_bar.grid_remove()

    def _on_cancel_clicked(self) -> None:
        """Set the shared cancel event; always effective within one generation."""
        self._cancel_event.set()

    def _poll(self) -> None:
        """Drain every queued scalar message, then reschedule unless terminal."""
        try:
            while True:
                if self._handle_message(self._queue.get_nowait()):
                    return
        except queue.Empty:
            pass
        self._update_elapsed_label()
        self._after_id = self.after(self._poll_interval_ms, self._poll)

    def _poll_batch(self) -> None:
        """Drain every queued batch message, then reschedule unless terminal."""
        try:
            while True:
                if self._handle_batch_message(self._batch_queue.get_nowait()):
                    return
        except queue.Empty:
            pass
        self._update_elapsed_label()
        self._after_id = self.after(self._poll_interval_ms, self._poll_batch)

    def _set_generation(self, generation: int) -> None:
        """Update the generation label and inner progress bar together."""
        self._generation_label["text"] = (
            f"Generation {generation} / {self._max_generations}"
        )
        self._progress_bar["value"] = min(generation, self._max_generations)

    def _set_replicate(self, replicate_index: int) -> None:
        """Update the replicate label and outer progress bar together."""
        self._replicate_label["text"] = (
            f"Replicate {replicate_index} / {self._max_replicates}"
        )
        self._replicate_bar["value"] = min(replicate_index, self._max_replicates)

    def _show_replicate_bar(self) -> None:
        """Show the outer replicate-count row — batch mode's own progress axis."""
        self._replicate_label.grid()
        self._replicate_bar.grid()

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
