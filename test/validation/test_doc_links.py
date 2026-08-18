"""Tests for the offline Markdown link checker."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "dev" / "bin" / "check-doc-links"


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    """Copy and run the checker against an isolated documentation tree."""
    checker = root / "dev" / "bin" / "check-doc-links"
    checker.parent.mkdir(parents=True)
    shutil.copy2(CHECKER, checker)
    return subprocess.run(
        [sys.executable, str(checker)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_checker_accepts_valid_links_and_github_dash_slugs(
    tmp_path: Path,
) -> None:
    """Valid links include GitHub-style em-dash and en-dash anchors."""
    (tmp_path / "doc").mkdir()
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "# Project",
                "",
                "[Section](doc/guide.md#part-i--ground-floor)",
                "[Years](doc/guide.md#years-20082011)",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "doc" / "guide.md").write_text(
        (
            "# Guide\n\n"
            "## Part I \N{EM DASH} Ground floor\n\n"
            "## Years 2008\N{EN DASH}2011\n"
        ),
        encoding="utf-8",
    )
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "README.md").write_text("# Ignored cache file\n", encoding="utf-8")

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout
        == "All local Markdown links, anchors, and documents are connected\n"
    )


def test_checker_accepts_a_code_span_heading_with_an_underscore(
    tmp_path: Path,
) -> None:
    """A code-span heading's underscore is literal identifier text, not markup.

    GitHub's own slugger does not treat an underscore inside `` `code` ``
    as emphasis syntax; a heading like `` ### `convergence_statistic` ``
    anchors at ``#convergence_statistic``, underscore intact.
    """
    (tmp_path / "doc").mkdir()
    (tmp_path / "README.md").write_text(
        "# Project\n\n[Key](doc/reference.md#convergence_statistic)\n",
        encoding="utf-8",
    )
    (tmp_path / "doc" / "reference.md").write_text(
        "# Reference\n\n### `convergence_statistic`\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr


def test_checker_rejects_missing_file(tmp_path: Path) -> None:
    """A local link to an absent file fails with the source path."""
    (tmp_path / "README.md").write_text(
        "# Project\n\n[Missing](doc/missing.md)\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "README.md: missing target doc/missing.md" in result.stderr


def test_checker_rejects_missing_anchor(tmp_path: Path) -> None:
    """A local link to an absent heading anchor fails precisely."""
    (tmp_path / "README.md").write_text(
        "# Project\n\n[Missing](#not-present)\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "README.md: missing anchor #not-present" in result.stderr


def test_checker_rejects_orphan_document(tmp_path: Path) -> None:
    """A non-design document without an incoming link is rejected."""
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "orphan.md: orphan document" in result.stderr
