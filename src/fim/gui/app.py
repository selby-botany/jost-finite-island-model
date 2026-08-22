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

import json
import queue
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import webview
import yaml

from fim import paths
from fim.cli import load_config
from fim.gui import runner
from fim.gui.batch_runner import default_max_workers
from fim.gui.config_form import (
    field_for_error,
    form_values_to_payload,
    params_to_form_values,
    payload_to_yaml_text,
    starter_form_values,
    tab_for_error,
)
from fim.model.params import SimulationParams
from fim.viz.scatter import scatter_panels

_YAML_FILE_TYPES = ("YAML files (*.yaml;*.yml)", "All files (*.*)")


class Api:
    """The `window.pywebview.api` surface every `webui/*.js` screen calls into.

    Grows one method per bridge call as each screen is built (design
    §4); holds almost no state of its own beyond what a given call
    needs — `_cancel_event` (set by `start_run`, read by `cancel_run`)
    is the one exception, since a running scalar simulation's own
    cancel button has to reach the same `threading.Event` the
    background worker thread is already checking. Every real piece of
    work still routes through `fim.gui`'s existing business-logic
    modules (`config_form`, `runner`, `batch_runner`, `recent_runs`,
    `animation`) or `fim.viz.scatter`'s public data functions, never
    reimplemented here.
    """

    def __init__(self) -> None:
        """Start with no run in flight."""
        self._cancel_event: threading.Event | None = None

    def start_run(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form, then start a scalar run pushing live progress to the page.

        Runs on a background `threading.Thread` (`fim.gui.runner.
        start_run`, unchanged from the Tk-era build — design §1.2) so
        this call itself returns immediately; the caller drives Screen 2
        from the `fim.onRunProgress`/`fim.onRunDone`/`fim.onRunCancelled`/
        `fim.onRunError` calls a second background thread pushes via
        `window.evaluate_js` as each message arrives (design §3.4's
        "push, not poll" — proven safe from an arbitrary background
        thread, not only `webview.start`'s own driver thread, before this
        method was written; see `test/gui/test_running_screen.py`).

        Args:
            values: The same shape `validate_form` accepts.

        Returns:
            `{"ok": True}` once the run has *started* — not once it
            finishes; the real outcome arrives via the pushed calls
            above. `{"ok": False, "message": ...}` if the form does not
            validate, or if `output_directory` (extremely unlikely: a
            fresh timestamp-named directory) already exists.
        """
        try:
            payload = form_values_to_payload(values)
            params = SimulationParams.from_mapping(payload)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        output_directory = paths.default_output_directory()
        message_queue: queue.Queue[runner.RunMessage] = queue.Queue()
        cancel_event = threading.Event()
        try:
            runner.start_run(params, output_directory, message_queue, cancel_event)
        except FileExistsError as error:
            return {"ok": False, "message": str(error)}
        self._cancel_event = cancel_event
        window = webview.windows[0]
        threading.Thread(
            target=_drain_run_messages,
            args=(window, message_queue, params.max_generations),
            daemon=True,
        ).start()
        return {"ok": True}

    def cancel_run(self) -> None:
        """Request cancellation of whichever scalar run `start_run` last started.

        A no-op if no run is currently in flight — mirrors `GuiProgress
        Store.write_generation`'s own tolerance of a `cancel_event` that
        was never going to matter, rather than raising for a Cancel click
        that arrives a moment after the run already finished on its own.
        """
        if self._cancel_event is not None:
            self._cancel_event.set()

    def get_starter_form(self) -> dict[str, str]:
        """Return a fresh form's default values (Screen 1, design §3.6, §4.1).

        `config_form.starter_form_values` is the single source of "GUI
        defaults" — the identical values `fim.cli.STARTER_CONFIG` itself
        expands to — so this bridge method adds no logic of its own
        beyond calling it.
        """
        return starter_form_values()

    def validate_form(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form exactly as "Run simulation" would (design §3.6, §4.7).

        Args:
            values: One string per `config_form.all_fields()` entry, plus
                every composite selector's own keys (`m_*`, `mu_*`,
                `cs_*`) — `webui/screens/input.js`'s own responsibility
                to collect from the live form.

        Returns:
            `{"ok": True}` if `values` parses into a valid
            `SimulationParams`; otherwise `{"ok": False, "message": ...,
            "field": ..., "tab": ...}` — `message` is the caught
            `ValueError`'s own text verbatim (matching the CLI's own
            wording, design §4.7), `field`/`tab` are `None` when the
            message names no field this form exposes (an unknown-key
            error, for instance), for the caller to switch to and
            highlight (§4.0 #2 of the original design) when they are not.
        """
        try:
            payload = form_values_to_payload(values)
            SimulationParams.from_mapping(payload)
        except ValueError as error:
            message = str(error)
            return {
                "ok": False,
                "message": message,
                "field": field_for_error(message),
                "tab": tab_for_error(message),
            }
        return {"ok": True}

    def load_yaml(self) -> dict[str, Any]:
        """Browse for and load a YAML config, returning the form values it renders to.

        Routes through `fim.cli.load_config` — the identical function
        `fim run` uses (design §3.6) — so a config that runs from the
        terminal loads identically here, error for error.

        Returns:
            `{"ok": True, "values": {...}}` on success;
            `{"ok": False, "message": ""}` if the dialog was cancelled
            (no banner to show); `{"ok": False, "message": "..."}` on a
            real load or validation failure.
        """
        window = webview.windows[0]
        selection = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=_YAML_FILE_TYPES
        )
        if not selection:
            return {"ok": False, "message": ""}
        try:
            params = load_config(Path(selection[0]))
            values = params_to_form_values(params)
        except (OSError, ValueError, yaml.YAMLError) as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "values": values}

    def save_yaml(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form, then save it as a `fim run`-compatible YAML file.

        Args:
            values: The same shape `validate_form` accepts.

        Returns:
            `{"ok": True, "path": "..."}` on success;
            `{"ok": False, "message": ""}` if the save dialog was
            cancelled; `{"ok": False, "message": "..."}` if the form does
            not currently validate (saving an invalid form is refused,
            the same as running one) or the write itself failed.
        """
        try:
            payload = form_values_to_payload(values)
            SimulationParams.from_mapping(payload)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        window = webview.windows[0]
        selection = window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename="config.yaml"
        )
        if not selection:
            return {"ok": False, "message": ""}
        target = Path(selection[0])
        try:
            target.write_text(payload_to_yaml_text(payload), encoding="utf-8")
        except OSError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "path": str(target)}

    def get_default_max_workers(self) -> int:
        """Return the Batch tab's own default parallel-worker count (design §4.1, H5).

        `max_workers` is not a `SimulationParams` field at all — it
        never reaches `form_values_to_payload` — so it has no
        `config_form` entry; this reuses `batch_runner.default_max_
        workers` directly rather than inventing a second default.
        """
        return default_max_workers()

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


def _drain_run_messages(
    window: webview.Window,
    message_queue: queue.Queue[runner.RunMessage],
    max_generations: int,
) -> None:
    """Push every `runner.RunMessage` to the page as it arrives, until the run ends.

    A plain `threading.Thread` target, not a closure captured inside
    `Api.start_run` — nothing about it needs `self`, and keeping it
    module-level matches this file's own picklability discipline for
    background-thread targets even though, unlike a `ProcessPoolExecutor`
    worker, a `threading.Thread` target is never actually pickled; the
    same shape either way is one less thing to reason about differently.

    Blocks on `message_queue.get()` between messages — the correct
    behavior for a dedicated thread with no other job, unlike `fim.gui.
    batch_runner`'s own non-blocking poll loop, which has to interleave
    watching several replicates' sidecar files at once.
    """
    while True:
        # Indexed access under an `if` on `message[0]`, not a tuple-
        # unpacking assignment: `message`'s static type is the whole
        # `runner.RunMessage` union, and only the `if` form lets mypy
        # narrow it to the one member each branch actually handles —
        # `store.py`'s and `batch_runner.py`'s own tests hit the same
        # "too many/few values to unpack" error the first time this
        # pattern was needed; indexing here follows that precedent
        # directly rather than rediscovering it.
        message = message_queue.get()
        if message[0] == "progress":
            progress_payload = {
                "generation": message[1],
                "maxGenerations": max_generations,
                "panels": message[2],
            }
            window.evaluate_js(f"fim.onRunProgress({json.dumps(progress_payload)})")
        elif message[0] == "done":
            result = message[1]
            payload = {
                "report": result.report,
                "panels": scatter_panels(result.final_state),
            }
            window.evaluate_js(f"fim.onRunDone({json.dumps(payload)})")
            return
        elif message[0] == "cancelled":
            window.evaluate_js(f"fim.onRunCancelled({json.dumps(message[1])})")
            return
        else:
            window.evaluate_js(f"fim.onRunError({json.dumps(message[1])})")
            return


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
