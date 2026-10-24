"""A finished batch says how it stopped and what its intervals mean.

The scalar results card explains its watched statistics in sentences. A
batch card used to show intervals in tooltips and nothing in prose, so
nothing said how many replicates ran or why the batch stopped. These tests
check the sentences as a pure function of a hand-built summary, then on a
real two-replicate batch.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import wait_or_fail

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

from .conftest import wait_for_run_view_ready
from .test_batch_results_screen import _SET_TINY_BATCH_FIELDS

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_MESSAGES = """
(() => {
    const interval = (mean, half) => ({
        mean: String(mean), low: String(mean - half), high: String(mean + half),
        sampleCount: 10,
    });
    const summary = {
        D: interval(0.25, 0.0125), G_ST: interval(0.125, 0.0625),
        H_S: interval(0.5, 0.25),
    };
    const base = {
        replicates: 10, cap: 200, minimum: 10, precision: 0.5, confidence: 0.95,
        method: "interval", stopEarly: true, statistics: ["D", "G_ST"],
    };
    const text = (messages) => messages.map((message) => message.text);
    return {
        met: batchMessagesFor(base, summary),
        unmet: text(batchMessagesFor({...base, precision: 0.01}, summary)),
        capped: text(batchMessagesFor(
            {...base, precision: 0.01, replicates: 200}, summary)),
        capMet: text(batchMessagesFor({...base, replicates: 200}, summary)),
        planned: text(batchMessagesFor(
            {...base, method: "planned_replicates"}, summary)),
        noStop: text(batchMessagesFor({...base, stopEarly: false}, summary)),
        one: text(batchMessagesFor({...base, replicates: 1}, summary)),
    };
})()
"""


def test_the_batch_sentences_say_how_the_batch_stopped(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Each way a batch can end has its own sentence."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_MESSAGES)

    # More than one replicate leads with the pooled-scatter sentence; the
    # stop sentence follows it.
    assert settled["met"][0]["text"] == (
        '10 replicates are overlaid in "Allele frequencies by deme pair". '
        "In each replicate, each deme's frequencies sum to 1."
    )
    assert settled["met"][1]["text"] == (
        "The batch ran 10 replicates and stopped because the 95% confidence "
        "interval for D and G_ST is within ±0.5."
    )
    assert settled["unmet"][1] == (
        "The batch ran all 10 replicates it allows and stopped at that limit: "
        "the 95% confidence interval for D and G_ST is still wider than "
        "±0.01."
    )
    assert settled["capped"][1].startswith("The batch ran all 200 replicates it allows")
    assert settled["capMet"][1] == (
        "The batch ran all 200 replicates it allows, and the 95% confidence "
        "interval for D and G_ST is within ±0.5."
    )
    assert settled["planned"][1] == (
        "The batch ran the 10 replicates you planned. Each ran long enough "
        "for the interval across them to reach ±0.5."
    )
    assert settled["noStop"][1] == (
        "The batch ran exactly 10 replicates, because stop_batch_early is off."
    )
    # Exactly one replicate: no pooling, so no pooled-scatter sentence and
    # the stop sentence stays at the head.
    assert "The batch ran 1 replicate and" in settled["one"][0]
    assert not settled["one"][0].startswith("1 replicate")


def test_the_batch_sentences_give_each_watched_interval(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Watched statistics first, then the meaning of an interval, then details."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_MESSAGES)

    messages = settled["met"]
    primary = [message["text"] for message in messages if not message.get("detail")]
    details = [message["text"] for message in messages if message.get("detail")]
    assert primary[0].startswith("10 replicates are overlaid")
    assert primary[2].startswith("The batch mean of D is 0.25")
    assert "95% confidence interval from" in primary[2]
    assert "D is a statistic the batch watches; that is within" in primary[2]
    assert "G_ST is a statistic the batch watches" in primary[3]
    assert primary[4].startswith("The interval is across replicates")
    assert "open a replicate" in primary[4]
    # Every shown statistic is explained in the collapsed group, watched first.
    assert details[0].startswith("D is allelic differentiation")
    assert details[1].startswith("G_ST is nearness to fixation")
    assert any(text.startswith("H_S is within-deme heterozygosity") for text in details)


def test_a_real_batch_explains_how_it_stopped(fast_batch_run_settings: Path) -> None:
    """A two-replicate batch's card carries the stop sentence and a details group."""
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            wait_or_fail(done_event, "batch (done, cancelled or error message)")
            outcome.put(
                window.evaluate_js(
                    "({items: Array.from(document.querySelectorAll("
                    "'#run-messages > li')).map((item) => item.textContent), "
                    "details: document.querySelectorAll("
                    "'#run-messages .run-message-details').length})"
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)

    # Two replicates means pooled: the pooled-scatter sentence leads, with
    # the stop sentence right after it.
    assert settled["items"][0].startswith("2 replicates are overlaid")
    assert settled["items"][1].startswith("The batch ran ")
    assert (
        " 2 replicate" in settled["items"][1]
        or "all 2 replicates" in settled["items"][1]
    )
    assert settled["details"] == 1
