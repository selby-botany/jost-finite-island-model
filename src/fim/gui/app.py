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
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Final

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

# `paths.default_output_directory()` names a directory by the current
# second (`run-YYYYMMDD-HHMMSS`, UTC) — deliberately unchanged here
# (`test/test_paths.py`'s own regression proof for Milestone G0, "the
# timestamped folder name format... is unchanged"), so two calls inside
# the same real second collide on the identical path. A real, if narrow,
# reliability gap this bridge owns fixing, not `fim.paths` itself: a
# user clicking "Run simulation" again within the same second a previous
# attempt's directory was created — or several of this project's own
# `gui`-marked tests, each starting a real run in quick succession —
# would otherwise see a confusing "output directory already exists"
# error for what is, from their perspective, an entirely fresh run.
# `_START_RUN_COLLISION_*` bounds how long `start_run` waits for the
# wall clock to cross into a new second before giving up for real.
_START_RUN_COLLISION_RETRY_INTERVAL_SECONDS: Final = 0.1
_START_RUN_COLLISION_MAX_WAIT_SECONDS: Final = 2.0

# The Tk-era `results_screen.py`'s own six named statistics (design
# §4.3, requirement G3's "all six named statistics") — `FinalReport`
# also carries `H_ST`, added after that screen was first built; G3
# names exactly these six, so `H_ST` stays out of the results screen.
_RESULT_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T")


def format_statistic(value: float | None) -> str:
    """Format one `FinalReport` statistic for display (Screen 3, design §4.3).

    A direct parallel to `cli._format_optional` — not a shared import,
    per this package's established front-end-boundary convention
    (`runner.run_artifact_targets`'s own docstring) — kept here rather
    than in `webui/screens/results.js` so the six statistics reach the
    page as ready-to-show strings: one formatting rule in Python beats
    the same rule reimplemented a second time in JavaScript, with the
    two silently drifting apart later.
    """
    return "undefined" if value is None else f"{value:.6g}"


def _resolve_available_output_directory() -> Path:
    """Return a fresh, not-yet-existing timestamped output directory.

    Retries past a same-second collision with `paths.default_output_
    directory()` (`_START_RUN_COLLISION_*`'s own comment: "a user
    clicking Run again within the same second") by waiting for the wall
    clock to cross into a new second, up to `_START_RUN_COLLISION_MAX_
    WAIT_SECONDS`. A separate, pure function rather than inlined into
    `Api.start_run` specifically so it can be unit-tested directly
    (`test/gui/test_app_api.py`) — `Api.start_run` itself cannot be,
    since it also touches `webview.windows[0]`, unavailable without a
    real window.
    """
    output_directory = paths.default_output_directory()
    waited_seconds = 0.0
    while (
        output_directory.exists()
        and waited_seconds < _START_RUN_COLLISION_MAX_WAIT_SECONDS
    ):
        time.sleep(_START_RUN_COLLISION_RETRY_INTERVAL_SECONDS)
        waited_seconds += _START_RUN_COLLISION_RETRY_INTERVAL_SECONDS
        output_directory = paths.default_output_directory()
    return output_directory


