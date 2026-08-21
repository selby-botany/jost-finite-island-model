"""Screen 4 — batch results (design doc §4.4): a replicate table beside
each watched statistic's across-replicate confidence interval.

New this revision (requirement G10, §2.1; §4.0 #7). Reached instead of
Screen 3 when a completed run's `n_replicates` was greater than one — a
batch has as many final states as replicates and no principled way to
privilege one as *the* scatter, so this screen shows the honest
alternative: a **replicate table** (id, status, final generation, and
every named statistic's final value — the same fields `report.json`
records per replicate) beside each statistic's **confidence interval**
from `fim.engine.replicate_summary` (the same computation `summary.json`
records), rendered as one row per statistic with the mean marked and the
interval drawn as an error bar — a labeled bar rather than a scatter,
since there is no principled single point to plot per statistic either.
A statistic omitted from the summary (fewer than two replicates had a
defined value) is shown as omitted, not silently blank.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Final

from matplotlib import pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from fim.engine import RunResult, replicate_summary
from fim.gui.screens.results_screen import ResultsView, format_statistic
from fim.statistics.interval import ConfidenceInterval

# `FinalReport`'s own full statistic set, in `fim.engine.replicate_summary`'s
# own docstring order — every one of them, not the mock's narrower six
# `ResultsScreen` shows (that scoping was tied to a specific, unchanged
# scalar-run mock image predating `H_ST`'s addition; Screen 4 has no such
# mock to stay narrower than, and design §4.4 asks for "every watched
# statistic's final value").
_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST")


@dataclass(frozen=True, slots=True)
class BatchResultsView:
    """Screen 4's input: every replicate result from one completed batch."""

    replicates: tuple[RunResult, ...]
    summary: dict[str, ConfidenceInterval]

    @classmethod
    def from_results(cls, replicates: tuple[RunResult, ...]) -> BatchResultsView:
        """Build a view from a just-completed background batch run."""
        return cls(replicates=replicates, summary=replicate_summary(replicates))


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


