"""Regression guard for the suite's own interpreter-shutdown diagnostics.

`test/conftest.py`'s own module docstring has the history these exist
for: a `pytest` process that finished every test, printed its summary,
and then hung in `Py_FinalizeEx -> wait_for_thread_shutdown`, diagnosed
only by catching it live with `sample <pid>`. The diagnostics replace
that manual step, so they are worth their own direct tests -- a
diagnostic that has silently stopped working is worse than none, since
the next incident will be read as "no offending thread found" rather
than "the detector is broken."

`test_a_real_hung_interpreter_is_reported_and_bounded` is the one that
matters most, and it earned that status immediately: the first version
of these diagnostics used `atexit`, every unit test below passed against
it, and only the end-to-end test caught that `Py_FinalizeEx` joins
non-daemon threads *before* running `atexit` callbacks -- making the
whole mechanism silent on precisely the hang it was built for. The unit
tests check the pieces; only a real hung child interpreter checks the
assumption the pieces rest on.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import conftest
from conftest import join_or_fail, readline_or_fail

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def test_live_non_daemon_threads_ignores_a_quiet_interpreter() -> None:
    """Only shutdown-blocking threads are reported; daemons are ignored.

    The healthy case, and the one that must stay silent: a false positive
    here would print noise on every passing `pytest` invocation.

    Deliberately does *not* assert the whole interpreter is quiet. This
    suite runs under `pytest-xdist` with randomized ordering, so a GUI
    test scheduled earlier in the same worker can legitimately still have
    a pywebview bridge thread alive when this runs -- that is the very
    condition being diagnosed elsewhere, and asserting its absence here
    made this test's result depend on execution order rather than on the
    code under test. The real contract is narrower and order-independent:
    a daemon thread, however many are running, is never reported, because
    a daemon thread cannot block `Py_FinalizeEx`.
    """
    idle = threading.Event()
    daemon = threading.Thread(target=idle.wait, name="fim-test-daemon", daemon=True)
    daemon.start()
    try:
        assert daemon not in conftest.live_non_daemon_threads()
    finally:
        idle.set()
        daemon.join(timeout=5)


def test_live_non_daemon_threads_finds_a_thread_that_blocks_shutdown() -> None:
    """A live non-daemon thread is reported -- the exact hang signature.

    This is the shape every incident in `test/gui/conftest.py`'s own
    history had: one non-daemon thread outliving the work that started
    it, which `threading._shutdown` then waits on forever. The thread is
    held open on an `Event` (not a sleep) so the assertion runs while it
    is genuinely alive, deterministically, with no timing race.
    """
    release = threading.Event()
    blocker = threading.Thread(target=release.wait, name="fim-test-blocker")
    blocker.daemon = False
    blocker.start()
    try:
        assert blocker in conftest.live_non_daemon_threads()
        # The reporter returns the same list it prints, so this also
        # proves the two cannot silently diverge.
        assert blocker in conftest.report_live_non_daemon_threads()
    finally:
        release.set()
        # No timing budget: a timed join could return with the thread
        # still alive on a loaded machine, failing the assertions below
        # for a reason that is about the scheduler, not the code. Only
        # the completion-signal backstop bounds it.
        join_or_fail(blocker, "released blocker thread")
    assert not blocker.is_alive()
    assert blocker not in conftest.live_non_daemon_threads()


def test_live_non_daemon_threads_ignores_daemon_threads() -> None:
    """A daemon thread is excluded: it cannot delay shutdown.

    `threading._shutdown` never joins daemon threads, so reporting one
    would point an incident investigation at a thread that provably is
    not the cause.
    """
    release = threading.Event()
    daemon = threading.Thread(target=release.wait, name="fim-test-daemon")
    daemon.daemon = True
    daemon.start()
    try:
        assert daemon.is_alive()
        assert daemon not in conftest.live_non_daemon_threads()
    finally:
        release.set()
        daemon.join(timeout=10)


def test_the_diagnostics_are_wired_to_a_real_pytest_hook() -> None:
    """`pytest_unconfigure` is what actually runs the diagnostics.

    Wiring is the whole mechanism: both functions only ever run because
    pytest calls this hook at the end of the session, and nothing else
    references them. A refactor that renamed or dropped the hook would
    leave both functions fully tested above and completely inert in a
    real run -- the exact "the detector is broken, but silently" failure
    this file exists to prevent.
    """
    assert callable(conftest.pytest_unconfigure)
    assert callable(conftest.report_live_non_daemon_threads)
    assert callable(conftest.arm_shutdown_watchdog)


def test_a_real_hung_interpreter_is_reported_and_bounded() -> None:
    """End to end: a genuinely stuck shutdown reports, dumps stacks, and exits.

    The unit tests above each prove one piece in isolation; only this one
    proves the whole mechanism works on the failure it was built for, and
    it is the test that caught the original `atexit`-based design being
    entirely silent on that failure (see this module's own docstring).

    It reproduces the real bug exactly -- a non-daemon thread left alive
    after the work finishes, wedging `Py_FinalizeEx ->
    wait_for_thread_shutdown` -- in a real child interpreter driven
    through the same `pytest_unconfigure` entry point pytest itself uses,
    and verifies all three required outcomes.

    Without these diagnostics this child would hang forever, which is
    precisely the observed CI symptom. A short `FIM_TEST_SHUTDOWN_TIMEOUT`
    keeps the test fast; the budget below is set well above it so a
    genuine regression fails as a timeout rather than hanging this suite
    in turn -- the one failure mode this whole file exists to make
    impossible. That budget counts only from the child's "work finished"
    line: interpreter start-up and imports before it are waited for
    without a timing budget, since their length depends on machine load
    (about 13 seconds on a loaded machine), not on the commit; only the
    completion-signal backstop (`conftest.readline_or_fail`) bounds them.
    """
    program = textwrap.dedent(
        """
        import sys, threading
        sys.path.insert(0, "test")
        import conftest

        # Never set: the thread outlives main, exactly like the real bug.
        threading.Thread(
            target=threading.Event().wait, name="fim-wedged-thread"
        ).start()
        print("work finished", flush=True)
        # Exactly what pytest itself calls at end of session.
        conftest.pytest_unconfigure(None)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"FIM_TEST_SHUTDOWN_TIMEOUT": "5", "PATH": "/usr/bin:/bin"},
        cwd=str(_REPOSITORY_ROOT),
    )
    # Start-up: no timing budget, only the completion-signal backstop.
    # Returns "" instead if the child died first.
    first_line = readline_or_fail(process, "child start-up ('work finished')")
    try:
        _stdout, stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise

    assert first_line == "work finished\n", first_line + stderr
    # 1. The readable report named the offending thread, while ordinary
    #    Python could still run.
    assert "non-daemon thread(s) alive at interpreter shutdown" in stderr
    assert "fim-wedged-thread" in stderr
    # 2. The watchdog fired *during* finalization, when Python-level code
    #    no longer could, and dumped the stacks `sample <pid>` used to be
    #    needed for.
    assert "Timeout" in stderr
    assert "Thread" in stderr
    # 3. The hang became a bounded, non-zero-exit failure rather than an
    #    unbounded stall that gives CI no result at all.
    assert process.returncode != 0
