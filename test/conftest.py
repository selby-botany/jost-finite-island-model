r"""Shared deterministic fixtures for the simulator test suite.

Also arms this suite's own interpreter-shutdown diagnostics (see
`pytest_unconfigure` below). `test/gui/conftest.py`'s own module
docstring records several separate investigations into a `pytest` process
that finished every test, printed its own summary, and then hung
indefinitely in CPython's `Py_FinalizeEx -> wait_for_thread_shutdown` --
each one diagnosed by catching the stalled process live and running
`sample <pid>` against it by hand. That technique works, but it needs a
person watching at the moment it happens, and the hang reproduces most
often on CI where nobody is. These two hooks capture the same evidence
automatically, on every run, with nothing installed: they are pure
standard library (`threading`, `faulthandler`), so they add no dependency
and work identically on every platform this suite runs on.

`atexit` was tried first and is **wrong for this specific hang**, proven
by the end-to-end test in `test/test_shutdown_diagnostics.py` before this
comment was written: `Py_FinalizeEx` calls `wait_for_thread_shutdown`
(joining every non-daemon thread) *before* it runs `atexit` callbacks, so
on the one failure these diagnostics exist to catch, an `atexit` hook is
never reached at all -- the first version of this file hung a child
interpreter with completely empty stderr. `pytest_unconfigure` runs at
the end of the session while the interpreter is still fully alive, which
is the last moment ordinary Python code is guaranteed to run.

Also holds `COMPLETION_BACKSTOP_SECONDS` and its helpers (`join_or_fail`,
`wait_or_fail`, `poll_or_fail`, `readline_or_fail`): the one bound, shared
by every test that waits for a real completion signal, that turns a hang
into a failure naming what never finished. Test modules import them with
`from conftest import ...`, as `test/test_shutdown_diagnostics.py`
already imports this module.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

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

# How long interpreter shutdown may take before the watchdog dumps every
# thread's stack and kills the process. Shutdown after a finished test
# session is normally instantaneous, so any value here is generous; this
# only has to be longer than a legitimately slow teardown (flushing
# coverage data, joining a briefly-busy worker) to avoid a false kill.
_SHUTDOWN_TIMEOUT_SECONDS = float(os.environ.get("FIM_TEST_SHUTDOWN_TIMEOUT", "120"))

COMPLETION_BACKSTOP_SECONDS = float(
    os.environ.get("FIM_TEST_COMPLETION_BACKSTOP", "120")
)
"""The one bound on every wait for a real completion signal in this suite.

A hang backstop, not a timing budget. Tests that wait for work whose
duration depends on machine load -- a batch's worker processes, a sweep,
a window's page load -- wait on the work's own completion signal (a
thread's end, an event, a flag the code clears in a `finally`), never on
a guess at how long it takes. Unbounded, though, a genuinely broken run
hangs the test, its xdist worker and a local pre-commit run instead of
failing. This bound turns that hang into a failure that names what never
finished (`backstop_message`).

It must stay far above anything a loaded machine needs: the slowest
waits observed under a heavily loaded `-n auto` run were tens of
seconds. A test that trips it on a healthy commit has found a real
defect or a missing completion signal, not a reason to raise it.

`FIM_TEST_COMPLETION_BACKSTOP` (seconds) overrides it, for a machine or
CI runner slow enough to need more, or a tiny value to watch the
backstop fire.
"""


class Waitable(Protocol):
    """An event-like completion signal: `threading.Event`, pywebview's own."""

    def wait(self, timeout: float | None = None) -> bool:
        """Block until set or `timeout` passes; return whether it is set."""
        ...


def backstop_message(what: str) -> str:
    """Name what did not finish within `COMPLETION_BACKSTOP_SECONDS`.

    Args:
        what: The thing waited for, e.g. "batch thread".

    Returns:
        The failure message every completion-signal backstop raises.
    """
    return (
        f"{what} did not finish within {COMPLETION_BACKSTOP_SECONDS:g} s "
        "(completion-signal backstop)"
    )


def join_or_fail(thread: threading.Thread, what: str) -> None:
    """Join `thread`, failing if it outlives `COMPLETION_BACKSTOP_SECONDS`.

    Args:
        thread: A started thread whose end is the completion signal.
        what: The thing waited for, named in the failure.

    Returns:
        None

    Raises:
        AssertionError: If `thread` is still alive at the backstop.
    """
    thread.join(COMPLETION_BACKSTOP_SECONDS)
    if thread.is_alive():
        raise AssertionError(backstop_message(what))


def poll_or_fail[T](
    read: Callable[[], T],
    is_done: Callable[[T], bool],
    what: str,
    *,
    interval: float,
) -> T:
    """Call `read` every `interval` seconds until `is_done` accepts its value.

    The interval affects only how soon the end is noticed; the deadline
    is `COMPLETION_BACKSTOP_SECONDS`, a hang backstop.

    Args:
        read: Reads the completion signal's current value.
        is_done: Whether a value read means the work has finished.
        what: The thing waited for, named in the failure.
        interval: Seconds to sleep between reads.

    Returns:
        The first value `is_done` accepts.

    Raises:
        AssertionError: If no accepted value is read before the backstop;
            the message includes the last value observed.
    """
    deadline = time.monotonic() + COMPLETION_BACKSTOP_SECONDS
    while True:
        value = read()
        if is_done(value):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{backstop_message(what)}; last observed value: {value!r}"
            )
        time.sleep(interval)


