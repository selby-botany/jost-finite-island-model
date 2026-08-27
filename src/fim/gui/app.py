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

import contextlib
import json
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Final, Protocol, cast

import webview
import yaml
from webview.menu import Menu, MenuAction, MenuSeparator

from fim import __version__ as fim_version
from fim import paths, update
from fim.cli import load_config
from fim.engine import (
    RunResult,
    deterministic_run_id,
    replicate_summary,
    report_for_state,
    reports_summary,
)
from fim.gui import batch_runner, recent_runs, runner
from fim.gui.animation import pre_render_frames
from fim.gui.config_form import (
    CONVERGENCE_STATISTIC_NAMES,
    field_for_error,
    form_values_to_payload,
    params_to_form_values,
    payload_to_yaml_text,
    starter_form_values,
    tab_for_error,
)
from fim.gui.store import read_live_state, read_progress_sidecar
from fim.model.initial import generate_initial_state
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import read_manifest
from fim.reanalyze import reanalyze_trajectory
from fim.viz.scatter import (
    deme_pair_panel,
    frequency_points,
    panels_from_points,
    pooled_frequency_points,
    pooled_scatter_panels,
    scatter_panels,
)

_YAML_FILE_TYPES = ("YAML files (*.yaml;*.yml)", "All files (*.*)")
_TRAJECTORY_FILE_TYPES = ("trajectory.jsonl files (*.jsonl)", "All files (*.*)")

# The existing `pyproject.toml` `[project.urls] Documentation` value,
# reused rather than invented (in-app help design §3.2) -- the Help
# menu's own "Documentation on GitHub" item, and `get_about_info`'s own
# repository link, both point here.
_REPOSITORY_URL = "https://github.com/selby-botany/jost-finite-island-model"
_DOCUMENTATION_URL = f"{_REPOSITORY_URL}#readme"


class _EvaluatesJs(Protocol):
    """Structural stand-in for the one `webview.Window` capability this
    module's background-thread targets actually use — pushing a script
    to the page via `evaluate_js`. Every real caller still passes a
    real `webview.Window` (which satisfies this structurally, with no
    inheritance needed); this exists so `test/gui/test_app_api.py` can
    exercise `_push_batch_progress`'s own real file-reading logic
    against a lightweight fake instead, with no `gui` marker and no
    real window required for logic that never otherwise touches
    pywebview.
    """

    def evaluate_js(self, script: str) -> Any: ...


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

# How often `_drain_batch_messages` re-polls every in-flight replicate's
# own `.progress` sidecar between checking `message_queue` for the
# batch's terminal outcome (design §3.4, §7.6). Coarser than `fim.gui.
# runner.PROGRESS_THROTTLE_INTERVAL_SECONDS` (a scalar run's own,
# in-process push interval) on purpose: each tick here re-reads a whole
# `trajectory.jsonl` per currently-reporting replicate
# (`read_live_state`'s own docstring), a real, if usually small, cost
# that grows with both replicate count and how far each has run.
_BATCH_POLL_INTERVAL_SECONDS: Final = 0.5

# The Tk-era `results_screen.py`'s own six named statistics (design
# §4.3, requirement G3's "all six named statistics") — `FinalReport`
# also carries `H_ST`, added after that screen was first built; G3
# names exactly these six, so `H_ST` stays out of the results screen.
_RESULT_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T")

# `format_statistic`'s own bare-call default — `cli._format_optional`'s
# `.6g`, preserved unchanged so `test_format_statistic_matches_the_cli_
# own_format_optional` keeps proving genuine CLI/GUI parity at this one
# value. Real GUI display never reaches this default: every call site
# below (`Api.reanalyze_trajectory`, `_drain_run_messages`, `_batch_
# done_payload`) always passes `digits` explicitly, threaded from `Api.
# _significant_digits` — this constant only matters to a bare, direct
# call, the way the parity test above makes one.
_FORMAT_STATISTIC_DEFAULT_DIGITS: Final = 6

# The GUI's own, separately configurable display precision (View menu's
# "Significant digits" submenu, `Api.set_significant_digits`) — purely
# cosmetic ("no record"): every persisted artifact (`fim.persistence.
# report.write_report`, `fim.persistence.manifest.write_manifest`) is
# written with full float precision regardless of this value, and
# nothing here ever touches what lands on disk. Starts lower than
# `_FORMAT_STATISTIC_DEFAULT_DIGITS` on purpose — three significant
# digits is plenty to read a scatter or a results table at a glance,
# where `.6g`'s extra digits mostly added noise.
_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS: Final = 3
_MIN_SIGNIFICANT_DIGITS: Final = 1
_MAX_SIGNIFICANT_DIGITS: Final = 17


def format_statistic(
    value: float | None, digits: int = _FORMAT_STATISTIC_DEFAULT_DIGITS
) -> str:
    """Format one `FinalReport` statistic for display (Screen 3, design §4.3).

    A direct parallel to `cli._format_optional` — not a shared import,
    per this package's established front-end-boundary convention
    (`runner.run_artifact_targets`'s own docstring) — kept here rather
    than in `webui/screens/results.js` so the six statistics reach the
    page as ready-to-show strings: one formatting rule in Python beats
    the same rule reimplemented a second time in JavaScript, with the
    two silently drifting apart later. `digits` is always passed
    explicitly by a real caller (`Api._significant_digits`, the View
    menu's own "Significant digits" submenu) — see `_FORMAT_STATISTIC_
    DEFAULT_DIGITS`'s own comment for why the default here stays at
    six regardless of that configurable value's own default.
    """
    return "undefined" if value is None else f"{value:.{digits}g}"


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


def _active_window() -> webview.Window | None:
    """Return the app's own single window, or `None` if it no longer exists.

    Every bridge method below assumes exactly one window
    (`webview.windows[0]` — this app never opens a second one) and, until
    this function existed, indexed that assumption directly and
    unconditionally. That is correct for the entire lifetime of a real
    run of the app, but a bridge call already dispatched to a background
    thread by pywebview's own JS delivery mechanism (`webview/util.py`'s
    `_call`) can still reach here after the window it was headed for is
    gone — closing the app the instant a click fires, in production; a
    fresh pytest window replacing a just-destroyed one is the only
    concrete way this has been reproduced (a low-frequency, whole-
    session-only flake in `test/gui/test_results_screen.py`, not
    isolatable to any two tests run alone — see that module's own
    history). Either way nothing is listening for a bridge method's
    return value once its own window is gone, so there is nothing a
    caller can usefully do except decline gracefully instead of
    indexing an empty list and crashing pywebview's own delivery
    thread with an unhandled `IndexError` — promoted to a hard test
    failure here by `pyproject.toml`'s `filterwarnings`, and in a real
    build just an ugly, unnecessary traceback in the log.
    """
    return webview.windows[0] if webview.windows else None


