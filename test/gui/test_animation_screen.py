"""Headless functional tests for Screen 5, the animation screen.

Every test constructs a real `AnimationScreen` (needs a display, hence
the `gui` marker — design doc §6.2/§6.4) and drives it by calling
internal stepping/scrubbing methods directly, never `mainloop()` or a
real `self.after()` wait (design §6.1's determinism contract: no test
may depend on real wall-clock timing for correctness).
`pre_render_frames` is always injected with a fake returning
pre-built frames, so no test here reads a real trajectory —
`fim.gui.animation`'s own real sampling/rendering is covered by
`test/gui/test_animation.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib import pyplot as plt

from fim.gui.animation import AnimationFrame
from fim.gui.app import Application
from fim.gui.screens.animation_screen import AnimationScreen
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams

pytestmark = pytest.mark.gui

_PARAMS = SimulationParams(N=20, m=0.1, mu=0.01, d=2, seed=1, loci=(LocusSpec(1, 200),))


def _build_frames(generations: list[int]) -> list[AnimationFrame]:
    """Build one real, pyplot-registered `Figure` per generation.

    `plt.figure()` (not the bare `matplotlib.figure.Figure()`
    constructor) is what registers with `plt.get_fignums()` — the
    figure-leak regression test below needs that registration to mean
    anything.
    """
    return [
        AnimationFrame(generation=generation, figure=plt.figure())
        for generation in generations
    ]


def test_animation_screen_show_embeds_the_first_frame(root: Application) -> None:
    """`.show()` displays frame 0 and its generation label."""
    frames = _build_frames([0, 5, 10])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)

    screen.show("run-1", _PARAMS, Path("/nonexistent"))

    assert screen._current_index == 0
    assert screen._generation_label["text"] == "Generation 0 / 10"
    assert screen._canvas is not None
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_scrub_jumps_to_the_requested_frame(root: Application) -> None:
    """Scrubbing to a specific index updates the displayed frame directly."""
    frames = _build_frames([0, 5, 10])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)
    screen.show("run-1", _PARAMS, Path("/nonexistent"))

    screen._on_scale_changed("2")

    assert screen._current_index == 2
    assert screen._generation_label["text"] == "Generation 10 / 10"
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_play_starts_and_steps_forward(root: Application) -> None:
    """Play sets the playing state; a manual step advances one frame."""
    frames = _build_frames([0, 5, 10])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)
    screen.show("run-1", _PARAMS, Path("/nonexistent"))

    screen._on_play_pause_clicked()

    assert screen._playing is True
    assert screen._play_button["text"] == "Pause"

    screen._step()

    assert screen._current_index == 1
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_pause_cancels_further_stepping(root: Application) -> None:
    """Pause stops playback and cancels the pending scheduled step."""
    frames = _build_frames([0, 5, 10])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)
    screen.show("run-1", _PARAMS, Path("/nonexistent"))
    screen._on_play_pause_clicked()
    playing_after_id = screen._after_id

    screen._on_play_pause_clicked()

    assert playing_after_id is not None
    assert screen._playing is False
    assert screen._play_button["text"] == "Play"
    assert screen._after_id is None
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_reaching_the_last_frame_stops_playback(
    root: Application,
) -> None:
    """Playback stops cleanly at the last frame instead of looping or erroring."""
    frames = _build_frames([0, 1])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)
    screen.show("run-1", _PARAMS, Path("/nonexistent"))

    screen._on_play_pause_clicked()
    screen._step()  # advances from frame 0 to frame 1, the last one

    assert screen._current_index == 1
    assert screen._playing is False
    assert screen._play_button["text"] == "Play"
    assert screen._after_id is None
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_play_disabled_for_a_single_frame(root: Application) -> None:
    """A single-frame trajectory disables Play/Pause; clicking it does nothing."""
    frames = _build_frames([0])
    screen = AnimationScreen(root, pre_render_frames=lambda *_args: frames)

    screen.show("run-1", _PARAMS, Path("/nonexistent"))

    assert "disabled" in screen._play_button.state()

    screen._on_play_pause_clicked()

    assert screen._playing is False
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_back_stops_playback_and_invokes_the_callback(
    root: Application,
) -> None:
    """ "Back" stops any running playback, then calls `on_back` with no arguments."""
    frames = _build_frames([0, 5, 10])
    calls: list[None] = []
    screen = AnimationScreen(
        root,
        pre_render_frames=lambda *_args: frames,
        on_back=lambda: calls.append(None),
    )
    screen.show("run-1", _PARAMS, Path("/nonexistent"))
    screen._on_play_pause_clicked()
    assert screen._play_button["text"] == "Pause"

    screen._on_back_clicked()

    assert calls == [None]
    assert screen._after_id is None
    assert screen._play_button["text"] == "Play"
    for frame in frames:
        plt.close(frame.figure)


def test_animation_screen_show_does_not_leak_frames_across_repeated_shows(
    root: Application,
) -> None:
    """`.show()` closes every previously pre-rendered frame, not just the shown one.

    Regression test for design §3.5's `plt.close` care item, sized for
    this screen's own worst case: several frames are pre-rendered per
    `.show()` call, and only one is ever on screen — closing only that
    one would leak the rest every time a fresh set replaces an old one.
    """
    # Frames are built lazily, inside `pre_render_frames`, exactly when
    # `.show()` calls it — the same as a real `pre_render_frames` only
    # ever creates figures when actually invoked. Building them eagerly
    # up front would already count them in `baseline`, hiding the leak
    # this test exists to catch.
    generation_sets = iter([[0, 1, 2], [0, 1, 2, 3]])
    screen = AnimationScreen(
        root, pre_render_frames=lambda *_args: _build_frames(next(generation_sets))
    )
    baseline = len(plt.get_fignums())

    screen.show("run-1", _PARAMS, Path("/nonexistent"))
    assert len(plt.get_fignums()) == baseline + 3

    screen.show("run-1", _PARAMS, Path("/nonexistent"))
    assert len(plt.get_fignums()) == baseline + 4

    for frame in screen._frames:
        plt.close(frame.figure)
