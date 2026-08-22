"""pywebview bootstrap and `Api` bridge — the GUI's own entry point
(design doc `20260821-claude-sonnet-5-graphical-interface.md` §3.1, §3.2).

`fim.launcher` dispatches here for the zero-argument and `--graphical`
paths, exactly as it dispatched to the Tk build's own `fim.gui.app:main`
before this migration — the entry point name and shape are stable across
the toolkit swap (§3.1: "`fim.launcher` does not change at all").

`create_window` and `main` are deliberately separate: `main` blocks
(`webview.start()` runs the GUI's own event loop until the window
closes), so no test calls it directly. Every headless test instead calls
`create_window()` itself, then drives the result with its own
`webview.start(callback)` — the pattern `§0.3`'s prototype proved works,
confirmed again directly against this window/bridge before writing this
module (see `test/gui/test_app.py`): `window.evaluate_js(...)` returns
the raw value of whatever JS expression it evaluates, not the resolved
value of a Promise that expression happens to produce — a `js_api` call
like `window.pywebview.api.ping()` returns a Promise to JS, so a direct
`evaluate_js("window.pywebview.api.ping()")` reads back `{}` (Chromium's
own JSON view of an unresolved Promise object), not `"pong"`. Every real
call into the bridge — from a test, and from `webui/*.js` alike —
therefore goes through a small `async` JS wrapper that `await`s the
`js_api` call and writes its result into the DOM (or, for a test, a
`window`-scoped variable), read back with a second, separate
`evaluate_js` call. This is not a workaround bolted on for testing: it is
also the correct, natural shape for a real UI event handler, which never
needs to return a value to Python either — only update its own screen
after the `await` resolves.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import webview


class Api:
    """The `window.pywebview.api` surface every `webui/*.js` screen calls into.

    Grows one method per bridge call as each screen is built (design
    §4); holds no state of its own beyond what a given call needs —
    every real piece of work still routes through `fim.gui`'s existing
    business-logic modules (`config_form`, `runner`, `batch_runner`,
    `recent_runs`, `animation`) or `fim.viz.scatter`'s public data
    functions, never reimplemented here.
    """

    def ping(self) -> str:
        """Prove the basic JS-to-Python bridge round trip (Milestone W1).

        The walking skeleton's first proof: nothing about pywebview's
        window/bridge wiring is broken in this exact packaging/hosting
        context, before any real screen is built on top of it.
        """
        return "pong"

    def ping_from_worker(self) -> str:
        """Prove a trivial picklable callable survives a real cross-process round trip.

        Deliberately proven this early (design §0.5, before `Live
        ProgressStore`/`batch_runner`'s own parallel plumbing, both
        already built and tested, are ever reached through this
        specific pywebview-hosted process): `ProcessPoolExecutor`
        working at all from inside a GUI application process, not just
        from a plain CLI process, is an assumption worth its own direct
        check rather than only discovering a failure three layers away
        inside a real batch run.
        """
        with ProcessPoolExecutor(max_workers=1) as executor:
            return executor.submit(_worker_ping).result()


def _worker_ping() -> str:
    """Module-level, picklable — the target of `Api.ping_from_worker`'s worker call.

    Must be module-level, not a closure or a bound method: the same
    picklability discipline `fim.engine._require_picklable` enforces on
    every real `store_factory` (`fim.gui.batch_runner._replicate_store_
    factory`, `src/fim/cli.py:404`), checked here for the bridge's own
    dispatcher itself.
    """
    return "pong from worker"


def create_window() -> webview.Window:
    """Build, but do not show, fim's one pywebview window over `webui/index.html`.

    Separate from `main` specifically so tests can drive the window
    themselves via `webview.start(callback)` without ever calling the
    real, blocking `main` — the same "construct real widgets, drive them
    synchronously, never call the real blocking entry point without a
    controlled exit" discipline the design's test plan requires (§6.1,
    §6.4), now against pywebview's own API instead of Tk's.

    Raises:
        RuntimeError: If pywebview itself reports the window as never
            created — `webview.create_window`'s own documented (if,
            absent any `window.events.initialized` hook of our own,
            never actually observed) `None` return, for "window
            initialization is cancelled." Surfaced loudly rather than
            silently narrowed away, since nothing downstream of this
            function is prepared to run without a real window.
    """
    created = webview.create_window(
        "fim",
        url=str(_webui_directory() / "index.html"),
        js_api=Api(),
        width=900,
        height=700,
    )
    if created is None:
        raise RuntimeError("pywebview did not create a window")
    return created


def main() -> int:
    """Launch the GUI and block until the window closes.

    Returns:
        0 always — `webview.start()` returning means the user closed the
        window, not an error condition to report differently.
    """
    create_window()
    webview.start()
    return 0


def _webui_directory() -> Path:
    """Return the directory holding `index.html` and its assets, frozen or not.

    Mirrors `fim.__init__._load_version`'s own resolution exactly (same
    `sys._MEIPASS` check, same fallback to a path resolved from
    `__file__`), rather than `importlib.resources` — PyInstaller's
    `datas` bundling already extracts every file under this package to a
    real directory on disk at `sys._MEIPASS`, the identical mechanism
    `version.txt` already relies on, so resolving `webui/` the same way
    keeps this module's only new resource-resolution logic consistent
    with the one already shipping and tested rather than introducing a
    second, different mechanism for what is structurally the same
    problem.
    """
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "fim" / "gui" / "webui"
    return Path(__file__).resolve().parent / "webui"
