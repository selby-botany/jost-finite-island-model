"""Screen 5 — animated trajectory (design doc §4.5): play back a
persisted run's sampled generations as a stepped scatter.

Reached only from Screen 3, never as a standalone entry point, since it
always operates on an already-completed run's persisted trajectory that
has already passed the integrity check (design §3.8, §4.6) — this
screen itself does not know or care whether that run was a live run, a
replicate opened from Screen 4, or a re-analyzed trajectory opened from
Screen 6, only the `(run_id, params, trajectory_path)` triple it was
handed. Frames are pre-rendered once per `.show()` call (design §3.8,
`fim.gui.animation.pre_render_frames`) and then only swapped, never
rebuilt, while stepping or scrubbing — playback is driven by
`root.after`-scheduled steps rather than `matplotlib.animation.
FuncAnimation`'s own timer, so there is exactly one event loop driving
the window (design §3.8).
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from matplotlib import pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from fim.gui import animation
from fim.gui.animation import AnimationFrame
from fim.model.params import SimulationParams

# A watchable cadence: fast enough to read as motion rather than a
# slideshow, slow enough that individual frames (up to
# `GUI_ANIMATION_MAX_FRAMES` of them) do not blur past unreadably.
_DEFAULT_STEP_INTERVAL_MS = 150

# A single-frame trajectory has nothing to play — Play/Pause stays
# disabled and pressing it is a no-op (design §4.3's analogous "Animate
# disabled when the persisted trajectory has only one generation" rule,
# applied here on the screen that would actually do the playing).
_MINIMUM_FRAMES_TO_ANIMATE = 2


class AnimationScreen(ttk.Frame):
    """Screen 5: step or scrub through a persisted trajectory's sampled frames."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        pre_render_frames: Callable[
            [Path, SimulationParams, str], list[AnimationFrame]
        ] = animation.pre_render_frames,
        step_interval_ms: int = _DEFAULT_STEP_INTERVAL_MS,
        on_back: Callable[[], None] | None = None,
    ) -> None:
        """Build the embedded canvas, generation label, play/pause, and scrub slider.

        Args:
            parent: The Tk container this screen is gridded into.
            pre_render_frames: Samples and builds every displayed
                frame. Defaults to the real
                `fim.gui.animation.pre_render_frames`; injectable so
                tests never render a real trajectory through here.
            step_interval_ms: Milliseconds between playback steps.
            on_back: Called when "Back" is clicked. Defaults to a
                no-op; `fim.gui.app` wires this to return to Screen 3 —
                design §4.5's mock omits this control, but this screen
                is otherwise a dead end with no other way to leave it.
        """
        super().__init__(parent)
        self._pre_render_frames = pre_render_frames
        self._step_interval_ms = step_interval_ms
        self._on_back = on_back if on_back is not None else (lambda: None)
        self._frames: list[AnimationFrame] = []
        self._current_index = 0
        self._playing = False
        self._updating_scale = False
        self._after_id: str | None = None
        self._canvas: FigureCanvasTkAgg | None = None

        self._canvas_frame = ttk.Frame(self)
        self._canvas_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")

        self._generation_label = ttk.Label(self)
        self._generation_label.grid(row=1, column=0, sticky="w", padx=4, pady=4)

        self._play_button = ttk.Button(
            self, text="Play", command=self._on_play_pause_clicked
        )
        self._play_button.grid(row=1, column=1, sticky="w", padx=4)

        self._scale = ttk.Scale(
            self,
            from_=0,
            to=0,
            orient="horizontal",
            command=self._on_scale_changed,
        )
        self._scale.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=4)

        ttk.Button(self, text="Back", command=self._on_back_clicked).grid(
            row=3, column=0, sticky="w", padx=4, pady=(0, 4)
        )

    def show(
        self, run_id: str, params: SimulationParams, trajectory_path: Path
    ) -> None:
        """Sample, pre-render, and display a fresh set of frames.

        Args:
            run_id: The run identity every trajectory row must belong to.
            params: The run's validated parameters.
            trajectory_path: The persisted `trajectory.jsonl` to read.
        """
        self._stop_playing()
        self._close_frames()
        self._frames = self._pre_render_frames(trajectory_path, params, run_id)
        if not self._frames:
            self._generation_label["text"] = "No frames to animate"
            self._play_button.state(["disabled"])
            return
        self._play_button.state(
            ["disabled"]
            if len(self._frames) < _MINIMUM_FRAMES_TO_ANIMATE
            else ["!disabled"]
        )
        self._scale.configure(to=len(self._frames) - 1)
        self._set_current_index(0)

    def _close_frames(self) -> None:
        """Close every pre-rendered frame's figure, not just the displayed one.

        Design §3.5's `plt.close` care item, sized for this screen's
        own worst case: up to `GUI_ANIMATION_MAX_FRAMES` figures are
        pre-rendered per `.show()` call, and only one of them is ever
        on screen at a time — closing only that one and forgetting the
        rest would leak the other ~99 every time a fresh set replaces
        an old one.
        """
        for frame in self._frames:
            plt.close(frame.figure)
        self._frames = []

    def _display_frame(self, frame: AnimationFrame) -> None:
        """Embed `frame`'s figure, replacing the currently displayed one.

        Rebuilds the `FigureCanvasTkAgg` rather than mutating an
        existing one's `.figure` — the same "destroy the widget,
        build a fresh canvas" approach `results_screen.py` already
        uses for a new run's figure, just invoked once per displayed
        frame here instead of once per run. Only the Tk canvas *widget*
        is torn down; `frame.figure` itself is owned by `self._frames`
        and is not closed here — see `_close_frames`.
        """
        for child in self._canvas_frame.winfo_children():
            child.destroy()
        # matplotlib's Tk backend ships no type annotations of its own —
        # every call into it is necessarily untyped under mypy --strict.
        canvas = FigureCanvasTkAgg(  # type: ignore[no-untyped-call]
            frame.figure, master=self._canvas_frame
        )
        canvas.draw()  # type: ignore[no-untyped-call]
        canvas.get_tk_widget().pack(fill="both", expand=True)  # type: ignore[no-untyped-call]
        self._canvas = canvas

    def _on_back_clicked(self) -> None:
        """Stop playback, then invoke `on_back` — navigation, not this screen's."""
        self._stop_playing()
        self._on_back()

    def _on_play_pause_clicked(self) -> None:
        """Toggle between playing and paused."""
        if self._playing:
            self._stop_playing()
        else:
            self._start_playing()

    def _on_scale_changed(self, value: str) -> None:
        """Jump directly to the scrubbed frame index.

        Guarded by `_updating_scale`: `_set_current_index` itself calls
        `self._scale.set(...)` to keep the slider in sync while
        stepping or on `.show()`, which would otherwise re-enter this
        callback for a value the frame index already reflects.
        """
        if self._updating_scale:
            return
        index = round(float(value))
        if 0 <= index < len(self._frames):
            self._set_current_index(index)

    def _set_current_index(self, index: int) -> None:
        """Display frame `index` and keep the scale and label in sync with it."""
        self._current_index = index
        frame = self._frames[index]
        self._display_frame(frame)
        self._updating_scale = True
        try:
            self._scale.set(index)
        finally:
            self._updating_scale = False
        last_generation = self._frames[-1].generation
        self._generation_label["text"] = (
            f"Generation {frame.generation} / {last_generation}"
        )

    def _start_playing(self) -> None:
        """Begin stepping forward, restarting from the first frame if at the last."""
        if len(self._frames) < _MINIMUM_FRAMES_TO_ANIMATE:
            return
        if self._current_index >= len(self._frames) - 1:
            self._set_current_index(0)
        self._playing = True
        self._play_button["text"] = "Pause"
        self._schedule_step()

    def _schedule_step(self) -> None:
        """Queue the next playback step after `step_interval_ms`."""
        self._after_id = self.after(self._step_interval_ms, self._step)

    def _step(self) -> None:
        """Advance one frame, stopping cleanly once the last frame is reached."""
        self._after_id = None
        if self._current_index >= len(self._frames) - 1:
            self._stop_playing()
            return
        self._set_current_index(self._current_index + 1)
        if self._current_index >= len(self._frames) - 1:
            self._stop_playing()
        else:
            self._schedule_step()

    def _stop_playing(self) -> None:
        """Stop playback, canceling any pending scheduled step."""
        self._playing = False
        self._play_button["text"] = "Play"
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
