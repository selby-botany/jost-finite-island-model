"""Static checks of the CI workflow files.

A job that installs the project on an Ubuntu runner builds PyGObject (through
`pywebview[gtk]`), which needs the GTK build packages first. The nightly
`slow-tests` job once lacked them and failed in `pip install` for days before
anyone noticed, so this invariant is checked on the workflow text, with no
network and no runner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim.cli import STARTER_CONFIG

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


_SED_FROM = re.compile(r"-e 's/([^/]+)/[^/]*/'")
_REPLACE_FROM = re.compile(r"-replace '([^']+)', '[^']*'")
_SMOKE_JOBS = [
    (workflow, name)
    for workflow in ("ci.yml", "beta.yml")
    for name, job in _jobs(workflow).items()
    if "run tiny.yaml" in _runs(job)
]
_UNIX_SMOKE_JOBS = [
    (workflow, name)
    for workflow, name in _SMOKE_JOBS
    if ".exe" not in _runs(_jobs(workflow)[name])
]


def test_there_are_packaging_smoke_jobs_to_check() -> None:
    """The checks below must not pass by finding no jobs."""
    assert len(_SMOKE_JOBS) == 10
    assert len(_UNIX_SMOKE_JOBS) == 6


@pytest.mark.parametrize(("workflow", "job"), _SMOKE_JOBS)
def test_every_smoke_edit_matches_a_line_of_the_starter_config(
    workflow: str, job: str
) -> None:
    """Each edit of the starter configuration finds something to edit.

    The packaging smoke tests shrink the `fim init` configuration with `sed`
    (macOS, Linux) or `-replace` (Windows). An edit whose pattern no longer
    matches changes nothing and fails silently (`N: 450` outlived the
    starter's `N: 225` that way), so every pattern must be a line of
    `fim.cli.STARTER_CONFIG`.
    """
    starter = STARTER_CONFIG.splitlines()
    script = _runs(_jobs(workflow)[job])
    patterns = _SED_FROM.findall(script) + _REPLACE_FROM.findall(script)

    assert patterns
    for pattern in patterns:
        assert any(line.startswith(pattern) for line in starter), (
            f"{workflow} job {job}: the starter config has no line {pattern!r}"
        )


@pytest.mark.parametrize(("workflow", "job"), _UNIX_SMOKE_JOBS)
def test_every_unix_smoke_job_also_runs_the_default_batch(
    workflow: str, job: str
) -> None:
    """A packaged macOS or Linux build is run on the default as well: a batch.

    The single-run smoke test pins one run to check the flat layout. The
    default must be exercised too: the same shrunken configuration with
    `n_replicates` left alone, checked for a summary and at least the
    10-replicate minimum.
    """
    script = _runs(_jobs(workflow)[job])
    batch = script[script.index("tiny-batch.yaml") :]
    edits = batch[: batch.index("run tiny-batch.yaml")]

    assert "run tiny-batch.yaml --output smoke-batch --quiet" in batch
    assert "n_replicates" not in edits
    assert "summary.json" in batch
    assert "-lt 10" in batch


@pytest.mark.parametrize(
    ("workflow", "job"),
    [entry for entry in _SMOKE_JOBS if entry not in _UNIX_SMOKE_JOBS],
)
def test_every_windows_smoke_job_runs_the_default_batch(
    workflow: str, job: str
) -> None:
    """The Windows smoke run leaves `n_replicates` alone and expects batch files."""
    script = _runs(_jobs(workflow)[job])

    assert "n_replicates" not in script
    assert '"summary.json"' in script
