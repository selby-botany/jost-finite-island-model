"""Static checks of the CI workflow files.

A job that installs the project on an Ubuntu runner builds PyGObject (through
`pywebview[gtk]`), which needs the GTK build packages first. The nightly
`slow-tests` job once lacked them and failed in `pip install` for days before
anyone noticed, so this invariant is checked on the workflow text, with no
network and no runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
BUILD_PACKAGES = ("libcairo2-dev", "libgirepository1.0-dev", "pkg-config")


def _jobs(name: str) -> dict[str, dict[str, Any]]:
    """Return the jobs of one workflow file, by name."""
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _runs(job: dict[str, Any]) -> str:
    """Return every `run:` script of a job, joined."""
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


@pytest.mark.parametrize("workflow", ["ci.yml", "beta.yml"])
def test_every_ubuntu_job_that_installs_the_project_installs_the_gtk_build_packages(
    workflow: str,
) -> None:
    """`pip install -e .[...]` on Ubuntu is preceded by the PyGObject build packages."""
    checked = 0
    for name, job in _jobs(workflow).items():
        runner = str(job.get("runs-on", ""))
        script = _runs(job)
        if not runner.startswith("ubuntu") or "pip install -e" not in script:
            continue
        checked += 1
        install = script.index("pip install -e")
        apt = script.find("apt-get install")
        assert 0 <= apt < install, f"{workflow} job {name}: no apt-get before pip"
        for package in BUILD_PACKAGES:
            assert package in script[apt:install], (
                f"{workflow} job {name} does not install {package} before pip"
            )
    assert checked > 0
