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

import pytest


@pytest.fixture(autouse=True)
def _isolate_logging(log_isolation: None) -> None:
    """Opt every test in this directory into the shared `log_isolation`."""
