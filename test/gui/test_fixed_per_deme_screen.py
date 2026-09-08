"""Headless functional tests for the "fixed per deme" initial-condition
preview (botanist GUI design doc
`20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.3).

Real DOM-driven proof that `webui/screens/fixed-per-deme.js` actually
computes and shows the right preview text as `d`, the loci
configuration, and the checked sub-choice change —
`test/gui/test_config_form.py`'s own
`test_initial_conditions_to_payload_fixed_per_deme_*` tests already
prove `initial_conditions_to_payload`/`_fixed_per_deme_p0` correct as
plain Python calls; these tests prove the page's own JavaScript reaches
the right mode/choice through the real form and that a submitted run
actually uses the expanded `p_0`.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def _set_field(name: str, value: str) -> str:
    """One `setField`-shaped JS statement (`test_running_screen.py`'s own helper)."""
    return (
        f"(function() {{ const field = document.getElementById('field-{name}'); "
        f"field.value = '{value}'; "
        "field.dispatchEvent(new Event('input', {bubbles: true})); })();"
    )


def test_selecting_fixed_per_deme_mode_shows_the_default_all_same_preview(
    window: webview.Window,
) -> None:
    """Switching to fixed-per-deme mode previews the default "all same" choice."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "3"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="fixed_per_deme"]\').click();'
            )
            settled = _poll_until(
                "document.getElementById('fixed-per-deme-preview').textContent",
                lambda value: value is not None and value != "",
            )
            outcome.put({"previewText": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["previewText"] == "Fixes all 3 deme(s) for allele 0."


def test_choosing_all_different_previews_each_demes_own_allele(
    window: webview.Window,
) -> None:
    """ "All different" previews deme *i* fixed for allele *i*, for the current `d`."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "3"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="fixed_per_deme"]\').click();'
            )
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="fixed_per_deme_choice"]'
                '[value="all_different"]\').click();'
            )
            settled = _poll_until(
                "document.getElementById('fixed-per-deme-preview').textContent",
                lambda value: (
                    value not in (None, "", "Fixes all 3 deme(s) for allele 0.")
                ),
            )
            outcome.put({"previewText": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["previewText"] == (
        "Fixes: deme 1 → allele 0; deme 2 → allele 1; deme 3 → allele 2."
    )


def test_choosing_all_but_one_previews_the_last_demes_own_distinct_allele(
    window: webview.Window,
) -> None:
    """ "All but one" previews every deme but the last fixed for allele 0."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "4"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="fixed_per_deme"]\').click();'
            )
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="fixed_per_deme_choice"]'
                '[value="all_but_one"]\').click();'
            )
            settled = _poll_until(
                "document.getElementById('fixed-per-deme-preview').textContent",
                lambda value: value is not None and value != "",
            )
            outcome.put({"previewText": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert (
        result["previewText"] == "Fixes deme(s) 1-3 for allele 0, deme 4 for allele 1."
    )


def test_changing_d_updates_the_preview_live(window: webview.Window) -> None:
    """Growing `d` while fixed-per-deme mode is active recomputes the preview."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="fixed_per_deme"]\').click();'
            )
            _poll_until(
                "document.getElementById('fixed-per-deme-preview').textContent",
                lambda value: value == "Fixes all 2 deme(s) for allele 0.",
            )
            window.evaluate_js(_set_field("d", "5"))
            settled = _poll_until(
                "document.getElementById('fixed-per-deme-preview').textContent",
                lambda value: value == "Fixes all 5 deme(s) for allele 0.",
            )
            outcome.put({"previewText": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["previewText"] == "Fixes all 5 deme(s) for allele 0."


def test_a_real_run_with_fixed_per_deme_all_different_completes() -> None:
    """A run submitted in fixed-per-deme "all different" mode actually completes.

    Same event-driven "wait on a real `threading.Event`, never poll a
    live background run" shape `test_running_screen.py`'s own real-run
    tests already use, for the identical reason those tests' own
    docstrings record.
    """
    started_event = threading.Event()
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_run_started() -> None:
        started_event.set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(
        api=Api(on_run_started=on_run_started, on_message=on_message), hidden=True
    )
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("N", "20"))
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(_set_field("seed", "20260814"))
            window.evaluate_js(_set_field("mu_value", "0.01"))
            window.evaluate_js(_set_field("locus_lengths", "200"))
            window.evaluate_js(_set_field("convergence_window", "4"))
            window.evaluate_js(_set_field("convergence_tolerance", "1.0"))
            window.evaluate_js(_set_field("max_generations", "10"))
            window.evaluate_js(_set_field("n_replicates", "1"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="fixed_per_deme"]\').click();'
            )
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="fixed_per_deme_choice"]'
                '[value="all_different"]\').click();'
            )
            window.evaluate_js("document.getElementById('run-button').click();")
            settled = None
            if done_event.wait(timeout=30.0):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "outcomeText: document.getElementById('results-outcome')"
                    ".textContent"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=40.0)

    assert settled is not None, (
        f"done_event was never set (start_run called: "
        f"{started_event.is_set()}, messages: {messages!r})"
    )
    assert settled["runViewState"] == "completed"
    assert settled["outcomeText"] != ""
    assert messages[-1][0] == "done"
