"""Static checks that every GitHub Actions dependency is immutably pinned."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"
DEPENDABOT_CONFIG = PROJECT_ROOT / ".github" / "dependabot.yml"
_FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow_files() -> list[Path]:
    """Return every workflow file, sorted for a stable test order."""
    return sorted(WORKFLOWS_DIRECTORY.glob("*.yml"))


def _every_uses_reference(workflow: dict[object, object]) -> list[str]:
    """Return every `uses:` value across every job and step in a workflow."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow has no jobs mapping"
    references = []
    for job in jobs.values():
        for step in job.get("steps", ()):
            uses = step.get("uses")
            if uses is not None:
                references.append(uses)
    return references


def test_every_workflow_action_is_pinned_to_a_full_commit_sha() -> None:
    """No `uses:` reference names a floating tag, branch, or short SHA.

    Regression test for R11: `actions/checkout@v4` and friends are
    mutable tags a repository owner can silently repoint at a different
    commit — a supply-chain risk this project's own `bin/` wrappers
    already avoid by pinning Docker images to a digest. Every workflow
    dependency must name a full 40-character commit SHA instead,
    typically with a `# vN` comment for human readability.
    """
    assert _workflow_files(), "no workflow files found to check"
    for path in _workflow_files():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for reference in _every_uses_reference(workflow):
            name, _, ref = reference.partition("@")
            assert ref, f"{path.name}: {reference!r} has no @ref at all"
            assert _FULL_COMMIT_SHA.match(ref), (
                f"{path.name}: {name}@{ref} is not pinned to a full commit SHA"
            )


def test_gitleaks_scan_has_the_token_pull_request_scanning_requires() -> None:
    """The gitleaks-action step passes GITHUB_TOKEN and runs on pull_request.

    Regression test: gitleaks-action requires an explicit `GITHUB_TOKEN`
    env var to scan a pull request's diff via the GitHub compare-commits
    API -- without it, every `pull_request`-triggered run fails before
    scanning even starts, while `push`-triggered runs (which don't need
    that API call) succeed. That 100%-failing, diff-content-independent
    check trains reviewers to expect the "scan" job to be red regardless
    of what a PR contains, burying any *other* check (e.g. `build`) that
    fails for a real, diff-specific reason in the same noise. Confirmed
    against this repo's own CI history: every `pull_request` run of
    gitleaks-ci.yml failed and every `push` run passed, until the
    `GITHUB_TOKEN` env var was added.
    """
    path = WORKFLOWS_DIRECTORY / "gitleaks-ci.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))

    on = workflow.get(True, workflow.get("on"))
    assert "pull_request" in on, f"{path.name}: must run on pull_request"

    scan_job = workflow["jobs"]["scan"]
    gitleaks_steps = [
        step
        for step in scan_job["steps"]
        if str(step.get("uses", "")).startswith("gitleaks/gitleaks-action@")
    ]
    assert gitleaks_steps, f"{path.name}: no gitleaks-action step found"
    for step in gitleaks_steps:
        env = step.get("env", {})
        assert "GITHUB_TOKEN" in env, (
            f"{path.name}: gitleaks-action step is missing the GITHUB_TOKEN "
            "env var pull_request scans require"
        )


def test_dependabot_tracks_pip_and_github_actions() -> None:
    """Dependabot is configured to refresh both dependency ecosystems.

    Regression test for R11: SHA-pinned actions and version-ranged pip
    dependencies both still need a mechanism to move forward on their
    own schedule — a pin with nothing ever refreshing it just becomes a
    silently stale one instead of a silently floating one.
    """
    config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    ecosystems = {update["package-ecosystem"] for update in config["updates"]}

    assert config["version"] == 2
    assert {"pip", "github-actions"} <= ecosystems
