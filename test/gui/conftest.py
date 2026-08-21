"""Shared fixtures for `fim.gui` functional tests."""

from __future__ import annotations

import gc
from collections.abc import Iterator

import pytest
from matplotlib import pyplot as plt

from fim.gui.app import Application


@pytest.fixture(scope="session", autouse=True)
def _gc_disabled_for_session() -> Iterator[None]:
    """Disable the cyclic garbage collector for the whole `gui`-marked session.

    `fim.gui.runner`/`fim.gui.batch_runner` each run a real background
    `threading.Thread` calling straight into `fim.engine`; this
    package's `gui`-marked suite, run in full, reliably crashed the
    whole `pytest` process — `Fatal Python error: Aborted`, always
    mid-`Garbage-collecting`, the reported Python-level frame a
    different, unrelated line inside `fim.engine`/`fim.statistics`
    every time — never when any one file's tests ran alone. Isolated
    with `gc.disable()` alone, reproducible on demand: crashed on every
    one of several repeated full-suite runs with the collector enabled,
    clean on every run with it disabled. The collector traversing some
    native-extension object's graph (Tkinter's own `PhotoImage`/widget
    bookkeeping and matplotlib's `Agg`/Tk-canvas backend are this
    session's only candidates unique to the `gui`-marked suite) while a
    background thread is concurrently inside unrelated `fim.engine` C-
    extension calls is the shape every crash shares; reference
    counting, not the cyclic collector, reclaims the overwhelming
    majority of Python objects regardless, so leaving the cyclic
    collector off for a test session this short costs nothing real.
    Scoped to this package's tests only — nothing about `fim.gui`
    itself, or any other test in the suite, disables the collector.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


@pytest.fixture(scope="session")
def _session_root() -> Iterator[Application]:
    """Build and tear down exactly one real Tk root for the whole test session.

    Tkinter does not tolerate repeatedly instantiating and destroying
    `tk.Tk()` within one process: the underlying Tcl interpreter's
    global bookkeeping does not always reset cleanly between a
    `destroy()` and the next `Tk()`, and this package's own `gui`-marked
    suite — dozens of tests, each previously building and tearing down
    its own root — reliably crashed the whole `pytest` process
    (`Fatal Python error: Aborted`, always mid-`Garbage-collecting`, in
    whichever background thread `fim.gui.runner`/`fim.gui.batch_runner`
    happened to have running at the time) when run in full, never when
    any single file ran alone. One root for the entire session, reused
    by every test via the `root` fixture below, removes the repeated
    create/destroy cycle that triggered it: reproduced consistently
    before this fixture existed, gone consistently after (confirmed
    over several repeated full-suite runs).
    """
    application = Application()
    try:
        yield application
    finally:
        application.destroy()


@pytest.fixture
def root(_session_root: Application) -> Iterator[Application]:
    """Hand each test the one shared Tk root, then clean up after it.

    Every existing test already treats `root`/`app` as "a `tk.Tk()`
    instance the test's own screen is built against and never has to
    close itself" — this fixture keeps that exact contract; only what
    backs it changed (session-scoped instead of per-test). Per-test
    isolation instead comes from destroying every widget the test
    created as the shared root's child, and closing every `pyplot`
    figure a `ResultsScreen`/`BatchResultsScreen` embedded and left
    alive (each screen's own job on new-run/navigate-away, not this
    fixture's, but a test that calls `.show()` without also driving
    that navigation leaves one behind either way) — both before the
    *next* test's background thread could possibly race a stale
    object's finalization against it.
    """
    try:
        yield _session_root
    finally:
        plt.close("all")
        for child in _session_root.winfo_children():
            child.destroy()
