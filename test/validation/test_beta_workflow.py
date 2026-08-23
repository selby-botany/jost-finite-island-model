"""Static checks on the beta build workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BETA_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "beta.yml"


def test_beta_workflow_exists() -> None:
    """The file is a real, committed part of the repository, not a per-branch add-on.

    `beta.yml` lives on every branch, exactly like `ci.yml` -- what makes
    it "the staging beta pipeline" is its own trigger condition below,
    not the file's presence or absence on any one branch.
    """
    assert BETA_WORKFLOW.is_file()


def test_beta_workflow_is_gated_to_staging_not_dev_or_main() -> None:
    """Only a push to `staging` (or a manual dispatch) triggers a beta build.

    The gate lives inside the workflow's own trigger condition, not in
    whether the file exists on a given branch -- `dev` and `main` both
    carry this identical file and never trigger it, since neither name
    appears in `push.branches`.
    """
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    on = workflow[True]  # PyYAML parses the bare `on:` key as the boolean True.

    assert on["push"]["branches"] == ["staging"]
    assert "workflow_dispatch" in on


def test_beta_artifacts_are_never_mistaken_for_a_real_release() -> None:
    """Every beta-built executable/archive filename carries a `-beta` suffix.

    A tester downloading `fim-windows-x64-beta.exe` cannot mistake it for
    `fim-windows-x64.exe`, the real release `ci.yml`'s own `publish` job
    ships -- distinct at the filename level, not only in the release's
    own prerelease flag.
    """
    text = BETA_WORKFLOW.read_text(encoding="utf-8")

    for name in (
        "fim-windows-x64-beta.exe",
        "fim-windows-arm64-beta.exe",
        "fim-macos-arm64-beta.dmg",
        "fim-macos-x64-beta.dmg",
        "fim-linux-x64-beta",
    ):
        assert name in text


def test_publish_beta_marks_the_release_as_a_prerelease() -> None:
    """`gh release create` passes `--prerelease`, never omitted.

    A beta build must never appear as a real release on the project's
    own GitHub Releases page.
    """
    text = BETA_WORKFLOW.read_text(encoding="utf-8")

    assert "--prerelease" in text


def test_linux_beta_job_matches_ci_ymls_own_gtk_dependency_list() -> None:
    """`linux-beta-x64` carries the same GTK/WebKit build toolchain `ci.yml` does.

    Regression guard: this job originally shipped with only `binutils`
    (predating this codebase's own pywebview GUI), which fails
    `pip install -e ".[dev]"` outright -- `pyproject.toml`'s
    `pywebview[gtk]` extra needs a C compiler and GI/Cairo headers to
    build. `ci.yml`'s own `linux-x64` job already carries the correct,
    real-build-verified list; this job must not drift from it.
    """
    text = BETA_WORKFLOW.read_text(encoding="utf-8")

    for package in (
        "gcc",
        "libgirepository1.0-dev",
        "libcairo2-dev",
        "libwebkit2gtk-4.0-37",
        "xvfb",
        "xauth",
    ):
        assert package in text
