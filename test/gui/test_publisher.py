"""Tests for `fim.gui.publisher.Publisher` (no real window, no `gui` marker).

Every ordering claim here is driven by events a fake window's own
`evaluate_js` sets or waits on, never by a sleep: a regression that makes
`publish` block, or that lets delivery reorder or drop something it must
not, hangs at the real line responsible rather than racing a clock.
"""

from __future__ import annotations

import threading
from typing import Any

from conftest import poll_or_fail, wait_or_fail

from fim.gui.publisher import Publisher

_POLL_SECONDS = 0.01


class _RecordingWindow:
    """Records every script handed to `evaluate_js`, in delivery order."""

    def __init__(self) -> None:
        self.received: list[str] = []

    def evaluate_js(self, script: str) -> Any:
        self.received.append(script)
        return None


class _BlockingWindow:
    """Like `_RecordingWindow`, but one particular script blocks until released.

    `entered` is set the instant delivery reaches that script's own
    `evaluate_js` call -- the moment after which the publisher's own
    delivery thread is known to be stuck there, holding no lock.
    """

    def __init__(self, blocked_script: str, release: threading.Event) -> None:
        self.received: list[str] = []
        self._blocked_script = blocked_script
        self._release = release
        self.entered = threading.Event()

    def evaluate_js(self, script: str) -> Any:
        self.received.append(script)
        if script == self._blocked_script:
            self.entered.set()
            self._release.wait()
        return None


class _RaisingWindow:
    """Raises for one particular script, but still records every attempt."""

    def __init__(self, failing_script: str) -> None:
        self.received: list[str] = []
        self._failing_script = failing_script

    def evaluate_js(self, script: str) -> Any:
        self.received.append(script)
        if script == self._failing_script:
            raise RuntimeError("the page is gone")
        return None


def test_a_fresh_publisher_starts_no_thread() -> None:
    """The delivery thread is lazy: nothing is spent until the first publish."""
    publisher = Publisher()

    assert publisher._thread is None


def test_terminal_pushes_keep_arrival_order_and_are_never_dropped() -> None:
    """Every `coalesce_key=None` push is delivered, in the order it was sent."""
    publisher = Publisher()
    window = _RecordingWindow()

    for script in ("fim.onRunDone(1)", "fim.onReproducibilityChecked(2)"):
        publisher.publish(window, script)

    poll_or_fail(
        lambda: window.received,
        lambda received: len(received) == 2,
        "both terminal pushes delivered",
        interval=_POLL_SECONDS,
    )
    assert window.received == ["fim.onRunDone(1)", "fim.onReproducibilityChecked(2)"]


def test_publish_never_blocks_while_delivery_is_stuck_on_an_earlier_push() -> None:
    """A producer's own call returns at once, even mid-delivery of another push.

    Also proves coalescing: two progress ticks published while delivery is
    stuck on an unrelated push collapse into one -- only the newer one is
    ever handed to `evaluate_js`.
    """
    publisher = Publisher()
    release = threading.Event()
    window = _BlockingWindow("fim.onRunDone(1)", release)

    publisher.publish(window, "fim.onRunDone(1)")
    wait_or_fail(window.entered, "delivery to reach the blocking push")

    # Both return immediately: `publish` never waits on `evaluate_js`,
    # and the second replaces the first in place rather than queuing.
    publisher.publish(window, "fim.onRunProgress(1)", coalesce_key="run-progress")
    publisher.publish(window, "fim.onRunProgress(2)", coalesce_key="run-progress")

    release.set()
    poll_or_fail(
        lambda: window.received,
        lambda received: len(received) == 2,
        "the done push and the surviving progress tick delivered",
        interval=_POLL_SECONDS,
    )
    assert window.received == ["fim.onRunDone(1)", "fim.onRunProgress(2)"]


def test_a_coalesced_key_is_scoped_to_its_own_window() -> None:
    """Two windows' progress ticks never coalesce into each other."""
    publisher = Publisher()
    release = threading.Event()
    gate_window = _BlockingWindow("gate", release)
    other_window = _RecordingWindow()

    publisher.publish(gate_window, "gate")
    wait_or_fail(gate_window.entered, "delivery to reach the gate")

    publisher.publish(other_window, "fim.onRunProgress(1)", coalesce_key="run-progress")
    publisher.publish(gate_window, "fim.onRunProgress(2)", coalesce_key="run-progress")

    release.set()
    poll_or_fail(
        lambda: other_window.received,
        lambda received: len(received) == 1,
        "the other window's own progress tick delivered",
        interval=_POLL_SECONDS,
    )
    assert other_window.received == ["fim.onRunProgress(1)"]


