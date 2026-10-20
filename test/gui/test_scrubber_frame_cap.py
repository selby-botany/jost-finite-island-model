"""The live scrubber's own frame buffer stays bounded under a long run.

`scrubber.js`'s `appendLiveFrame` used to grow `frames` without limit for
the life of a run (`20261019-claude-opus-5-5-fim-gui-tabs-design-review.md`,
finding B2), so an hour-long scalar run could hold tens of thousands of
panels in memory. `decimateLiveFramesIfNeeded` bounds it at
`MAX_LIVE_SCRUBBER_FRAMES` by thinning the older half of the buffer every
time that cap is crossed again, while always keeping the first frame and
the newest half intact.

Both tests here drive the real page synchronously, with no real run: a
JS loop calls `window.fim.appendLiveFrame` directly, many times, in one
`evaluate_js` round trip, so nothing here depends on how fast a real
simulation reports progress.
"""

from __future__ import annotations

import queue
from typing import Any

import pytest
import webview

from fim.gui.app import create_window

from .conftest import wait_for_run_view_ready

pytestmark = pytest.mark.gui

_OUTCOME_TIMEOUT_SECONDS = 40.0

# Mirrors `MAX_LIVE_SCRUBBER_FRAMES` in `webui/config/behavior.js`. Kept
# as a plain literal here, not read back from the page, the same way
# other window tests already mirror a JS constant's value in a comment
# rather than introspecting it (`MINIMUM_FRAMES_TO_ANIMATE`, for one).
_CAP = 2048

_PUSH_SYNTHETIC_FRAMES = """
(function (fromGeneration, toGeneration) {
    for (let generation = fromGeneration; generation < toGeneration; generation += 1) {
        window.fim.appendLiveFrame(
            {
                generation: generation,
                panels: [],
                pairPanel: null,
                pairStatistics: null,
                statistics: {},
                literatureVisuals: null
            },
            () => {}
        );
    }
})(%d, %d);
"""

_READ_STATE = """({
    generations: window.fim.getScrubberGenerations(),
    currentIndex: Number(document.getElementById('scrubber-range').value)
})"""

_DRAG_TO_INDEX_50 = """
(function () {
    const range = document.getElementById('scrubber-range');
    range.value = '50';
    range.dispatchEvent(new Event('input', { bubbles: true }));
})();
"""


class _Scrubber:
    """Replicates `decimateLiveFramesIfNeeded`/`appendLiveFrame` exactly, in Python.

    Mirrors the JS line for line -- the push order, the decimation
    triggered after each one, and the live-tracking reassignment -- so a
    test can assert the real page's output *equals* this, not merely
    falls within some guessed tolerance. `frames` holds generation
    numbers alone, since that is the only field either implementation
    reads.

    Args:
        cap: `MAX_LIVE_SCRUBBER_FRAMES`.
    """

    def __init__(self, cap: int = _CAP) -> None:
        self._cap = cap
        self.frames: list[int] = []
        self.current_index = 0
        self.is_live_tracking = True

    def push(self, generation: int) -> None:
        """Append one frame, exactly as one `appendLiveFrame` call does."""
        self.frames.append(generation)
        if len(self.frames) > self._cap:
            tracked_generation = (
                None if self.is_live_tracking else self.frames[self.current_index]
            )
            first = self.frames[0]
            split_index = len(self.frames) // 2
            older_middle = self.frames[1:split_index]
            newer_half = self.frames[split_index:]
            thinned_older_middle = older_middle[0::2]
            self.frames = [first, *thinned_older_middle, *newer_half]
            if tracked_generation is not None:
                self.current_index = min(
                    range(len(self.frames)),
                    key=lambda index: (
                        abs(self.frames[index] - tracked_generation),
                        index,
                    ),
                )
        if self.is_live_tracking:
            self.current_index = len(self.frames) - 1

    def push_many(self, generations: range) -> None:
        """Append every generation in `generations`, in order."""
        for generation in generations:
            self.push(generation)

    def drag_to(self, index: int) -> None:
        """Select `index`, exactly as dragging the range input to it does."""
        self.current_index = index
        self.is_live_tracking = self.current_index == len(self.frames) - 1


def test_the_live_frame_buffer_stays_bounded_and_ordered_under_a_long_run() -> None:
    """5,000 pushes while live-tracking: the cap holds; the first and newest survive."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(_PUSH_SYNTHETIC_FRAMES % (0, 5000))
            outcome.put(window.evaluate_js(_READ_STATE))
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    generations = state["generations"]
    expected = _Scrubber()
    expected.push_many(range(5000))

    assert generations == expected.frames
    assert state["currentIndex"] == expected.current_index
    assert len(generations) <= _CAP
    assert generations[0] == 0
    assert generations[-1] == 4999
    assert all(
        generations[index] > generations[index - 1]
        for index in range(1, len(generations))
    )


def test_an_inspected_generation_is_remapped_after_the_buffer_is_thinned() -> None:
    """Scrubbing to an old frame, then pushing past the cap, keeps the nearest one."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(_PUSH_SYNTHETIC_FRAMES % (0, 100))
            window.evaluate_js(_DRAG_TO_INDEX_50)
            window.evaluate_js(_PUSH_SYNTHETIC_FRAMES % (100, 5100))
            outcome.put(window.evaluate_js(_READ_STATE))
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    expected = _Scrubber()
    expected.push_many(range(100))
    expected.drag_to(50)
    expected.push_many(range(100, 5100))

    assert state["generations"] == expected.frames
    assert state["currentIndex"] == expected.current_index
    # The remap landed on a real, still-present frame, not an out-of-range
    # index silently clamped back to the live head.
    assert state["currentIndex"] != len(expected.frames) - 1
