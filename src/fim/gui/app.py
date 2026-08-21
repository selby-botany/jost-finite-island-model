"""Tk application shell: root window and screen-switching mechanism.

`Application` owns exactly one `Tk` root and stacks every screen as a
`ttk.Frame` occupying the same grid cell, raised over its siblings with
`tkraise()` — design doc §4's "one `Tk` root ..., these are wireframes of
layout and behavior" framing. Each milestone in the implementation plan
(`dev/doc/apps/selby/jost-finite-island-model/
20260819-claude-sonnet-5-graphical-interface.md` §7) adds its own screen
and wires it into `main()`.

`Application` also carries the one menu item requirement G9/design §3.9
name outside any specific milestone's own commit bullet: "Check for
updates…", user-initiated only — never on startup, never on a timer
(SECURITY.md's threat model: "the only network operation is the explicit
`fim update --check` command"). `_check_for_updates` calls the identical
`fim.update` logic that command uses and renders the same three outcomes
as a dialog instead of stdout lines — on a background thread, unlike the
CLI's own blocking call: a frozen window is a GUI-specific cost a
blocking terminal command never pays, so this follows the same
background-thread-plus-`root.after`-poll shape every other network- or
time-costly action in this application already uses (design §3.4),
rather than freezing on the Tk main thread for the length of one HTTPS
round trip.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Final, Literal

from fim import __version__, paths, update
from fim.engine import RunResult
from fim.gui.screens.animation_screen import AnimationScreen
from fim.gui.screens.batch_results_screen import BatchResultsScreen, BatchResultsView
from fim.gui.screens.input_screen import InputScreen
from fim.gui.screens.open_run_screen import OpenRunScreen
from fim.gui.screens.progress_screen import ProgressScreen
from fim.gui.screens.results_screen import ResultsScreen, ResultsView
from fim.model.params import SimulationParams
from fim.reanalyze import ReanalyzedGeneration


class Application(tk.Tk):
    """Root window holding every screen as a stacked, raised `ttk.Frame`."""

    def __init__(self) -> None:
        """Build the root window, its menu bar, and its screen-stacking container."""
        super().__init__()
        self.title("fim")
        menu_bar = tk.Menu(self)
        self._help_menu = tk.Menu(menu_bar, tearoff=False)
        self._help_menu.add_command(
            label="Check for updates…",
            command=lambda: _check_for_updates(self, self._help_menu),
        )
        menu_bar.add_cascade(label="Help", menu=self._help_menu)
        self.config(menu=menu_bar)
        self._container = ttk.Frame(self)
        self._container.pack(fill="both", expand=True)
        self._container.rowconfigure(0, weight=1)
        self._container.columnconfigure(0, weight=1)
        self._screens: dict[str, ttk.Frame] = {}

    def register_screen(self, name: str, screen: ttk.Frame) -> None:
        """Add one screen to the stack, under `name`, without showing it.

        Args:
            name: Identifier `show_screen` later raises this screen by.
            screen: A `ttk.Frame` already built with this application (or
                its container) as an ancestor.
        """
        screen.grid(in_=self._container, row=0, column=0, sticky="nsew")
        self._screens[name] = screen

    def show_screen(self, name: str) -> None:
        """Raise a previously registered screen above every other one.

        Args:
            name: The identifier passed to `register_screen`.

        Raises:
            KeyError: If `name` was never registered.
        """
        self._screens[name].tkraise()


# A watchable-but-cheap poll cadence, matching every other queue-drained
# background action in this application (design §3.4).
_UPDATE_POLL_INTERVAL_MS: Final = 100
# The one and only entry `_help_menu` ever holds (`Application.__init__`) —
# named rather than a bare `0` so `_poll_update_result`'s enable/disable
# calls read as "the Check-for-updates entry," not a magic index.
_CHECK_FOR_UPDATES_INDEX: Final = 0

DoneMessage = tuple[Literal["done"], tuple[str, str]]
ErrorMessage = tuple[Literal["error"], str]
UpdateMessage = DoneMessage | ErrorMessage


def _check_for_updates(root: tk.Misc, help_menu: tk.Menu) -> None:
    """Query the latest GitHub release on a background thread.

    Returns immediately; `_poll_update_result` shows the outcome as a
    dialog once the worker thread finishes. Backgrounded so the window
    stays responsive during the network call — a frozen window is a
    cost only a GUI pays, never a blocking terminal command — matching
    every other network- or time-costly action in this application
    (design §3.4). The menu entry disables for the duration of one
    check so a user cannot start a second, overlapping one before the
    first finishes; `_poll_update_result` re-enables it once the result
    (or failure) arrives.
    """
    help_menu.entryconfigure(_CHECK_FOR_UPDATES_INDEX, state="disabled")
    message_queue: queue.Queue[UpdateMessage] = queue.Queue()

    def worker() -> None:
        try:
            message_queue.put(("done", update.latest_release()))
        except RuntimeError as error:
            message_queue.put(("error", str(error)))

    threading.Thread(target=worker, daemon=True).start()
    _poll_update_result(root, help_menu, message_queue)


def _poll_update_result(
    root: tk.Misc,
    help_menu: tk.Menu,
    message_queue: queue.Queue[UpdateMessage],
) -> None:
    """Drain the worker's one message, rescheduling itself until it arrives.

    Never a blocking queue read — the same `root.after`-driven poll
    every other background action in this application already uses
    (design §3.4).
    """
    try:
        message = message_queue.get_nowait()
    except queue.Empty:
        root.after(
            _UPDATE_POLL_INTERVAL_MS,
            lambda: _poll_update_result(root, help_menu, message_queue),
        )
        return
    help_menu.entryconfigure(_CHECK_FOR_UPDATES_INDEX, state="normal")
    _show_update_result(message)


def _show_update_result(message: UpdateMessage) -> None:
    """Render one completed update check's outcome as a dialog.

    The exact three success outcomes `cli._command_update` prints as
    stdout lines (design §3.9), rendered as a `messagebox` dialog
    instead — same `fim.update.compare_versions` call, same wording,
    only the presentation differs. A `match` on the message's literal
    tag (not an `if`-chain on a separately bound variable) is what lets
    mypy narrow each branch's payload type, the same reason
    `fim.gui.screens.progress_screen._handle_message` uses one.
    """
    match message:
        case ("error", text):
            messagebox.showerror("Check for updates", text)
        case ("done", (latest_tag, release_url)):
            comparison = update.compare_versions(
                __version__, latest_tag.removeprefix("v")
            )
            if comparison < 0:
                messagebox.showinfo(
                    "Check for updates",
                    f"A newer fim release is available: {latest_tag}\n{release_url}",
                )
            elif comparison == 0:
                messagebox.showinfo(
                    "Check for updates", f"fim {__version__} is current"
                )
            else:
                messagebox.showinfo(
                    "Check for updates",
                    f"fim {__version__} is newer than the latest release {latest_tag}",
                )


def main() -> int:
    """Launch the fim GUI: build the root window and run its main loop.

    Wires Screen 1 (input) -> Screen 2 (progress) -> Screen 3/4 (results):
    "Run simulation" starts a real background run in
    `paths.default_output_directory()` and switches to Screen 2 —
    scalar mode for `n_replicates == 1`, batch mode otherwise (design
    §4.1: "there is no separate 'batch mode' toggle; `n_replicates`
    *is* the toggle"). A cancelled or failed run returns to Screen 1
    with its message shown in the banner (design §4.7), form values
    intact. A completed scalar run becomes a `ResultsView`
    (`ResultsView.from_run_result`) shown on Screen 3; a completed batch
    becomes a `BatchResultsView` (`BatchResultsView.from_results`) shown
    on Screen 4 instead — the same routing split as Screen 2 itself.
    Screen 3's own "New run" returns to Screen 1 with the just-run
    configuration still in the form — "Reset to defaults" is what
    starts genuinely fresh. Screen 4's "Open replicate" raises Screen 3
    for the selected replicate, with its own output subdirectory rather
    than the batch's top-level one (design §4.0 #8). Screen 1's own
    "Open a run…" refreshes and raises Screen 6; its "Open ▶" re-
    analyzes a persisted trajectory
    (`fim.reanalyze.reanalyze_trajectory`) into a `ReanalyzedGeneration`,
    becomes a `ResultsView`
    (`ResultsView.from_reanalyzed_generation`), and is shown on Screen 3
    exactly like a live run (design §4.6: "opening a run re-renders
    Screen 3"). Screen 3's "Animate" raises Screen 5 for the shown
    view's own `trajectory.jsonl`; Screen 5's "Back" returns to
    Screen 3 (design §4.5's mock omits the control, but the screen is
    otherwise a dead end).

    Returns:
        Always 0 — a normal window close ends the process successfully;
        an unhandled exception inside the loop propagates instead.
    """
    app = Application()
    current_output_directory: Path | None = None
    current_n_replicates: int | None = None

    def start_run(params: SimulationParams) -> None:
        nonlocal current_output_directory, current_n_replicates
        current_output_directory = paths.default_output_directory()
        current_n_replicates = params.n_replicates
        if params.n_replicates == 1:
            progress_screen.start(params, current_output_directory)
        else:
            progress_screen.start_batch(params, current_output_directory)
        app.show_screen("progress")

    def show_results(result: RunResult) -> None:
        assert current_output_directory is not None, (
            "on_done fired without a preceding start_run"
        )
        results_screen.show(
            ResultsView.from_run_result(result), current_output_directory
        )
        app.show_screen("results")

    def show_batch_results(results: tuple[RunResult, ...]) -> None:
        assert current_output_directory is not None, (
            "on_batch_done fired without a preceding start_run"
        )
        batch_results_screen.show(
            BatchResultsView.from_results(results), current_output_directory
        )
        app.show_screen("batch_results")

    def show_replicate(view: ResultsView, output_directory: Path) -> None:
        results_screen.show(view, output_directory)
        app.show_screen("results")

    def show_reanalyzed_results(
        reanalyzed: ReanalyzedGeneration, output_directory: Path
    ) -> None:
        results_screen.show(
            ResultsView.from_reanalyzed_generation(reanalyzed), output_directory
        )
        app.show_screen("results")

    def show_open_run() -> None:
        open_run_screen.refresh()
        app.show_screen("open_run")

    def show_animation(view: ResultsView, output_directory: Path) -> None:
        animation_screen.show(
            view.run_id, view.params, output_directory / "trajectory.jsonl"
        )
        app.show_screen("animation")

    def return_to_input(message: str) -> None:
        input_screen.show_message(message)
        app.show_screen("input")

    def show_cancelled(generation: int) -> None:
        return_to_input(
            f"Run cancelled at generation {generation}; no artifacts were written"
        )

    def show_batch_cancelled(replicate_index: int, _generation: int) -> None:
        assert current_n_replicates is not None, (
            "on_batch_cancelled fired without a preceding start_run"
        )
        return_to_input(
            f"Batch cancelled during replicate {replicate_index} of "
            f"{current_n_replicates}; no artifacts were written"
        )

    input_screen = InputScreen(app, on_run=start_run, on_open_run=show_open_run)
    progress_screen = ProgressScreen(
        app,
        on_done=show_results,
        on_cancelled=show_cancelled,
        on_batch_done=show_batch_results,
        on_batch_cancelled=show_batch_cancelled,
        on_error=return_to_input,
    )
    results_screen = ResultsScreen(
        app,
        on_new_run=lambda: app.show_screen("input"),
        on_animate=show_animation,
    )
    batch_results_screen = BatchResultsScreen(app, on_open_replicate=show_replicate)
    open_run_screen = OpenRunScreen(app, on_open=show_reanalyzed_results)
    animation_screen = AnimationScreen(app, on_back=lambda: app.show_screen("results"))
    for name, screen in (
        ("input", input_screen),
        ("progress", progress_screen),
        ("results", results_screen),
        ("batch_results", batch_results_screen),
        ("open_run", open_run_screen),
        ("animation", animation_screen),
    ):
        app.register_screen(name, screen)
    app.show_screen("input")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
