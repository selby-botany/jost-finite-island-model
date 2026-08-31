"""Shared fixtures for `test/cli/` — scoped here, not `test/conftest.py`.

Every test under this directory calls `fim.cli.main`/`fim.launcher.main`
for real, which ends up calling `fim.logging_setup.configure()`. Scoped
to `test/cli/` specifically (not the whole suite) because `test/test_
paths.py`/`test/test_logging_setup.py` — siblings of this directory,
not descendants — legitimately need the *real*
`fim.paths.default_log_file`/`project_root` behavior to test those
functions themselves; a suite-wide autouse fixture here would silently
replace the exact function under test in those files instead (confirmed
directly: it did, once, before this fixture was scoped down to here).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from fim import paths as fim_paths


@pytest.fixture(autouse=True)
def _isolate_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep every `fim.logging_setup.configure()` call off the real checkout.

    Absent an explicit `-L file=...`, `configure()` resolves its own
    default log file against the real, installed `fim` package's own
    checkout root (`fim.paths.project_root`), not this test's own
    `tmp_path`. Left alone, that would create and write to this
    repository's own real `logs/` directory on every `cli.main()` call
    in this directory, for real, on every suite run (confirmed
    directly, once, before this fixture existed).

    Also restores the `fim`/`py.warnings` loggers' own handlers, level,
    and propagation after every test — `configure()`'s own handler
    replacement (`doc/fim-logging-design.md` §3.2) only cleans up a
    *previous* call's handlers on the *next* call, so the very last test
    to call it in a given run would otherwise leave a handler open
    against a `tmp_path` pytest has already torn down.
    """
    monkeypatch.setattr(
        fim_paths, "default_log_file", lambda _root=None: tmp_path / "fim.log"
    )
    loggers = [logging.getLogger("fim"), logging.getLogger("py.warnings")]
    snapshots = [
        (list(logger.handlers), logger.level, logger.propagate) for logger in loggers
    ]
    try:
        yield
    finally:
        for logger, (original_handlers, original_level, original_propagate) in zip(
            loggers, snapshots, strict=True
        ):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.setLevel(original_level)
            logger.propagate = original_propagate
