"""Headless functional tests for the "About fim" dialog (Help menu).

Real DOM-driven proof that `webui/screens/config-modals.js`'s own
`showAboutModal` actually wires the page correctly —
`test/gui/test_app_api.py`'s own `test_get_about_info_names_the_installed_
version` already proves the bridge method itself is correct as a plain
Python call; this test proves the page's own JavaScript calls it and
fills the dialog's fields, which no Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

from fim import __dev_commit__ as fim_dev_commit
from fim import __version__ as fim_version

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def test_about_menu_shows_name_version_and_selby_attribution(
    window: webview.Window,
) -> None:
    """`fim.menu.about()` opens `modal-about`, filled from `Api.get_about_info`.

    The trigger wraps `fim.menu.about()` in `setTimeout(..., 0)`, matching
    `fim.gui.app._build_menu`'s own real dispatcher exactly — calling an
    `async` `fim.menu.*` method directly as a bare `evaluate_js` expression
    deadlocks (`test_input_screen.py`'s own
    `test_menu_new_configuration_resets_an_edited_field` docstring has the
    full mechanism).
    """
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
            window.evaluate_js("setTimeout(() => { window.fim.menu.about(); }, 0);")
            settled = _poll_until(
                "({"
                "dialogOpen: document.getElementById('modal-about').open, "
                "name: document.getElementById('about-name').textContent, "
                "version: document.getElementById('about-version').textContent, "
                "license: document.getElementById('about-license').textContent, "
                "brandingNote: document.getElementById("
                "'about-branding-note').textContent, "
                "copyrightYear: document.getElementById("
                "'about-copyright-year').textContent, "
                "organizationText: document.getElementById("
                "'about-organization-link').textContent, "
                "organizationUrl: document.getElementById("
                "'about-organization-link').dataset.fimAboutExternal, "
                "repositoryUrl: document.getElementById("
                "'about-repository-link').dataset.fimAboutExternal, "
                "logoSrc: document.querySelector('.about-logo').getAttribute('src'), "
                "activeElementIsCloseButton: "
                "document.activeElement === document.querySelector("
                "'#modal-about [data-modal-close]')"
                "})",
                lambda value: value is not None and value["dialogOpen"] is True,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["dialogOpen"] is True
    assert settled["name"] == "FIM"
    # `showAboutModal`'s own logic: a dev checkout's commit label is
    # appended in parentheses (`fim.__dev_commit__`, non-`None` for
    # every real `git` checkout this test itself runs from); `None` off
    # a release install/frozen build leaves the bare version alone.
    expected_version = (
        f"{fim_version} ({fim_dev_commit})" if fim_dev_commit else fim_version
    )
    assert settled["version"] == expected_version
    assert "AGPL" in settled["license"]
    assert "Marie Selby Botanical Gardens" in settled["brandingNote"]
    assert "not covered by this license" in settled["brandingNote"]
    assert settled["copyrightYear"] == "2026"
    assert settled["organizationText"] == "Marie Selby Botanical Gardens"
    assert settled["organizationUrl"] == "https://selby.org/botany/"
    assert "selby-botany/jost-finite-island-model" in settled["repositoryUrl"]
    assert settled["logoSrc"] == "assets/selby-orchid-logo.jpeg"
    # `showModal()`'s own default -- focus the first focusable element in
    # DOM order -- would otherwise land on `about-organization-link`
    # (found from a real screenshot: a focus ring around the
    # organization link the keyboard user never actually chose). The
    # Close button's own `autofocus` attribute claims that default
    # instead.
    assert settled["activeElementIsCloseButton"] is True