def test_a_failed_push_does_not_stop_delivery_of_the_rest() -> None:
    """One push raising does not break the ones queued after it."""
    publisher = Publisher()
    window = _RaisingWindow("fim.onRunError(1)")

    publisher.publish(window, "fim.onRunError(1)")
    publisher.publish(window, "fim.onRunDone(2)")

    poll_or_fail(
        lambda: window.received,
        lambda received: len(received) == 2,
        "both pushes attempted",
        interval=_POLL_SECONDS,
    )
    assert window.received == ["fim.onRunError(1)", "fim.onRunDone(2)"]


def test_stop_drains_the_backlog_then_refuses_new_pushes() -> None:
    """`stop` waits for what is already queued, then `publish` becomes a no-op."""
    publisher = Publisher()
    window = _RecordingWindow()
    publisher.publish(window, "fim.onRunDone(1)")

    assert publisher.stop(5.0) is True
    assert window.received == ["fim.onRunDone(1)"]

    publisher.publish(window, "fim.onRunDone(2)")
    assert window.received == ["fim.onRunDone(1)"]


def test_stop_bounds_the_wait_when_delivery_cannot_finish() -> None:
    """A push that never returns makes `stop` report it did not drain in time."""
    publisher = Publisher()
    release = threading.Event()
    window = _BlockingWindow("fim.onRunDone(1)", release)
    publisher.publish(window, "fim.onRunDone(1)")
    wait_or_fail(window.entered, "delivery to begin the blocking push")

    try:
        assert publisher.stop(0.05) is False
    finally:
        release.set()


def test_on_delivered_fires_once_actual_delivery_is_attempted() -> None:
    """The hook `Api`'s own `on_message` is built from fires after the real push."""
    publisher = Publisher()
    window = _RecordingWindow()
    delivered = threading.Event()

    publisher.publish(window, "fim.onRunDone(1)", on_delivered=delivered.set)

    wait_or_fail(delivered, "on_delivered")
    assert window.received == ["fim.onRunDone(1)"]


def test_a_coalesced_away_entry_never_fires_its_own_on_delivered() -> None:
    """Only the surviving entry's own `on_delivered` fires, not the replaced one's."""
    publisher = Publisher()
    release = threading.Event()
    window = _BlockingWindow("gate", release)
    superseded_delivered = threading.Event()
    surviving_delivered = threading.Event()

    publisher.publish(window, "gate")
    wait_or_fail(window.entered, "delivery to reach the gate")

    publisher.publish(
        window,
        "fim.onRunProgress(1)",
        coalesce_key="run-progress",
        on_delivered=superseded_delivered.set,
    )
    publisher.publish(
        window,
        "fim.onRunProgress(2)",
        coalesce_key="run-progress",
        on_delivered=surviving_delivered.set,
    )

    release.set()
    wait_or_fail(surviving_delivered, "the surviving tick's own on_delivered")
    assert superseded_delivered.is_set() is False
    assert window.received == ["gate", "fim.onRunProgress(2)"]


def test_on_delivered_fires_even_when_the_push_itself_fails() -> None:
    """A test-only hook learns the attempt happened, whether or not it succeeded.

    Matches `Publisher._deliver`'s own documented trade-off: a failed
    push is logged and does not stop delivery of the rest, so a test-
    only observer built from `on_delivered` must not be left waiting
    forever just because the one page-side handler it is watching
    happens to be broken.
    """
    publisher = Publisher()
    window = _RaisingWindow("fim.onRunError(1)")
    delivered = threading.Event()

    publisher.publish(window, "fim.onRunError(1)", on_delivered=delivered.set)

    wait_or_fail(delivered, "on_delivered, despite the push raising")


def test_a_push_declined_after_stop_still_fires_on_delivered() -> None:
    """A push that loses the shutdown race still releases a blocking waiter.

    Regression test: `stop` can run on another thread between a producer
    deciding to publish and that producer's own `publish` call actually
    taking the lock -- `fim.gui.app._publish_and_wait` (every run's or
    batch's own terminal push, and a saved run's own pause/cancel push)
    blocks on exactly this entry's `on_delivered`, so a push declined
    after `stop` must call it anyway, synchronously, right here, or that
    caller hangs forever. Found live: `test_closing_pauses_a_running_
    saved_run_at_a_checkpoint` timed out at the 120 s completion backstop
    before this fix, with the run claim never released.
    """
    publisher = Publisher()
    window = _RecordingWindow()
    assert publisher.stop(5.0) is True

    delivered = threading.Event()
    publisher.publish(window, "fim.onRunPaused(1)", on_delivered=delivered.set)

    assert delivered.is_set() is True
    assert window.received == []