def _save_dialog_path(
    selection: Sequence[str] | None,
) -> Path | None:
    """Normalise pywebview's platform-inconsistent SAVE dialog return value.

    pywebview's macOS backend (`cocoa.py`) returns a bare `str` for a
    SAVE dialog (the full chosen path) while every OPEN dialog and every
    non-macOS backend returns a tuple.  Calling `selection[0]` on a bare
    string yields the first *character* of the path, not the path itself
    — `Path("/Users/jim/.../config.yaml")[0]` is `Path("/")`, a directory,
    so our `is_dir()` cancel-guard silently swallows every real save.

    Accepts all observed shapes:
    - ``None``                → cancelled, return ``None``
    - ``""``  / ``()``        → cancelled (empty), return ``None``
    - ``str``                 → macOS SAVE, treat the whole string as the path
    - ``(str, ...)``          → sequence, take the first element
    """
    if not selection:
        return None
    path = Path(selection) if isinstance(selection, str) else Path(selection[0])
    return None if path.is_dir() else path


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
        on_message: (
            Callable[[runner.RunMessage | batch_runner.BatchMessage], None] | None
        ) = None,
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
            on_message: Test-only hook, called with every message
                `_drain_run_messages` (a `runner.RunMessage`) or
                `_drain_batch_messages` (a `batch_runner.BatchMessage`)
                dispatches, right after that message's own `window.
                evaluate_js` push — the same "push, not poll" shape
                this bridge already uses toward the page, extended to
                let a test observe it directly in Python (a `threading.
                Event`/`queue.Queue`, no `evaluate_js` call of the
                test's own involved) instead of polling the DOM for the
                same fact.
        """
        self._cancel_event: threading.Event | None = None
        self._open_folder = open_folder
        self._on_run_started = on_run_started
        self._on_message = on_message
        # The View menu's own "Significant digits" submenu (`set_
        # significant_digits`) mutates this directly; every real
        # `format_statistic` call site below reads it fresh at the
        # moment a screen is populated, so a change here takes effect
        # starting with the next run's own results — an already-open
        # Screen 3/4 was formatted once, at push time, and is not
        # retroactively reformatted.
        self._significant_digits: int = _DEFAULT_DISPLAY_SIGNIFICANT_DIGITS
        # The Progress screen's own live "Compare demes directly"
        # selector (`set_live_deme_pair`) mutates this directly; unlike
        # `_significant_digits` above, a background run's own thread
        # reads it fresh on *every* tick, not once at thread-start
        # (`get_live_deme_pair`, threaded into `_drain_run_messages`/
        # `_drain_batch_messages` as a bound-method callable rather than
        # a snapshotted value) — the whole point of a *live* selector is
        # that picking a pair mid-run affects the very next push, not
        # only a future run the way `_significant_digits` does.
        self._live_deme_pair: tuple[int, int] | None = None

    def start_run(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form, then start a run pushing live progress to the page.

        Dispatches to a scalar or a real parallel batch run based on
        `params.n_replicates` alone — design §4.1's "there is no
        separate 'batch mode' toggle; `n_replicates` *is* the toggle,"
        so this one bridge method serves both, and `webui/screens/
        input.js`'s own `onRunClicked` never needs to know or care
        which. Either path runs on a background `threading.Thread` so
        this call itself returns immediately; the caller drives Screen 2
        from the pushed `fim.onRun*`/`fim.onBatch*` calls a second
        background thread makes via `window.evaluate_js` as each
        message arrives (design §3.4's "push, not poll" — proven safe
        from an arbitrary background thread, not only `webview.start`'s
        own driver thread, before this method was written; see
        `test/gui/test_running_screen.py`).

        Args:
            values: The same shape `validate_form` accepts, plus (for a
                batch) the Batch tab's own `max_workers` field — not a
                `SimulationParams` field at all, parsed here directly.

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
        if params.n_replicates > 1:
            return self._start_batch_run(params, output_directory, values)
        return self._start_scalar_run(params, output_directory)

    def _start_scalar_run(
        self, params: SimulationParams, output_directory: Path
    ) -> dict[str, Any]:
        """The `n_replicates == 1` half of `start_run` (`fim.gui.runner`, unchanged)."""
        # Checked before starting anything with a side effect (`_active_
        # window`'s own docstring): a run started with no window left to
        # report to would run to completion with nobody ever draining
        # its message queue — an orphaned background thread and an
        # output directory nobody's UI ever shows, worse than simply
        # declining up front.
        window = _active_window()
        if window is None:
            return {"ok": False, "message": "no active window"}
        message_queue: queue.Queue[runner.RunMessage] = queue.Queue()
        cancel_event = threading.Event()
        try:
            runner.start_run(params, output_directory, message_queue, cancel_event)
        except FileExistsError as error:
            return {"ok": False, "message": str(error)}
        self._cancel_event = cancel_event
        # A fresh run never inherits a previous run's own live pair
        # selection — the same "never left showing stale state from
        # whichever screen used it last" reasoning `animation.js`'s own
        # `showAnimation` documents for its identical selector.
        self._live_deme_pair = None
        if self._on_run_started is not None:
            self._on_run_started()
        threading.Thread(
            target=_drain_run_messages,
            args=(
                window,
                message_queue,
                params.max_generations,
                params.d,
                output_directory,
                self._significant_digits,
                self.get_live_deme_pair,
                self._on_message,
            ),
            daemon=True,
        ).start()
        return {"ok": True}

    def _start_batch_run(
        self,
        params: SimulationParams,
        output_directory: Path,
        values: dict[str, str],
    ) -> dict[str, Any]:
        """The `n_replicates > 1` half of `start_run` (`fim.gui.batch_runner`, §7.6)."""
        # See `_start_scalar_run`'s identical check for why this comes
        # first, before any side effect.
        window = _active_window()
        if window is None:
            return {"ok": False, "message": "no active window"}
        max_workers = _parse_max_workers(values.get("max_workers", ""))
        run_id = deterministic_run_id(params)
        message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
        cancel_event = threading.Event()
        try:
            batch_runner.start_batch_run(
                params,
                output_directory,
                message_queue,
                cancel_event,
                max_workers=max_workers,
            )
        except FileExistsError as error:
            return {"ok": False, "message": str(error)}
        self._cancel_event = cancel_event
        # See `_start_scalar_run`'s identical reset for why.
        self._live_deme_pair = None
        if self._on_run_started is not None:
            self._on_run_started()
        threading.Thread(
            target=_drain_batch_messages,
            args=(
                window,
                message_queue,
                params,
                run_id,
                output_directory,
                self._significant_digits,
                self.get_live_deme_pair,
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

    def get_initial_state_panels(self, values: dict[str, str]) -> dict[str, Any]:
        """Compute scatter panels and statistics for the configured p_0 state.

        Called by `webui/screens/run-view-initial.js` on entry to the
        `initial` state (and on form-value changes in a future phase) so
        the canvas is never blank at startup — the user sees the starting
        frequency distribution immediately, the same scatter the running
        state would show at generation 0.

        Args:
            values: The same shape `validate_form` / `start_run` accept.

        Returns:
            `{"ok": True, "panels": [...], "demeCount": d,
            "statistics": {...}, "generation": 0,
            "maxGenerations": max_generations}` on success;
            `{"ok": False}` if `values` does not parse to a valid
            `SimulationParams` (the caller silently leaves the canvas
            blank — invalid form values are already reported through the
            normal validation path).
        """
        try:
            payload = form_values_to_payload(values)
            params = SimulationParams.from_mapping(payload)
        except ValueError:
            return {"ok": False}
        state = generate_initial_state(params)
        report = report_for_state(
            state,
            params,
            run_id="p_0",
            converged=False,
            reason="initial conditions",
        )
        statistics = {
            name: format_statistic(report[name], self._significant_digits)
            for name in _RESULT_STATISTIC_NAMES
        }
        return {
            "ok": True,
            "panels": scatter_panels(state),
            "demeCount": params.d,
            "statistics": statistics,
            "generation": 0,
            "maxGenerations": params.max_generations,
        }

    def get_initial_state_deme_pair_panel(
        self, values: dict[str, str], first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Return one chosen deme pair panel for the current p_0 form values.

        Supports the initial screen's axis selectors, which can choose a
        specific pair even when the default overview does not render every
        pair as separate panels (`d > 6`).

        Args:
            values: The same form payload `get_initial_state_panels` accepts.
            first_deme: 1-based X-axis deme number.
            second_deme: 1-based Y-axis deme number.

        Returns:
            `{"ok": True, "panel": ...}` on success; `{"ok": False,
            "message": ...}` if the form is invalid or the pair is invalid.
        """
        try:
            payload = form_values_to_payload(values)
            params = SimulationParams.from_mapping(payload)
            state = generate_initial_state(params)
            panel = deme_pair_panel(
                frequency_points(state), first_deme - 1, second_deme - 1
            )
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "panel": panel}

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
        window = _active_window()
        if window is None:
            return {"ok": False, "message": "no active window"}
        selection = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=_YAML_FILE_TYPES
        )
        if not selection or Path(selection[0]).is_dir():
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
        window = _active_window()
        if window is None:
            return {"ok": False, "message": "no active window"}
        selection = window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(Path.home()),
            save_filename="config.yaml",
        )
        target = _save_dialog_path(selection)
        if target is None:
            return {"ok": False, "message": ""}
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
        return batch_runner.default_max_workers()

    def get_significant_digits(self) -> int:
        """Return the GUI's current display-rounding precision.

        Mirrors `get_default_max_workers`'s own "let the page ask
        rather than duplicate a default" shape — nothing today calls
        this outside a test, since the View menu's own items each
        carry a fixed literal digit count rather than reflecting the
        current selection (`_build_menu`'s own comment on why: native
        menu items here have no dynamic-checkmark support to reflect
        back).
        """
        return self._significant_digits

    def set_significant_digits(self, digits: int) -> dict[str, Any]:
        """Change the GUI's display-rounding precision (View menu, design §4.5).

        Purely cosmetic and "no record": every persisted artifact keeps
        full float precision regardless of this value (`_DEFAULT_
        DISPLAY_SIGNIFICANT_DIGITS`'s own comment). Takes effect
        starting with the next `format_statistic` call a running or
        future screen makes — an already-open Screen 3/4 was formatted
        once, at push time, and is not retroactively reformatted.

        Returns:
            `{"ok": True, "digits": digits}` on success; `{"ok": False,
            "message": ...}` if `digits` falls outside `[_MIN_
            SIGNIFICANT_DIGITS, _MAX_SIGNIFICANT_DIGITS]` — double-
            precision floats carry roughly seventeen significant
            decimal digits, so anything past that bound would just
            print noise, not real information.
        """
        if not _MIN_SIGNIFICANT_DIGITS <= digits <= _MAX_SIGNIFICANT_DIGITS:
            return {
                "ok": False,
                "message": (
                    "significant digits must be between "
                    f"{_MIN_SIGNIFICANT_DIGITS} and {_MAX_SIGNIFICANT_DIGITS}"
                ),
            }
        self._significant_digits = digits
        return {"ok": True, "digits": digits}

    def get_live_deme_pair(self) -> tuple[int, int] | None:
        """Return the deme pair the Progress screen's live selector wants, or `None`.

        A bound-method reference to *this*, not a snapshot of its
        return value, is what `_start_scalar_run`/`_start_batch_run`
        actually thread into `_drain_run_messages`/`_drain_batch_
        messages` — each background thread calls it fresh on every
        tick, so a selection made mid-run affects the very next push,
        unlike `_significant_digits`'s own thread-start snapshot.
        """
        return self._live_deme_pair

    def set_live_deme_pair(
        self, first_deme: int | None, second_deme: int | None
    ) -> dict[str, Any]:
        """Set (or clear) the deme pair a running simulation's progress pushes include.

        The Progress screen's own live counterpart to Screens 3/4/5's
        "Compare demes directly" selector (`get_deme_pair_panel`/`get_
        batch_deme_pair_panel`/`get_animation_deme_pair_frames`) —
        those each recompute one already-*completed* run's own pair on
        demand; this instead tells a *currently running* simulation's
        own background thread which pair to keep including in every
        subsequent push, since polling a static trajectory on demand
        makes no sense for one still being written.

        No range/distinctness validation here (unlike `set_significant_
        digits`'s own bounds check) — this setter has no `d` or points
        on hand to validate against, and `screens/progress.js`'s own
        selector already disables "Show pair" whenever `first_deme ==
        second_deme` (`app.js`'s shared `wireDemePairSelector`). An
        out-of-range or identical pair reaching a push anyway is caught
        per-tick instead, where real data exists to catch it against
        (`_drain_run_messages`/`_push_batch_progress`'s own `deme_pair_
        panel` call, wrapped to skip that one tick's `pairPanel` rather
        than crash the whole push).

        Args:
            first_deme: 1-based deme number for the X axis, or `None`
                (with `second_deme` also `None`) to clear the selection
                back to the default panel — no UI trigger reaches this
                any more (simplify-main-plot design dropped the "Show
                overview" button), but the clearing behavior itself
                stays, directly callable and directly tested.
            second_deme: 1-based deme number for the Y axis, or `None`.

        Returns:
            `{"ok": True}` always — nothing here can fail validation on
            its own terms; see the docstring above for why.
        """
        if first_deme is None or second_deme is None:
            self._live_deme_pair = None
        else:
            self._live_deme_pair = (first_deme, second_deme)
        return {"ok": True}

    def list_recent_runs(self) -> list[dict[str, Any]]:
        """List every run under `results/`, newest first (Screen 6, design §4.6).

        `fim.gui.recent_runs.list_recent_runs` is unchanged from the
        Tk-era build (no `tkinter` import today, needs none tomorrow) —
        this bridge method adds little logic of its own beyond calling
        it and reshaping each `RecentRun` into a JSON-ready dict.
        `trajectoryPath` is joined here, in Python (`pathlib.Path`'s
        own platform-correct separator), rather than the page
        concatenating `directory` and `"trajectory.jsonl"` itself —
        string-joining a path client-side would silently produce a
        mixed-separator path on Windows. `None` for a batch row (design
        §0, §4.0 #9): it has no single trajectory of its own to open.
        """
        return [
            {
                "runId": run.run_id,
                "directory": str(run.directory),
                "trajectoryPath": (
                    None if run.is_batch else str(run.directory / "trajectory.jsonl")
                ),
                "endedAt": run.ended_at,
                "label": run.label,
                "isBatch": run.is_batch,
            }
            for run in recent_runs.list_recent_runs()
        ]

    def browse_for_trajectory(self) -> dict[str, Any]:
        """Browse for a `trajectory.jsonl` via the OS's own native file picker.

        `window.create_file_dialog(...)`, not an HTML `<input
        type="file">` — design §4.6's own "a *better* native-feel win
        than Tk's `filedialog.askopenfilename`, since pywebview's
        dialog is the OS's own file picker on every platform."

        Returns:
            `{"ok": True, "path": "..."}` on a real selection;
            `{"ok": False, "path": ""}` for a cancelled dialog —
            mirrors `load_yaml`'s own cancelled-dialog shape exactly,
            the established convention every dialog-backed bridge
            method here follows.
        """
        window = _active_window()
        if window is None:
            return {"ok": False, "path": ""}
        selection = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=_TRAJECTORY_FILE_TYPES
        )
        if not selection or Path(selection[0]).is_dir():
            return {"ok": False, "path": ""}
        return {"ok": True, "path": selection[0]}

    def open_run(self, values: dict[str, str]) -> dict[str, Any]:
        """Re-analyze a persisted trajectory, matching `fim stats`'s own semantics.

        Reached from Screen 6 (a recent-runs row or a browsed path) or
        Screen 4 ("Open replicate" — the exact same operation over one
        replicate's own `trajectory.jsonl`, design §4.4). The returned
        payload is deliberately shaped exactly like `_drain_run_
        messages`'s own `"done"` payload, so the caller can hand it
        straight to the already-built `window.fim.showResults` —
        design §4.6's "opening a run re-renders Screen 3... unchanged"
        realized here as literal reuse, not a second rendering path.

        Args:
            values: `{"trajectoryPath": "...", "generationMode":
                "final"|"choose", "generation": "...", "differentiation
                Orders": "..."}` — `webui/screens/open-run.js`'s own
                form fields, mirroring the Tk-era `OpenRunScreen`'s
                `_parse_generation`/`_parse_differentiation_orders`
                (ported here as module-level functions, the same
                presentation-adjacent-but-toolkit-independent shape
                `_reveal_in_file_browser`/`_parse_max_workers` already
                established).

        Returns:
            `{"ok": True, "runId", "report", "panels", "statistics",
            "outputDirectory", "generationCount", "demeCount"}` on
            success; `{"ok": False, "message": ...}` if no trajectory
            was given, the generation/q-sweep fields do not parse, or `fim.
            reanalyze.reanalyze_trajectory` itself raises (a
            trajectory-integrity failure, an edited file, or a
            generation that does not exist — design §4.7's "shown
            verbatim, matching `fim stats`'s wording").
        """
        trajectory_path_text = values.get("trajectoryPath", "")
        if not trajectory_path_text:
            return {"ok": False, "message": "no trajectory selected"}
        try:
            generation = _parse_generation(
                values.get("generationMode", "final"), values.get("generation", "")
            )
            differentiation_orders = _parse_differentiation_orders(
                values.get("differentiationOrders", "")
            )
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        trajectory_path = Path(trajectory_path_text)
        try:
            reanalyzed = reanalyze_trajectory(
                trajectory_path,
                generation=generation,
                differentiation_orders=differentiation_orders,
            )
        except (OSError, ValueError) as error:
            # `OSError` (its own `FileNotFoundError` case, in practice):
            # `reanalyze_trajectory`'s own `read_manifest` call raises it
            # directly for a missing/unreadable manifest — not a
            # `ValueError` `fim.reanalyze`'s own docstring documents,
            # but exactly the same "shown verbatim, matching `fim
            # stats`'s wording" (design §4.7) case from this bridge
            # method's own caller's point of view.
            return {"ok": False, "message": str(error)}
        report = reanalyzed.report
        return {
            "ok": True,
            "runId": reanalyzed.manifest.run_id,
            "report": report,
            "panels": scatter_panels(reanalyzed.state),
            "statistics": {
                name: format_statistic(
                    cast("float | None", report[name]), self._significant_digits
                )
                for name in _RESULT_STATISTIC_NAMES
            },
            "outputDirectory": str(trajectory_path.parent),
            "generationCount": reanalyzed.manifest.generation_count,
            "demeCount": reanalyzed.params.d,
        }

    def get_animation_frames(self, output_directory: str) -> dict[str, Any]:
        """Sample and ship every animation frame for one run, in a single call.

        Design §3.8, §4.5: loads the whole sampled set up front, as raw
        coordinate data — play, pause, and scrub are then pure
        client-side JavaScript (`webui/screens/animation.js`), with
        zero further Python calls and zero further rendering calls of
        any kind during playback.

        Args:
            output_directory: The run's own artifact directory (Screen
                3's `outputDirectory`, already on hand from whichever
                bridge call last raised it — `start_run`'s `"done"`
                push or `open_run`'s own return value).

        Returns:
            `{"ok": True, "demeCount": ..., "frames": [{"generation",
            "panels"}, ...]}` — one entry per sampled generation, each
            `panels` already in `scatter_panels`' own client-ready shape
            (`fim.viz.scatter.panels_from_points`, design §3.8: "whoever
            renders this... is responsible for any further reduction a
            high deme count needs"). `demeCount` rides along for the
            same reason `onRunDone`/`onBatchDone`'s own payloads carry
            it — `animation.js`'s own deme-pair selector needs it to
            populate its two axis dropdowns, exactly like Screens 3/4's
            identical selector already does. `{"ok": False, "message":
            ...}` if the trajectory or its manifest cannot be read.
        """
        directory = Path(output_directory)
        try:
            manifest = read_manifest(directory / "manifest.json")
            params = manifest.params()
            frames = pre_render_frames(
                directory / "trajectory.jsonl", params, manifest.run_id
            )
        except (OSError, ValueError, KeyError) as error:
            return {"ok": False, "message": str(error)}
        return {
            "ok": True,
            "demeCount": params.d,
            "frames": [
                {
                    "generation": frame.generation,
                    "panels": panels_from_points(frame.points, params.d),
                }
                for frame in frames
            ],
        }

    def get_animation_deme_pair_frames(
        self, output_directory: str, first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Recompute one explicit deme-pair panel for every sampled animation frame.

        `get_animation_frames`'s own "Compare demes directly" counterpart
        (design §3.8, §4.5) — the same choice Screens 3/4 already offer
        between the default view (a small-multiples pairwise grid for
        `d <= scatter.PAIRWISE_MAX_DEMES`, one Deme-1-vs-Deme-2 panel
        above it, unified-run-view design §3.6) and one explicit raw
        deme pair, extended to the whole animated trajectory rather
        than one static state. Still just one call:
        `webui/screens/animation.js` fires this once, when the user
        picks a pair, not once per frame or once per playback tick —
        design §3.8's own "zero further Python calls... during
        playback" holds exactly as it does for `get_animation_frames`
        itself, since the whole sampled set for that one pair comes
        back together, the same shape the default view's own frames
        already arrived in.

        Args:
            output_directory: The run's own artifact directory.
            first_deme: 1-based deme number for the X axis, matching
                every panel's own "Deme N" label convention.
            second_deme: 1-based deme number for the Y axis.

        Returns:
            `{"ok": True, "frames": [{"generation", "panel"}, ...]}` —
            one entry per sampled generation, in the same order `get_
            animation_frames` already returns them (`pre_render_frames`
            re-samples identically both times — same trajectory, same
            `max_frames` default — so the two calls' own generation
            lists always agree). `{"ok": False, "message": ...}` if the
            trajectory or its manifest cannot be read, or the requested
            demes are out of range or identical.
        """
        directory = Path(output_directory)
        try:
            manifest = read_manifest(directory / "manifest.json")
            params = manifest.params()
            frames = pre_render_frames(
                directory / "trajectory.jsonl", params, manifest.run_id
            )
            panel_frames = [
                {
                    "generation": frame.generation,
                    "panel": deme_pair_panel(
                        frame.points, first_deme - 1, second_deme - 1
                    ),
                }
                for frame in frames
            ]
        except (OSError, ValueError, KeyError) as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "frames": panel_frames}

    def get_deme_pair_panel(
        self, output_directory: str, first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Recompute one explicit deme-pair 2-D panel for a completed run (Screen 3).

        The large-`d` counterpart to Screen 3's own default panel
        (`showResults`'s own `panels[0]`, drawn whenever `d >
        scatter.PAIRWISE_MAX_DEMES` — Deme 1 vs. Deme 2 by default,
        unified-run-view design §3.6): this bridge method lets the user
        switch to any other specific raw deme pair instead, on demand,
        rather than the page ever computing or requesting every `C(d,
        2)` pair up front (unbounded in `d`, unlike the direct/pairwise
        layout `panels_from_points` already handles automatically for
        small `d`).

        Args:
            output_directory: The run's own artifact directory (Screen
                3's `outputDirectory`, already on hand from whichever
                bridge call last raised it — `start_run`'s `"done"`
                push or `open_run`'s own return value).
            first_deme: 1-based deme number for the X axis, matching
                every panel's own "Deme N" label convention.
            second_deme: 1-based deme number for the Y axis.

        Returns:
            `{"ok": True, "panel": ...}`, `panel` being one
            `deme_pair_panel`-shaped entry. `{"ok": False, "message":
            ...}` if the trajectory cannot be read, or the requested
            demes are out of range or identical.
        """
        trajectory_path = Path(output_directory) / "trajectory.jsonl"
        try:
            state = reanalyze_trajectory(trajectory_path).state
            panel = deme_pair_panel(
                frequency_points(state), first_deme - 1, second_deme - 1
            )
        except (OSError, ValueError) as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "panel": panel}

    def get_batch_deme_pair_panel(
        self, output_directory: str, first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Recompute one explicit deme-pair pooled 2-D panel for a completed batch.

        `get_deme_pair_panel`'s own counterpart for a batch's pooled
        scatter (`onBatchDone`'s own `panels[0]`, `_batch_done_payload`
        below): every published replicate's own final state is
        rediscovered from disk by directory name
        (`batch_runner.replicate_output_directory`'s own `replicate-
        NNN` naming), not passed in from the page — the same "read the
        published, atomic artifacts on disk" source of truth `list_
        recent_runs` and `open_run` already use, rather than the page
        tracking and forwarding every replicate's own trajectory path
        itself.

        Args:
            output_directory: The batch's own top-level artifact
                directory (Screen 4's `outputDirectory`).
            first_deme: 1-based deme number for the X axis.
            second_deme: 1-based deme number for the Y axis.

        Returns:
            `{"ok": True, "panel": ...}` on success; `{"ok": False,
            "message": ...}` if no replicate trajectory can be found or
            read, or the requested demes are out of range or
            identical.
        """
        directory = Path(output_directory)
        trajectory_paths = sorted(directory.glob("replicate-*/trajectory.jsonl"))
        if not trajectory_paths:
            return {"ok": False, "message": f"no replicates found under {directory}"}
        try:
            states = [reanalyze_trajectory(path).state for path in trajectory_paths]
            panel = deme_pair_panel(
                pooled_frequency_points(states), first_deme - 1, second_deme - 1
            )
        except (OSError, ValueError) as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "panel": panel}

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

    def open_external_link(self, url: str) -> None:
        """Open `url` in the OS default browser (in-app help design §4.3).

        Every Tier 2 doc link and every bare `http(s)://` link inside a
        Tier 1 doc routes here, never to native `<a>` navigation — an
        unhandled link click inside a pywebview window can navigate the
        *whole application window* away from `index.html`, not open a
        new tab. `webbrowser.open`, best-effort, no return value the
        caller needs — the same `_reveal_in_file_browser` precedent this
        module already follows for another OS-dispatched action.
        """
        webbrowser.open(url)

    def check_for_updates(self) -> dict[str, Any]:
        """Perform the same opt-in GitHub release check `fim update --check` does.

        Reuses `fim.update` directly (that module's own docstring: "so
        `fim.gui`'s 'Check for updates' action performs exactly the same
        GitHub Releases lookup and version comparison... rather than a
        second implementation") — this bridge method is the first real
        caller of that promise from the pywebview build; the Tk build's
        own equivalent action called the same module the same way.

        Returns:
            `{"ok": True, "available": bool, "current": str, "latest":
            str, "url": str}` on a successful check (`available` is
            whether `latest` is newer than `current` — `fim update
            --check`'s own three-way `comparison`, collapsed to the one
            boolean the page actually needs to decide whether to show a
            "download" link); `{"ok": False, "message": ...}` if the
            network call itself fails (`fim.update.latest_release`'s own
            documented `RuntimeError` — a failed opt-in check, not an
            application error).
        """
        try:
            latest_tag, release_url = update.latest_release()
        except RuntimeError as error:
            return {"ok": False, "message": str(error)}
        latest_version = latest_tag.removeprefix("v")
        comparison = update.compare_versions(fim_version, latest_version)
        return {
            "ok": True,
            "available": comparison < 0,
            "current": fim_version,
            "latest": latest_version,
            "url": release_url,
        }

    def get_about_info(self) -> dict[str, str]:
        """Return the static "About fim" facts the Help menu shows (design §4.5).

        No bridge state, no network call — `fim.__version__` and the
        project's own already-declared URLs, the same values `pyproject.
        toml`'s `[project.urls]` and `fim --version` already report.
        """
        return {
            "version": fim_version,
            "repository": _REPOSITORY_URL,
            "license": "GNU Affero General Public License v3 or later (AGPLv3+)",
        }


def _drain_run_messages(
    window: _EvaluatesJs,
    message_queue: queue.Queue[runner.RunMessage],
    max_generations: int,
    deme_count: int,
    output_directory: Path,
    digits: int = _FORMAT_STATISTIC_DEFAULT_DIGITS,
    live_deme_pair: Callable[[], tuple[int, int] | None] = lambda: None,
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

    `deme_count` rides along for both the `"done"` payload's own
    `demeCount` field (`results.js`'s large-`d` deme-pair selector) and
    every `"progress"` push's own `demeCount` (`screens/progress.js`'s
    identical *live* selector, wired once the first push a fresh run
    makes carries it); nothing here computes with it directly either
    way.

    `digits` is `_start_scalar_run`'s own snapshot of `Api._significant_
    digits` at the moment this thread was started, not a live read of
    it later — a change made mid-run via the View menu applies starting
    with the *next* run's own thread, not this one already in flight.
    `live_deme_pair`, by contrast, *is* read fresh on every `"progress"`
    tick (`Api.get_live_deme_pair`, a bound-method reference, not a
    snapshotted value) — the Progress screen's own live "Compare demes
    directly" selector needs a pair picked mid-run to affect the very
    next push, unlike `digits`. Defaults to a no-op returning `None`,
    matching every other optional parameter here (`digits`, `on_
    message`) — nothing calls this function directly today (always via
    `_start_scalar_run`'s own thread), but a future direct call needs
    no new argument to keep working.
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
            progress_payload: dict[str, object] = {
                "generation": message[1],
                "maxGenerations": max_generations,
                "panels": message[2],
                "demeCount": deme_count,
                # The six named statistics for this tick's own state
                # (`runner.py`'s `on_generation`, `message[4]`) — keeps
                # the running-state stats table live and populated
                # rather than blank until the run finishes (design §8
                # Phase G).
                "statistics": {
                    name: format_statistic(message[4][name], digits)
                    for name in _RESULT_STATISTIC_NAMES
                },
            }
            pair = live_deme_pair()
            if pair is not None:
                first_deme, second_deme = pair
                # Out of range for this run's own `d`, or the two demes
                # match: `screens/progress.js`'s own selector already
                # keeps "Show pair" disabled whenever X equals Y, so
                # this is a defensive fallback, not an expected path —
                # skip this one tick's `pairPanel` rather than drop the
                # whole progress push over it.
                with contextlib.suppress(ValueError):
                    progress_payload["pairPanel"] = deme_pair_panel(
                        message[3], first_deme - 1, second_deme - 1
                    )
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
                    name: format_statistic(result.report[name], digits)
                    for name in _RESULT_STATISTIC_NAMES
                },
                "outputDirectory": str(output_directory),
                "generationCount": result.manifest.generation_count,
                "demeCount": deme_count,
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


def _parse_max_workers(value: str) -> int | None:
    """Parse the Batch tab's own `max_workers` field.

    Returns `None` (meaning "use `batch_runner.default_max_workers()`")
    for anything that does not parse to a positive integer — this field
    has no validation UI of its own (`config_form.py`'s "O(1)/O(d)
    fields get a live widget" cardinality rule never covered it, since
    it is not a `SimulationParams` field at all), so a blank or
    corrupted value falls back to the default silently rather than
    blocking the run on a field the user has no way to see flagged.
    """
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_generation(mode: str, generation_text: str) -> int | None:
    """Parse Screen 6's "final"/"choose" generation selector.

    Ported directly from the Tk-era `OpenRunScreen._parse_generation`
    (design §4.6) — the same parsing rule, moved from a private Tk
    screen method to a module-level function here.

    Args:
        mode: `"final"` (the default) or `"choose"`.
        generation_text: The explicit generation number, read only when
            `mode == "choose"`.

    Returns:
        `None` for `"final"` (`reanalyze_trajectory`'s own "defaults to
        the run's final persisted generation"); the parsed integer for
        `"choose"`.

    Raises:
        ValueError: If `mode == "choose"` and `generation_text` is
            empty or not an integer.
    """
    if mode != "choose":
        return None
    try:
        return int(generation_text.strip())
    except ValueError as error:
        raise ValueError("generation must be an integer") from error


def _parse_differentiation_orders(text: str) -> tuple[float, ...]:
    """Parse Screen 6's optional differentiation-q sweep field.

    Ported directly from the Tk-era `OpenRunScreen`'s own module-level
    `_parse_differentiation_orders` (design §4.6) — unchanged.

    Args:
        text: Zero or more space/comma-separated numbers; empty means
            no sweep.

    Returns:
        `()` for an empty/whitespace-only `text`; the parsed orders
        otherwise.

    Raises:
        ValueError: If any token is not a number.
    """
    stripped = text.strip()
    if not stripped:
        return ()
    tokens = stripped.replace(",", " ").split()
    try:
        return tuple(float(token) for token in tokens)
    except ValueError as error:
        raise ValueError(
            "differentiation-q sweep must be space/comma-separated numbers"
        ) from error


def _push_batch_progress(
    window: _EvaluatesJs,
    params: SimulationParams,
    run_id: str,
    working_directory: Path,
    live_deme_pair: Callable[[], tuple[int, int] | None] = lambda: None,
    digits: int = _FORMAT_STATISTIC_DEFAULT_DIGITS,
) -> None:
    """Read every currently-reporting replicate's live state, push a pooled scatter.

    A replicate that has not yet written its own `.progress` sidecar
    (`read_progress_sidecar` returns `None`) or whose sidecar-reported
    generation is not yet safely readable (`read_live_state` returns
    `None` — a transient race, not an error; see its own docstring) is
    silently skipped for *this* tick, not treated as a failure: the
    next tick, `_BATCH_POLL_INTERVAL_SECONDS` later, tries again.

    `live_deme_pair` (`Api.get_live_deme_pair`, a bound-method
    reference read fresh every tick — see `_drain_run_messages`'s own
    identical parameter for the full reasoning) computes one more
    panel for the Progress screen's own live "Compare demes directly"
    selector, reusing this tick's own already-pooled points rather than
    re-reading every replicate's trajectory a second time.

    `statistics` is `reports_summary`'s own across-replicate confidence
    interval, computed from each currently-reporting replicate's *live*
    report (`report_for_state` on its just-read state) and pre-formatted
    server-side exactly like `_batch_done_payload`'s own `summary` field
    — keeps the running-state stats table live and populated rather
    than blank until the batch finishes (design §8 Phase G). Naturally
    empty (`{}`) for the first tick or two, before a second replicate
    has reported anything to summarize yet — `reports_summary` never
    raises for that, unlike `replicate_summary`.
    """
    states: list[ModelState] = []
    for index in range(1, params.n_replicates + 1):
        replicate_run_id = f"{run_id}-r{index:03}"
        directory = batch_runner.replicate_output_directory(
            working_directory, run_id, replicate_run_id
        )
        sidecar = read_progress_sidecar(directory / ".progress")
        if sidecar is None:
            continue
        state = read_live_state(
            directory / "trajectory.jsonl",
            replicate_run_id,
            sidecar["generation"],
            params.loci,
        )
        if state is not None:
            states.append(state)
    pooled_points = pooled_frequency_points(states) if states else None
    panels = (
        panels_from_points(pooled_points, params.d) if pooled_points is not None else []
    )
    raw_summary = reports_summary(
        [
            report_for_state(
                state, params, run_id=run_id, converged=False, reason="in progress"
            )
            for state in states
        ]
    )
    statistics = {
        name: {
            "mean": format_statistic(interval["mean"], digits),
            "low": format_statistic(interval["low"], digits),
            "high": format_statistic(interval["high"], digits),
            "sampleCount": interval["sample_count"],
        }
        for name, interval in raw_summary.items()
    }
    progress_payload: dict[str, object] = {
        "replicateCount": params.n_replicates,
        "reportedReplicateCount": len(states),
        "panels": panels,
        "demeCount": params.d,
        "statistics": statistics,
    }
    pair = live_deme_pair()
    if pair is not None and pooled_points is not None:
        first_deme, second_deme = pair
        # Same defensive fallback as `_drain_run_messages`'s own
        # identical case: out of range for this batch's own `d`, or
        # the two demes match.
        with contextlib.suppress(ValueError):
            progress_payload["pairPanel"] = deme_pair_panel(
                pooled_points, first_deme - 1, second_deme - 1
            )
    window.evaluate_js(f"fim.onBatchProgress({json.dumps(progress_payload)})")


def _batch_done_payload(
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    results: tuple[RunResult, ...],
    digits: int = _FORMAT_STATISTIC_DEFAULT_DIGITS,
) -> dict[str, Any]:
    """Build `fim.onBatchDone`'s own payload from a batch's final results (design §4.4).

    `replicates` is the row data for Screen 4's own table (one row per
    *published* replicate — `results`' own length, not necessarily
    `params.n_replicates`: an adaptive `replicate_tolerance` stop can
    end a batch short of its own cap), including each row's own
    `trajectoryPath` — joined here, in Python
    (`batch_runner.replicate_output_directory`), rather than the page
    concatenating `outputDirectory` and a replicate directory name
    itself (the same Windows mixed-separator risk `list_recent_runs`'
    own docstring names) — for "Open replicate" (design §4.4) to hand
    straight to `Api.open_run` with no path logic of its own. `summary`
    is `replicate_summary`'s own per-statistic confidence interval,
    pre-formatted server-side (`format_statistic`, matching every
    other statistic this bridge ever sends the page — §3.5's "the
    client never reimplements Python's own display formatting"
    extended from Screen 3 to Screen 4) — omitted entirely (an empty
    `{}`) if `replicate_summary` itself has too few results to define
    an interval from, its own documented `ValueError` case, not
    something this bridge treats as a real error partway through an
    otherwise-successful batch.

    `digits` is `_drain_batch_messages`'s own snapshot of `Api.
    _significant_digits`, taken when the batch's own background thread
    started — matching `_drain_run_messages`'s identical "started, not
    live" scope for the same View-menu setting.

    `p0Statistics` carries the six named statistics for the seeded
    generation-0 state, pre-formatted like every other statistic this
    bridge sends — giving the table a baseline row the researcher can
    compare every replicate against.
    """
    replicates = [
        {
            "generation": result.report["generation"],
            "converged": result.report["converged"],
            "reason": result.report["reason"],
            "statistics": {
                name: format_statistic(result.report[name], digits)
                for name in _RESULT_STATISTIC_NAMES
            },
            "trajectoryPath": str(
                batch_runner.replicate_output_directory(
                    output_directory, run_id, result.run_id
                )
                / "trajectory.jsonl"
            ),
            # `result.run_id` (`"{run_id}-r{index:03}"`, `batch_runner.
            # replicate_output_directory`'s own naming convention) --
            # `webui/screens/run-view-completed.js`'s own `replicateLabel`
            # extracts the short `#NNN` suffix for display. Multiple
            # replicates legitimately converging at the same generation
            # is unremarkable, not a bug, so the table needs this to
            # tell those rows apart.
            "replicateId": result.run_id,
        }
        for result in results
    ]
    try:
        raw_summary = replicate_summary(results)
    except ValueError:
        raw_summary = {}
    summary = {
        name: {
            "mean": format_statistic(interval["mean"], digits),
            "low": format_statistic(interval["low"], digits),
            "high": format_statistic(interval["high"], digits),
            "sampleCount": interval["sample_count"],
        }
        for name, interval in raw_summary.items()
    }
    p0_state = generate_initial_state(params)
    p0_report = report_for_state(
        p0_state,
        params,
        run_id=run_id,
        converged=False,
        reason="initial conditions",
    )
    p0_statistics = {
        name: format_statistic(p0_report[name], digits)
        for name in _RESULT_STATISTIC_NAMES
    }
    return {
        "runId": run_id,
        "outputDirectory": str(output_directory),
        "panels": pooled_scatter_panels(
            [result.final_state for result in results], params.d
        ),
        "replicates": replicates,
        "summary": summary,
        "demeCount": params.d,
        "p0Statistics": p0_statistics,
    }


def _drain_batch_messages(
    window: _EvaluatesJs,
    message_queue: queue.Queue[batch_runner.BatchMessage],
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    digits: int = _FORMAT_STATISTIC_DEFAULT_DIGITS,
    live_deme_pair: Callable[[], tuple[int, int] | None] = lambda: None,
    on_message: Callable[[batch_runner.BatchMessage], None] | None = None,
) -> None:
    """Push every `batch_runner.BatchMessage`, polling live progress between them.

    Unlike `_drain_run_messages`'s single blocking `message_queue.get()`
    loop, this thread has two jobs to interleave (`fim.gui.batch_
    runner`'s own module docstring: "a lightweight poller... discovers
    progress by reading each in-flight replicate's own `.progress`
    sidecar"): draining `message_queue` for the batch's own terminal
    outcome, and periodically pushing a pooled live-progress scatter —
    nothing about a batch's own per-generation progress is queued the
    way a scalar run's is, since it is entirely file-mediated (design
    §3.4).

    The very first message is always `("started", working_directory)`
    — `batch_runner._batch_worker` posts it before calling `fim(...)`
    at all — so this thread blocks for it specifically before its own
    poll loop starts: nothing here is possible without knowing where
    the batch's replicates are actually writing.

    `digits` rides along only to hand to `_batch_done_payload` once the
    batch's own `"done"` message arrives — this thread does no
    statistic formatting of its own before that point. `live_deme_pair`
    rides along the opposite way — read on every poll tick, handed
    straight to `_push_batch_progress` (see its own docstring), never
    to `_batch_done_payload` (Screen 4's own completed-batch selector
    is a separate, on-demand mechanism, `get_batch_deme_pair_panel`).
    """
    started = message_queue.get()
    if started[0] != "started":
        # Structurally unreachable: `_batch_worker` always posts
        # `("started", working_directory)` first, before anything else
        # (`batch_runner.py`'s own comment at that call site) — this
        # narrows `started[1]`'s type for mypy the same way every other
        # `BatchMessage`/`RunMessage` union member is narrowed elsewhere
        # in this file, rather than indexing it away with a cast.
        raise RuntimeError(f"expected a 'started' message first, got {started[0]!r}")
    if on_message is not None:
        on_message(started)
    working_directory = started[1]
    while True:
        try:
            message = message_queue.get(timeout=_BATCH_POLL_INTERVAL_SECONDS)
        except queue.Empty:
            _push_batch_progress(
                window, params, run_id, working_directory, live_deme_pair, digits
            )
            continue
        if message[0] == "done":
            payload = _batch_done_payload(
                params, run_id, output_directory, message[1], digits
            )
            window.evaluate_js(f"fim.onBatchDone({json.dumps(payload)})")
        elif message[0] == "cancelled":
            cancelled_payload = {"replicateIndex": message[1], "generation": message[2]}
            window.evaluate_js(f"fim.onBatchCancelled({json.dumps(cancelled_payload)})")
        else:
            window.evaluate_js(f"fim.onBatchError({json.dumps(message[1])})")
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


def create_window(*, api: Api | None = None, hidden: bool = False) -> webview.Window:
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
        hidden: Forwarded to `webview.create_window`'s own `hidden`
            parameter. `False` (the default) is production's own real
            shape — a real user expects to see the window they just
            opened. Every `gui`-marked test passes `True`: the DOM
            renders and `evaluate_js` behaves identically either way
            (confirmed directly against a real window before this
            parameter was added), so a headless CI runner and a local
            `git push` both drive the exact same window a visible one
            would be, without a real macOS/Windows window ever
            flashing on screen — the literal complaint that motivated
            this parameter, not merely a CI convenience.

    Raises:
        RuntimeError: If pywebview itself reports the window as never
            created — `webview.create_window`'s own documented (if,
            absent any `window.events.initialized` hook of our own,
            never actually observed) `None` return, for "window
            initialization is cancelled." Surfaced loudly rather than
            silently narrowed away, since nothing downstream of this
            function is prepared to run without a real window.
    """
    # pywebview's local-file HTTP adapter creates a `ThreadingMixIn`
    # request handler per WebKit connection. Its default non-daemon
    # handlers can outlive a closed Cocoa window and block Python's
    # interpreter shutdown in `wait_for_thread_shutdown`.
    ThreadingMixIn.daemon_threads = True
    created = webview.create_window(
        "fim",
        url=str(_webui_directory() / "index.html"),
        js_api=api if api is not None else Api(),
        width=900,
        height=700,
        hidden=hidden,
    )
    if created is None:
        raise RuntimeError("pywebview did not create a window")
    return created


_STATISTIC_SUBSCRIPT_LETTERS: Final[dict[str, str]] = {"S": "ₛ", "T": "ₜ"}


def _statistic_menu_label(name: str) -> str:
    """Render one `CONVERGENCE_STATISTIC_NAMES` entry for a native menu item.

    Args:
        name: A `CONVERGENCE_STATISTIC_NAMES` entry, e.g. `"G_ST"`.

    Returns:
        The name with its `_`-suffix (if any) rendered as true Unicode
        subscript characters (`"G_ST"` -> `"Gₛₜ"`) instead of a literal
        underscore — native menu items are plain text, so the `<sub>`
        tags `index.html`'s own labels use for the identical purpose
        are not available here; this is the closest plain-text
        equivalent. Raises `KeyError` on a suffix letter with no
        subscript mapping above, deliberately: every current statistic
        name only ever needs `S`/`T`, so a future addition needing
        something else should fail loudly here rather than silently
        rendering a wrong or missing glyph.
    """
    base, _, suffix = name.partition("_")
    if not suffix:
        return base
    return base + "".join(_STATISTIC_SUBSCRIPT_LETTERS[letter] for letter in suffix)


def _build_menu(window: webview.Window) -> list[Menu]:
    """Build the native File/Configure/Run/View/Help menu bar (design §4.5).

    Edit and Window are still left out (design §4.5's own "considered,
    deferred": every text field already gets native Cut/Copy/Paste from
    the WebView engine itself, and a Window menu has no app-specific
    value for a single-window tool) — but View is not deferred any
    longer: "significant digits to display" is a genuine display
    toggle, unlike anything on offer when that call was first made.
    Configure is new too, for a different reason — decluttering the
    input screen's own canvas: the six-tab bar (design §4.1) that used
    to be the only way to reach Population/Migration/Mutation/Initial
    conditions/Convergence/Batch is now hidden (`app.css`'s own `.tab-
    bar { display: none; }`), with this menu as the replacement entry
    point — the tabs, panels, and every field inside them are otherwise
    completely unchanged; only how you get to one is different.

    Every item except Quit is a thin closure calling `window.evaluate_js(
    "fim.menu.X()")` — the identical "no-op stub, overridden by whichever
    screen owns the real behavior" convention `app.js` already
    established for `onRunProgress`/`onBatchDone`/etc., extended to a
    `fim.menu` namespace so no new Python business logic exists anywhere
    in this menu: every item reuses an `Api` method or JS-side screen
    state a screen already tracks (design §4.5's own "the menu is a
    second entry point into logic that exists, not new logic"). Quit
    alone needs no JS round trip — `window.destroy()` is a window-level
    call, not app state.

    The View menu's own "Significant digits" submenu, and Configure's
    own "Deme weighting"/"Mutation model"/"Convergence statistic"
    submenus (unified-run-view design §3.1.3), are this menu's exception
    to "no new Python business logic": each item is a fixed literal
    value, not a reflection of whatever is currently selected —
    pywebview's own `MenuAction` has no portable, dynamic checkmark/
    label-update support to show that back, so none of these four
    submenus (unlike every other item here) can indicate the current
    choice; the setting itself is still fully real, only its on-menu
    display is not.
    """

    def dispatch(script: str) -> Callable[[], None]:
        def call_into_page() -> None:
            # `setTimeout(..., 0)`, not a bare `script` expression: calling
            # an `async` `fim.menu.*` function (`newConfiguration`,
            # `checkForUpdates`, `about` — every one that itself `await`s a
            # real `window.pywebview.api.*` bridge call) directly as the
            # `evaluate_js` expression deadlocks — confirmed live, not
            # theoretical: `conftest.py`'s own `drive_and_read` docstring
            # already documents the exact same pywebview behavior for the
            # test harness's own `ready` polling ("whatever `evaluate_js`
            # does internally... appears to block the page's own JS event
            # loop for the duration of that one call," starving any
            # microtask the awaited call needs to ever resolve). Wrapping
            # every dispatch in `setTimeout` — not only the `async` ones,
            # so this dispatcher never has to know or track which
            # `fim.menu.*` methods happen to be `async` today — makes the
            # outer `evaluate_js` expression itself a plain, synchronous
            # `setTimeout` call (returns a timer id immediately), with the
            # real work deferred to the page's own next event-loop tick,
            # fully decoupled from this call's own return.
            window.evaluate_js(f"setTimeout(() => {{ {script} }}, 0);")

        return call_into_page

    file_menu = Menu(
        "File",
        [
            MenuAction("New configuration", dispatch("fim.menu.newConfiguration()")),
            MenuAction("Open configuration…", dispatch("fim.menu.openConfiguration()")),
            MenuAction("Save configuration…", dispatch("fim.menu.saveConfiguration()")),
            MenuSeparator(),
            MenuAction("Open run…", dispatch("fim.menu.openRun()")),
            MenuAction(
                "Reveal output folder", dispatch("fim.menu.revealOutputFolder()")
            ),
            MenuSeparator(),
            MenuAction("Quit fim", window.destroy),
        ],
    )
    # `(tab id, menu label)` pairs, in the exact order `index.html`'s
    # own (now-hidden) tab bar used — one native `MenuAction` per
    # section, each just asking `fim.menu.configureTab` (`screens/
    # input.js`) to open that section's own `modal-<id>` dialog over
    # whatever the run view currently shows (unified-run-view design
    # §3.1) — the same modal an invalid field on "Run simulation" opens
    # for the "jump to the invalid section" case (design §4.0 #2) — no
    # second, menu-only navigation path.
    configure_tabs = (
        ("population", "Population"),
        ("migration", "Migration"),
        ("mutation", "Mutation"),
        ("initial_conditions", "Initial conditions"),
        ("convergence", "Convergence"),
        ("batch", "Batch"),
    )
    # Direct value-selector leaves (design §3.1.3, user-approved as the
    # starting set: "try it and refine it as needed"). `deme_weighting`/
    # `mutation_model` are genuinely categorical — one `MenuAction` per
    # legal value, `View > Significant digits`-shaped, toggling that one
    # field directly with no modal opened. Convergence statistic is not:
    # the field is a *set* (any combination of the six, ANDed/ORed via
    # `convergence_combinator`), not a single choice, so a menu that
    # instead picked one exclusively would silently discard whatever
    # multi-statistic combination the Convergence modal already has
    # configured on a single errant click — a real, easy-to-trigger
    # data-loss risk, not merely a UX nitpick. Each leaf here toggles one
    # statistic's own membership in that set instead, correctly matching
    # the field's actual semantics (as with Significant digits, there is
    # no dynamic checkmark to show which are currently on — see this
    # function's own docstring for why).
    configure_menu = Menu(
        "Configure",
        [
            *(
                MenuAction(
                    label, dispatch(f"fim.menu.configureTab({json.dumps(tab_id)})")
                )
                for tab_id, label in configure_tabs
            ),
            MenuSeparator(),
            Menu(
                "Deme weighting",
                [
                    MenuAction(
                        value,
                        dispatch(f"fim.menu.setDemeWeighting({json.dumps(value)})"),
                    )
                    for value in ("size", "equal")
                ],
            ),
            Menu(
                "Mutation model",
                [
                    MenuAction(
                        value,
                        dispatch(f"fim.menu.setMutationModel({json.dumps(value)})"),
                    )
                    for value in ("infinite_alleles", "finite_alleles")
                ],
            ),
            Menu(
                "Convergence statistic",
                [
                    MenuAction(
                        _statistic_menu_label(name),
                        dispatch(
                            "fim.menu.toggleConvergenceStatistic("
                            f"{json.dumps(f'cs_{name}')})"
                        ),
                    )
                    for name in CONVERGENCE_STATISTIC_NAMES
                ],
            ),
        ],
    )
    # No "Animate" item (unified-run-view design §3.2.4, §8 Phase E): the
    # time slider is simply part of `completed`'s own view now, not a
    # second trigger reachable from a menu.
    run_menu = Menu(
        "Run",
        [
            MenuAction("Run simulation", dispatch("fim.menu.runSimulation()")),
            MenuAction("Cancel run", dispatch("fim.menu.cancelRun()")),
        ],
    )
    # Literal digit counts, not a live reflection of `Api._significant_
    # digits` — see `_build_menu`'s own docstring for why. `3`
    # (`_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS`, spelled out rather than
    # interpolated so this list reads the same as every other menu item
    # here: a plain literal, not a runtime-computed label) carries no
    # "(default)" annotation — every title anywhere in this menu tree
    # must stay free of `(`/`)` (`test_app_api.py`'s own `test_no_menu_
    # title_contains_a_paren`, added after a real, confirmed crash: the
    # GTK/Linux pywebview backend derives a native "detailed action
    # name" straight from a menu item's own label text and hands it to
    # `g_menu_item_set_detailed_action`, which parses anything after an
    # opening paren as GVariant target syntax — `"3 (default)"` produced
    # `g_menu_item_set_detailed_action: ... 'app._View_Significant_
    # digits_3_(default)' has invalid format: 0-7:unknown keyword`, a
    # fatal `GLib-GIO-ERROR` that aborted the whole process
    # (`Trace/breakpoint trap (core dumped)`) — not a Python exception,
    # not caught by anything, and invisible on macOS/Windows, where
    # this was written and tested (CI's own `linux-beta-x64` smoke test
    # is what actually caught it).
    view_menu = Menu(
        "View",
        [
            Menu(
                "Significant digits",
                [
                    MenuAction("2", dispatch("fim.menu.setSignificantDigits(2)")),
                    MenuAction("3", dispatch("fim.menu.setSignificantDigits(3)")),
                    MenuAction("4", dispatch("fim.menu.setSignificantDigits(4)")),
                    MenuAction("5", dispatch("fim.menu.setSignificantDigits(5)")),
                    MenuAction("6", dispatch("fim.menu.setSignificantDigits(6)")),
                    MenuAction("8", dispatch("fim.menu.setSignificantDigits(8)")),
                ],
            ),
        ],
    )
    help_menu = Menu(
        "Help",
        [
            MenuAction("Usage guide", dispatch("fim.menu.help('usage')")),
            MenuAction(
                "Configuration reference", dispatch("fim.menu.help('configuration')")
            ),
            MenuSeparator(),
            MenuAction(
                "Documentation on GitHub",
                dispatch(f"fim.menu.openExternal({json.dumps(_DOCUMENTATION_URL)})"),
            ),
            MenuSeparator(),
            MenuAction("Check for updates", dispatch("fim.menu.checkForUpdates()")),
            MenuAction("About fim", dispatch("fim.menu.about()")),
        ],
    )
    return [file_menu, configure_menu, run_menu, view_menu, help_menu]


def main() -> int:
    """Launch the GUI and block until the window closes.

    Returns:
        0 always — `webview.start()` returning means the user closed the
        window, not an error condition to report differently.
    """
    window = create_window()
    webview.start(menu=_build_menu(window))
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


if __name__ == "__main__":
    # `bin/fim-gui` invokes `python3 -m fim.gui.app "$@"`.  Without this
    # block the module is imported and exits silently — `main()` is never
    # called.
    #
    # Default behaviour is detached (the shell prompt returns immediately),
    # matching the natural expectation for a GUI launcher.  Pass
    # `--no-detach` to block until the window closes — useful for scripts
    # that need to wait for the user to finish.
    import argparse as _argparse

    _p = _argparse.ArgumentParser(
        prog="fim-gui",
        description="Launch the Finite Island Model graphical interface.",
        add_help=True,
    )
    _p.add_argument(
        "--version",
        action="version",
        version=f"fim-gui {fim_version}",
    )
    _p.add_argument(
        "--no-detach",
        action="store_true",
        default=False,
        help="block until the GUI window closes instead of returning immediately",
    )
    # parse_known_args so that unrecognised flags do not abort the launch.
    _args, _ = _p.parse_known_args()
    if _args.no_detach:
        raise SystemExit(main())
    # Detached default: reuse launcher._launch_gui so the subprocess
    # mechanics (frozen-vs-source, start_new_session, DEVNULL fds) live
    # in exactly one place.
    from fim.launcher import _launch_gui

    raise SystemExit(_launch_gui(detach=True))
