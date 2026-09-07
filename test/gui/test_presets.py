"""Unit tests for `fim.gui.presets` (no display, no `gui` marker).

`list_presets`/`get_preset` only ever read a plain HTML file from disk —
none of the pywebview machinery this package's other tests need.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fim.gui.presets import Preset, get_preset, list_presets

_REAL_WEBUI_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "src" / "fim" / "gui" / "webui"
)

# The seven "Worked examples" titles `doc/usage.md` documents today, in
# order — a snapshot, not a re-derivation: if a future edit adds,
# removes, or renames an example (and regenerates `usage.html` to
# match, `dev/bin/generate-help-html`'s own job), this list is meant to
# need updating right alongside it, the same "if it changes, a human
# notices and updates this test" precedent `test_starter_form_values_
# reflects_the_cli_starter_config` already sets for `STARTER_CONFIG`.
_EXPECTED_TITLES = (
    "Unequal island sizes with a migration hub",
    "Stepping-stone (spatial) migration",
    "Stochastic migrant counts",
    "Finite-length alleles (the K-allele model)",
    "Per-base mutation rate across unequal locus lengths",
    "Several convergence statistics",
    "An adaptive replicate batch with a confidence interval",
)

_REAL_PRESETS = list_presets(_REAL_WEBUI_DIRECTORY)


def test_list_presets_returns_the_seven_worked_examples() -> None:
    """Every `doc/usage.md` worked example is found, in its own document order.

    Reads the real, committed `webui/help/usage.html` directly — this
    is the one test proving that file (and this module's own parser)
    actually agree, not a synthetic fixture standing in for it.
    """
    assert tuple(preset.title for preset in _REAL_PRESETS) == _EXPECTED_TITLES
    for preset in _REAL_PRESETS:
        assert preset.yaml_text.strip() != ""
        assert preset.preset_id != ""


def test_get_preset_returns_the_matching_preset() -> None:
    """`get_preset` finds one preset by its own id, out of the real seven."""
    preset = get_preset(_REAL_WEBUI_DIRECTORY, "stepping-stone-spatial-migration")

    assert preset is not None
    assert preset.title == "Stepping-stone (spatial) migration"
    assert "topology: ring" in preset.yaml_text


def test_get_preset_returns_none_for_an_unknown_id() -> None:
    """An id naming no real preset is `None`, not a raised exception."""
    assert get_preset(_REAL_WEBUI_DIRECTORY, "not-a-real-preset") is None


def test_list_presets_returns_empty_for_a_directory_with_no_help_html(
    tmp_path: Path,
) -> None:
    """A missing `help/usage.html` (a stale install) is `[]`, not a crash."""
    assert list_presets(tmp_path) == []


def test_parser_scopes_to_the_worked_examples_section_only(tmp_path: Path) -> None:
    """A heading and YAML block outside the section are not mistaken for a preset.

    Direct regression coverage for a real bug found writing this
    module: the opening `<h2 id="worked-examples">Worked examples</h2>`
    tag's own *closing* tag was originally mistaken for the section's
    own end (both tags are on the same line, immediately adjacent),
    closing the section before a single `<h3>` inside it was ever
    reached — every real preset silently vanished (`list_presets`
    returned `[]` against the real file, not a subtly wrong single
    entry). This synthetic fixture puts one heading before the section,
    one correctly inside it, and one after — the exact shape that bug
    would get wrong in three different ways at once.
    """
    html_text = (
        "<h2 id='intro'>Intro</h2>"
        "<h3 id='not-a-preset'>Not a preset</h3>"
        "<pre><code class='language-yaml'>N: 0</code></pre>"
        "<h2 id='worked-examples'>Worked examples</h2>"
        "<h3 id='a'>Example A</h3>"
        "<pre><code class='language-yaml'>N: 1\nd: 2</code></pre>"
        "<h2 id='re-analyze-a-trajectory'>Re-analyze a trajectory</h2>"
        "<h3 id='not-a-preset-either'>Not a preset either</h3>"
        "<pre><code class='language-yaml'>N: 99</code></pre>"
    )
    (tmp_path / "help").mkdir()
    (tmp_path / "help" / "usage.html").write_text(html_text, encoding="utf-8")

    presets = list_presets(tmp_path)

    assert [preset.preset_id for preset in presets] == ["a"]
    assert presets[0].yaml_text == "N: 1\nd: 2"


@pytest.mark.parametrize(
    "preset", _REAL_PRESETS, ids=[p.preset_id for p in _REAL_PRESETS]
)
def test_every_preset_parses_as_yaml(preset: Preset) -> None:
    """Every real preset's own text is at least syntactically valid YAML.

    Whether it also validates as a full `SimulationParams` (six of the
    seven do; the seventh's own genuinely per-locus `mu` has no form
    representation, exactly like a hand-loaded YAML file with the same
    shape already does not) is `test/gui/test_app_api.py`'s own concern
    (`get_preset_form_values`), not this module's.
    """
    parsed = yaml.safe_load(preset.yaml_text)
    assert isinstance(parsed, dict)
    assert "N" in parsed
