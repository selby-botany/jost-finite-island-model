"""Static and built-artifact checks on the source distribution's file list."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def _sdist_include() -> set[str]:
    """Return `[tool.hatch.build.targets.sdist]`'s `include` list as a set."""
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    sdist_config = payload["tool"]["hatch"]["build"]["targets"]["sdist"]
    include = sdist_config["include"]
    assert isinstance(include, list)
    assert all(isinstance(path, str) for path in include)
    return set(include)


def test_sdist_includes_docs_tests_and_release_metadata() -> None:
    """The source archive is reviewable and buildable end to end.

    Regression test for R12: the sdist previously included only
    `LICENSE.md`, `README.md`, `src/fim`, and `version.txt` — a
    scientific package's source archive should also carry the
    documentation, the test suite, the maintainer tooling under `dev/`,
    and the release metadata (`CHANGELOG.md`, `SECURITY.md`,
    `CONTRIBUTING.md`) a reviewer or downstream packager needs, not just
    the importable code.
    """
    include = _sdist_include()

    assert {
        "/CHANGELOG.md",
        "/CONTRIBUTING.md",
        "/SECURITY.md",
        "/dev",
        "/doc",
        "/src/fim",
        "/test",
    } <= include


@pytest.mark.packaging
def test_sdist_excludes_gitignored_scratch_content() -> None:
    """`doc/dev/` (gitignored review/reference material) never ships.

    Regression test for a defect this remediation would otherwise have
    introduced: `hatchling`'s explicit `include` for `/doc` walks the
    filesystem rather than consulting `doc/.gitignore` (a *nested*
    .gitignore, which hatchling does not read), so it swept the entire
    gitignored `doc/dev/` scratch directory — AI review drafts and
    reference PDFs never meant for distribution — straight into a real
    built sdist. Verified by actually building one, not just reading the
    config: a config-only check cannot see hatchling's own file-walk
    behavior. Marked `packaging` (R17): it needs a real `python -m
    build` invocation, which it makes itself rather than depending on
    `build`'s own package step having already run.
    """
    with tempfile.TemporaryDirectory() as outdir:
        # `sys.executable`, not a bare "python3": this must be the exact
        # interpreter running the test (with `build` already installed as
        # a dev dependency), not whatever "python3" happens to resolve to
        # first on PATH.
        subprocess.run(
            [sys.executable, "-m", "build", "--sdist", "--outdir", outdir],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
        (archive,) = Path(outdir).glob("*.tar.gz")
        with tarfile.open(archive) as tar:
            names = tar.getnames()

    assert not any("/doc/dev/" in name for name in names)
