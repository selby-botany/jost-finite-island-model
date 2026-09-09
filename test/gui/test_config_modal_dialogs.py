"""Static-analysis guard over this project's remaining `<dialog>` markup.

The six Configure-section modals this file's own name once described
are gone (botanist GUI redesign doc `20260907-claude-sonnet-5-botanist-
gui-redesign.md` §4: every field they held now lives directly on the
always-visible `screen-configure`); `modal-presets` is what remains
(`modal-save-preset` uses a real `<form method="dialog">` with its own
Accept/Cancel buttons instead, design §4.7 — neither is `[data-modal-
close]`). `index.html`'s own comment above `modal-presets` records why
its own "Cancel" button still needs an explicit `tabindex="0"` (a
dialog-fix report's own (b): this project's WKWebView host only
includes an element in the `Tab` order that the author explicitly
opted in with `tabindex`, unless the OS-level "Full Keyboard Access"
setting is on — without it, "Cancel" is reachable by pointer but not by
keyboard, exactly the gap `Tab`, `Tab`, ..., `Return` is supposed to
close). This test answers the same question `test_webui_global_scope.py`
already asks for a different invariant: in milliseconds, with no window
and no simulation run required, rather than only failing much later
inside a real keyboard-navigation session.
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
    Exactly two such buttons exist today (`modal-presets`'s own "Cancel"
    and `modal-preset-yaml`'s own "Close", design doc §10's examples-
    library YAML view) — asserted precisely, not merely "at least one,"
    so a dialog added later without this same opt-in is caught by this
    test changing count, not only by a missing `tabindex`.
    """
    markup = _INDEX_HTML.read_text(encoding="utf-8")
    buttons = _MODAL_CLOSE_BUTTON.findall(markup)

    assert len(buttons) == 2, (
        f"expected exactly 2 [data-modal-close] buttons, found {len(buttons)}"
    )
    for button in buttons:
        assert 'tabindex="0"' in button, f'missing tabindex="0": {button}'
