"""Shared fixtures for `test/cli/` — scoped here, not `test/conftest.py`.

Every test under this directory calls `fim.cli.main` for real, which
calls `fim.logging_setup.configure()` unconditionally. Scoped to
`test/cli/` specifically (not the whole suite) because `test/test_
paths.py`/`test/test_logging_setup.py` — siblings of this directory,
not descendants — legitimately need the *real*
`fim.paths.default_log_file`/`project_root` behavior to test those
functions themselves; see `test/conftest.py`'s own `log_isolation`
fixture for the full reasoning and why that rules out a suite-wide
autouse fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from fim import paths
from fim.gui import preferences as preferences_module


@pytest.fixture(autouse=True)
def _isolate_logging(log_isolation: None) -> None:
    """Opt every test in this directory into the shared `log_isolation`."""


@pytest.fixture(autouse=True)
def _isolate_path_overrides() -> Iterator[None]:
    """Restore every `fim.paths`/`fim.gui.preferences` override after each test.

    `cli.main`'s own `--root`/`--results-directory`/`--log-directory`/
    `--preferences-file` handling (`20260918-claude-sonnet-5-
    configurable-storage-root-design.md`, `selby/restricted`) calls the
    matching `set_*_override` function directly, with no matching
    `set_*_override(None)` call anywhere in `cli.main` itself -- by
    design, the override is meant to outlive that one call for the rest
    of the real process's own lifetime. A test that exercises one of
    these flags via a real `cli.main([...])` call would otherwise leave
    it set for every later test in the whole suite, regardless of
    directory -- the exact kind of state leak `log_isolation`'s own
    docstring, one file up, already warns about for logging specifically.
    """
    try:
        yield
    finally:
        paths.set_root_override(None)
        paths.set_results_directory_override(None)
        paths.set_log_directory_override(None)
        preferences_module.set_preferences_file_override(None)
