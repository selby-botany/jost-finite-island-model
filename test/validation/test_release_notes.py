"""Tests for changelog-backed GitHub release notes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "build"
EXTRACTOR = PROJECT_ROOT / "dev" / "bin" / "extract-release-notes"
# The release jobs (`windows`, `publish`) live in `ci.yml` (R8 remediation),
# not a separate `release.yml`, so their `needs:` on `build` is structural
# rather than a race between two independently triggered workflows.
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


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


def test_release_jobs_cannot_run_without_a_passing_build_and_valid_tag() -> None:
    """`windows`/`publish` structurally depend on `build` and `verify-tag`.

    Regression test for R8: `windows` and `publish` used to live in a
    separate `release.yml`, triggered independently by the same tag push
    with no dependency on `ci.yml`'s `build` job at all — a tag could
    publish a release before CI had even started, let alone passed.
    Parsing the workflow YAML (rather than grepping for `needs:` as text)
    confirms the actual dependency graph GitHub Actions will enforce:
    `windows` cannot start until every `build` matrix leg and
    `verify-tag` have succeeded, and `publish` cannot start until
    `windows` has.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    windows_needs = jobs["windows"]["needs"]
    assert isinstance(windows_needs, list)
    assert set(windows_needs) == {"build", "verify-tag"}
    assert jobs["publish"]["needs"] == "windows"

    for job_name in ("verify-tag", "windows", "publish"):
        assert jobs[job_name]["if"] == "startsWith(github.ref, 'refs/tags/v')"


def test_verify_tag_job_checks_annotation_and_main_ancestry() -> None:
    """`verify-tag` rejects a lightweight tag and one not reachable from main.

    Regression test for R8: nothing previously checked that a release tag
    was an annotated tag (not a bare `git tag v1.2.3` ref) or that its
    commit was actually reachable from `main` — any ref matching `v*`,
    from any branch, published a release.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify-tag"]["steps"]
    run_steps = "\n".join(step["run"] for step in steps if "run" in step)
    checkout_step = next(
        step for step in steps if step.get("uses", "").startswith("actions/checkout")
    )

    assert checkout_step["with"]["fetch-depth"] == 0
    assert 'git rev-parse --verify --quiet "${GITHUB_REF_NAME}^{tag}"' in run_steps
    assert 'git merge-base --is-ancestor "${GITHUB_SHA}" origin/main' in run_steps


def test_ci_build_includes_slow_statistical_tests() -> None:
    """The authoritative release gate excludes only packaging-marked tests."""
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "'not packaging'" in build_script
    assert '"${ci}" && test_markers=' in build_script
