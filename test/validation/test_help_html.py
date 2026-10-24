"""Tests for heading anchors and freshness of generated in-app Help pages."""

from __future__ import annotations

import json
import re
import runpy
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import markdown_it
import pytest

from fim.statistics.catalog import CATALOG

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "dev" / "bin" / "generate-help-html"
REGISTRY = PROJECT_ROOT / "doc" / "help-topics.json"
HELP_DIR = PROJECT_ROOT / "src" / "fim" / "gui" / "webui" / "help"
WEBUI = PROJECT_ROOT / "src" / "fim" / "gui" / "webui"


def _registry() -> dict[str, list[dict[str, object]]]:
    """Read the help topic registry."""
    registry: dict[str, list[dict[str, object]]] = json.loads(
        REGISTRY.read_text(encoding="utf-8")
    )
    return registry


def _committed_pages() -> dict[str, str]:
    """Return every committed Help fragment by topic id."""
    return {
        page.stem: page.read_text(encoding="utf-8") for page in HELP_DIR.glob("*.html")
    }


def _block(source: str, start: str) -> str:
    """Return the text of a JavaScript object literal that begins at `start`."""
    return source.split(start, 1)[1].split("\n};", 1)[0]


@pytest.fixture(scope="module")
def inject_header_ids() -> Callable[[str], str]:
    """Load the Help generator without running its command-line entry point."""
    namespace = runpy.run_path(str(GENERATOR))
    inject: Callable[[str], str] = namespace["inject_header_ids"]
    return inject


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("`_read_only`", "_read_only"),
        ("`trailing_`", "trailing_"),
        ("`_both_`", "_both_"),
        ("`convergence_statistic`", "convergence_statistic"),
        ("_Notes_", "notes"),
        ("**Bold** and `code`", "bold-and-code"),
        ("A &amp; B", "a--b"),
        ("A &lt; B", "a--b"),
    ],
)
def test_help_heading_ids_preserve_rendered_text(
    inject_header_ids: Callable[[str], str], heading: str, anchor: str
) -> None:
    """Rendered code is literal text, not a second pass of Markdown emphasis."""
    renderer = markdown_it.MarkdownIt("gfm-like", {"html": True}).disable("linkify")
    rendered = renderer.render(f"### {heading}\n\n### {heading}\n")

    result = inject_header_ids(rendered)

    assert re.findall(r'<h3 id="([^"]+)">', result) == [anchor, f"{anchor}-1"]


