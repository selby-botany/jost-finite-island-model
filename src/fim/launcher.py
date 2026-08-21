"""Dispatcher for the single packaged executable's three ways into the GUI.

Design doc `20260819-claude-sonnet-5-graphical-interface.md` §5.1: the
Windows release ships one `.exe`, opened by double-clicking (GUI, no
arguments), from a terminal (CLI), or via an explicit `--graphical
[--detach]` flag pair for a shortcut, `.bat` wrapper, or Start Menu tile
that wants to name the GUI directly rather than relying on the
zero-argument heuristic. `fim.cli.main` keeps its exact existing
signature, behavior, and test suite untouched; this module only adds
branches in front of it. `fim = "fim.launcher:main"` in
`pyproject.toml`'s `[project.scripts]` is the only thing that changes
which callable actually runs `fim` — every documented invocation with a
command still reaches the unmodified CLI parser.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the GUI (implicitly, or via --graphical) or the CLI.

    `fim.exe` invoked with zero arguments today already fails
    (`fim.cli.main`'s subparsers are `required=True`, so `parse_args`
    itself raises `SystemExit(2)` before any command runs) — there is no
    existing zero-argument behavior this branch could regress. Neither
    `--graphical` nor `--detach` collides with any flag `fim.cli`'s
    parser defines today, so every other invocation (`fim run ...`,
    `fim --version`, `fim init`, and so on) is unaffected: `arguments`
    matches none of the shapes below and control passes straight
    through to the unmodified CLI parser.

    Args:
        argv: Arguments excluding the program name, or ``None`` for
            ``sys.argv``.

    Returns:
        The dispatched entry point's own process-style exit status.
    """
    arguments = sys.argv[1:] if argv is None else list(argv)
    if not arguments:
        return _launch_gui(detach=False)
    if arguments == ["--graphical"]:
        return _launch_gui(detach=False)
    if arguments in (["--graphical", "--detach"], ["--detach", "--graphical"]):
        return _launch_gui(detach=True)
    if "--detach" in arguments and "--graphical" not in arguments:
        # A clear, argparse-style usage error rather than a silent no-op
        # — the same pattern `run`'s existing `--workers`/`--sequential`
        # mutual-exclusivity check already uses in cli.py.
        print("fim: error: --detach requires --graphical", file=sys.stderr)
        return 2
    from fim.cli import main as cli_main

    return cli_main(arguments)


def _launch_gui(*, detach: bool) -> int:
    """Start the GUI, either in this process or as a detached one.

    Args:
        detach: When true, relaunch this same executable/script with
            `--graphical` as an independent, detached process and
            return immediately; the calling shell is never blocked
            waiting for the GUI window to close. When false, run the
            GUI in this process — the correct default, since silently
            backgrounding a command a terminal user typed would be
            more surprising than simply blocking until the window
            closes.

    Returns:
        0 for a detached launch (the child's own exit status is not
        this process's concern); the foreground GUI's own exit status
        otherwise.
    """
    if detach:
        import subprocess

        # sys.argv[0] works identically for a `pip install -e .`
        # console script and a packaged fim.exe -- no platform branch
        # needed here.
        subprocess.Popen(
            [sys.argv[0], "--graphical"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("fim: GUI launched (detached)")
        return 0
    if sys.platform == "win32":
        # PyInstaller's console=True build (§5.1: kept so --help,
        # --version, and run's progress output still work from a
        # terminal) would otherwise flash a console window behind the
        # GUI on a double-click launch. Applies to every foreground GUI
        # launch path, not only the zero-argument one -- `fim
        # --graphical` from an existing terminal deserves the same
        # no-flashing-console treatment. This is the one genuinely
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
