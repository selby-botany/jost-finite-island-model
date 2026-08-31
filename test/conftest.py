"""Shared deterministic fixtures for the simulator test suite."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from hypothesis import settings

from fim import paths
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams

settings.register_profile(
    "deterministic",
    derandomize=True,
    deadline=None,
    max_examples=100,
)
settings.load_profile("deterministic")


@pytest.fixture
def rng() -> Callable[[int], np.random.Generator]:
    """Return the only sanctioned deterministic RNG factory for tests."""

    def factory(seed: int) -> np.random.Generator:
        return np.random.Generator(np.random.PCG64(seed))

    return factory


@pytest.fixture
def tiny_params() -> SimulationParams:
    """Return a small, fast configuration for integration tests."""
    return SimulationParams(
        N=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=20260814,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
    )


@pytest.fixture
def log_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep `fim.logging_setup.configure()` off the real checkout — opt-in.

    Every real entry point (`fim.cli.main`, `fim.launcher.main`,
    `fim.gui.app.main`) calls `configure()` unconditionally. Absent an
    explicit `-L file=...`/`FIM_LOG_OPTIONS`, that call resolves its own
    default log file against the real, installed `fim` package's own
    checkout root (`fim.paths.project_root`), not whichever test's own
    `tmp_path` happens to be running — confirmed directly, more than
    once, before this fixture existed: left unguarded, a test that
    calls one of those three functions for real writes to this
    repository's own real `logs/` directory on every suite run.

    Defined here, at the top level, so every test under `test/` can
    request it by fixture-dependency injection with no import of its
    own — but deliberately **not** `autouse` here: a suite-wide autouse
    version was tried first and rejected, since it silently replaced
    the exact `fim.paths.default_log_file` function `test_paths.py`'s
    own dedicated tests need to call for real (caught by an immediate
    test failure). Each place that actually needs it opts in instead,
    with its own thin autouse wrapper depending on this fixture:
    `test/cli/conftest.py` (scoped to `test/cli/`), `test/test_
    launcher.py` (no subdirectory of its own to scope a conftest.py
    to), and `test/gui/conftest.py` (`doc/fim-logging-design.md` §12).
    """
    monkeypatch.setattr(
        paths, "default_log_file", lambda _root=None: tmp_path / "fim.log"
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
