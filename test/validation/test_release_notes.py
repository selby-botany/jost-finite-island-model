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


def test_publish_generates_a_checksum_manifest_before_release_create() -> None:
    """A consolidated checksum manifest covers every released artifact.

    Regression test for R11: only the Windows executable had a checksum
    (its own `.sha256` sidecar, documented in `README.md` for a Windows
    user's manual verification); the wheel and sdist `python -m build`
    produces had none at all. `SHA256SUMS` must be generated, from
    inside `dist/`, before `gh release create dist/*` runs (so it is
    itself one of the uploaded artifacts).
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    step_texts = [step.get("run", "") for step in steps]
    checksum_index = next(
        index for index, text in enumerate(step_texts) if "SHA256SUMS" in text
    )
    release_index = next(
        index for index, text in enumerate(step_texts) if "gh release create" in text
    )

    assert "sha256sum" in step_texts[checksum_index]
    assert checksum_index < release_index


def test_release_jobs_cannot_run_without_a_passing_build_and_valid_tag() -> None:
    """`windows`/`windows-arm64`/`publish` depend on `build` and `verify-tag`.

    Regression test for R8: `windows` and `publish` used to live in a
    separate `release.yml`, triggered independently by the same tag push
    with no dependency on `ci.yml`'s `build` job at all — a tag could
    publish a release before CI had even started, let alone passed.
    Parsing the workflow YAML (rather than grepping for `needs:` as text)
    confirms the actual dependency graph GitHub Actions will enforce:
    neither `windows` nor `windows-arm64` (PyInstaller cannot cross-compile,
    so building for both Windows architectures needs two independent jobs)
    can start until every `build` matrix leg and `verify-tag` have
    succeeded, and `publish` cannot start until both have.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    for job_name in ("windows", "windows-arm64"):
        job_needs = jobs[job_name]["needs"]
        assert isinstance(job_needs, list)
        assert set(job_needs) == {"build", "verify-tag"}
    publish_needs = jobs["publish"]["needs"]
    assert isinstance(publish_needs, list)
    assert set(publish_needs) == {"windows", "windows-arm64"}

    for job_name in ("verify-tag", "windows", "windows-arm64", "publish"):
        assert jobs[job_name]["if"] == "startsWith(github.ref, 'refs/tags/v')"


def test_contents_write_is_scoped_to_the_publish_job_only() -> None:
    """Only `publish` carries the elevated `contents: write` permission.

    Regression test for R13: `release.yml` set `contents: write` at the
    workflow level, so every job in the file — including the ones that
    only build and smoke-test, and never touch the repository or a
    release — ran with write access to repository contents it never
    needed. The workflow-level default is `contents: read`; `publish` is
    the only job (it calls `gh release create`) that overrides it.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    for job_name in ("build", "verify-tag", "windows", "windows-arm64"):
        assert "permissions" not in jobs[job_name]


def test_verify_tag_job_checks_annotation_and_main_ancestry() -> None:
    """`verify-tag` rejects a lightweight tag and one not reachable from main.

    Regression test for R8: nothing previously checked that a release tag
    was an annotated tag (not a bare `git tag v1.2.3` ref) or that its
    commit was actually reachable from `main` — any ref matching `v*`,
    from any branch, published a release.

    Regression test, second finding: the first live annotated-tag release
    (`v1.1.0`) failed this exact job even though the tag genuinely was
    annotated, because `actions/checkout`'s own fetch of the triggering
    ref force-overwrites the local `refs/tags/<name>` to point directly at
    `GITHUB_SHA`, stripping the annotation before this step ever runs. The
    fix re-fetches the tag under a name checkout never touches
    (`refs/tags/verify-tag-check`) rather than trusting the
    already-clobbered local ref.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify-tag"]["steps"]
    run_steps = "\n".join(step["run"] for step in steps if "run" in step)
    checkout_step = next(
        step for step in steps if step.get("uses", "").startswith("actions/checkout")
    )

    assert checkout_step["with"]["fetch-depth"] == 0
    assert '"+refs/tags/${GITHUB_REF_NAME}:refs/tags/verify-tag-check"' in run_steps
    assert (
        'git rev-parse --verify --quiet "refs/tags/verify-tag-check^{tag}"' in run_steps
    )
    assert 'git merge-base --is-ancestor "${GITHUB_SHA}" origin/main' in run_steps


def test_windows_artifact_has_a_short_retention() -> None:
    """Neither inter-job handoff artifact lingers past its purpose.

    Regression test for R14: `actions/upload-artifact` defaults to the
    repository's general retention setting (up to 90 days) with no
    `retention-days` override. The `windows`/`windows-arm64` artifacts
    exist only to hand each executable to `publish` within the same
    workflow run; their durable home is the GitHub Release `publish`
    creates from them, not this transient artifact.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job_name in ("windows", "windows-arm64"):
        upload_step = next(
            step
            for step in workflow["jobs"][job_name]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact")
        )
        assert upload_step["with"]["retention-days"] == 1


def test_publish_verifies_the_downloaded_checksum_before_shipping_it() -> None:
    """`publish` verifies both exes against their own `.sha256` before release.

    Regression test for R14: `publish` downloaded the Windows executable
    and its `.sha256` sidecar and re-shipped both without ever actually
    verifying they still matched each other — trusting the inter-job
    artifact hand-off blindly rather than checking it. Both the x64 and
    arm64 executables must be checked the same way.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    step_texts = [step.get("run", "") for step in steps]
    download_indices = [
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/download-artifact")
    ]
    assert len(download_indices) == 2, "expected one download per Windows artifact"
    verify_index = next(
        index for index, text in enumerate(step_texts) if "sha256sum -c" in text
    )
    release_index = next(
        index for index, text in enumerate(step_texts) if "gh release create" in text
    )

    assert "fim-windows-x64.exe.sha256" in step_texts[verify_index]
    assert "fim-windows-arm64.exe.sha256" in step_texts[verify_index]
    assert max(download_indices) < verify_index < release_index


def test_publish_rejects_a_malformed_tag_before_comparing_to_version_txt() -> None:
    """The tag/version check guards the tag's shape, not just its value.

    Regression test for R14: comparing `${GITHUB_REF_NAME#v}` to
    `version.txt` by bare string equality never confirmed the tag looked
    like `vX.Y.Z` in the first place — only that whatever followed the
    stripped `v` happened to equal the file's content.
    """
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["publish"]["steps"]
    verify_step = next(
        step for step in steps if "run" in step and "version.txt" in step["run"]
    )

    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in verify_step["run"]
    assert 'test "${GITHUB_REF_NAME#v}" = "$(cat version.txt)"' in verify_step["run"]


def test_ci_build_runs_every_test_marker() -> None:
    """The authoritative release gate applies no marker exclusion at all.

    Regression test for R17: `--ci` used to hard-code `not packaging`
    even in CI mode, so a test marked `packaging` (declared in
    `pyproject.toml` but, before this fix, carried by zero tests) could
    never run through any path a contributor or CI actually exercises.
    `--ci` now drops the `-m` marker filter entirely — the local default
    still excludes `slow`, `statistical`, and `packaging` for fast
    iteration, but the authoritative gate excludes nothing.
    """
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "pytest_marker_args=(-m 'not slow and not packaging')" in build_script
    assert '"${ci}" && pytest_marker_args=()' in build_script
