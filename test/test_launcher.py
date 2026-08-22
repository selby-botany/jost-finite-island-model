"""Unit tests for the packaged single-exe's GUI/CLI dispatch.

No subprocess and no real PyInstaller build (§6.5's packaging smoke
layer owns that): these exercise `fim.launcher.main`'s branching logic
directly, with `fim.cli.main`, `fim.gui.app.main`, and
`subprocess.Popen` replaced by recording stubs so a real simulation
run, a real `Tk` root, or a real detached process is never built here.
Design doc `20260819-claude-sonnet-5-graphical-interface.md` §7.9's own
commit bullets: "explicit regression test that every existing
non-empty-argv CLI invocation still reaches `fim.cli.main` unchanged"
and "unit tests for all four cases: `--graphical` alone (foreground
GUI, no `subprocess.Popen` call), `--graphical --detach`/`--detach
--graphical` (asserts the exact `subprocess.Popen` argv and
`start_new_session=True`, mocked), `--detach` alone (exit status 2,
the clear usage error), and every existing subcommand/flag combination
still reaching `fim.cli.main` unchanged."

The Windows-only `FreeConsole` mechanic is exercised at the end of this
file rather than beside the flag-parsing tests above — it fires from
every foreground GUI launch path, not just one of them, so it stays
next to the plain zero-argv dispatch test it was originally written
against.
"""

from __future__ import annotations

import ctypes
import subprocess
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


def test_launcher_graphical_alone_launches_the_gui_in_the_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--graphical` alone runs the GUI in this process — no subprocess spawned."""
    calls: list[None] = []

    def fake_gui_main() -> int:
        calls.append(None)
        return 0

    def fail_if_popen_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("subprocess.Popen was called")

    monkeypatch.setattr(fim.gui.app, "main", fake_gui_main)
    monkeypatch.setattr(subprocess, "Popen", fail_if_popen_called)

    status = launcher.main(["--graphical"])

    assert status == 0
    assert calls == [None]


@pytest.mark.parametrize(
    "argv",
    [["--graphical", "--detach"], ["--detach", "--graphical"]],
)
def test_launcher_graphical_detach_spawns_a_detached_subprocess_when_frozen(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """A packaged (frozen) build relaunches `[sys.argv[0], "--graphical"]`.

    `sys.frozen` is the same flag PyInstaller sets and
    `fim.__init__._load_version` already checks for the bundled
    `version.txt` -- only a real packaged executable's `sys.argv[0]` is a
    directly re-executable binary path. The foreground GUI itself is
    never entered by *this* process — only the relaunched (here, faked)
    child would reach it.
    """
    popen_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            popen_calls.append((args, kwargs))

    def fail_if_gui_main_called() -> int:
        pytest.fail("gui.app.main was called in the detaching process")

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(fim.gui.app, "main", fail_if_gui_main_called)
    monkeypatch.setattr("sys.argv", ["/path/to/fim"])
    monkeypatch.setattr("sys.frozen", True, raising=False)

    status = launcher.main(argv)

    assert status == 0
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args == (["/path/to/fim", "--graphical"],)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_launcher_graphical_detach_relaunches_via_python_module_when_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from source relaunches `[sys.executable, "-m", "fim.launcher"]`.

    Regression test: `bin/fim --graphical --detach` (a `python3 -m
    fim.launcher` invocation, `sys.frozen` never set) previously relaunched
    `[sys.argv[0], "--graphical"]` unconditionally -- for this invocation
    shape, `sys.argv[0]` is the raw `launcher.py` source file path, which
    has no execute permission and no shebang-driven interpreter of its
    own, so the relaunch failed with a real `PermissionError` rather than
    starting the GUI. `monkeypatch.delattr` guards against `sys.frozen`
    leaking in from another test or the interpreter that happens to be
    running these tests.
    """
    popen_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            popen_calls.append((args, kwargs))

    def fail_if_gui_main_called() -> int:
        pytest.fail("gui.app.main was called in the detaching process")

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(fim.gui.app, "main", fail_if_gui_main_called)
    monkeypatch.setattr("sys.argv", ["/path/to/launcher.py"])
    monkeypatch.setattr("sys.executable", "/path/to/python3")
    monkeypatch.delattr("sys.frozen", raising=False)

    status = launcher.main(["--graphical", "--detach"])

    assert status == 0
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args == (["/path/to/python3", "-m", "fim.launcher", "--graphical"],)
    assert kwargs["start_new_session"] is True


def test_launcher_bare_detach_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--detach` without `--graphical` is a clear usage error, not a silent no-op."""

    def fail_if_gui_main_called() -> int:
        pytest.fail("gui.app.main was called")

    def fail_if_cli_main_called(argv: Sequence[str] | None = None) -> int:
        pytest.fail("cli.main was called")

    monkeypatch.setattr(fim.gui.app, "main", fail_if_gui_main_called)
    monkeypatch.setattr(fim.cli, "main", fail_if_cli_main_called)

    status = launcher.main(["--detach"])

    assert status == 2
    assert "fim: error: --detach requires --graphical" in capsys.readouterr().err


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
