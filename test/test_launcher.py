"""Unit tests for the packaged single-exe's GUI/CLI dispatch.

No subprocess and no real PyInstaller build (§6.5's packaging smoke
layer owns that): these exercise `fim.launcher.main`'s branching logic
directly, with `fim.cli.main` and `fim.gui.app.main` replaced by
recording stubs so a real simulation run or a real `Tk` root is never
built here. Design doc `20260819-claude-sonnet-5-graphical-interface.md`
§7.9's own commit bullet: "explicit regression test that every existing
non-empty-argv CLI invocation still reaches `fim.cli.main` unchanged."
`--graphical`/`--detach` do not exist yet — the next commit adds them
and their own tests.
"""

from __future__ import annotations

import ctypes
from collections.abc import Sequence

import pytest

import fim.cli
import fim.gui.app
from fim import launcher


def test_launcher_dispatches_empty_argv_to_the_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero arguments launches the GUI, never the CLI parser."""
    calls: list[None] = []

    def fake_gui_main() -> int:
        calls.append(None)
        return 0

    def fail_if_called(argv: Sequence[str] | None = None) -> int:
        pytest.fail("cli.main was called")

    monkeypatch.setattr(fim.gui.app, "main", fake_gui_main)
    monkeypatch.setattr(fim.cli, "main", fail_if_called)

    status = launcher.main([])

    assert status == 0
    assert calls == [None]


def test_launcher_dispatches_empty_sys_argv_to_the_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`argv=None` falls back to `sys.argv[1:]`, same as `fim.cli.main`."""
    calls: list[None] = []

    def fake_gui_main() -> int:
        calls.append(None)
        return 0

    monkeypatch.setattr(fim.gui.app, "main", fake_gui_main)
    monkeypatch.setattr("sys.argv", ["fim"])

    status = launcher.main(None)

    assert status == 0
    assert calls == [None]


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["run", "config.yaml"],
        ["init", "--output", "out.yaml"],
        ["stats", "trajectory.jsonl"],
    ],
)
def test_launcher_dispatches_nonempty_argv_to_cli_main_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Every non-empty invocation reaches `fim.cli.main` with `argv` intact.

    `fim.gui.app.main` is stubbed to fail the test outright if reached —
    the GUI must never be a side effect of a CLI-shaped invocation.
    """
    received: list[Sequence[str] | None] = []

    def fake_cli_main(passed_argv: Sequence[str] | None = None) -> int:
        received.append(passed_argv)
        return 42

    def fail_if_called() -> int:
        pytest.fail("gui.app.main was called")

    monkeypatch.setattr(fim.cli, "main", fake_cli_main)
    monkeypatch.setattr(fim.gui.app, "main", fail_if_called)

    status = launcher.main(argv)

    assert status == 42
    assert received == [argv]


def test_launcher_dispatches_nonempty_sys_argv_to_cli_main_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`argv=None` with a non-empty `sys.argv` also reaches the CLI, unchanged."""
    received: list[Sequence[str] | None] = []

    def fake_cli_main(passed_argv: Sequence[str] | None = None) -> int:
        received.append(passed_argv)
        return 0

    monkeypatch.setattr(fim.cli, "main", fake_cli_main)
    monkeypatch.setattr("sys.argv", ["fim", "run", "config.yaml"])

    status = launcher.main(None)

    assert status == 0
    assert received == [["run", "config.yaml"]]


def test_launcher_frees_the_console_only_on_win32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows-only `FreeConsole` mechanic fires only for `sys.platform == "win32"`.

    `ctypes.windll` does not exist on this (non-Windows) test platform,
    so it is faked in with `raising=False` rather than patched onto the
    real attribute.
    """
    calls: list[None] = []

    class _FakeKernel32:
        def free_console(self) -> None:
            calls.append(None)

        FreeConsole = free_console

    class _FakeWindll:
        kernel32 = _FakeKernel32()

    def fake_gui_main() -> int:
        return 0

    monkeypatch.setattr(fim.gui.app, "main", fake_gui_main)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)

    launcher.main([])

    assert calls == [None]
