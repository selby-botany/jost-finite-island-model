"""Static-analysis guard over the Configure menu's own `<dialog>` markup.

`index.html`'s own comment above the six `modal-*` dialogs records why
each "Close" button needs an explicit `tabindex="0"` (a dialog-fix
report's own (b): this project's WKWebView host only includes an
element in the `Tab` order that the author explicitly opted in with
`tabindex`, unless the OS-level "Full Keyboard Access" setting is on —
without it, "Close" is reachable by pointer but not by keyboard, exactly
the gap `Tab`, `Tab`, ..., `Return` is supposed to close). This test
answers the same question `test_webui_global_scope.py` already asks for
a different invariant: in milliseconds, with no window and no simulation
run required, rather than only failing much later inside a real keyboard-
navigation session.
"""

from __future__ import annotations

import re
from pathlib import Path

_INDEX_HTML = (
    Path(__file__).resolve().parents[2] / "src" / "fim" / "gui" / "webui" / "index.html"
)

_MODAL_CLOSE_BUTTON = re.compile(r"<button[^>]*\bdata-modal-close\b[^>]*>")


def test_every_modal_close_button_has_an_explicit_tabindex() -> None:
    """Every `[data-modal-close]` button carries `tabindex="0"`.

    A `<button>` is natively focusable, but this project's own WKWebView
    host does not include it in the `Tab` order without this explicit
    opt-in (see this module's own docstring) — a future dialog copied
    from an existing one without it would silently reintroduce the gap.
    """
    markup = _INDEX_HTML.read_text(encoding="utf-8")
    buttons = _MODAL_CLOSE_BUTTON.findall(markup)

    assert len(buttons) >= 6, (
        f"expected at least 6 Configure modal close buttons, found {len(buttons)}"
    )
    for button in buttons:
        assert 'tabindex="0"' in button, f'missing tabindex="0": {button}'
