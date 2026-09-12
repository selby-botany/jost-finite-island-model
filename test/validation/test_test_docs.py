"""Tests for deterministic generated test-suite documentation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "dev" / "bin" / "generate-test-docs"

# A minimal, `.venv*`-free `PATH` -- no activated virtualenv, and none of
# this repository's own `bin/` wrappers pre-added by a caller (`pytest`
# itself always runs from inside an activated virtualenv, so this is the
# only way to actually exercise the "plain, unactivated shell" case a
# background agent hits when it forgets, or targets the wrong worktree's
# own `.venv-312`, entirely from within the test suite).
_UNACTIVATED_PATH = "/usr/bin:/bin"


def _repository_managed_venv_exists() -> bool:
    """Whether `bin/python3`'s own "prefer repository-managed
    environments" glob (`.venv`, `.venv-*`) has anything to find here.

    True on every local development checkout (`.venv-312`, by this
    project's own convention). False in CI (confirmed live,
    `34718735434`): CI installs directly into the runner's own
    already-provisioned Python rather than creating a project-local
    virtualenv at all, so there is no "unactivated shell forgot to
    activate a venv" scenario to reproduce there in the first place --
    every ambient Python on a CI runner already has every package this
    project needs, on every `PATH`, which is exactly what makes
    `test_generator_runs_without_an_activated_virtualenv` unable to
    observe the real, local-only failure mode it is named for: with
    `_UNACTIVATED_PATH` and no `.venv*` for `bin/python3` to prefer
    instead, the fallback loop finds a genuinely bare `/usr/bin/python3`
    with none of this project's own dependencies installed (confirmed
    directly -- the same `ModuleNotFoundError: No module named
    'pydoc_markdown'` CI hit, reproduced locally against a bare
    `/usr/bin/python3` under this exact stripped `PATH`), which is a
    fact about the CI runner's own install strategy, not a regression
    in `bin/python3` or either generator.
    """
    return (PROJECT_ROOT / ".venv").exists() or any(PROJECT_ROOT.glob(".venv-*"))


# Mirrors `generate-test-docs`'s own `_GROUPS` tuple -- kept as a
# separate, independently-written list (not imported from the
# generator) so this test can actually catch the generator's own
# `_GROUPS` silently drifting out of sync with the real directories
# under `test/`, the same purpose `test_api_docs.py`'s sibling test
# serves for `src/fim/`.
_GROUPS: tuple[str, ...] = (
    "",
    "cli",
    "convergence",
    "engine",
    "gui",
    "model",
    "persistence",
    "statistics",
    "validation",
    "viz",
)


def test_generator_documents_every_test_module(tmp_path: Path) -> None:
    """Every committed test module receives its own anchor in `TESTS.md`."""
    output = tmp_path / "TESTS.md"

    result = subprocess.run(
        [str(GENERATOR), str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    test_root = PROJECT_ROOT / "test"
    for group in _GROUPS:
        label = group or "test"
        directory = test_root / group if group else test_root
        for path in sorted(directory.glob("*.py")):
            if path.name == "__init__.py":
                continue
            qualified = f"{label}.{path.stem}"
            assert f'<a id="{qualified}"></a>' in rendered, qualified


def test_every_test_directory_is_a_documented_group() -> None:
    """`_GROUPS` here (and the generator's own) covers every real
    subdirectory of `test/` that actually holds `.py` files, other than
    `test/data/` (fixture JSON, not code)."""
    test_root = PROJECT_ROOT / "test"
    actual = {
        path.name
        for path in test_root.iterdir()
        if path.is_dir()
        and path.name not in {"data", "__pycache__"}
        and any(path.glob("*.py"))
    }
    documented = {group for group in _GROUPS if group}
    assert actual == documented


def test_generator_runs_without_an_activated_virtualenv(tmp_path: Path) -> None:
    """The generator resolves its own virtualenv from the repository's own
    path, with no dependency on the caller's shell having activated one
    first -- the exact recurring failure mode (a background agent
    forgetting, or activating the wrong worktree's own `.venv-312`) this
    generator's `pydoc-markdown` `PATH` widening (mirroring `dev/bin/
    generate-api-docs`'s own) exists to eliminate.

    Compared against a normal, ordinary-environment run rather than the
    committed `test/TESTS.md` -- freshness of the committed file against
    the real test tree is `pre-push`'s own job; this test's own job is
    narrower: confirm the two environments produce identical output.
    """
    if not _repository_managed_venv_exists():
        pytest.skip(
            "no repository-managed virtualenv (.venv/.venv-*) present -- "
            "this environment installs packages directly into its own "
            "already-provisioned Python (see _repository_managed_venv_exists's "
            "own docstring), so bin/python3's venv-preference fallback has "
            "nothing to find here regardless of PATH; the invariant this "
            "test checks does not apply"
        )
    unactivated_output = tmp_path / "unactivated" / "TESTS.md"
    ordinary_output = tmp_path / "ordinary" / "TESTS.md"

    unactivated = subprocess.run(
        [str(GENERATOR), str(unactivated_output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": _UNACTIVATED_PATH},
    )
    ordinary = subprocess.run(
        [str(GENERATOR), str(ordinary_output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert unactivated.returncode == 0, unactivated.stderr
    assert ordinary.returncode == 0, ordinary.stderr
    assert unactivated_output.read_text(encoding="utf-8") == ordinary_output.read_text(
        encoding="utf-8"
    )
