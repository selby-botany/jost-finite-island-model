"""Tests for changelog-backed GitHub release notes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "build"
EXTRACTOR = PROJECT_ROOT / "dev" / "bin" / "extract-release-notes"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"


def _run_extractor(
    changelog: Path,
    tag: str,
) -> subprocess.CompletedProcess[str]:
    """Run the release-note extractor against one changelog."""
    return subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            tag,
            "--changelog",
            str(changelog),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_extractor_returns_only_matching_release(tmp_path: Path) -> None:
    """The requested version body excludes adjacent release sections."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [Unreleased]",
                "",
                "- Future change.",
                "",
                "## [1.1.0] - 2026-09-01",
                "",
                "### Added",
                "",
                "- New release.",
                "",
                "## [1.0.0] - 2026-08-14",
                "",
                "### Added",
                "",
                "- First release.",
                "",
                "[Unreleased]: https://example.invalid/compare/v1.1.0...HEAD",
                "[1.1.0]: https://example.invalid/releases/v1.1.0",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_extractor(changelog, "v1.1.0")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "### Added\n\n- New release.\n"

    first = _run_extractor(changelog, "v1.0.0")

    assert first.returncode == 0, first.stderr
    assert first.stdout == "### Added\n\n- First release.\n"


def test_extractor_rejects_missing_release(tmp_path: Path) -> None:
    """A tag without a changelog section fails rather than publishing blanks."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [1.0.0] - 2026-08-14\n\n- First release.\n",
        encoding="utf-8",
    )

    result = _run_extractor(changelog, "v2.0.0")

    assert result.returncode == 1
    assert "CHANGELOG.md has no [2.0.0] release section" in result.stderr


def test_release_workflow_uses_changelog_notes() -> None:
    """GitHub releases use changelog notes and a non-conflicting build path."""
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "pyinstaller --workpath .pyinstaller-build --noconfirm" in workflow
    assert "$actualVersion -ne $expectedVersion" in workflow
    assert "Compare-Object $expectedArtifacts $actualArtifacts" in workflow
    assert "dev/bin/extract-release-notes" in workflow
    assert "--notes-file release-notes.md" in workflow
    assert "--generate-notes" not in workflow


def test_ci_build_includes_slow_statistical_tests() -> None:
    """The authoritative release gate excludes only packaging-marked tests."""
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "'not packaging'" in build_script
    assert '"${ci}" && test_markers=' in build_script
