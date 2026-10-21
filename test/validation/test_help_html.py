"""Tests for heading anchors and freshness of generated in-app Help pages."""

from __future__ import annotations

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
    assert [page.name for page in generated] == [
        "configuration.html",
        "statistics.html",
        "usage.html",
    ]
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
    text = namespace["render_doc"]("statistics.md")
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
    text = namespace["render_doc"]("statistics.md")
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
    rendered = namespace["render_doc"]("statistics.md")
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
