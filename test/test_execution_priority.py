"""Tests for lowered priority on GUI worker processes."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fim import execution_priority


def test_linux_priority_is_applied_to_each_existing_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux niceness is per thread, so enumerate the process task IDs."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(execution_priority.sys, "platform", "linux")
    monkeypatch.setattr(
        execution_priority.Path,
        "iterdir",
        lambda _path: iter([Path("/proc/self/task/11"), Path("/proc/self/task/12")]),
    )
    monkeypatch.setattr(
        execution_priority.os,
        "setpriority",
        lambda which, who, priority: calls.append((which, who, priority)),
        raising=False,
    )
    monkeypatch.setattr(execution_priority.os, "PRIO_PROCESS", 0, raising=False)

    execution_priority.apply_lower_priority()

    assert calls == [
        (os.PRIO_PROCESS, 11, 10),
        (os.PRIO_PROCESS, 12, 10),
    ]


def test_macos_priority_targets_the_current_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platforms with process-scoped niceness lower the current process."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(execution_priority.sys, "platform", "darwin")
    monkeypatch.setattr(
        execution_priority.os,
        "setpriority",
        lambda which, who, priority: calls.append((which, who, priority)),
        raising=False,
    )
    monkeypatch.setattr(execution_priority.os, "PRIO_PROCESS", 0, raising=False)

    execution_priority.apply_lower_priority()

    assert calls == [(os.PRIO_PROCESS, 0, 10)]


def test_linux_lowest_priority_uses_nice_nineteen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lowest priority uses the maximum supported positive nice value."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(execution_priority.sys, "platform", "linux")
    monkeypatch.setattr(
        execution_priority.Path,
        "iterdir",
        lambda _path: iter([Path("/proc/self/task/11")]),
    )
    monkeypatch.setattr(
        execution_priority.os,
        "setpriority",
        lambda which, who, priority: calls.append((which, who, priority)),
        raising=False,
    )
    monkeypatch.setattr(execution_priority.os, "PRIO_PROCESS", 0, raising=False)

    execution_priority.apply_priority("lowest")

    assert calls == [(os.PRIO_PROCESS, 11, 19)]


def test_macos_lowest_priority_uses_background_process_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS Lowest selects the Darwin background process priority."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(execution_priority.sys, "platform", "darwin")
    monkeypatch.setattr(
        execution_priority.os,
        "setpriority",
        lambda which, who, priority: calls.append((which, who, priority)),
        raising=False,
    )
    monkeypatch.setattr(execution_priority.os, "PRIO_DARWIN_PROCESS", 4, raising=False)
    monkeypatch.setattr(execution_priority.os, "PRIO_DARWIN_BG", 0x1000, raising=False)

    execution_priority.apply_priority("lowest")

    assert calls == [(4, 0, 0x1000)]


def test_windows_priority_uses_below_normal_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows child jobs use the below-normal process priority class."""
    calls: list[tuple[int, int]] = []

    def get_current_process() -> int:
        return 7

    def set_priority_class(process: int, priority: int) -> int:
        calls.append((process, priority))
        return 1

    monkeypatch.setattr(execution_priority.sys, "platform", "win32")
    monkeypatch.setattr(
        execution_priority.ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(
                GetCurrentProcess=get_current_process,
                SetPriorityClass=set_priority_class,
            )
        ),
        raising=False,
    )

    execution_priority.apply_lower_priority()

    assert calls == [(7, 0x00004000)]


def test_windows_lowest_priority_uses_idle_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows Lowest selects the idle process priority class."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(execution_priority.sys, "platform", "win32")
    monkeypatch.setattr(
        execution_priority.ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(
                GetCurrentProcess=lambda: 7,
                SetPriorityClass=lambda process, priority: (
                    calls.append((process, priority)) or 1
                ),
            )
        ),
        raising=False,
    )

    execution_priority.apply_priority("lowest")

    assert calls == [(7, 0x00000040)]
