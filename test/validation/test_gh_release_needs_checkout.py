"""Regression guard: `gh release create` needs a preceding checkout.

`gh` shells out to `git` to determine which repository it is targeting
when no `--repo`/`GH_REPO` override applies to the whole invocation
context it needs (branch/remote information, not just the repo name).
With no `actions/checkout` step anywhere earlier in the same job, it
fails immediately with "fatal: not a git repository" before ever
reaching the release-creation call. Found live: `beta.yml`'s
`publish-beta` job had none of `ci.yml`'s real `publish` job's steps
that happen to establish that context (its own leading `actions/
checkout`) -- `publish-beta` only ever downloaded artifacts and never
otherwise needed the source tree, so nothing forced the checkout to be
there.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"


def test_every_gh_release_create_job_checks_out_the_repository_first() -> None:
    """A job calling `gh release create` has a checkout step before it."""
    failures: list[str] = []
    for workflow_path in sorted(WORKFLOWS_DIRECTORY.glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", ())
            release_index = next(
                (
                    index
                    for index, step in enumerate(steps)
                    if "gh release create" in step.get("run", "")
                ),
                None,
            )
            if release_index is None:
                continue
            checkout_index = next(
                (
                    index
                    for index, step in enumerate(steps)
                    if step.get("uses", "").startswith("actions/checkout")
                ),
                None,
            )
            if checkout_index is None or checkout_index > release_index:
                failures.append(f"{workflow_path.name}:{job_name}")
    assert not failures, (
        "job(s) call gh release create with no preceding checkout: "
        + ", ".join(failures)
    )
