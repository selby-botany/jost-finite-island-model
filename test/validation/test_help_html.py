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
    assert [page.name for page in generated] == ["configuration.html", "usage.html"]
    for page in generated:
        text = page.read_text(encoding="utf-8")
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        fragments = set(re.findall(r'href="#([^"]+)"', text))
        assert fragments <= ids, (page.name, fragments - ids)
        committed = PROJECT_ROOT / "src" / "fim" / "gui" / "webui" / "help"
        assert text == (committed / page.name).read_text(encoding="utf-8")