def _reveal_in_file_browser(directory: Path) -> None:
    """Open `directory` in the platform's file browser.

    Ported unchanged from the Tk-era `results_screen.py`'s own
    `_reveal_in_file_browser` (design §4.3: "reuses whatever native
    folder-opening mechanism the Tk build's `results_screen.py` already
    implements... that helper is presentation-adjacent but toolkit-
    independent"). `check=False` throughout: a file browser's own exit
    status is not this button's concern, and `explorer.exe` on Windows
    is well known to return a nonzero status for benign reasons.
    """
    if sys.platform == "win32":
        subprocess.run(["explorer", str(directory)], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(directory)], check=False)
    else:
        subprocess.run(["xdg-open", str(directory)], check=False)


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

    def __init__(
        self,
        *,
        open_folder: Callable[[Path], None] = _reveal_in_file_browser,
        on_run_started: Callable[[], None] | None = None,
        on_message: Callable[[runner.RunMessage], None] | None = None,
    ) -> None:
        """Start with no run in flight.

        Args:
            open_folder: Reveals a directory in the platform file
                browser, called by `open_output_folder`. Defaults to
                the real, OS-dispatching implementation; injectable so
                tests never launch one — the same `open_folder`
                injection point the Tk-era `ResultsScreen.__init__`
                offered.
            on_run_started: Test-only hook, called synchronously from
                `start_run` the moment `_cancel_event` is assigned (real
                UI code never sets this — `create_window`'s own default
                `Api()` call passes neither hook, so production
                behavior is unchanged). Exists so a test can know
                *exactly* when `cancel_run` would stop being a no-op,
                without polling `window.evaluate_js` for a DOM signal
                to infer it — see `test/gui/test_running_screen.py`'s
                own module docstring for why a test-side
                `window.evaluate_js` poll loop is the wrong tool here.
            on_message: Test-only hook, called with every
                `runner.RunMessage` `_drain_run_messages` dispatches,
                right after that message's own `window.evaluate_js`
                push — the same "push, not poll" shape this bridge
                already uses toward the page, extended to let a test
                observe it directly in Python (a `threading.Event`/
                `queue.Queue`, no `evaluate_js` call of the test's own
                involved) instead of polling the DOM for the same fact.
        """
        self._cancel_event: threading.Event | None = None
        self._open_folder = open_folder
        self._on_run_started = on_run_started
        self._on_message = on_message

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
            validate, or if a fresh timestamp-named `output_directory`
            still collides after waiting out
            `_START_RUN_COLLISION_MAX_WAIT_SECONDS` for the wall clock
            to cross into a new second (see that constant's own
            comment) — in practice reached only if something else is
            actively writing into `results/` at exactly this rate.
        """
        try:
            payload = form_values_to_payload(values)
            params = SimulationParams.from_mapping(payload)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        output_directory = _resolve_available_output_directory()
        message_queue: queue.Queue[runner.RunMessage] = queue.Queue()
        cancel_event = threading.Event()
        try:
            runner.start_run(params, output_directory, message_queue, cancel_event)
        except FileExistsError as error:
            return {"ok": False, "message": str(error)}
        self._cancel_event = cancel_event
        if self._on_run_started is not None:
            self._on_run_started()
        window = webview.windows[0]
        threading.Thread(
            target=_drain_run_messages,
            args=(
                window,
                message_queue,
                params.max_generations,
                output_directory,
                self._on_message,
            ),
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

    def open_output_folder(self, path: str) -> None:
        """Reveal a completed run's output directory (Screen 3, design §4.3).

        Args:
            path: The directory to reveal — `webui/screens/results.js`'s
                own copy of the `outputDirectory` `onRunDone` last
                pushed it, not state this bridge tracks itself (design
                §4.0's "holds almost no state of its own" applies here
                too: nothing about a already-shown results screen
                needs `Api` to remember which run it was showing).
        """
        self._open_folder(Path(path))

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
    output_directory: Path,
    on_message: Callable[[runner.RunMessage], None] | None = None,
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

    `on_message`, if given (test-only — see `Api.__init__`'s own
    docstring), is called *after* each message's own `window.
    evaluate_js` push has already returned, never concurrently with it:
    this thread is still the only one calling `evaluate_js` at that
    point, so a test's own hook firing here never becomes a second
    concurrent caller the way a test-side polling loop would.
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
            if on_message is not None:
                on_message(message)
        elif message[0] == "done":
            result = message[1]
            payload = {
                "runId": result.run_id,
                "report": result.report,
                "panels": scatter_panels(result.final_state),
                "statistics": {
                    name: format_statistic(result.report[name])
                    for name in _RESULT_STATISTIC_NAMES
                },
                "outputDirectory": str(output_directory),
                "generationCount": result.manifest.generation_count,
            }
            window.evaluate_js(f"fim.onRunDone({json.dumps(payload)})")
            if on_message is not None:
                on_message(message)
            return
        elif message[0] == "cancelled":
            window.evaluate_js(f"fim.onRunCancelled({json.dumps(message[1])})")
            if on_message is not None:
                on_message(message)
            return
        else:
            window.evaluate_js(f"fim.onRunError({json.dumps(message[1])})")
            if on_message is not None:
                on_message(message)
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


def create_window(*, api: Api | None = None) -> webview.Window:
    """Build, but do not show, fim's one pywebview window over `webui/index.html`.

    Separate from `main` specifically so tests can drive the window
    themselves via `webview.start(callback)` without ever calling the
    real, blocking `main` — the same "construct real widgets, drive them
    synchronously, never call the real blocking entry point without a
    controlled exit" discipline the design's test plan requires (§6.1,
    §6.4), now against pywebview's own API instead of Tk's.

    Args:
        api: The `Api` instance to serve as `js_api`. Defaults to a
            plain `Api()` (production shape, unchanged); a test passes
            its own `Api(on_run_started=..., on_message=...)` to
            observe a run event-driven rather than by polling
            `window.evaluate_js` for a DOM signal (`test/gui/
            test_running_screen.py`'s own module docstring).

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
        js_api=api if api is not None else Api(),
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