def test_generated_help_is_current_and_local_links_resolve(tmp_path: Path) -> None:
    """Committed Help matches the generator and every local jump has a target."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--output-dir", str(tmp_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    generated = sorted(tmp_path.glob("*.html"))
    topic_ids = [str(topic["id"]) for topic in _registry()["topics"]]
    assert [page.name for page in generated] == sorted(f"{i}.html" for i in topic_ids)
    for manifest in ("topics.json", "search-index.json"):
        assert (tmp_path / manifest).read_text(encoding="utf-8") == (
            HELP_DIR / manifest
        ).read_text(encoding="utf-8"), manifest
    pages = {page.stem: page.read_text(encoding="utf-8") for page in generated}
    for page in generated:
        text = pages[page.stem]
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        fragments = set(re.findall(r'href="#([^"]+)"', text))
        assert fragments <= ids, (page.name, fragments - ids)
        committed = PROJECT_ROOT / "src" / "fim" / "gui" / "webui" / "help"
        assert text == (committed / page.name).read_text(encoding="utf-8")
        for link in re.findall(r"<a\b[^>]*>", text):
            attributes = dict(re.findall(r'data-fim-(help|anchor)="([^"]+)"', link))
            if "help" not in attributes:
                continue
            topic = attributes["help"]
            assert topic in pages, (page.name, topic)
            if "anchor" in attributes:
                assert f'id="{attributes["anchor"]}"' in pages[topic], (
                    page.name,
                    topic,
                    attributes["anchor"],
                )


def test_every_statistic_has_a_bundled_definition_and_implementation_link() -> None:
    """Every catalog row has a bundled definition and a resolvable API link."""
    namespace = runpy.run_path(str(GENERATOR))
    text = namespace["render_doc"]("statistics")
    sections = dict(
        re.findall(r'<h2 id="([^"]+)">.*?</h2>(.*?)(?=<h2|\Z)', text, re.DOTALL)
    )
    for entry in CATALOG:
        assert entry.help_anchor in sections, entry.key
        section = sections[entry.help_anchor]
        assert "data-fim-external=" in section, entry.key
        assert "/src/fim/API.md#fim.statistics." in section, entry.key


def test_statistics_api_links_resolve_without_redundant_guide_or_source_links() -> None:
    """The reference owns statistic definitions; API sections link to source."""
    source = (PROJECT_ROOT / "doc/statistics.md").read_text(encoding="utf-8")
    api = (PROJECT_ROOT / "src/fim/API.md").read_text(encoding="utf-8")
    anchors = re.findall(r"\]\(\.\./src/fim/API\.md#([^)]+)\)", source)
    assert anchors
    for anchor in anchors:
        assert f'<a id="{anchor}"></a>' in api, anchor
    assert "jost-differentiation-measures.md" not in source
    assert "[Source](" not in source


def test_derived_display_rows_have_bundled_definitions() -> None:
    """Non-catalog predictions and effective counts also resolve offline."""
    namespace = runpy.run_path(str(GENERATOR))
    text = namespace["render_doc"]("statistics")
    script = (PROJECT_ROOT / "src/fim/gui/webui/statistics-catalog.js").read_text(
        encoding="utf-8"
    )
    mapping = script.split("const STATISTIC_REFERENCE_ANCHORS = {", 1)[1].split(
        "};", 1
    )[0]
    anchors = re.findall(r': "([^"]+)"', mapping)
    assert anchors
    for anchor in anchors:
        assert f'id="{anchor}"' in text, anchor


def test_statistics_reference_is_regenerated_by_the_commit_hook() -> None:
    """A reference-only edit refreshes the bundled page before committing."""
    hook = (PROJECT_ROOT / "dev/git-hooks/pre-commit").read_text(encoding="utf-8")
    help_cases = hook.split("help_sources_staged=false", 1)[1].split(
        'if "${help_sources_staged}"', 1
    )[0]
    assert "doc/statistics.md" in help_cases


@pytest.mark.parametrize(
    ("expression", "element"),
    [
        (r"\frac{H_T-H_S}{1-H_S}", "mfrac"),
        (r"\sum_{i=1}^{d}w_i p_{i,a}", "munderover"),
        (r"\sqrt{J_XJ_Y}", "msqrt"),
        (r"{}^{H}D_S", "msup"),
        (r"\operatorname{TV}(p_i,p_{\mathrm{rest},i})", "msub"),
        (r"\TeX", "mpadded"),
    ],
)
def test_help_renders_tex_as_native_mathml(expression: str, element: str) -> None:
    """Fractions, sums, roots, and scripts are real math, not raw TeX."""
    namespace = runpy.run_path(str(GENERATOR))
    rendered = namespace["render_tex"](expression, display=True)
    root = ElementTree.fromstring(rendered)
    assert root.attrib["display"] == "block"
    assert root.find(f".//{{http://www.w3.org/1998/Math/MathML}}{element}") is not None
    assert "\\" not in "".join(root.itertext())


def test_statistics_equations_have_unique_visible_tags_and_no_raw_tex() -> None:
    """Every tagged source equation survives generation as offline display math."""
    namespace = runpy.run_path(str(GENERATOR))
    source = (PROJECT_ROOT / "doc/statistics.md").read_text(encoding="utf-8")
    tags = re.findall(r"\\tag\{([^}]+)\}", source)
    assert tags
    assert len(tags) == len(set(tags))
    rendered = namespace["render_doc"]("statistics")
    labels = re.findall(r'<span class="equation-tag">\(([^<]+)\)</span>', rendered)
    assert Counter(labels) == Counter(tags)
    assert rendered.count('display="block"') == len(tags)
    assert "language-math" not in rendered
    assert "\\" not in rendered
    assert "<script" not in rendered


def test_help_math_preserves_code_examples_and_literal_identifiers() -> None:
    """Math conversion never interprets the contents of code spans or fences."""
    namespace = runpy.run_path(str(GENERATOR))
    source = "`$H_T$`\n\n```python\nvalue = '$H_T$'\n```\n\n$H_{ST}$"
    rendered = namespace["render_inline_math"](source)
    assert "`$H_T$`" in rendered
    assert "```python\nvalue = '$H_T$'\n```" in rendered
    assert "<var>H</var><sub>ST</sub>" in rendered


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("H_S", "<var>H</var><sub>S</sub>"),
        ("H_{ST}", "<var>H</var><sub>ST</sub>"),
        ("N_i", "<var>N</var><sub>i</sub>"),
        ("L_1", "<var>L</var><sub>1</sub>"),
        ("n_replicates", "<var>n_replicates</var>"),
        ("initial_allele_count", "<var>initial_allele_count</var>"),
    ],
)
def test_inline_tex_subscripts_do_not_split_literal_identifiers(
    expression: str, expected: str
) -> None:
    """Single-character TeX subscripts render without mangling legacy names."""
    namespace = runpy.run_path(str(GENERATOR))
    assert namespace["render_math"](expression) == expected


def test_help_rejects_unsupported_tex_commands() -> None:
    """A new macro cannot silently degrade to visible backslash text."""
    namespace = runpy.run_path(str(GENERATOR))
    with pytest.raises(ValueError, match="Unsupported TeX command"):
        namespace["render_tex"](r"\unsupportedcommand{x}")


def test_registry_covers_every_desktop_guide_page_and_keeps_stable_ids() -> None:
    """Every `doc/gui/` page is a Help topic; long-standing ids still exist."""
    topics = _registry()["topics"]
    sources = {str(topic["source"]) for topic in topics}
    for page in (PROJECT_ROOT / "doc" / "gui").glob("*.md"):
        assert page.relative_to(PROJECT_ROOT).as_posix() in sources, page.name
    ids = {str(topic["id"]) for topic in topics}
    assert {"usage", "configuration", "statistics"} <= ids
    groups = {str(group["id"]) for group in _registry()["groups"]}
    assert {str(topic["group"]) for topic in topics} == groups


def test_commit_hook_regenerates_help_for_every_registered_source() -> None:
    """Editing any registered source refreshes the committed Help pages."""
    hook = (PROJECT_ROOT / "dev/git-hooks/pre-commit").read_text(encoding="utf-8")
    help_cases = hook.split("help_sources_staged=false", 1)[1].split(
        'if "${help_sources_staged}"', 1
    )[0]
    assert "doc/help-topics.json" in help_cases
    for topic in _registry()["topics"]:
        source = str(topic["source"])
        covered = source in help_cases or (
            source.startswith("doc/gui/") and "doc/gui/*.md" in help_cases
        )
        assert covered, source


def test_desktop_guide_links_resolve_relative_to_their_own_directory() -> None:
    """`../configuration.md` from `doc/gui/` stays inside Help."""
    page = (HELP_DIR / "gui-configure.html").read_text(encoding="utf-8")
    assert 'data-fim-help="configuration"' in page
    assert "blob/main/doc/gui/configuration.md" not in page
    assert "blob/main/configuration.md" not in page


def test_search_index_entries_point_at_real_sections() -> None:
    """Every search result opens an existing topic and heading."""
    pages = _committed_pages()
    index = json.loads((HELP_DIR / "search-index.json").read_text(encoding="utf-8"))
    assert index
    topics_with_entries = set()
    for entry in index:
        assert entry["topic"] in pages, entry
        assert entry["heading"], entry
        if entry["anchor"]:
            assert f'id="{entry["anchor"]}"' in pages[entry["topic"]], entry
        topics_with_entries.add(entry["topic"])
    assert topics_with_entries == set(pages)


def test_topic_manifest_omits_sources_and_lists_registry_order() -> None:
    """Help home reads titles and summaries, never repository paths."""
    manifest = json.loads((HELP_DIR / "topics.json").read_text(encoding="utf-8"))
    assert [topic["id"] for topic in manifest["topics"]] == [
        topic["id"] for topic in _registry()["topics"]
    ]
    for topic in manifest["topics"]:
        assert "source" not in topic
        assert topic["title"] and topic["summary"]


def test_every_configure_field_has_an_f1_help_target() -> None:
    """`FIELD_HELP` and the F1 map list the same keys, and anchors exist."""
    field_help = (WEBUI / "field-help.js").read_text(encoding="utf-8")
    field_keys = set(
        re.findall(r"^    (\w+):", _block(field_help, "const FIELD_HELP = {"), re.M)
    )
    help_js = (WEBUI / "screens" / "help.js").read_text(encoding="utf-8")
    mapping = _block(help_js, "const FIELD_HELP_TOPICS = {")
    targets: dict[str, tuple[str, str]] = {}
    for key, reference_anchor, topic, anchor in re.findall(
        r'(\w+): (?:configurationAnchor\(\s*"([^"]*)"\s*\)'
        r'|\{ topic: "([^"]+)", anchor: "([^"]*)" \})',
        mapping,
    ):
        targets[key] = (
            ("configuration", reference_anchor) if topic == "" else (topic, anchor)
        )
    assert set(targets) == field_keys
    pages = _committed_pages()
    for key, (topic, anchor) in targets.items():
        assert topic in pages, key
        assert f'id="{anchor}"' in pages[topic], (key, anchor)


def test_every_screen_help_target_exists() -> None:
    """F1 and "Help for this screen" open real screens' real topics."""
    help_js = (WEBUI / "screens" / "help.js").read_text(encoding="utf-8")
    index_html = (WEBUI / "index.html").read_text(encoding="utf-8")
    entries = re.findall(
        r'"(screen-[\w-]+)": \{\s*topic: "([^"]+)",\s*anchor: "([^"]*)"',
        _block(help_js, "const SCREEN_HELP = {"),
    )
    assert len(entries) >= 7
    pages = _committed_pages()
    for screen, topic, anchor in entries:
        assert f'id="{screen}"' in index_html, screen
        assert topic in pages, screen
        if anchor:
            assert f'id="{anchor}"' in pages[topic], (screen, anchor)
