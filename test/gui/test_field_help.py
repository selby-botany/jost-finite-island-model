"""Static-analysis guard over the Configure workspace's inline field
tooltips (botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
redesign.md` §4.6).

`webui/field-help.js`'s own `FIELD_HELP` object is the single content
source every tooltip draws from -- these tests check both directions of
the one invariant that keeps it honest: every key names a real Configure
field or mode-selector group this screen actually has, and every such
field or group this screen actually has is named by a real key. Static,
not DOM-driven (`test_config_modal_dialogs.py`'s own precedent): both
`index.html` and `field-help.js` are plain text on disk, so this answers
"did someone add a field without a tooltip, or leave a stale tooltip for
a field that no longer exists" in milliseconds, with no window and no
simulation run required, rather than only failing much later inside a
real hover/focus session.
"""

from __future__ import annotations

import re
from pathlib import Path

_WEBUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "fim" / "gui" / "webui"
_INDEX_HTML = _WEBUI_ROOT / "index.html"
_FIELD_HELP_JS = _WEBUI_ROOT / "field-help.js"

# Only inside `screen-configure` -- other screens' own `label[for="field-
# *"]`s (none exist today, but nothing stops one existing later) are out
# of this screen's own scope. A plain slice on the section's own
# start/end markers is enough: `index.html` has exactly one `screen-
# configure` section, confirmed by `test_screen_configure_exists_
# exactly_once` below, so slicing between its own open tag and the next
# `</section>` cannot silently grab the wrong region.
_CONFIGURE_SECTION_START = '<section id="screen-configure"'
_CONFIGURE_SECTION_END = "</section>"

_FIELD_LABEL = re.compile(r'<label for="field-([a-zA-Z0-9_]+)"')
_GROUP_LEGEND = re.compile(r'<legend data-field-help="([a-zA-Z0-9_]+)"')

# `field-help.js`'s own `FIELD_HELP` object keys -- a plain
# `    key: "..." +`/`    key: "text",`-shaped line for every entry
# (`field-help.js`'s own multi-line string-concatenation style), so a
# bare "starts a new object property" match is enough; no need to parse
# the object as real JS.
_FIELD_HELP_KEY = re.compile(r'^\s{4}([a-zA-Z0-9_]+):\s*"', re.MULTILINE)


def _configure_section_html() -> str:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    start = html.index(_CONFIGURE_SECTION_START)
    end = html.index(_CONFIGURE_SECTION_END, start)
    return html[start:end]


def _configure_field_and_group_keys() -> set[str]:
    """Every `field-<key>` label and `data-field-help="<key>"` legend key."""
    section = _configure_section_html()
    return set(_FIELD_LABEL.findall(section)) | set(_GROUP_LEGEND.findall(section))


def _field_help_keys() -> set[str]:
    return set(_FIELD_HELP_KEY.findall(_FIELD_HELP_JS.read_text(encoding="utf-8")))


def test_screen_configure_exists_exactly_once() -> None:
    """`_configure_section_html`'s own slicing assumption holds.

    A second `screen-configure` (or a first one removed entirely) would
    make the `str.index` calls above silently return the wrong slice --
    checked directly here rather than trusted implicitly.
    """
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert html.count(_CONFIGURE_SECTION_START) == 1


def test_every_field_help_key_names_a_real_configure_field_or_group() -> None:
    """No `FIELD_HELP` entry is stale -- every key matches a real field/group.

    Catches a field renamed or removed after its own tooltip was
    written, left behind as a key nothing ever looks up.
    """
    stale_keys = _field_help_keys() - _configure_field_and_group_keys()

    assert stale_keys == set(), (
        f"FIELD_HELP has entries for fields/groups Configure does not have: "
        f"{sorted(stale_keys)}"
    )


def test_every_configure_field_and_group_has_a_tooltip() -> None:
    """Every Configure field/group has a `FIELD_HELP` entry -- none forgotten.

    Catches a field added to Configure later without a matching tooltip
    -- design §4.6's own "every field carries a hover/focus tooltip,"
    not "most fields."
    """
    missing_keys = _configure_field_and_group_keys() - _field_help_keys()

    assert missing_keys == set(), (
        f"Configure has fields/groups with no FIELD_HELP entry: {sorted(missing_keys)}"
    )
