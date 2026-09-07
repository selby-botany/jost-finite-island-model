"""Parse the "Worked examples" section of the bundled usage guide into
selectable Configure presets (botanist GUI design doc
`20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.5, `selby/restricted`).

`doc/usage.md` already documents seven complete, runnable configurations
under "Worked examples" — each demonstrating one `configuration.md`
option, worked through by hand and committed once, nowhere else.
`dev/bin/generate-help-html` already renders that same file into
`webui/help/usage.html` at commit time (the Help screen's own content
source, `screens/help.js`), and `packaging/fim.spec` already bundles the
whole `webui/` tree into every packaged executable — so that rendered
HTML, not `doc/usage.md` itself, is the one artifact guaranteed to exist
both in a development checkout and inside a frozen `.exe`/`.app`
(`doc/usage.md` is not bundled on its own; only `webui/` is).

This module reads that already-bundled HTML back, rather than embedding
a second, hand-copied set of example configurations the way `fim.cli.
STARTER_CONFIG` embeds its own single starter scenario: `doc/usage.md`
stays the one and only place these seven examples are written, exactly
as `dev/bin/generate-help-html`'s own "never hand-edit the generated
file" rule already establishes for the HTML itself, one level up.
Staleness between `doc/usage.md` and the committed `usage.html` is
already a generic, existing gate (`dev/git-hooks/pre-push`/`./build
--ci`, `dev/bin/generate-help-html --help`'s own documented purpose for
`--output-dir`) — nothing here duplicates that. `test/gui/test_presets.
py`'s own `test_list_presets_returns_the_seven_worked_examples` instead
guards this module's own parser against the real, committed file
directly, by name and count, the same "if it changes, a human notices
and updates this test" precedent `test_starter_form_values_reflects_
the_cli_starter_config` already sets for `STARTER_CONFIG`.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

_WORKED_EXAMPLES_SECTION_ID = "worked-examples"


@dataclass(frozen=True, slots=True)
class Preset:
    """One worked example: its stable id, display title, and raw YAML text.

    Args:
        preset_id: The heading's own HTML `id` attribute (`dev/bin/
            generate-help-html`'s own GitHub-style slug of the title) —
            stable across a regeneration as long as the title itself
            does not change, and already unique (one `<h3>` per
            example, `doc/usage.md`'s own structure).
        title: The example's own heading text, plain (no embedded
            `<code>` markup — none of the seven titles use any).
        yaml_text: The example's own complete YAML configuration, exactly
            as `doc/usage.md` presents it — ready for `yaml.safe_load`.
    """

    preset_id: str
    title: str
    yaml_text: str


class _WorkedExamplesParser(HTMLParser):
    """Extract every `(id, title, yaml)` triple from one `<h2>` section.

    Built once per `list_presets`/`get_preset` call rather than kept
    reusable: `HTMLParser` instances are cheap, and a fresh one avoids
    any doubt about stale state across repeated `feed()` calls, which no
    call site here ever needs to make more than once anyway.
    """

    def __init__(self, section_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._section_id = section_id
        self._in_section = False
        self._in_heading = False
        self._in_yaml_block = False
        self._current_id: str | None = None
        self._current_title_parts: list[str] = []
        self._current_yaml_parts: list[str] = []
        self.presets: list[Preset] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "h2":
            self._in_section = attr_map.get("id") == self._section_id
            return
        if not self._in_section:
            return
        if tag == "h3":
            self._in_heading = True
            self._current_id = attr_map.get("id")
            self._current_title_parts = []
            return
        if tag == "code" and (attr_map.get("class") or "") == "language-yaml":
            self._in_yaml_block = True
            self._current_yaml_parts = []

    def handle_endtag(self, tag: str) -> None:
        # No `h2`-close handling here on purpose: the opening `<h2
        # id="worked-examples">` tag's own closing `</h2>` (the same
        # element, one line later) would otherwise end the section
        # before a single `<h3>` inside it is ever reached. `handle_
        # starttag`'s own `h2` branch already reassigns `_in_section` on
        # every `<h2>` *start* tag it sees — `True` for this section's
        # own opening tag, `False` the moment the next section's `<h2
        # id="re-analyze-a-trajectory">` starts — which is both
        # necessary and sufficient, with no lookahead needed.
        if tag == "h3" and self._in_heading:
            self._in_heading = False
        elif tag == "code" and self._in_yaml_block:
            self._in_yaml_block = False
            if self._current_id is not None:
                self.presets.append(
                    Preset(
                        preset_id=self._current_id,
                        title="".join(self._current_title_parts).strip(),
                        yaml_text="".join(self._current_yaml_parts),
                    )
                )

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._current_title_parts.append(data)
        elif self._in_yaml_block:
            self._current_yaml_parts.append(data)


def _parse_worked_examples(html_text: str) -> list[Preset]:
    """Return every worked-example preset found in `html_text`, in document order."""
    parser = _WorkedExamplesParser(_WORKED_EXAMPLES_SECTION_ID)
    parser.feed(html_text)
    return parser.presets


def list_presets(webui_directory: Path) -> list[Preset]:
    """Return every worked-example preset bundled at `webui_directory`.

    Args:
        webui_directory: `fim.gui.app._webui_directory()`'s own return
            value — the directory holding `index.html` and `help/
            usage.html`, frozen or not.

    Returns:
        One `Preset` per `doc/usage.md` "Worked examples" `<h3>`
        section, in the same order the guide presents them. Empty if
        `help/usage.html` is missing or has no such section — callers
        treat that as "no presets available" (a stale or hand-modified
        install), not a reason to fail the Configure screen outright.
    """
    usage_html_path = webui_directory / "help" / "usage.html"
    try:
        html_text = usage_html_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _parse_worked_examples(html_text)


def get_preset(webui_directory: Path, preset_id: str) -> Preset | None:
    """Return one preset by its own id, or `None` if no such preset exists.

    Args:
        webui_directory: Same as `list_presets`.
        preset_id: A `Preset.preset_id` from a prior `list_presets` call.
    """
    for preset in list_presets(webui_directory):
        if preset.preset_id == preset_id:
            return preset
    return None
