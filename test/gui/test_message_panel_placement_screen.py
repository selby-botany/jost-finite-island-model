"""The run messages sit between the graphs and the results tables.

The messages explain a result (why the run stopped, what each statistic's
average is, what a converged line still does). They used to be the last
element on the page, below every table, where they were rarely seen. They
now sit directly under the graph row and above the full-width results
tables, with `run-reason` still last.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_ORDER = """
(() => {
    const follows = (earlier, later) => Boolean(
        document.getElementById(earlier).compareDocumentPosition(
            document.getElementById(later)) & Node.DOCUMENT_POSITION_FOLLOWING);
    return {
        messagesAfterGraphRow: follows('run-plot-row', 'run-messages'),
        messagesBeforeBatchTable: follows('run-messages', 'batch-results-table'),
        messagesBeforeRunTable: follows('run-messages', 'run-results-table'),
        reasonAfterMessages: follows('run-messages', 'run-reason'),
        sameParentAsReason: document.getElementById('run-messages').parentElement
            === document.getElementById('run-reason').parentElement,
    };
})()
"""


def test_the_messages_sit_between_the_graphs_and_the_tables(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Graph row, then messages, then the results tables, then the reason."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_ORDER)

    assert settled == {
        "messagesAfterGraphRow": True,
        "messagesBeforeBatchTable": True,
        "messagesBeforeRunTable": True,
        "reasonAfterMessages": True,
        "sameParentAsReason": True,
    }