def readline_or_fail(process: subprocess.Popen[str], what: str) -> str:
    """Read `process`'s first stdout line, failing at the backstop.

    For a child interpreter whose start-up (interpreter launch, imports)
    is waited for without a timing budget: it prints one flushed line
    once it is up. A child that never prints it and never exits is
    killed, and the wait fails instead of hanging.

    Args:
        process: A child started with `stdout=subprocess.PIPE, text=True`.
        what: The thing waited for, named in the failure.

    Returns:
        The line, with its newline, or "" if the child exited first.

    Raises:
        AssertionError: If neither happens before the backstop.
    """
    assert process.stdout is not None
    stdout = process.stdout
    lines: list[str] = []
    # `readline` on a pipe cannot time out, so it runs on a daemon thread
    # whose end is the signal; killing the child closes the pipe and ends
    # that thread too.
    reader = threading.Thread(target=lambda: lines.append(stdout.readline()))
    reader.daemon = True
    reader.start()
    reader.join(COMPLETION_BACKSTOP_SECONDS)
    if reader.is_alive():
        process.kill()
        process.communicate()
        raise AssertionError(backstop_message(what))
    return lines[0]


def wait_or_fail(event: Waitable, what: str) -> None:
    """Wait for `event`, failing if it is not set by the backstop.

    Args:
        event: The completion signal.
        what: The thing waited for, named in the failure.

    Returns:
        None

    Raises:
        AssertionError: If `event` is still unset at the backstop.
    """
    if not event.wait(COMPLETION_BACKSTOP_SECONDS):
        raise AssertionError(backstop_message(what))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep GUI tests on one xdist worker during parallel CI runs.

    Args:
        items: Test items collected for the current pytest session.

    Returns:
        None
    """
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(pytest.mark.xdist_group("gui"))


def live_non_daemon_threads() -> list[threading.Thread]:
    """Return every non-daemon thread other than the main one.

    Exactly the set `threading._shutdown` -- and therefore
    `Py_FinalizeEx -> wait_for_thread_shutdown` -- blocks on. A daemon
    thread never delays shutdown and the main thread is the one doing the
    waiting, so neither can explain a hang; both are excluded rather than
    reported as noise a reader has to filter out under incident pressure.

    Args:
        None

    Returns:
        The threads still alive that will block interpreter shutdown, in
        `threading.enumerate()` order. Empty when shutdown is unblocked.
    """
    main = threading.main_thread()
    return [
        thread
        for thread in threading.enumerate()
        if thread is not main and not thread.daemon and thread.is_alive()
    ]


def report_live_non_daemon_threads() -> list[threading.Thread]:
    """Name every thread that is about to block interpreter shutdown.

    Runs from `pytest_unconfigure`, while the interpreter is still fully
    working: ordinary Python code, ordinary `stderr`. That timing is the
    entire point -- once `Py_FinalizeEx` is actually wedged, Python-level
    code can no longer run and nothing can say anything at all, which is
    why every previous incident needed `sample <pid>` by hand.

    Prints nothing when shutdown is unblocked, so a healthy run's own
    output is completely unchanged.

    Args:
        None

    Returns:
        The offending threads, so a caller (and this suite's own tests)
        can act on the same list that was reported. Empty when shutdown
        is unblocked.
    """
    blocking = live_non_daemon_threads()
    if not blocking:
        return blocking
    print(
        f"\nfim: {len(blocking)} non-daemon thread(s) alive at interpreter "
        f"shutdown; these will block Py_FinalizeEx:",
        file=sys.stderr,
    )
    for thread in blocking:
        # `_target` is private, but it is the single most useful fact
        # here: `repr(Thread)` alone gives a name like `Thread-7`, which
        # does not identify the code that started it. Read defensively so
        # a Thread subclass that does not set it still reports the rest.
        target = getattr(thread, "_target", None)
        target_name = getattr(target, "__qualname__", None) or repr(target)
        print(
            f"  {thread!r} target={target_name} "
            f"module={getattr(target, '__module__', None)}",
            file=sys.stderr,
        )
    sys.stderr.flush()
    return blocking


def arm_shutdown_watchdog() -> None:
    """Bound interpreter shutdown, dumping every thread's stack if it hangs.

    `faulthandler`'s watchdog is a real C thread, so unlike
    `report_live_non_daemon_threads` above it still fires *during*
    finalization, once Python-level code can no longer run at all --
    which is precisely the window this suite's own hang lives in. It
    dumps the same per-thread stacks `sample <pid>` was previously
    collected by hand for, into the run's own captured output.

    Armed at the end of the session rather than at import: a timer
    started at session start would have to outlast the entire test run
    and would kill a legitimately slow one. Started here, its whole
    budget applies to shutdown alone.

    `exit=True` turns an unbounded hang into a bounded, non-zero-exit
    failure. That matters beyond diagnostics: a hang gives CI no result
    at all and blocks the runner indefinitely, whereas a failure is a
    reported, actionable outcome that still carries the stacks needed to
    fix it.

    Args:
        None

    Returns:
        None
    """
    if _SHUTDOWN_TIMEOUT_SECONDS > 0:
        faulthandler.dump_traceback_later(_SHUTDOWN_TIMEOUT_SECONDS, exit=True)


def _real_results_index() -> Path:
    """Return the developer's own default Study index file."""
    return paths.project_root() / "results" / ".fim" / "studies" / "study-default.json"


def _index_entries(path: Path) -> frozenset[str]:
    """Return the run directories the Study index at `path` lists.

    Empty when the file is absent or unreadable: the guard below then
    has nothing to compare, which is the safe direction.
    """
    try:
        listed = json.loads(path.read_text(encoding="utf-8"))["run_directories"]
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()
    return frozenset(str(entry) for entry in listed)


_REAL_ENTRIES_AT_START: frozenset[str] = frozenset()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Remember the developer's real results index before any test runs.

    Only the controlling process records it; xdist workers start after it.
    """
    global _REAL_ENTRIES_AT_START  # noqa: PLW0603
    if not hasattr(session.config, "workerinput"):
        _REAL_ENTRIES_AT_START = _index_entries(_real_results_index())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session if a test registered a run in the real results index.

    `fim run` records every run in the default Study under the results
    directory, wherever its own `--output` is, so a test that runs it without
    isolating the results directory pollutes the index the app's Home screen
    reads (one suite run once added 35 throwaway runs to it). Making that a
    failure, not a silent side effect, keeps it from coming back.

    Only *outside* entries count: a run written elsewhere is listed by its
    absolute path, while the developer's own runs in `results/` are bare
    directory names. Comparing modification times instead (this guard's first
    form) failed a session whenever the developer's desktop app finished a
    run of its own while the tests were going, which made a test's outcome
    depend on something other than the commit.
    """
    del exitstatus
    if hasattr(session.config, "workerinput"):
        return
    added = _index_entries(_real_results_index()) - _REAL_ENTRIES_AT_START
    outside = sorted(entry for entry in added if Path(entry).is_absolute())
    if outside:
        print(
            "\nERROR: this test session registered runs outside `results/` in "
            f"the real results index ({_real_results_index()}): {outside[:3]}. "
            "A test must isolate the results directory (see "
            "test/cli/conftest.py)."
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_unconfigure(config: pytest.Config) -> None:
    """Run both shutdown diagnostics as the session ends.

    Ordering is deliberate and load-bearing. The readable report runs
    first, while ordinary Python still works, so the plain-language "here
    is the offending thread and what started it" line is always written.
    The watchdog is armed second, so its budget covers everything after
    this hook returns -- pytest's own remaining teardown *and*
    interpreter finalization, which is where the hang actually lives.

    `pytest_unconfigure`, not `atexit`: `Py_FinalizeEx` joins non-daemon
    threads before running `atexit` callbacks, so an `atexit` hook is
    unreachable on exactly the hang being diagnosed (see this module's
    own docstring -- an `atexit` version was written first and proven
    silent by `test/test_shutdown_diagnostics.py`'s end-to-end test).

    Args:
        config: The finishing session's own configuration. Unused --
            the hook's signature is pytest's, not this module's choice.

    Returns:
        None
    """
    del config
    report_live_non_daemon_threads()
    arm_shutdown_watchdog()


@pytest.fixture
def rng() -> Callable[[int], np.random.Generator]:
    """Return the only sanctioned deterministic RNG factory for tests."""

    def factory(seed: int) -> np.random.Generator:
        return np.random.Generator(np.random.PCG64(seed))

    return factory


@pytest.fixture
def tiny_params() -> SimulationParams:
    """Return a small, fast, single-run configuration for integration tests.

    `n_replicates=1`/`replicate_tolerance=None` explicitly, not
    `SimulationParams`'s own current defaults (`200`/`0.01`) — this
    fixture's whole point is one small, fast, ordinary scalar run; a
    caller that actually wants to test replicate-batch behavior should
    build that configuration itself, not get it by accident from every
    other test that happens to share this fixture.
    """
    return SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=20260814,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
        n_replicates=1,
        replicate_tolerance=None,
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
