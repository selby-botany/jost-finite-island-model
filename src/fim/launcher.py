"""Dispatcher for the single packaged executable's ways into the GUI.

Design doc `20260819-claude-sonnet-5-graphical-interface.md` §5.1: the
Windows release ships one `.exe`, opened by double-clicking (GUI, no
arguments) or from a terminal (CLI). `fim.cli.main` keeps its exact
existing signature, behavior, and test suite untouched; this module
only adds a branch in front of it. `fim = "fim.launcher:main"` in
`pyproject.toml`'s `[project.scripts]` is the only thing that changes
which callable actually runs `fim` — every documented invocation with a
command still reaches the unmodified CLI parser.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the GUI (zero arguments) or the CLI (anything else).

    `fim.exe` invoked with zero arguments today already fails
    (`fim.cli.main`'s subparsers are `required=True`, so `parse_args`
    itself raises `SystemExit(2)` before any command runs) — there is no
    existing zero-argument behavior this branch could regress. Every
    other invocation (`fim run ...`, `fim --version`, `fim init`, and so
    on) is unaffected: a non-empty `arguments` passes straight through
    to the unmodified CLI parser.

    Args:
        argv: Arguments excluding the program name, or ``None`` for
            ``sys.argv``.

    Returns:
        The dispatched entry point's own process-style exit status.
    """
    arguments = sys.argv[1:] if argv is None else list(argv)
    if not arguments:
        return _launch_gui()
    from fim.cli import main as cli_main

    return cli_main(arguments)


def _launch_gui() -> int:
    """Start the GUI in this process.

    Returns:
        The GUI's own exit status.
    """
    if sys.platform == "win32":
        # PyInstaller's console=True build (§5.1: kept so --help,
        # --version, and run's progress output still work from a
        # terminal) would otherwise flash a console window behind the
        # GUI on a double-click launch. This is the one genuinely
        # Windows-specific mechanic in this dispatcher (§8) and has no
        # cross-platform automated coverage of its visual effect — only
        # the release workflow's Windows smoke step and the manual QA
        # checklist exercise it for real.
        import ctypes

        ctypes.windll.kernel32.FreeConsole()  # type: ignore[attr-defined]
    from fim.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
