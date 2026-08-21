"""Screen 3 — results (design doc §4.3): run summary beside the canonical
scatter, embedded via `FigureCanvasTkAgg`.

Design §13 Screen 2 realized: run summary (requirement G3's "all six
named statistics, convergence outcome") beside the scatter
`fim.viz.scatter.plot_frequency_scatter` already returns without writing
anything when `path=None` (design §3.5) — the same figure-building call
`fim.gui.runner._run_worker` makes to save `scatter.png`, just a second,
independent call building a second `Figure` for this screen to keep
alive on screen instead of on disk.

`ResultsView` renders a just-completed background run
(`fim.engine.RunResult`, as `fim.gui.runner`'s `("done", result)`
message carries it) today. Milestone G5 (§7.7) adds a second source —
an opened, re-analyzed trajectory (design §4.6) — as another
`ResultsView` classmethod alongside `from_run_result`, at which point
this screen's own widget logic still only ever depends on `ResultsView`
itself, never on either source type by name.
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Final

from matplotlib import pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from fim.engine import RunResult
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.viz.scatter import plot_frequency_scatter

# The mock's own six named statistics (design §4.3) — `FinalReport` also
# carries `H_ST`, added after that mock was drawn; requirement G3 names
# exactly these six ("all six named statistics"), so `H_ST` is not shown.
_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T")


@dataclass(frozen=True, slots=True)
class ResultsView:
    """Screen 3's shared input: whatever every result source supplies.

    Built via a classmethod per source (`from_run_result` today) rather
    than the screen depending on any source type directly.
    """

    run_id: str
    report: Mapping[str, object]
    state: ModelState
    params: SimulationParams
    generation_count: int

    @classmethod
    def from_run_result(cls, result: RunResult) -> ResultsView:
        """Build a view from a just-completed background run."""
        return cls(
            run_id=result.run_id,
            report=result.report,
            state=result.final_state,
            params=result.params,
            generation_count=result.manifest.generation_count,
        )


def _animate_is_enabled(generation_count: int) -> bool:
    """Return whether "Animate" should be enabled for a persisted trajectory.

    "Animate" plays back a persisted trajectory's several generations
    (design §4.5); a single-generation run has nothing to animate. In
    practice `generation_count` is always at least 2 for any run a
    validated `SimulationParams` can produce — `convergence_window`'s
    own minimum of 2 forces at least one step past generation 0 before
    stability can first be evaluated — so this guard is a defensive
    floor design §4.3 names explicitly, not a case this GUI can
    actually reach today; it stays a real, independently testable
    predicate rather than an inline comparison so that guarantee is
    checked directly, not merely assumed.
    """
    return generation_count > 1


def _format_statistic(value: object) -> str:
    """Format one report statistic for display, matching `cli._format_optional`.

    Args:
        value: A `ResultsView.report` entry — `float | None` in every
            real case (`RunResult.report`'s six named statistics), but
            typed as `object` here since `ResultsView.report` is a
            plain `Mapping[str, object]` shared across every source.
    """
    if value is None:
        return "undefined"
    if isinstance(value, int | float):
        return f"{float(value):.6g}"
    return str(value)


def _reveal_in_file_browser(directory: Path) -> None:
    """Open `directory` in the platform's file browser.

    Args:
        directory: The directory to reveal. `check=False` throughout:
            a file browser's own exit status is not this button's
            concern, and `explorer.exe` on Windows is well known to
            return a nonzero status for benign reasons.
    """
    if sys.platform == "win32":
        subprocess.run(["explorer", str(directory)], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(directory)], check=False)
    else:
        subprocess.run(["xdg-open", str(directory)], check=False)


class ResultsScreen(ttk.Frame):
    """Screen 3: one completed run's summary and embedded scatter."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_new_run: Callable[[], None] | None = None,
        on_animate: Callable[[ResultsView, Path], None] | None = None,
        open_folder: Callable[[Path], None] = _reveal_in_file_browser,
    ) -> None:
        """Build the summary labels, canvas frame, and action buttons.

        Args:
            parent: The Tk container this screen is gridded into.
            on_new_run: Called when "New run" is clicked. Defaults to a
                no-op — `fim.gui.app` wires this to return to Screen 1.
            on_animate: Called with the shown `ResultsView` and its
                output directory when "Animate" is clicked. Defaults
                to a no-op; Milestone G6 (§7.8) wires this to a real
                animation screen that does not exist yet.
            open_folder: Reveals a directory in the platform file
                browser. Defaults to the real, OS-dispatching
                implementation; injectable so tests never launch one.
        """
        super().__init__(parent)
        self._on_new_run = on_new_run if on_new_run is not None else (lambda: None)
        self._on_animate = (
            on_animate
            if on_animate is not None
            else (lambda _result, _output_directory: None)
        )
        self._open_folder = open_folder
        self._result: ResultsView | None = None
        self._output_directory: Path | None = None
        self._canvas: FigureCanvasTkAgg | None = None
        self._figure: Figure | None = None

        self._run_label = ttk.Label(self)
        self._run_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self._outcome_label = ttk.Label(self)
        self._outcome_label.grid(row=1, column=0, sticky="w", padx=4)

        self._statistic_labels: dict[str, ttk.Label] = {}
        for offset, name in enumerate(_STATISTIC_NAMES):
            label = ttk.Label(self)
            label.grid(row=2 + offset, column=0, sticky="w", padx=4)
            self._statistic_labels[name] = label

        self._canvas_frame = ttk.Frame(self)
        self._canvas_frame.grid(
            row=1,
            column=1,
            rowspan=len(_STATISTIC_NAMES) + 1,
            sticky="nsew",
            padx=4,
            pady=4,
        )

        button_row = len(_STATISTIC_NAMES) + 2
        buttons = ttk.Frame(self)
        buttons.grid(row=button_row, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Button(buttons, text="New run", command=self._on_new_run_clicked).pack(
            side="left"
        )
        self._animate_button = ttk.Button(
            buttons, text="Animate", command=self._on_animate_clicked
        )
        self._animate_button.pack(side="left")
        ttk.Button(
            buttons, text="Open output folder", command=self._on_open_folder_clicked
        ).pack(side="left")

    def show(self, view: ResultsView, output_directory: Path) -> None:
        """Render one run's summary and a fresh embedded scatter.

        Closes the previously embedded figure first, if any — design
        §3.5's own care item: `pyplot` keeps every created `Figure`
        registered in memory until closed, and this screen's whole
        point is to keep its figure alive on screen rather than
        closing it right after saving the way `fim.gui.runner` does
        with its own (separate) scatter, so a long GUI session that
        runs many simulations without this would leak one `Figure` per
        run shown here.

        Args:
            view: A just-completed background run
                (`ResultsView.from_run_result`, as `fim.gui.runner`'s
                `("done", result)` message carries it).
            output_directory: The run's artifact directory — "Open
                output folder" reveals this, and "Animate" passes it
                through to `on_animate`.
        """
        self._close_figure()
        self._result = view
        self._output_directory = output_directory
        report = view.report

        self._run_label["text"] = view.run_id
        reason = str(report["reason"]).capitalize()
        self._outcome_label["text"] = f"{reason}: generation {report['generation']}"
        for name in _STATISTIC_NAMES:
            self._statistic_labels[name]["text"] = (
                f"{name:<9}= {_format_statistic(report[name])}"
            )

        for child in self._canvas_frame.winfo_children():
            child.destroy()
        figure = plot_frequency_scatter(view.state, view.params, None)
        # matplotlib's Tk backend ships no type annotations of its own —
        # every call into it is necessarily untyped under mypy --strict.
        canvas = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            figure, master=self._canvas_frame
        )
        canvas.draw()  # type: ignore[no-untyped-call]
        canvas.get_tk_widget().pack(fill="both", expand=True)  # type: ignore[no-untyped-call]
        self._canvas = canvas
        self._figure = figure

        if _animate_is_enabled(view.generation_count):
            self._animate_button.state(["!disabled"])
        else:
            self._animate_button.state(["disabled"])

    def _close_figure(self) -> None:
        """Close the currently embedded figure, if any (design §3.5)."""
        if self._figure is not None:
            plt.close(self._figure)
            self._figure = None

    def _on_animate_clicked(self) -> None:
        """Forward the shown result and its output directory to `on_animate`."""
        if self._result is not None and self._output_directory is not None:
            self._on_animate(self._result, self._output_directory)

    def _on_new_run_clicked(self) -> None:
        """Close the embedded figure — navigating away — then invoke `on_new_run`."""
        self._close_figure()
        self._on_new_run()

    def _on_open_folder_clicked(self) -> None:
        """Reveal the run's output directory via the injected `open_folder`."""
        if self._output_directory is not None:
            self._open_folder(self._output_directory)
