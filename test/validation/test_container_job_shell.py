"""Regression guard: bashisms inside a `container:`-scoped job need `shell: bash`.

Inside a GitHub Actions `container:`, `run:` steps default to plain `sh`
(dash in the images this project uses), not bash, since the image is not
guaranteed to have bash on PATH. `set -o pipefail` is a bashism dash
rejects outright ("Illegal option -o pipefail"). Found live: `ci.yml`'s
`linux-x64` job and `beta.yml`'s mirrored `linux-beta-x64` job both hit
exactly this on their first real runs (`linux-x64` had never been
exercised by a real tag push yet; `linux-beta-x64` was this project's
first-ever run of the newly added beta pipeline).
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIRECTORY = PROJECT_ROOT / ".github" / "workflows"


def test_every_pipefail_step_in_a_container_job_sets_shell_bash() -> None:
    """No `container:`-scoped step relies on the (non-bash) default shell.

    Scoped to steps that actually use a bashism (`set -o pipefail`, the
    one that already bit this project) rather than every step in a
    container job -- a plain single command has no shell-dialect
    dependency worth demanding an explicit override for.
    """
    failures: list[str] = []
    for workflow_path in sorted(WORKFLOWS_DIRECTORY.glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            if "container" not in job:
                continue
            for step in job.get("steps", ()):
                run_text = step.get("run", "")
                if "pipefail" not in run_text:
                    continue
                if step.get("shell") != "bash":
                    failures.append(
                        f"{workflow_path.name}:{job_name}:"
                        f"{step.get('name', run_text.splitlines()[0])}"
                    )
    assert not failures, (
        "container-scoped step(s) use a bashism without shell: bash: "
        + ", ".join(failures)
    )
