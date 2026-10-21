"""One thread delivers every push to the page, in order.

Before this module existed, every background thread that had something
to tell the page -- a run's drain thread, a batch's progress poller, the
sweep's own event callback, the reproducibility check -- called `window.
evaluate_js` directly, itself, and blocked on it
(`20261019-claude-opus-5-5-fim-gui-tabs-design-review.md`, finding M4).
The sweep's own callback did this while holding its own context lock
(`fim.sweep_run._Context.emit`), so a page slow to answer one tick's
`evaluate_js` round trip stalled every other point thread waiting on
that same lock, not merely the page.

A `Publisher` is the one place any of them has to touch `evaluate_js`
now. `publish` only ever appends to (or, for a coalescible push, updates
in place within) an in-memory list and returns; it never blocks on the
page. One background thread drains that list, delivering each entry in
turn, never while holding any lock that a producer might need to make
progress of its own.

Progress is the one kind of push that is fine to coalesce: only the
newest tick of a given `coalesce_key` is ever worth delivering, so an
older, still-undelivered one is simply replaced rather than queued
again. Everything else -- a terminal outcome, an annotation such as a
reproducibility result, a one-off status note -- is queued with no
`coalesce_key` at all, and is always delivered, in the order it was
published, never dropped and never reordered relative to another
non-coalesced push for the same window.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Hashable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class EvaluatesJs(Protocol):
    """Structural stand-in for the one capability a push actually needs.

    Matches `fim.gui.app._EvaluatesJs`: any real caller passes a real
    `webview.Window`, which already satisfies this; a test can pass a
    lightweight fake with no real window or `gui` marker needed.
    """

    def evaluate_js(self, script: str) -> Any: ...


class _Entry:
    """One queued push, pending delivery."""

    __slots__ = ("coalesce_key", "label", "on_delivered", "script", "window")

    def __init__(
        self,
        window: EvaluatesJs,
        script: str,
        coalesce_key: Hashable | None,
        label: str | None,
        on_delivered: Callable[[], None] | None,
    ) -> None:
        self.window = window
        self.script = script
        self.coalesce_key = coalesce_key
        self.label = label
        self.on_delivered = on_delivered


class Publisher:
    """Delivers every push handed to `publish`, from one background thread.

    One instance belongs to one `Api` (`Api.__init__`'s own `self.
    _publisher`), the same way `_close_gate` does — never a module-level
    singleton, so tests building several `Api` instances never share a
    delivery thread or queue. The thread itself starts lazily, on the
    first call to `publish`: most `Api` instances in this suite are
    built and never push anything at all, and starting a thread only
    once one is actually needed keeps those instances just as cheap as
    they were before this module existed.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._entries: list[_Entry] = []
        self._accepting = True
        self._thread: threading.Thread | None = None

    def publish(
        self,
        window: EvaluatesJs,
        script: str,
        *,
        coalesce_key: Hashable | None = None,
        label: str | None = None,
        on_delivered: Callable[[], None] | None = None,
    ) -> None:
        """Enqueue one push; returns at once, never calling `evaluate_js` itself.

        Args:
            window: Where `script` will eventually be delivered.
            script: The JavaScript to evaluate there.
            coalesce_key: When given, a still-undelivered entry already
                queued for the same `window` and the same key is
                overwritten by this one instead of being queued again --
                the right behavior for a progress tick, where only the
                newest is ever worth showing. `None` (the default)
                queues unconditionally, preserving arrival order against
                every other `None`-keyed push to the same window: the
                shape every terminal outcome, annotation, and one-off
                status note needs. A replaced entry's own `on_delivered`
                is simply dropped along with it -- never called -- since
                it never actually reached the page.
            label: Named in a delivery failure's log line, in place of
                `script`'s own handler name.
            on_delivered: Called from the delivery thread, with no
                arguments, immediately after this exact entry's own
                `evaluate_js` attempt returns or raises -- the hook
                `Api`'s own `on_message`/`on_run_started` test-only
                callbacks (see `Api.__init__`'s own docstring) are built
                from, so a test observing it still knows the real push
                it names has actually been attempted, in order, not
                merely accepted. Called whether or not that attempt
                succeeded: every real page already has every handler
                `fim.gui.webui` pushes to, so a failure here means the
                window itself is gone, which no test-only hook needs to
                distinguish from success. Called synchronously, on the
                calling thread, right here -- never queued at all -- if
                `stop` has already been called: `fim.gui.app._publish_
                and_wait` blocks on exactly this callback, and a push
                that arrives once `stop` has already won the shutdown
                race must still release that wait, since nothing further
                will ever be delivered to call it from.
        """
        with self._condition:
            if not self._accepting:
                declined = True
            elif coalesce_key is not None and self._replace_pending(
                window, coalesce_key, script, on_delivered
            ):
                return
            else:
                declined = False
                self._entries.append(
                    _Entry(window, script, coalesce_key, label, on_delivered)
                )
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._deliver, name="fim-gui-publisher", daemon=True
                    )
                    self._thread.start()
                self._condition.notify()
        if declined:
            logger.debug(
                "declined %s: no longer accepting pushes",
                label or script.split("(", 1)[0],
            )
            if on_delivered is not None:
                on_delivered()

    def _replace_pending(
        self,
        window: EvaluatesJs,
        coalesce_key: Hashable,
        script: str,
        on_delivered: Callable[[], None] | None,
    ) -> bool:
        """Overwrite a still-queued entry sharing `window` and `coalesce_key`.

        Must be called with `self._condition` already held. Returns
        whether a match was found and overwritten; the caller enqueues a
        new entry itself when this returns `False`.
        """
        for entry in self._entries:
            if entry.window is window and entry.coalesce_key == coalesce_key:
                entry.script = script
                entry.on_delivered = on_delivered
                self._condition.notify()
                return True
        return False

    def _deliver(self) -> None:
        """Pop and deliver one entry at a time, forever, until told to stop.

        Never holds `self._condition` across `evaluate_js` itself — the
        one invariant finding M4 exists to establish. A push that fails
        (a closed window, most concretely) is logged at `warning` and
        does not stop delivery of the rest; the same tolerance `fim.gui.
        app._check_reproducibility`/`_push_sweep_event`/`_push_sweep_
        error` already had on their own before this module existed.

        A run or batch drain's own pushes had no such tolerance before:
        a failed `evaluate_js` there used to propagate out of that
        drain's own dedicated thread, which `pytest`'s own thread-
        exception hook (`pyproject.toml`'s `filterwarnings`) promotes to
        a hard test failure. Centralizing delivery onto one thread for
        every job removes that specific promotion for a push failure,
        deliberately: letting one job's broken push kill the one thread
        every other job's pushes now also depend on would be a strictly
        worse failure mode than the sharp exception it replaces. No
        currently passing test exercises a push that actually fails --
        every real page already defines every handler pushed to -- so
        this is not known to have weakened any coverage in practice;
        flagged here as a deliberate, inspectable trade-off rather than
        left implicit.
        """
        while True:
            with self._condition:
                while not self._entries and self._accepting:
                    self._condition.wait()
                if not self._entries:
                    return
                entry = self._entries.pop(0)
            try:
                entry.window.evaluate_js(entry.script)
            except Exception:
                logger.warning(
                    "could not push %s",
                    entry.label or entry.script.split("(", 1)[0],
                    exc_info=True,
                )
            if entry.on_delivered is not None:
                entry.on_delivered()

    def stop(self, timeout: float) -> bool:
        """Stop accepting new pushes, then wait for the rest to be delivered.

        Called once, at shutdown (`Api.stop_active_work`), inside the
        same overall deadline that already bounds joining every worker
        thread — this only waits for the publisher's own backlog, which
        in practice is empty or near it, since nothing new is being
        produced once every worker has itself been asked to stop.

        Args:
            timeout: Longest wait for the queue to drain, in seconds.

        Returns:
            Whether every already-queued push was delivered before
            `timeout` ran out.
        """
        thread = self._thread
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()
