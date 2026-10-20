"""Tests for heading anchors and freshness of generated in-app Help pages."""

from __future__ import annotations

import re
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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
    """Every catalog row links to math, an API symbol, and source."""
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
        assert "/src/fim/statistics/" in section and ".py" in section, entry.key
        assert re.search(r'data-fim-external="[^"]+/doc/[^"]+\.md', section), entry.key


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
