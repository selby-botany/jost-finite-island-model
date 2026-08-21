"""Static checks on the beta-build workflow (design doc
20260821-claude-sonnet-5-windows-beta-build-pipeline.md, extended to
every platform ci.yml's own release path now builds)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BETA_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "beta.yml"
PLATFORM_JOBS = (
    "windows-beta-x64",
    "windows-beta-arm64",
    "macos-beta-arm64",
    "macos-beta-x64",
    "linux-beta-x64",
)


def test_beta_workflow_triggers_on_staging_push_and_dispatch() -> None:
    """Only a `staging` push or a manual dispatch starts this workflow.

    Never a tag: widening `refs/tags/v*` to also cover a branch push
    would break `publish` in ci.yml (it hard-validates `GITHUB_REF_NAME`
    against `^v[0-9]+\\.[0-9]+\\.[0-9]+$`), which is exactly why this is
    a separate workflow rather than a broadened trigger on the existing
    one.
    """
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML resolves a bare `on:` key as the boolean True (YAML 1.1's
    # on/off/yes/no boolean words) rather than the string "on" -- not a
    # bug in this test, confirmed against a real parse of this exact
    # file before writing the lookup this way.
    triggers = workflow[True]

    assert triggers["push"]["branches"] == ["staging"]
    assert "workflow_dispatch" in triggers
    assert "tags" not in triggers["push"]


def test_every_platform_job_depends_on_the_computed_label() -> None:
    """Every platform build waits on `compute-beta-label` before running."""
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    for job_name in PLATFORM_JOBS:
        assert jobs[job_name]["needs"] == "compute-beta-label"


def test_publish_beta_needs_every_platform_job() -> None:
    """`publish-beta` cannot start until every platform build has."""
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    publish_needs = jobs["publish-beta"]["needs"]

    assert isinstance(publish_needs, list)
    assert set(publish_needs) == {"compute-beta-label", *PLATFORM_JOBS}


def test_no_job_is_gated_on_a_release_tag() -> None:
    """No job here reuses ci.yml's `refs/tags/v*` release gate.

    This workflow's own trigger (staging push / manual dispatch) already
    decides when it runs; an inherited tag check from the release
    workflow would silently make every job here a no-op.
    """
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    for job_name in jobs:
        assert "if" not in jobs[job_name]


def test_every_artifact_filename_carries_a_beta_suffix() -> None:
    """No beta artifact filename collides with a real release filename.

    Regression guard: a beta and a real release download sitting in the
    same folder must never be mistaken for each other by filename alone
    (design doc §3.4/§3.6). The real filenames are checked with a word
    boundary, not a bare substring: `fim-linux-x64` (no extension) is
    itself a substring of its own `fim-linux-x64-beta` variant, so a
    naive `in` check would always find a false positive there.
    """
    workflow_text = BETA_WORKFLOW.read_text(encoding="utf-8")

    for filename in (
        "fim-windows-x64-beta.exe",
        "fim-windows-arm64-beta.exe",
        "fim-macos-arm64-beta.dmg",
        "fim-macos-x64-beta.dmg",
        "fim-linux-x64-beta",
    ):
        assert filename in workflow_text
    # The real release names, without -beta, must never appear here --
    # this workflow builds distinct artifacts, never re-uses ci.yml's.
    for real_filename in (
        "fim-windows-x64.exe",
        "fim-windows-arm64.exe",
        "fim-macos-arm64.dmg",
        "fim-macos-x64.dmg",
        "fim-linux-x64",
    ):
        boundary_pattern = rf"(?<![\w-]){re.escape(real_filename)}(?![\w-])"
        assert re.search(boundary_pattern, workflow_text) is None, real_filename


def test_publish_beta_creates_an_explicit_prerelease() -> None:
    """The GitHub Release this workflow creates is always marked prerelease."""
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    steps = jobs["publish-beta"]["steps"]
    release_step = next(
        step for step in steps if "gh release create" in step.get("run", "")
    )

    assert "--prerelease" in release_step["run"]


def test_beta_label_does_not_embed_a_hardcoded_target_version() -> None:
    """The computed label is date-plus-sequence only, never `1.2.0`-style.

    Design doc §3.3: a beta label should not imply a promise about a
    not-yet-decided real release number.
    """
    workflow = yaml.safe_load(BETA_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    steps = jobs["compute-beta-label"]["steps"]
    compute_step = next(step for step in steps if step.get("id") == "compute")

    assert 'label="beta-${date_stamp}.${next_seq}"' in compute_step["run"]
