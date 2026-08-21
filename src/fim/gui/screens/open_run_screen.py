"""Screen 6 — open an existing run (design doc §4.6): pick a persisted
trajectory and re-analyze it, mirroring `fim stats`.

This screen never renders a summary or a scatter itself: it resolves a
trajectory, calls `fim.reanalyze.reanalyze_trajectory`, and hands the
result to `on_open` — the same GUI/CLI parity requirement (G6, G8)
`fim.reanalyze` was extracted from `cli.py` for. `fim.gui.app` wires
`on_open` to Screen 3 (`ResultsView.from_reanalyzed_generation`),
exactly matching design §4.6: "opening a run re-renders Screen 3 for
the selected generation."

A recent-runs row for a batch manifest is listed (design §0, §4.0 #9)
but cannot be opened here — a batch has no single trajectory of its own
to verify or re-analyze (design §3.8, §4.6); selecting one shows a
banner naming Screen 4's "Open replicate" as the actual path to any one
replicate's trajectory, rather than either silently doing nothing or
attempting (and failing) to re-analyze the batch-level manifest itself.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from fim.gui import recent_runs
from fim.gui.recent_runs import RecentRun
from fim.reanalyze import ReanalyzedGeneration, reanalyze_trajectory

_GENERATION_MODE_FINAL = "final"
_GENERATION_MODE_CHOOSE = "choose"


class OpenRunScreen(ttk.Frame):
    """Screen 6: pick a persisted trajectory and generation, then re-analyze it."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_open: Callable[[ReanalyzedGeneration, Path], None] | None = None,
        list_recent_runs: Callable[[], list[RecentRun]] = recent_runs.list_recent_runs,
        open_dialog: Callable[[], str] = filedialog.askopenfilename,
    ) -> None:
        """Build the recent-runs list, browse button, and generation/q inputs.

        Args:
            parent: The Tk container this screen is gridded into.
            on_open: Called with the re-analyzed generation and the
                trajectory's parent directory once "Open" succeeds.
                Defaults to a no-op; `fim.gui.app` wires this to raise
                Screen 3.
            list_recent_runs: Populates the recent-runs list. Defaults
                to the real `fim.gui.recent_runs.list_recent_runs`;
                injectable so tests never touch the real filesystem.
            open_dialog: Returns the path "Browse for trajectory.jsonl…"
                reads, or an empty string for a cancelled dialog.
                Defaults to the real file-open dialog; injectable so
                tests never open one.
        """
        super().__init__(parent)
        self._on_open = on_open if on_open is not None else (lambda _r, _d: None)
        self._list_recent_runs = list_recent_runs
        self._open_dialog = open_dialog
        self._trajectory_path: Path | None = None
        self._recent_run_directories: dict[str, Path] = {}
        self._recent_run_is_batch: dict[str, bool] = {}

        self._banner = ttk.Label(self, foreground="red", wraplength=480)
        self._banner.grid(
            row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 8)
        )

        ttk.Label(self, text="Recent runs (results/*/manifest.json):").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4
        )
        self._recent_runs_tree = ttk.Treeview(
            self,
            columns=("ended_at", "outcome"),
            show="tree headings",
            height=6,
            selectmode="browse",
        )
        self._recent_runs_tree.heading("#0", text="run")
        self._recent_runs_tree.heading("ended_at", text="ended")
        self._recent_runs_tree.heading("outcome", text="outcome")
        self._recent_runs_tree.grid(
            row=2, column=0, columnspan=2, sticky="nsew", padx=4, pady=(0, 8)
        )
        self._recent_runs_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._on_recent_run_selected()
        )

        ttk.Button(
            self, text="Browse for trajectory.jsonl…", command=self._on_browse_clicked
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 8))

        self._generation_mode = tk.StringVar(self, value=_GENERATION_MODE_FINAL)
        ttk.Label(self, text="Generation:").grid(row=4, column=0, sticky="w", padx=4)
        generation_frame = ttk.Frame(self)
        generation_frame.grid(row=4, column=1, sticky="w", padx=4)
        ttk.Radiobutton(
            generation_frame,
            text="final",
            variable=self._generation_mode,
            value=_GENERATION_MODE_FINAL,
        ).pack(side="left")
        ttk.Radiobutton(
            generation_frame,
            text="choose",
            variable=self._generation_mode,
            value=_GENERATION_MODE_CHOOSE,
        ).pack(side="left")
        self._generation_entry = ttk.Entry(generation_frame, width=10)
        self._generation_entry.pack(side="left", padx=(4, 0))

        ttk.Label(self, text="Differentiation-q sweep:").grid(
            row=5, column=0, sticky="w", padx=4
        )
        self._q_entry = ttk.Entry(self, width=20)
        self._q_entry.grid(row=5, column=1, sticky="w", padx=4)

        ttk.Button(self, text="Open ▶", command=self._on_open_clicked).grid(
            row=6, column=1, sticky="e", padx=4, pady=(8, 4)
        )

        self.refresh()

    def refresh(self) -> None:
        """Reload the recent-runs list from `list_recent_runs`."""
        self._recent_runs_tree.delete(*self._recent_runs_tree.get_children())
        self._recent_run_directories.clear()
        self._recent_run_is_batch.clear()
        for run in self._list_recent_runs():
            self._recent_runs_tree.insert(
                "",
                "end",
                iid=run.run_id,
                text=run.run_id,
                values=(run.ended_at, run.label),
            )
            self._recent_run_directories[run.run_id] = run.directory
            self._recent_run_is_batch[run.run_id] = run.is_batch

    def _on_browse_clicked(self) -> None:
        """Set the trajectory path from a browsed file, overriding any selection."""
        path = self._open_dialog()
        if not path:
            return
        self._trajectory_path = Path(path)
        self._set_banner("")

    def _on_open_clicked(self) -> None:
        """Validate the current inputs, re-analyze, and invoke `on_open`."""
        if self._trajectory_path is None:
            self._set_banner("no trajectory selected")
            return
        try:
            generation = self._parse_generation()
            differentiation_orders = _parse_differentiation_orders(self._q_entry.get())
        except ValueError as error:
            self._set_banner(str(error))
            return
        try:
            result = reanalyze_trajectory(
                self._trajectory_path,
                generation=generation,
                differentiation_orders=differentiation_orders,
            )
        except ValueError as error:
            self._set_banner(str(error))
            return
        self._set_banner("")
        self._on_open(result, self._trajectory_path.parent)

    def _on_recent_run_selected(self) -> None:
        """Set the trajectory path from the selected recent-runs row.

        A batch row has no single trajectory to select (design §0,
        §4.0 #9, §4.6): the previous selection (if any) is cleared and
        the banner names Screen 4's "Open replicate" as the actual path
        to any one replicate's own trajectory, instead of leaving the
        prior trajectory silently selected or attempting a re-analysis
        that would only fail on the batch-level manifest's own shape.
        """
        selection = self._recent_runs_tree.selection()
        if not selection:
            return
        run_id = selection[0]
        if self._recent_run_is_batch[run_id]:
            self._trajectory_path = None
            self._set_banner(
                "batch runs have no single trajectory — open a replicate from "
                "its own batch results screen instead"
            )
            return
        self._trajectory_path = (
            self._recent_run_directories[run_id] / "trajectory.jsonl"
        )
        self._set_banner("")

    def _parse_generation(self) -> int | None:
        """Return the explicit generation from the "choose" entry, or `None` for final.

        Raises:
            ValueError: If "choose" is selected but the entry is empty
                or not an integer.
        """
        if self._generation_mode.get() != _GENERATION_MODE_CHOOSE:
            return None
        text = self._generation_entry.get().strip()
        try:
            return int(text)
        except ValueError as error:
            raise ValueError("generation must be an integer") from error

    def _set_banner(self, message: str) -> None:
        """Show one whole-form message (design §4.7), or clear it with ""."""
        self._banner["text"] = message


def _parse_differentiation_orders(text: str) -> tuple[float, ...]:
    """Parse the optional differentiation-q sweep entry into an order tuple.

    Args:
        text: Zero or more space/comma-separated numbers; empty means
            no sweep.

    Raises:
        ValueError: If any token is not a number.
    """
    stripped = text.strip()
    if not stripped:
        return ()
    tokens = stripped.replace(",", " ").split()
    try:
        return tuple(float(token) for token in tokens)
    except ValueError as error:
        raise ValueError(
            "differentiation-q sweep must be space/comma-separated numbers"
        ) from error