class BatchResultsScreen(ttk.Frame):
    """Screen 4: a batch's replicate table and per-statistic confidence intervals."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_open_replicate: Callable[[ResultsView, Path], None] | None = None,
        open_folder: Callable[[Path], None] = _reveal_in_file_browser,
        export_dialog: Callable[[], str] = filedialog.asksaveasfilename,
    ) -> None:
        """Build the header, replicate table, CI panel, and action buttons.

        Args:
            parent: The Tk container this screen is gridded into.
            on_open_replicate: Called with the selected replicate's
                `ResultsView` and its own output subdirectory when
                "Open replicate" is clicked. Defaults to a no-op;
                `fim.gui.app` wires this to raise Screen 3.
            open_folder: Reveals a directory in the platform file
                browser. Defaults to the real, OS-dispatching
                implementation; injectable so tests never launch one.
            export_dialog: Returns the path "Export summary.json"
                copies the file to, or an empty string for a cancelled
                dialog. Defaults to the real file-save dialog;
                injectable so tests never open one.
        """
        super().__init__(parent)
        self._on_open_replicate = (
            on_open_replicate
            if on_open_replicate is not None
            else (lambda _view, _output_directory: None)
        )
        self._open_folder = open_folder
        self._export_dialog = export_dialog
        self._view: BatchResultsView | None = None
        self._output_directory: Path | None = None
        self._canvas: FigureCanvasTkAgg | None = None
        self._figure: Figure | None = None

        self._header_label = ttk.Label(self)
        self._header_label.grid(
            row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4
        )

        self._table = ttk.Treeview(
            self,
            columns=("status", "generation", *_STATISTIC_NAMES),
            show="headings",
        )
        self._table.heading("status", text="Status")
        self._table.heading("generation", text="Generation")
        for name in _STATISTIC_NAMES:
            self._table.heading(name, text=name)
        self._table.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        self._canvas_frame = ttk.Frame(self)
        self._canvas_frame.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        self._open_replicate_button = ttk.Button(
            buttons, text="Open replicate", command=self._on_open_replicate_clicked
        )
        self._open_replicate_button.pack(side="left")
        ttk.Button(
            buttons,
            text="Export summary.json",
            command=self._on_export_summary_clicked,
        ).pack(side="left")
        ttk.Button(
            buttons, text="Open batch folder", command=self._on_open_folder_clicked
        ).pack(side="left")

    def show(self, view: BatchResultsView, output_directory: Path) -> None:
        """Render one batch's replicate table and a fresh confidence-interval panel.

        Closes the previously embedded figure first, if any — the same
        `plt.close` care item `ResultsScreen.show` observes (design
        §3.5): this screen keeps its own figure alive on screen, so a
        long GUI session that views many batches without this would
        leak one `Figure` per batch shown here.

        Args:
            view: A just-completed background batch
                (`BatchResultsView.from_results`, as `fim.gui.
                batch_runner`'s `("done", results)` message carries the
                replicate tuple that builds it).
            output_directory: The batch's own top-level artifact
                directory — "Open batch folder" reveals this,
                "Export summary.json" copies `output_directory /
                'summary.json'`, and each replicate row's own
                subdirectory (`fim.gui.batch_runner.
                replicate_output_directory`) is resolved relative to
                it.
        """
        self._close_figure()
        self._view = view
        self._output_directory = output_directory

        self._header_label["text"] = f"Batch: {len(view.replicates)} replicate(s)"
        self._table.delete(*self._table.get_children())
        for result in view.replicates:
            report = result.report
            values = (
                str(report["reason"]).capitalize(),
                str(report["generation"]),
                *(format_statistic(report[name]) for name in _STATISTIC_NAMES),
            )
            self._table.insert("", "end", iid=result.run_id, values=values)

        for child in self._canvas_frame.winfo_children():
            child.destroy()
        figure = _confidence_interval_figure(view.summary)
        # matplotlib's Tk backend ships no type annotations of its own —
        # every call into it is necessarily untyped under mypy --strict.
        canvas = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            figure, master=self._canvas_frame
        )
        canvas.draw()  # type: ignore[no-untyped-call]
        canvas.get_tk_widget().pack(fill="both", expand=True)  # type: ignore[no-untyped-call]
        self._canvas = canvas
        self._figure = figure

    def _close_figure(self) -> None:
        """Close the currently embedded figure, if any (design §3.5)."""
        if self._figure is not None:
            plt.close(self._figure)
            self._figure = None

    def _on_export_summary_clicked(self) -> None:
        """Copy the already-written `summary.json` to a user-chosen path.

        A convenience copy of the file `fim.gui.batch_runner` already
        wrote (design §4.4: "not new computation"), not a re-render —
        `shutil.copyfile` byte-for-byte, never re-serialized.
        """
        if self._output_directory is None:
            return
        destination = self._export_dialog()
        if not destination:
            return
        shutil.copyfile(self._output_directory / "summary.json", destination)

    def _on_open_folder_clicked(self) -> None:
        """Reveal the batch's own top directory via the injected `open_folder`."""
        if self._output_directory is not None:
            self._open_folder(self._output_directory)

    def _on_open_replicate_clicked(self) -> None:
        """Forward the selected row's replicate view and directory to the caller.

        The replicate's own subdirectory is `output_directory /
        f"replicate-{index:03}"`, where `index` is the row's 1-based
        position in `view.replicates` — the identical numbering
        `fim.gui.batch_runner._write_batch_artifacts` used when it
        wrote that directory, since both derive from the same
        ascending-replicate-index order `fim.engine.fim`'s sequential
        batch loop produces (`fim.gui.batch_runner.
        replicate_output_directory` re-derives the same index by
        parsing it back out of the run ID; recomputing it from table
        position here is the same number without needing the batch's
        own run ID, which `on_batch_done`'s replicate tuple alone does
        not carry).
        """
        if self._view is None or self._output_directory is None:
            return
        selection = self._table.selection()
        if not selection:
            return
        run_id = selection[0]
        for index, result in enumerate(self._view.replicates, start=1):
            if result.run_id == run_id:
                directory = self._output_directory / f"replicate-{index:03}"
                self._on_open_replicate(ResultsView.from_run_result(result), directory)
                return


def _confidence_interval_figure(
    summary: dict[str, ConfidenceInterval],
) -> Figure:
    """Build one figure with a labeled row per statistic (design §4.4).

    A present statistic draws its mean as a point with a horizontal
    error bar spanning `half_width` on either side — the "interval
    shaded, mean marked" mock's own information content, realized as an
    error bar rather than a filled span (Screen 3's `plot_frequency_
    scatter` precedent is the project's only prior embedded-figure
    design; this follows the same "structural, not pixel-identical to
    the mock" latitude design §4's "wireframes ... not final visuals"
    framing already established there). An omitted statistic — fewer
    than two replicates had a defined value, `replicate_summary`'s own
    documented case — gets its axis label but no point, and an
    "omitted" text annotation in its row instead of a silently blank
    one (design §4.4).
    """
    figure = Figure()
    axes = figure.add_subplot(111)
    positions = range(len(_STATISTIC_NAMES))
    for position, name in zip(positions, _STATISTIC_NAMES, strict=True):
        interval = summary.get(name)
        if interval is None:
            axes.text(0.5, position, "omitted", va="center", ha="center")
            continue
        axes.errorbar(
            [interval["mean"]],
            [position],
            xerr=[[interval["half_width"]], [interval["half_width"]]],
            fmt="o",
        )
    axes.set_yticks(list(positions))
    axes.set_yticklabels(_STATISTIC_NAMES)
    axes.set_xlabel("Value")
    return figure
