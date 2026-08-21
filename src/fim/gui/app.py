"""Tk application shell: root window and screen-switching mechanism.

`Application` owns exactly one `Tk` root and stacks every screen as a
`ttk.Frame` occupying the same grid cell, raised over its siblings with
`tkraise()` — design doc §4's "one `Tk` root ..., these are wireframes of
layout and behavior" framing. No screen is registered here yet; each
milestone in the implementation plan (`dev/doc/apps/selby/
jost-finite-island-model/20260819-claude-sonnet-5-graphical-interface.md`
§7) adds its own screen and wires it into `main()`.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class Application(tk.Tk):
    """Root window holding every screen as a stacked, raised `ttk.Frame`."""

    def __init__(self) -> None:
        """Build the root window and its single screen-stacking container."""
        super().__init__()
        self.title("fim")
        self._container = ttk.Frame(self)
        self._container.pack(fill="both", expand=True)
        self._container.rowconfigure(0, weight=1)
        self._container.columnconfigure(0, weight=1)
        self._screens: dict[str, ttk.Frame] = {}

    def register_screen(self, name: str, screen: ttk.Frame) -> None:
        """Add one screen to the stack, under `name`, without showing it.

        Args:
            name: Identifier `show_screen` later raises this screen by.
            screen: A `ttk.Frame` already built with this application (or
                its container) as an ancestor.
        """
        screen.grid(in_=self._container, row=0, column=0, sticky="nsew")
        self._screens[name] = screen

    def show_screen(self, name: str) -> None:
        """Raise a previously registered screen above every other one.

        Args:
            name: The identifier passed to `register_screen`.

        Raises:
            KeyError: If `name` was never registered.
        """
        self._screens[name].tkraise()


def main() -> int:
    """Launch the fim GUI: build the root window and run its main loop.

    Returns:
        Always 0 — a normal window close ends the process successfully;
        an unhandled exception inside the loop propagates instead.
    """
    app = Application()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
