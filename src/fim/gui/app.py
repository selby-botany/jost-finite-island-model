"""Tk application shell: root window and screen-switching mechanism.

`Application` owns exactly one `Tk` root and stacks every screen as a
`ttk.Frame` occupying the same grid cell, raised over its siblings with
`tkraise()` — design doc §4's "one `Tk` root ..., these are wireframes of
layout and behavior" framing. Each milestone in the implementation plan
(`dev/doc/apps/selby/jost-finite-island-model/
20260819-claude-sonnet-5-graphical-interface.md` §7) adds its own screen
and wires it into `main()`.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from fim import paths
from fim.engine import RunResult
from fim.gui.screens.input_screen import InputScreen
from fim.gui.screens.progress_screen import ProgressScreen
from fim.gui.screens.results_screen import ResultsScreen, ResultsView
from fim.model.params import SimulationParams


class Application(tk.Tk):
    """Root window holding every screen as a stacked, raised `ttk.Frame`."""

    def __init__(self) -> None:
        """Build the root window and its single screen-stacking container."""
        super().__init__()
        self.title("fim")
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


def main() -> int:
    """Launch the fim GUI: build the root window and run its main loop.

    Wires Screen 1 (input) -> Screen 2 (progress) -> Screen 3 (results):
    "Run simulation" starts a real background scalar run in
    `paths.default_output_directory()` and switches to Screen 2. A
    cancelled or failed run returns to Screen 1 with its message shown
    in the banner (design §4.6), form values intact. A completed run
    becomes a `ResultsView` (`ResultsView.from_run_result`) and is
    shown on Screen 3 alongside its output directory. Screen 3's own
    "New run" returns to Screen 1 with the just-run configuration still
    in the form — "Reset to defaults" is what starts genuinely fresh.
    "Animate" is still a no-op here — Milestone G6 (§7.8) wires it to
    an animation screen that does not exist yet.

    Returns:
        Always 0 — a normal window close ends the process successfully;
        an unhandled exception inside the loop propagates instead.
    """
    app = Application()
    current_output_directory: Path | None = None

    def start_run(params: SimulationParams) -> None:
        nonlocal current_output_directory
        current_output_directory = paths.default_output_directory()
        progress_screen.start(params, current_output_directory)
        app.show_screen("progress")

    def show_results(result: RunResult) -> None:
        assert current_output_directory is not None, (
            "on_done fired without a preceding start_run"
        )
        results_screen.show(
            ResultsView.from_run_result(result), current_output_directory
        )
        app.show_screen("results")

    def show_cancelled(generation: int) -> None:
        input_screen.show_message(
            f"Run cancelled at generation {generation}; no artifacts were written"
        )
        app.show_screen("input")

    def show_error(message: str) -> None:
        input_screen.show_message(message)
        app.show_screen("input")

    def show_input() -> None:
        app.show_screen("input")

    input_screen = InputScreen(app, on_run=start_run)
    progress_screen = ProgressScreen(
        app,
        on_done=show_results,
        on_cancelled=show_cancelled,
        on_error=show_error,
    )
    results_screen = ResultsScreen(app, on_new_run=show_input)
    app.register_screen("input", input_screen)
    app.register_screen("progress", progress_screen)
    app.register_screen("results", results_screen)
    app.show_screen("input")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
