"""pywebview bootstrap and `Api` bridge — the GUI's own entry point
(`doc/fim-gui-design.md` §4).

`fim.launcher` dispatches here for the zero-argument and `--graphical`
paths, exactly as it dispatched to the Tk build's own `fim.gui.app:main`
before the pywebview migration (`doc/fim-gui-design.md` §2) — the
entry point name and shape are stable across the toolkit swap:
`fim.launcher` itself did not change at all.

`create_window` and `main` are deliberately separate: `main` blocks
(`webview.start()` runs the GUI's own event loop until the window
closes), so no test calls it directly. Every headless test instead calls
`create_window()` itself, then drives the result with its own
`webview.start(callback)`, confirmed directly against this window/bridge
before writing this
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
import faulthandler
import functools
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from math import isfinite
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Final, Protocol, TextIO, cast

import webview
import yaml
from webview.menu import Menu, MenuAction, MenuSeparator

from fim import __version__ as fim_version
from fim import logging_setup, paths, update
from fim.cli import load_config
from fim.engine import (
    RunResult,
    deterministic_run_id,
    pooled_convergence_histories,
    replicate_summary,
    report_for_state,
    reports_summary,
)
from fim.gui import batch_runner, presets, recent_runs, runner
from fim.gui.animation import pre_render_frames
from fim.gui.config_form import (
    field_for_error,
    form_values_to_payload,
    m_from_params,
    mu_from_params,
    params_to_form_values,
    payload_to_yaml_text,
    starter_form_values,
    tab_for_error,
)
from fim.gui.preferences import (
    GuiPreferences,
    load_preferences,
    preferences_file_path,
    save_preferences,
)
from fim.gui.store import read_live_state, read_progress_sidecar
from fim.gui.trajectory_history import sampled_statistic_history
from fim.model.initial import generate_initial_state
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import RunManifest, read_batch_manifest, read_manifest
from fim.reanalyze import reanalyze_trajectory
from fim.statistics import (
    effective_allele_count,
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_shannon_differentiation,
    identity_recovery_equilibrium,
    identity_recovery_half_life,
    identity_recovery_rate,
)
from fim.viz.scatter import (
    deme_pair_panel,
    frequency_points,
    panels_from_points,
    pooled_frequency_points,
    pooled_scatter_panels,
    scatter_panels,
)

logger = logging.getLogger(__name__)

_YAML_FILE_TYPES = ("YAML files (*.yaml;*.yml)", "All files (*.*)")
_TRAJECTORY_FILE_TYPES = ("trajectory.jsonl files (*.jsonl)", "All files (*.*)")

# The macOS menu bar's own bold, leftmost app-name item (`_set_macos_
# application_name`) and the window's own title bar text (`create_
# window`) are two different, unrelated pieces of UI text -- deliberately
# not the same string. The menu bar name stays short (matching a real
# macOS app's own convention: "Safari," "Mail," never a parenthetical),
# while the window title spells the project out for a user who has never
# seen the abbreviation before.
_MACOS_APPLICATION_NAME = "FIM"
_WINDOW_TITLE = "Finite Island Model (fim)"

# The existing `pyproject.toml` `[project.urls] Documentation` value,
# reused rather than invented (doc/fim-gui-design.md §11) -- the Help
# menu's own "Documentation on GitHub" item, and `get_about_info`'s own
# repository link, both point here.
_REPOSITORY_URL = "https://github.com/selby-botany/jost-finite-island-model"
_DOCUMENTATION_URL = f"{_REPOSITORY_URL}#readme"

# How long ordinary shutdown may take before the deadman forces exit
# (`_start_shutdown_deadman`). Generous on purpose: closing a GUI has no
# real work left to do, so honest shutdown is effectively instantaneous
# and anything approaching this bound is already a hang. The margin
# exists only so an unusually loaded machine -- a batch run's worker
# processes still winding down, a slow filesystem flushing a trajectory
# -- can never be mistaken for one.
_SHUTDOWN_DEADMAN_SECONDS: Final[float] = 20.0

# Distinct from every ordinary exit status this application produces (0
# success, 2 usage/configuration error) so a forced exit is
# recognizable as such in a log, a shell's `$?`, or a bug report,
# rather than being confused with a clean close.
_SHUTDOWN_DEADMAN_EXIT_CODE: Final[int] = 3


def _log_bridge_call[ApiMethod: Callable[..., Any]](method: ApiMethod) -> ApiMethod:
    """Log every `Api` bridge call at DEBUG, by method name only.

    `doc/fim-logging-design.md` §10's own `fim.gui.*` row: applied to
    every public `Api` method below, one line per call, naming only
    the method — never its arguments. A real call can carry a full
    configuration form (`start_run`) or a filesystem path a user
    typed (`open_run`), neither of which belongs in a log file by
    default; unlike `fim.cli.main`'s own "parsed arguments" DEBUG line
    (reachable only from a terminal invocation on this same machine), a
    bridge call originates from JS running inside the app's own
    embedded browser, one step further removed from a trusted
    terminal.
    """

    @functools.wraps(method)
    def wrapper(self: Api, *args: Any, **kwargs: Any) -> Any:
        logger.debug("bridge: %s", method.__name__)
        return method(self, *args, **kwargs)

    return cast(ApiMethod, wrapper)


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


# How often `_drain_batch_messages` re-polls every in-flight replicate's
# own `.progress` sidecar between checking `message_queue` for the
# batch's terminal outcome (`doc/fim-gui-design.md` §7.2). Coarser than `fim.gui.
# runner.PROGRESS_THROTTLE_INTERVAL_SECONDS` (a scalar run's own,
# in-process push interval) on purpose: each tick here re-reads a whole
# `trajectory.jsonl` per currently-reporting replicate
# (`read_live_state`'s own docstring), a real, if usually small, cost
# that grows with both replicate count and how far each has run.
_BATCH_POLL_INTERVAL_SECONDS: Final = 0.5

# The six named statistics Milestone G3 (`doc/fim-gui-design.md` §13)
# established for the results view — `FinalReport` also carries
# `H_ST`, added after that view was first built; G3 names exactly
# these six, so `H_ST` stays out of it.
_RESULT_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T")

# Distinguishes a user-saved preset's own id (`Api.save_current_as_
# preset`) from a built-in worked-example's bare slug (`fim.gui.
# presets.Preset.preset_id`) in `list_presets`'s combined result, so the
# two id spaces can never collide even if a user happens to choose a
# name matching a built-in slug.
_USER_PRESET_ID_PREFIX: Final = "user:"

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
    """Format one `FinalReport` statistic for the completed run view
    (`doc/fim-gui-design.md` §5.2).

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


# `H_S` above this value is the regime where `G_ST`'s own ratio-of-
# heterozygosities construction can badly under-report real
# differentiation between demes that in fact share no alleles at all
# (design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §7.7,
# `effective_allele_count`'s own docstring has the full citation) — the
# threshold at which `_effective_allele_summary`'s own `gStCaution` flag
# starts firing. A plain, disclosed choice, not a measured one: nothing
# about the underlying mathematics singles out one exact cutover point,
# so this exists to bound the note to a real, materially-affected regime
# rather than showing on every run regardless of how small the effect is.
_EFFECTIVE_ALLELE_CAUTION_THRESHOLD: Final = 0.7

# The Compare workspace's own minimum selection (design doc §8: "pick
# two or more previously completed runs") -- a single run has nothing
# to overlay against, so `compare_runs` rejects it outright rather than
# rendering a one-run "comparison" with an empty legend.
_COMPARE_MINIMUM_RUNS: Final = 2


def _effective_allele_summary(
    report: Mapping[str, Any], digits: int
) -> dict[str, str | bool]:
    """Return `H_S`/`H_T`'s own effective-allele-count readout, plus a caution flag.

    Design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §7.7:
    the "effective number of alleles" transform
    (`fim.statistics.effective_allele_count`) is the corrected reading
    Jost's own foundational papers argue for over a raw heterozygosity —
    shown here beside `H_S`/`H_T`, not instead of them, so a reader
    already used to reading `H_S`/`H_T` directly loses nothing.

    Args:
        report: A finished run's own `FinalReport` (or an equivalent
            mapping — the p_0 preview's own `report_for_state` result
            shares the same shape); only `H_S`/`H_T` are read, both
            always defined floats (unlike `G_ST`, `FinalReport`'s own
            docstring has the one field that can be `None`).
        digits: The GUI's own configured display precision.

    Returns:
        `{"H_S": "<formatted effective count>", "H_T": "<formatted
        effective count>", "gStCaution": <bool>}` — `gStCaution` is
        `True` exactly when this run's own `H_S` exceeds
        `_EFFECTIVE_ALLELE_CAUTION_THRESHOLD`.
    """
    within = effective_allele_count(cast("float", report["H_S"]))
    total = effective_allele_count(cast("float", report["H_T"]))
    return {
        "H_S": format_statistic(within, digits),
        "H_T": format_statistic(total, digits),
        "gStCaution": cast("float", report["H_S"])
        > _EFFECTIVE_ALLELE_CAUTION_THRESHOLD,
    }


def _sigma_band_payload(manifest: RunManifest, digits: int) -> dict[str, Any] | None:
    """Build the trajectory panel's own client-ready within-run sigma-band payload.

    Sigma-band GUI design doc `20260910-claude-sonnet-5-gui-sigma-band-
    design.md` (`selby/restricted`), approach B1: reused unchanged by
    both a live run's own `"done"` push (`_drain_run_messages`, this
    function's own first caller) and a reopened run's own bridge
    methods (that design's own slice 4) — one shared shape, not two
    independently maintained ones.

    Args:
        manifest: A run's own manifest — `sigma_band`/`sigma_band_
            multiplier`/`sigma_band_window` are `None` together
            whenever the run never requested a band, or requested one
            but only ever hit the hard generation cap without
            converging (`RunManifest`'s own docstring, and the sigma-
            band backend design doc's own decision 3).
        digits: `Api._significant_digits`, the same display precision
            every other statistic this bridge sends already uses.

    Returns:
        `None` when the run has no sigma band at all — the page's own
        drawing code treats this identically to `convergenceGenerations`
        being absent (`run-view-completed.js`'s own `renderTrajectory`:
        no explicit "not available" flag needed, the field's own
        absence already says so). Otherwise `{"multiplier": ...,
        "window": ..., "band": {name: {"mean", "sigma", "lower",
        "upper"}, ...}}` — `multiplier`/`window` are the small, already-
        exact numbers `SimulationParams` itself validated (no formatting
        benefit); every value inside `band` is `format_statistic`-
        formatted, the identical convention every other statistic this
        bridge sends already follows — the page parses a formatted
        string back to a number only where it needs to do arithmetic
        with it (`webui/screens/run-view-running.js`'s own
        `accumulateLiveTrajectory` already establishes this shape for
        the ordinary trajectory panel).
    """
    if manifest.sigma_band is None:
        return None
    return {
        "multiplier": manifest.sigma_band_multiplier,
        "window": manifest.sigma_band_window,
        "band": {
            name: {
                key: format_statistic(value, digits) for key, value in interval.items()
            }
            for name, interval in manifest.sigma_band.items()
        },
    }


# The three report statistics with a closed-form equilibrium prediction at
# all (`K_ST`/`H_S`/`H_T` have no `fim.statistics.equilibrium_*` function) —
# shared by `_equilibrium_reference`/`_equilibrium_reference_payload` below
# and `Api.get_equilibrium_predictions`, so a future fourth prediction added
# to one is not silently missing from the other.
_EQUILIBRIUM_STATISTIC_NAMES: Final = ("D", "G_ST", "E_ST")


def _equilibrium_reference(
    n: int, m: float, mu: float, d: int, digits: int
) -> dict[str, str]:
    """Predict `D`/`G_ST`/`E_ST` at equilibrium for one scalar `(N, m, mu, d)`.

    Factored out of `Api.get_equilibrium_predictions` so the trajectory
    panel's own predicted-equilibrium overlay (`_equilibrium_reference_
    payload`, below) computes these three exactly the same way Explore
    does — one formula, not two independently maintained ones.

    Args:
        n: Population size (gene copies per deme).
        m: Migration rate.
        mu: Mutation rate.
        d: Deme count.
        digits: The GUI's own configured display precision.

    Returns:
        `{"D": ..., "G_ST": ..., "E_ST": ...}`, each `format_statistic`-
        formatted — including its own `"undefined"` convention where a
        prediction has no defined value for these inputs (`D` when `mu`
        is exactly `0`, for instance).
    """

    def predict(function: Callable[..., float], *args: float | int) -> str:
        try:
            return format_statistic(function(*args), digits)
        except ValueError:
            return format_statistic(None, digits)

    return {
        "D": predict(equilibrium_d, m, mu, d),
        "G_ST": predict(equilibrium_g_st, n, m, mu, d),
        "E_ST": predict(equilibrium_shannon_differentiation, n, m, mu, d),
    }


def _equilibrium_reference_payload(
    params: SimulationParams, digits: int
) -> dict[str, str] | None:
    """Build the trajectory panel's own predicted-equilibrium overlay payload.

    Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
    redesign.md` §6.2's own closing paragraph: "the trajectory panel
    also draws the predicted equilibrium as a light dashed reference
    line... visible while [the run] is still happening, not only in
    retrospect on Results."

    The design text frames this as reusing a value from a prior Explore
    visit (§5) once the current configuration has been explored there.
    This instead recomputes it directly from the run's own configuration
    every time, deliberately deviating from that literal wording:
    `equilibrium_d`/`equilibrium_g_st`/`equilibrium_shannon_
    differentiation` are pure, free functions of `(N, m, mu, d)` with no
    simulation involved (`Api.get_equilibrium_predictions`'s own
    docstring) — recomputing directly from the configuration that is
    actually running is strictly more correct than reusing a possibly-
    stale value from a past, possibly-different Explore visit (or no
    visit at all — most runs never touch Explore first), and costs
    nothing extra. See this function's own commit message for the same
    reasoning recorded against the design doc.

    Args:
        params: A validated configuration — `SimulationParams.from_
            mapping`'s own result at run-start, or a reopened run's own
            `ReanalyzedGeneration.params`.
        digits: The GUI's own configured display precision.

    Returns:
        `None` when `N`/`m`/`mu` are not all plain scalars — a per-deme
        `N`, a migration matrix, or a per-locus `mu` has no single
        `(N, m, mu)` triple this family of functions accepts, and this
        feature does not attempt to reduce one to a representative
        scalar (matching `renderTrajectory`'s own existing scalar-run
        scope boundary, and Explore's own `_parse_equilibrium_inputs`
        scalar-only fields). Otherwise `{"D": ..., "G_ST": ...,
        "E_ST": ...}`, each `format_statistic`-formatted exactly like
        `get_equilibrium_predictions`'s own identically-named fields.
    """
    if (
        not isinstance(params.N, int)
        or not isinstance(params.m, float)
        or not isinstance(params.mu, float)
    ):
        return None
    return _equilibrium_reference(params.N, params.m, params.mu, params.d, digits)


def _identity_recovery_reference_payload(
    params: SimulationParams,
) -> dict[str, float] | None:
    """Build the trajectory panel's own identity-recovery curve overlay payload.

    A second, genuinely different closed-form reference from `_
    equilibrium_reference_payload`'s own flat asymptote line — Whitlock
    (1992)'s `identity_recovery_trajectory` predicts probability of
    identity by descent, `f_0`, *as a function of generation*, not just
    where it eventually settles. Already surfaced once in this GUI as a
    single number, `identity_recovery_half_life` (`Api.get_equilibrium_
    predictions`) — this is the same closed form, drawn as a full curve
    instead of collapsed to one derived generation count.

    Sent as `{"rate": L, "equilibrium": f_hat_0}` — two plain floats,
    not a pre-sampled array of points — because `identity_recovery_
    trajectory`'s own formula (`f_hat_0 * (1 - L**generations)`, at the
    `f0_initial = 0.0` starting point this function always assumes, see
    below) is cheap, exact, closed-form arithmetic in `generations`
    alone; the page can evaluate it at any generation it is already
    plotting (whatever sampling the simulated curve itself uses) with no
    second, independently-sampled series to keep in sync, mirroring how
    little `_equilibrium_reference_payload`'s own flat line sends.

    `f0_initial` is fixed at `0.0` — Whitlock's own primary scenario, a
    deme founded from a single common ancestor (maximal identity by
    descent) recovering variation via migration — rather than derived
    from this particular run's own initial condition (a Dirichlet draw,
    an ancestral split, explicit frequencies, or fixed alleles, `initial_
    conditions_mode`): those starting states are not, in general,
    disturbances *from* identity-by-descent equilibrium in the sense
    Whitlock's own model assumes, and `identity_recovery_trajectory`
    itself takes no position on which one produced a real run's actual
    `f_0[0]`. Drawn unconditionally, regardless of the run's own chosen
    initial-conditions mode — matching `identity_recovery_half_life`'s
    own existing, identically unconditional presentation on Explore.
    This is a real, load-bearing scope choice, not an oversight: fitting
    this curve to a specific run's own actual starting identity is a
    separate, larger design question, tracked in its own design note
    rather than guessed at here (`20260911-claude-sonnet-5-derived-
    differentiation-trajectory-design.md`, `selby/restricted`).

    Whitlock (1992)'s own model also assumes zero mutation, unlike this
    project's typical runs (`mu > 0`) — this curve is therefore a
    genuine theoretical approximation, not a prediction consistent with
    a run's own configured mutation rate; the GUI labels it plainly as
    such rather than implying it is a mutation-aware D/G_ST-style
    equilibrium the way `_equilibrium_reference_payload`'s own line is.

    Args:
        params: A validated configuration — `SimulationParams.from_
            mapping`'s own result at run-start, or a reopened run's own
            `ReanalyzedGeneration.params`.

    Returns:
        `None` when `N`/`m` are not both plain scalars (a per-deme `N`
        or a migration matrix has no single `(N, m)` pair this closed
        form accepts) — matching `_equilibrium_reference_payload`'s own
        scalar-only scope boundary. Otherwise `{"rate": L, "equilibrium":
        f_hat_0}`, both raw floats (not `format_statistic`-formatted —
        the page computes and rounds displayed values itself from these
        two, the same "send the ingredients, not a rendered result"
        shape the rest of this payload already uses for the curve data
        itself).
    """
    if not isinstance(params.N, int) or not isinstance(params.m, float):
        return None
    return {
        "rate": identity_recovery_rate(params.N, params.m),
        "equilibrium": identity_recovery_equilibrium(params.N, params.m),
    }


def _run_config_summary(params: SimulationParams) -> dict[str, str]:
    """Summarize one run's configuration into a fixed set of commonly-swept fields.

    The Compare workspace's own "for runs that differ in exactly one
    field... the legend highlights that one field's differing value"
    (design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §8)
    needs a *comparable* representation, not the full configuration —
    reusing `m_from_params`/`mu_from_params` rather than a second,
    bespoke summarizer keeps this in lockstep with the Configure form's
    own notion of "the same value" (a scalar `m` and a `d`-by-`d`
    matrix that happens to encode that same scalar are still different
    configurations, and are reported as such — no equivalence-checking
    beyond string equality is attempted).

    Args:
        params: A validated configuration, typically `ReanalyzedGeneration.
            params` from `reanalyze_trajectory`.

    Returns:
        `{"N", "d", "m", "mu", "mutation_model", "seed"}` — every value
        a short, human-readable string, `seed` last (the field a reader
        cares about least when scanning for "what's different about
        this run," `Api.list_home_runs`'s own home enrichment design
        doc `20260909-claude-sonnet-5-home-enrichment-design.md`,
        `selby/restricted`). `m`/`mu` collapse their own
        composite modes to one representative string each (a scalar
        rate, a `"<topology> @ <rate>"` pair, `"matrix"`, or
        `"mu_b=<rate>"`) rather than every sub-field, so a `d`-by-`d`
        matrix or a `mu_b`-derived rate compares as a single field like
        every other, not several.
    """
    n_text = (
        str(params.N)
        if isinstance(params.N, int)
        else ",".join(str(value) for value in params.N)
    )
    m_values = m_from_params(params)
    if m_values["m_mode"] == "scalar":
        m_text = m_values["m_rate"]
    elif m_values["m_mode"] == "topology":
        m_text = f"{m_values['m_topology']} @ {m_values['m_topology_rate']}"
    else:
        m_text = "matrix"
    mu_values = mu_from_params(params)
    mu_text = (
        mu_values["mu_value"]
        if mu_values["mu_mode"] == "mu"
        else f"mu_b={mu_values['mu_b_value']}"
    )
    return {
        "N": n_text,
        "d": str(params.d),
        "m": m_text,
        "mu": mu_text,
        "mutation_model": params.mutation_model,
        "seed": str(params.seed),
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Read one small JSON file already known to hold a plain object, or `None`.

    `Api.list_home_runs`'s own `report.json`/`summary.json` reads (home
    enrichment design doc `20260909-claude-sonnet-5-home-enrichment-
    design.md`, `selby/restricted`): both are already-computed,
    already-written artifacts a completed run writes once and never
    touches again (`fim.persistence.report.write_report`) — this never
    recomputes anything, only reads a file back. `None` for anything
    that stops this from working (missing, unreadable, malformed JSON,
    or JSON that does not even parse to an object) — `Api.list_home_
    runs`'s own row for that run still appears, with its statistics
    simply omitted, the identical "skip rather than fail the whole
    scan" precedent `fim.gui.recent_runs._recent_run_from_file` already
    sets for a malformed `manifest.json`, one file deeper.
    """
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# The Explore workspace (design doc `20260907-claude-sonnet-5-botanist-
# gui-redesign.md` §5): fixed *display* domains for `get_equilibrium_
# sweep`'s own curve, one per sweepable axis -- chosen to show a
# genuinely informative curve on their own, independent of whatever the
# caller's current value happens to be, so the same axis always
# produces a comparably-shaped plot rather than one whose range quietly
# depends on where the user started. `mu`'s own lower bound is strictly
# positive (never `0.0`) since `equilibrium_d` rejects `mu == 0`
# outright (see that function's own docstring).
_EQUILIBRIUM_SWEEP_POINTS: Final = 24
_EQUILIBRIUM_SWEEP_DOMAINS: Final[dict[str, tuple[float, float]]] = {
    "N": (10.0, 5000.0),
    "d": (2.0, 50.0),
    "m": (0.0001, 0.5),
    "mu": (0.000001, 0.1),
}

# The fewest points `_geometric_sweep` can produce a real ratio from —
# below this, "spaced between two values" has no geometric meaning left.
_MINIMUM_SWEEP_POINTS: Final = 2


def _geometric_sweep(low: float, high: float, count: int) -> list[float]:
    """Return `count` values geometrically spaced from `low` to `high`, inclusive.

    Used instead of a linear sweep because every axis
    `_EQUILIBRIUM_SWEEP_DOMAINS` names spans several orders of magnitude
    (a migration or mutation rate meaningfully differs at `0.001` versus
    `0.01` versus `0.1`) — a linear sweep across the same bounds would
    spend almost every point on the least interesting, largest-value
    end of the range.

    Args:
        low: First value (must be strictly positive).
        high: Last value (must be greater than `low`).
        count: How many values to return, at least `2`.

    Returns:
        `count` values, `low` and `high` themselves included as the
        first and last.
    """
    if count < _MINIMUM_SWEEP_POINTS:
        return [low]
    ratio = (high / low) ** (1.0 / (count - 1))
    return [low * ratio**index for index in range(count)]


_MINIMUM_EQUILIBRIUM_N: Final = 1
_MINIMUM_EQUILIBRIUM_DEMES: Final = 2


def _parse_equilibrium_inputs(
    n: str, m: str, mu: str, d: str
) -> tuple[int, float, float, int]:
    """Parse and range-check Explore's four scalar fields.

    Deliberately lighter than `form_values_to_payload`/`SimulationParams.
    from_mapping`: Explore has no seed, loci, or convergence settings to
    validate — only these four numbers. Range checks mirror `fim.
    statistics.differentiation`'s own private `_validate_equilibrium_
    inputs`/`_validate_identity_recovery_inputs` (not imported directly —
    both start with `_`, this project's own "always private" rule) so
    every genuinely invalid input (an `N` under `1`, a `d` under `2`, an
    `m`/`mu` outside `[0, 1]`) is rejected once, here, before any
    individual prediction call — rather than four of the five
    predictions each independently raising and being caught as
    "undefined." A field that parses and is in range but still leaves a
    *specific* prediction undefined (`equilibrium_d` requires `mu`
    strictly greater than `0`, unlike every other function here) is
    deliberately left to that one prediction's own try/except instead —
    that is a real "undefined at this configuration" case, not a bad
    input, and only `D` should read as undefined for it.

    Args:
        n: Population size, as typed.
        m: Migration rate, as typed.
        mu: Mutation rate, as typed.
        d: Deme count, as typed.

    Returns:
        `(n, m, mu, d)` as `(int, float, float, int)`.

    Raises:
        ValueError: If any of the four does not parse as a number, or
            parses but is out of range.
    """
    try:
        n_value, m_value, mu_value, d_value = (int(n), float(m), float(mu), int(d))
    except (TypeError, ValueError) as error:
        raise ValueError("N, d, m, and mu must all be numbers") from error
    if n_value < _MINIMUM_EQUILIBRIUM_N:
        raise ValueError("N must be a positive gene-copy count")
    if d_value < _MINIMUM_EQUILIBRIUM_DEMES:
        raise ValueError("d must be at least 2")
    for name, value in (("m", m_value), ("mu", mu_value)):
        if not isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
    return (n_value, m_value, mu_value, d_value)


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
    `_reveal_in_file_browser`: the same native folder-opening mechanism,
    just no longer coupled to that screen's own toolkit.
    `check=False` throughout: a file browser's own exit
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

    Grows one method per bridge call as each screen is built
    (`doc/fim-gui-design.md` §4.2); holds almost no state of its own
    beyond what a given call
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
        preferences_path: Path | None = None,
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
            preferences_path: Where `GuiPreferences` are loaded from and
                saved to (`fim.gui.preferences`). Defaults to
                `preferences_file_path()`'s own real, platform-specific
                location; overridable so a test never touches — or
                collides with — a real user's saved preferences.
        """
        self._cancel_event: threading.Event | None = None
        self._open_folder = open_folder
        self._on_run_started = on_run_started
        self._on_message = on_message
        self._preferences_path = (
            preferences_path
            if preferences_path is not None
            else preferences_file_path()
        )
        # `load_preferences` never raises — an unreadable file is
        # quarantined and defaults returned (`fim.gui.preferences`'s own
        # docstring) — so this always succeeds, even on a corrupted or
        # first-ever launch. `_startup_warnings` is drained exactly once
        # by `get_startup_warnings`, called from `webui/app.js` right
        # after the first screen mounts: `__init__` runs before any
        # screen exists, so there is nowhere yet to show an inline
        # `{"ok": False, "message": ...}` error the way `start_run`'s own
        # error path does.
        preferences, warning = load_preferences(self._preferences_path)
        self._preferences: GuiPreferences = preferences
        self._startup_warnings: list[str] = [warning] if warning is not None else []
        # The View menu's own "Significant digits" submenu (`set_
        # significant_digits`) mutates this directly; every real
        # `format_statistic` call site below reads it fresh at the
        # moment a screen is populated, so a change here takes effect
        # starting with the next run's own results — an already-open
        # Screen 3/4 was formatted once, at push time, and is not
        # retroactively reformatted. Seeded from a saved preference when
        # one exists, falling back to the hardcoded default otherwise —
        # `GuiPreferences` itself carries no default of its own
        # (`fim.gui.preferences`'s own docstring: this constant stays the
        # single source of truth).
        self._significant_digits: int = (
            preferences.significant_digits
            if preferences.significant_digits is not None
            else _DEFAULT_DISPLAY_SIGNIFICANT_DIGITS
        )
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

    @_log_bridge_call
    def start_run(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form, then start a run pushing live progress to the page.

        Dispatches to a scalar or a real parallel batch run based on
        `params.n_replicates` alone — there is no separate "batch mode"
        toggle; `n_replicates` *is* the toggle (`doc/fim-gui-design.md`
        §7), so this one bridge method serves both, and `webui/screens/
        input.js`'s own `onRunClicked` never needs to know or care
        which. Either path runs on a background `threading.Thread` so
        this call itself returns immediately; the caller drives the
        running view from the pushed `fim.onRun*`/`fim.onBatch*` calls
        a second background thread makes via `window.evaluate_js` as
        each message arrives — push, not poll, confirmed safe from an
        arbitrary background thread, not only `webview.start`'s own
        driver thread, before this method was written; see
        `test/gui/test_running_screen.py`.

        Args:
            values: The same shape `validate_form` accepts, plus (for a
                batch) the Batch tab's own `max_workers` field — not a
                `SimulationParams` field at all, parsed here directly.

        Returns:
            `{"ok": True, "equilibrium": ..., "identityRecovery": ...}`
            once the run has *started* — not once it finishes; the real
            outcome arrives via the pushed calls above. `equilibrium` is
            `_equilibrium_reference_payload`'s own result (design doc
            §6.2's predicted-equilibrium trajectory overlay);
            `identityRecovery` is `_identity_recovery_reference_
            payload`'s own result (that same section's closed-form
            recovery *curve*, a second and different reference overlay —
            see that function's own docstring). Both `None` for a batch
            (never computed there — batch has no trajectory panel of its
            own to overlay onto) or for a scalar run whose `N`/`m`(/`mu`,
            for `equilibrium` only) are not all plain scalars; the page
            caches both client-side for the live trajectory panel to
            draw against on every subsequent progress tick (`webui/
            screens/run-view-running.js`'s own `setLiveEquilibriumReference`/
            `setLiveIdentityRecoveryReference`), and the same values are
            reused, not recomputed, in the eventual `"done"` push
            (`_drain_run_messages`). `{"ok": False, "message": ...}` if
            the form does not validate or the output directory cannot be
            allocated.
        """
        try:
            payload = form_values_to_payload(values)
            params = SimulationParams.from_mapping(payload)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        # Remembered as `get_initial_form`'s own default for the next
        # launch only once `values` is known to round-trip through the
        # exact validation path above — never a value that failed it,
        # and never re-validated by a second, separate rule of this
        # store's own (`fim.gui.preferences`'s own top docstring).
        self._preferences = self._preferences.with_form_values(values)
        save_preferences(self._preferences_path, self._preferences)
        try:
            output_directory = paths.default_output_directory()
        except FileExistsError as error:
            return {"ok": False, "message": str(error)}
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
            logger.warning("scalar run failed to start: %s", error)
            return {"ok": False, "message": str(error)}
        logger.info("scalar run starting: %s", output_directory)
        self._cancel_event = cancel_event
        # A fresh run never inherits a previous run's own live pair
        # selection — the same "never left showing stale state from
        # whichever screen used it last" reasoning `animation.js`'s own
        # `showAnimation` documents for its identical selector.
        self._live_deme_pair = None
        if self._on_run_started is not None:
            self._on_run_started()
        # The trajectory panel's own predicted-equilibrium overlay (design
        # doc §6.2, `_equilibrium_reference_payload`'s own docstring), and
        # its own separate identity-recovery curve overlay (`_identity_
        # recovery_reference_payload`'s own docstring): both computed
        # once, here, before the run's own background thread even starts,
        # and handed to both that thread (for the "done" push, below) and
        # this method's own immediate return (for the live page to cache
        # and draw on every progress tick while the run is still going) —
        # one shared computation each, not a second one for each of the
        # two places either is needed.
        equilibrium = _equilibrium_reference_payload(params, self._significant_digits)
        identity_recovery = _identity_recovery_reference_payload(params)
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
                equilibrium,
                identity_recovery,
            ),
            daemon=True,
        ).start()
        return {
            "ok": True,
            "equilibrium": equilibrium,
            "identityRecovery": identity_recovery,
        }

    def _start_batch_run(
        self,
        params: SimulationParams,
        output_directory: Path,
        values: dict[str, str],
    ) -> dict[str, Any]:
        """The `n_replicates > 1` half of `start_run`.

        See `fim.gui.batch_runner` and `doc/fim-gui-design.md` §7.2.
        """
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
            logger.warning("batch run failed to start: %s", error)
            return {"ok": False, "message": str(error)}
        logger.info(
            "batch run starting: %s (n_replicates=%d, max_workers=%s)",
            output_directory,
            params.n_replicates,
            max_workers,
        )
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

    @_log_bridge_call
    def cancel_run(self) -> None:
        """Request cancellation of whichever scalar run `start_run` last started.

        A no-op if no run is currently in flight — mirrors `GuiProgress
        Store.write_generation`'s own tolerance of a `cancel_event` that
        was never going to matter, rather than raising for a Cancel click
        that arrives a moment after the run already finished on its own.
        """
        if self._cancel_event is not None:
            self._cancel_event.set()

    @_log_bridge_call
    def open_output_folder(self, path: str) -> None:
        """Reveal a completed run's output directory in the OS file browser.

        Args:
            path: The directory to reveal — the page's own copy of the
                `outputDirectory` `onRunDone` last pushed it, not state
                this bridge tracks itself (`doc/fim-gui-design.md` §4.2:
                nothing about an already-shown completed run needs
                `Api` to remember which run it was showing).
        """
        self._open_folder(Path(path))

    @_log_bridge_call
    def get_starter_form(self) -> dict[str, str]:
        """Return a fresh form's default values.

        `config_form.starter_form_values` is the single source of "GUI
        defaults" — the identical values `fim.cli.STARTER_CONFIG` itself
        expands to — so this bridge method adds no logic of its own
        beyond calling it. `fim.menu.newConfiguration`'s own explicit,
        unconditional reset — distinct from `get_initial_form`, just
        below, which a fresh app launch calls instead.
        """
        return starter_form_values()

    @_log_bridge_call
    def get_initial_form(self) -> dict[str, str]:
        """Return the values a fresh app launch's own Input screen should show.

        Prefers the last successfully submitted form
        (`GuiPreferences.form_values`, saved by `start_run` below) over
        `get_starter_form`'s own true starter values — the confirmed gap
        P1 item 4 of the 2026-09-06 open-issues doc names directly
        ("form values... remain process-local"). Re-validated through
        the exact same `form_values_to_payload`/`SimulationParams.
        from_mapping` path `start_run` itself uses: a saved form that no
        longer validates (a hand-edited file, or a `config_form` field
        set that changed since it was saved) is discarded wholesale
        rather than applied partially — `starter_form_values()` is
        exactly as safe a fallback here as it is for a first-ever launch
        with nothing saved at all.
        """
        values = self._preferences.form_values
        if values is None:
            return starter_form_values()
        try:
            SimulationParams.from_mapping(form_values_to_payload(values))
        except ValueError:
            return starter_form_values()
        return values

    @_log_bridge_call
    def validate_form(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate the form exactly as "Run simulation" would.

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
            wording), `field`/`tab` are `None` when the
            message names no field this form exposes (an unknown-key
            error, for instance), for the caller to switch to and
            highlight when they are not.
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

    @_log_bridge_call
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

    @_log_bridge_call
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

    @_log_bridge_call
    def get_equilibrium_predictions(
        self, n: str, m: str, mu: str, d: str
    ) -> dict[str, Any]:
        """Return no-simulation-needed theoretical equilibrium predictions.

        The Explore workspace's own data source (design doc
        `20260907-claude-sonnet-5-botanist-gui-redesign.md` §5): every
        prediction here is a pure function of `(N, m, mu, d)` alone,
        computed directly from `fim.statistics`'s own equilibrium/
        identity-recovery family — no simulation ever runs to produce
        any of it, so this call returns essentially instantly regardless
        of how large `N`/`d` are.

        Args:
            n: Population size (gene copies per deme), as typed.
            m: Migration rate, as typed.
            mu: Mutation rate, as typed.
            d: Deme count, as typed.

        Returns:
            `{"ok": True, "predictions": {"D": ..., "G_ST": ...,
            "E_ST": ..., "identity_recovery_half_life": ...}}`, each
            value a string already formatted by `format_statistic`
            (including its own `"undefined"` convention where a
            prediction has no defined value for these inputs — `D` when
            `mu` is exactly `0`, for instance); `{"ok": False,
            "message": ...}` if `n`/`d`/`m`/`mu` do not even parse as
            numbers, or if a value parses but is out of range (that
            `ValueError`'s own message, verbatim, from whichever
            `fim.statistics` function first rejected it).
        """
        try:
            n_value, m_value, mu_value, d_value = _parse_equilibrium_inputs(n, m, mu, d)
        except ValueError as error:
            return {"ok": False, "message": str(error)}

        digits = self._significant_digits

        def predict(function: Callable[..., float], *args: float | int) -> str:
            try:
                return format_statistic(function(*args), digits)
            except ValueError:
                return format_statistic(None, digits)

        # `_parse_equilibrium_inputs` already rejected every out-of-range
        # input above; the only `ValueError` `_equilibrium_reference`'s own
        # `predict` calls (for "D"/"G_ST"/"E_ST") or the `predict` call just
        # below (for "identity_recovery_half_life") can still raise is
        # `equilibrium_d`'s own `mu == 0` case (a legitimately in-range
        # input that leaves *that one* prediction undefined), which each
        # function's own inner try/except already turns into `"undefined"`
        # rather than failing the whole call.
        predictions = {
            **_equilibrium_reference(n_value, m_value, mu_value, d_value, digits),
            "identity_recovery_half_life": predict(
                identity_recovery_half_life, n_value, m_value
            ),
        }
        return {"ok": True, "predictions": predictions}

    @_log_bridge_call
    def get_equilibrium_sweep(
        self, axis: str, n: str, m: str, mu: str, d: str
    ) -> dict[str, Any]:
        """Sweep one of N/d/m/mu and return predicted D/G_ST/E_ST across it.

        Explore's own curve (design doc
        `20260907-claude-sonnet-5-botanist-gui-redesign.md` §5.2):
        `axis` sweeps across `_EQUILIBRIUM_SWEEP_DOMAINS[axis]`, a fixed
        display range independent of the other three fields' current
        values, which are held fixed at whatever `get_equilibrium_
        predictions` was just called with. `E_ST` (`equilibrium_shannon_
        differentiation`) joins `D`/`G_ST` here rather than staying
        computed-but-unplotted the way `get_equilibrium_predictions`
        alone left it — it shares the identical `[0, 1]` differentiation
        domain those two already plot on, so the same axes and the same
        gap-handling client-side `drawLine` cover it with no new chart.

        Args:
            axis: Which field to sweep — one of `"N"`, `"d"`, `"m"`,
                `"mu"`.
            n: Population size, held fixed unless `axis == "N"`.
            m: Migration rate, held fixed unless `axis == "m"`.
            mu: Mutation rate, held fixed unless `axis == "mu"`.
            d: Deme count, held fixed unless `axis == "d"`.

        Returns:
            `{"ok": True, "axis": axis, "current": <parsed current value
            of axis>, "points": [{"x": ..., "D": ..., "G_ST": ...,
            "E_ST": ...}, ...]}` — `D`/`G_ST`/`E_ST` are `None` (not a
            formatted string — plotting reads these as numbers) wherever
            that point's own configuration makes the prediction
            undefined, e.g. `D`/`E_ST` at `mu == 0`; `{"ok": False,
            "message": ...}` if `axis` is not one of the four names
            above, or if `n`/`d`/`m`/`mu` do not parse.

            `identity_recovery_half_life` (generations, unbounded) is
            deliberately not part of this sweep: it shares no `[0, 1]`
            domain with `D`/`G_ST`/`E_ST` (`get_equilibrium_predictions`
            already surfaces it as Explore's own single-number
            prediction instead) — a second sweep/chart for it against
            `N`/`m` (the only two axes it depends on) is a reasonable,
            deliberately deferred follow-up, not built here.
        """
        if axis not in _EQUILIBRIUM_SWEEP_DOMAINS:
            return {
                "ok": False,
                "message": f"axis must be one of {sorted(_EQUILIBRIUM_SWEEP_DOMAINS)}",
            }
        try:
            n_value, m_value, mu_value, d_value = _parse_equilibrium_inputs(n, m, mu, d)
        except ValueError as error:
            return {"ok": False, "message": str(error)}

        low, high = _EQUILIBRIUM_SWEEP_DOMAINS[axis]
        swept = _geometric_sweep(low, high, _EQUILIBRIUM_SWEEP_POINTS)
        current: float | int = {
            "N": n_value,
            "d": d_value,
            "m": m_value,
            "mu": mu_value,
        }[axis]

        def value_at(current_axis_value: float) -> float | int:
            return (
                round(current_axis_value) if axis in ("N", "d") else current_axis_value
            )

        points = []
        for raw_value in swept:
            value = value_at(raw_value)
            sweep_n = int(value) if axis == "N" else n_value
            sweep_d = int(value) if axis == "d" else d_value
            sweep_m = float(value) if axis == "m" else m_value
            sweep_mu = float(value) if axis == "mu" else mu_value
            try:
                predicted_d: float | None = equilibrium_d(sweep_m, sweep_mu, sweep_d)
            except ValueError:
                predicted_d = None
            try:
                predicted_g_st: float | None = equilibrium_g_st(
                    sweep_n, sweep_m, sweep_mu, sweep_d
                )
            except ValueError:
                predicted_g_st = None
            try:
                predicted_e_st: float | None = equilibrium_shannon_differentiation(
                    sweep_n, sweep_m, sweep_mu, sweep_d
                )
            except ValueError:
                predicted_e_st = None
            points.append(
                {
                    "x": value,
                    "D": predicted_d,
                    "G_ST": predicted_g_st,
                    "E_ST": predicted_e_st,
                }
            )

        return {"ok": True, "axis": axis, "current": current, "points": points}

    @_log_bridge_call
    def load_yaml(self) -> dict[str, Any]:
        """Browse for and load a YAML config, returning the form values it renders to.

        Routes through `fim.cli.load_config` — the identical function
        `fim run` uses (doc/fim-gui-design.md) — so a config that runs from the
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

    @_log_bridge_call
    def list_presets(self) -> dict[str, Any]:
        """Return every preset's own id, title, and origin — built-in or user-saved.

        Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
        redesign.md` §4.5/§12, `selby/restricted`: built-in presets are
        the worked examples `fim.gui.presets` parses from the bundled
        `webui/help/usage.html` — see that module's own docstring for
        why that file, not `doc/usage.md` itself, is the one this reads.
        User-saved presets are `self._preferences.named_presets`
        (`save_current_as_preset`, below) — distinct in every way that
        matters: created and deleted by the user, at any time, never
        shipped with the app. No YAML/form text is sent for either kind
        here; `get_preset_form_values` fetches one preset's own values
        only once the user actually picks it.

        Returns:
            `{"ok": True, "presets": [{"id": ..., "title": ...,
            "builtin": <bool>}, ...]}` — built-in presets first, in
            `doc/usage.md`'s own document order, then user-saved presets
            sorted by name. A built-in preset's own `id` is its bare
            slug (`get_preset_form_values` reads it directly); a
            user-saved preset's own `id` is `"user:<name>"`
            (`_USER_PRESET_ID_PREFIX`), so the two id spaces can never
            collide even if a user happens to choose a name matching a
            built-in slug.
        """
        found = presets.list_presets(_webui_directory())
        result = [
            {"id": preset.preset_id, "title": preset.title, "builtin": True}
            for preset in found
        ]
        named_presets = self._preferences.named_presets or {}
        result.extend(
            {"id": f"{_USER_PRESET_ID_PREFIX}{name}", "title": name, "builtin": False}
            for name in sorted(named_presets)
        )
        return {"ok": True, "presets": result}

    @_log_bridge_call
    def get_preset_form_values(self, preset_id: str) -> dict[str, Any]:
        """Return one preset's own form values, ready for `applyFormValues`.

        Args:
            preset_id: A `preset_id` from a prior `list_presets` call —
                either a built-in slug or a `"user:<name>"` id.

        Returns:
            `{"ok": True, "values": {...}}` on success — the identical
            shape `load_yaml` returns, so both share one JS-side apply
            path; `{"ok": False, "message": ...}` if `preset_id` names
            no known preset, or if the preset's own configuration no
            longer validates. For a built-in preset this can only be a
            construct this form cannot represent (the "Per-base
            mutation rate across unequal locus lengths" example's own
            genuinely per-locus `mu` is the one worked example this
            affects today) — the identical message a hand-loaded YAML
            file with the same shape would already produce via
            `load_yaml`. For a user-saved preset this is re-validated
            the same way `get_initial_form` re-validates a saved
            `form_values` snapshot — a config that validated when saved
            can stop validating later only if a range this project
            itself enforces changed in the meantime, not through any
            fault of the saved file itself.
        """
        if preset_id.startswith(_USER_PRESET_ID_PREFIX):
            name = preset_id[len(_USER_PRESET_ID_PREFIX) :]
            named_presets = self._preferences.named_presets or {}
            values = named_presets.get(name)
            if values is None:
                return {"ok": False, "message": f"no such preset: {preset_id}"}
            try:
                SimulationParams.from_mapping(form_values_to_payload(values))
            except ValueError as error:
                return {"ok": False, "message": str(error)}
            return {"ok": True, "values": values}
        preset = presets.get_preset(_webui_directory(), preset_id)
        if preset is None:
            return {"ok": False, "message": f"no such preset: {preset_id}"}
        try:
            payload = yaml.safe_load(preset.yaml_text)
            params = SimulationParams.from_mapping(payload)
            values = params_to_form_values(params)
        except (ValueError, yaml.YAMLError) as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "values": values}

    @_log_bridge_call
    def get_preset_yaml(self, preset_id: str) -> dict[str, Any]:
        """Return one preset's own configuration as plain-text YAML.

        Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
        redesign.md` §10: "every worked example ... is available in-app
        as a preset (§4.5) *and* as a plain-text YAML view with a Copy
        to clipboard action, so a user who wants the file (to hand-edit,
        to script against, to share with a co-author) never has to leave
        the app to get it." Applies uniformly to a user-saved preset
        too — the same underlying mechanism, `payload_to_yaml_text`,
        already produces a `fim run`-compatible document from any valid
        form values regardless of where they came from (`save_yaml`'s
        own identical call, just against the live form instead of a
        saved preset).

        Args:
            preset_id: A `preset_id` from a prior `list_presets` call —
                either a built-in slug or a `"user:<name>"` id.

        Returns:
            `{"ok": True, "title": ..., "yaml": "..."}` on success — a
            built-in preset's own `yaml_text` is returned exactly as
            `doc/usage.md` presents it, unparsed and unreformatted; a
            user-saved preset's own values are rendered fresh through
            `payload_to_yaml_text`, in `configuration.md`'s own key
            order, matching what "Save current as…" would write to a
            file today. `{"ok": False, "message": ...}` if `preset_id`
            names no known preset, or (user-saved only) if its
            configuration no longer validates — the identical failure
            mode `get_preset_form_values` already reports for the same
            reason.
        """
        if preset_id.startswith(_USER_PRESET_ID_PREFIX):
            name = preset_id[len(_USER_PRESET_ID_PREFIX) :]
            named_presets = self._preferences.named_presets or {}
            values = named_presets.get(name)
            if values is None:
                return {"ok": False, "message": f"no such preset: {preset_id}"}
            try:
                payload = form_values_to_payload(values)
                SimulationParams.from_mapping(payload)
            except ValueError as error:
                return {"ok": False, "message": str(error)}
            return {"ok": True, "title": name, "yaml": payload_to_yaml_text(payload)}
        preset = presets.get_preset(_webui_directory(), preset_id)
        if preset is None:
            return {"ok": False, "message": f"no such preset: {preset_id}"}
        return {"ok": True, "title": preset.title, "yaml": preset.yaml_text}

    @_log_bridge_call
    def save_current_as_preset(
        self, name: str, values: dict[str, str]
    ) -> dict[str, Any]:
        """Save `values` as a user preset named `name`, persisted immediately.

        Args:
            name: The preset's own display name and unique key — saving
                under a name that already exists silently overwrites it
                (`GuiPreferences.with_named_preset`'s own docstring).
                Leading/trailing whitespace is stripped; an empty name
                is rejected.
            values: The current form's own values (the identical shape
                `start_run`/`save_yaml` already accept).

        Returns:
            `{"ok": True}` on success; `{"ok": False, "message": ...}`
            if `name` is empty (after stripping) or `values` does not
            currently validate — saving an invalid configuration under a
            name would only defer the same error to whenever it is next
            loaded, with less context than reporting it now, at the
            point the user can still fix it.
        """
        stripped_name = name.strip()
        if not stripped_name:
            return {"ok": False, "message": "a preset needs a name"}
        try:
            SimulationParams.from_mapping(form_values_to_payload(values))
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        self._preferences = self._preferences.with_named_preset(stripped_name, values)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True}

    @_log_bridge_call
    def delete_user_preset(self, name: str) -> dict[str, Any]:
        """Delete one user-saved preset by name, persisted immediately.

        A `name` that does not exist is a silent no-op (`GuiPreferences.
        without_named_preset`'s own docstring) — the GUI's own delete
        affordance only ever offers a name it just listed.
        """
        self._preferences = self._preferences.without_named_preset(name)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True}

    @_log_bridge_call
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

    @_log_bridge_call
    def get_default_max_workers(self) -> int:
        """Return the Batch tab's own default parallel-worker count.

        `max_workers` is not a `SimulationParams` field at all — it
        never reaches `form_values_to_payload` — so it has no
        `config_form` entry; this reuses `batch_runner.default_max_
        workers` directly rather than inventing a second default.
        """
        return batch_runner.default_max_workers()

    @_log_bridge_call
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

    @_log_bridge_call
    def set_significant_digits(self, digits: int) -> dict[str, Any]:
        """Change the GUI's display-rounding precision (View menu).

        Purely cosmetic and "no scientific record": every persisted run
        artifact keeps full float precision regardless of this value
        (`_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS`'s own comment). Takes
        effect starting with the next `format_statistic` call a running
        or future screen makes — an already-open Screen 3/4 was
        formatted once, at push time, and is not retroactively
        reformatted. A valid change is saved to `self._preferences`
        immediately (`fim.gui.preferences`'s own "synchronous, no
        debounce" design choice), so it survives to the next launch —
        distinct from the "no record" property above, which is only
        ever about a *run's own* output, never this GUI-local setting.

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
        self._preferences = self._preferences.with_significant_digits(digits)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True, "digits": digits}

    @_log_bridge_call
    def get_dark_mode_override(self) -> str | None:
        """Return the saved dark-mode override, or `None` to follow the OS.

        Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
        redesign.md` §11.2. No in-memory instance attribute the way
        `_significant_digits` has one: nothing here reads this on a hot
        per-tick path the way a background run's own thread reads
        `_significant_digits`-adjacent state, so `self._preferences.
        dark_mode_override` is already the one place this value lives.
        """
        return self._preferences.dark_mode_override

    @_log_bridge_call
    def set_dark_mode_override(self, value: str | None) -> dict[str, Any]:
        """Change the saved dark-mode override (Configure's own field).

        Args:
            value: `"light"`, `"dark"`, or `None` to return to following
                the OS-level preference.

        Returns:
            `{"ok": True, "value": value}` on success; `{"ok": False,
            "message": ...}` if `value` is anything other than those
            three — a caller-side bug (an unrecognized `<select>`
            option), not a value a real user could type.
        """
        if value is not None and value not in ("light", "dark"):
            return {
                "ok": False,
                "message": (
                    f"dark mode override must be 'light', 'dark', or null: {value!r}"
                ),
            }
        self._preferences = self._preferences.with_dark_mode_override(value)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True, "value": value}

    @_log_bridge_call
    def get_welcome_dismissed(self) -> bool:
        """Return whether the first-launch welcome panel has already been shown.

        Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
        redesign.md` §10. `initializeRunView` (`run-view-initial.js`)
        calls this once, at launch, to decide whether to show the panel
        at all.
        """
        return self._preferences.welcome_dismissed

    @_log_bridge_call
    def dismiss_welcome(self) -> None:
        """Record that the first-launch welcome panel has been shown.

        One-directional (`GuiPreferences.with_welcome_dismissed`'s own
        docstring) — called once, whichever of the panel's own two
        actions the user picks, never un-called.
        """
        self._preferences = self._preferences.with_welcome_dismissed()
        save_preferences(self._preferences_path, self._preferences)

    @_log_bridge_call
    def get_startup_warnings(self) -> list[str]:
        """Drain and return any warnings collected while loading saved preferences.

        One-shot: returns the warnings collected so far and clears them,
        so a second call (a page reload, a second screen re-checking)
        never re-shows an already-acknowledged warning. Called by
        `webui/app.js` once, right after the first screen mounts —
        `Api.__init__` runs too early to show anything itself: there is
        no screen yet to display an inline `{"ok": False, "message":
        ...}` error against (`fim.gui.preferences.load_preferences`'s
        own docstring on why this exists instead of one).
        """
        warnings, self._startup_warnings = self._startup_warnings, []
        return warnings

    @_log_bridge_call
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

    @_log_bridge_call
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

        No range validation here (unlike `set_significant_digits`'s own
        bounds check) — this setter has no `d` or points on hand to
        validate against. `first_deme == second_deme` needs none at
        all: it is a deliberate self-comparison (P1 item 6 of the
        2026-09-06 open-issues doc; `deme_pair_panel`'s own docstring),
        permitted in both `app.js`'s shared `wireDemePairSelector` and
        here. An out-of-range pair reaching a push anyway (a stale
        selection from a previous run with a different `d`) is caught
        per-tick instead, where real data exists to catch it against
        (`_drain_run_messages`/`_push_batch_progress`'s own `deme_pair_
        panel` call, wrapped to skip that one tick's `pairPanel` rather
        than crash the whole push).

        Args:
            first_deme: 1-based deme number for the X axis, or `None`
                (with `second_deme` also `None`) to clear the selection
                back to the default panel — no UI trigger reaches this
                any more (the page's own "Show overview" button was
                later removed), but the clearing behavior itself
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

    @_log_bridge_call
    def list_recent_runs(self) -> list[dict[str, Any]]:
        """List every run under `results/`, newest first (`doc/fim-gui-design.md` §9).

        `fim.gui.recent_runs.list_recent_runs` is unchanged from the
        Tk-era build (no `tkinter` import today, needs none tomorrow) —
        this bridge method adds little logic of its own beyond calling
        it and reshaping each `RecentRun` into a JSON-ready dict.
        `trajectoryPath` is joined here, in Python (`pathlib.Path`'s
        own platform-correct separator), rather than the page
        concatenating `directory` and `"trajectory.jsonl"` itself —
        string-joining a path client-side would silently produce a
        mixed-separator path on Windows. `None` for a batch row: it has
        no single trajectory of its own to open.
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

    @_log_bridge_call
    def list_home_runs(self) -> list[dict[str, Any]]:
        """List every run, enriched with its own config summary and final statistics.

        Home enrichment design doc `20260909-claude-sonnet-5-home-
        enrichment-design.md` (`selby/restricted`), approach A1: the
        identical row set/order `list_recent_runs` already returns
        (`RecentRun.manifest` is what makes reusing it possible without
        a second `manifest.json` read per row), plus one more small,
        already-computed file read per row — `report.json` for a
        scalar run, `summary.json` for a batch — never a `trajectory.
        jsonl` read or any new engine computation. `list_recent_runs`
        itself stays unchanged: Compare's own recent-runs table and
        "Open a run…"'s own generation/differentiation-q controls never
        asked for this heavier per-row read, so neither pays for it.

        Returns:
            One dict per run/batch, newest first: `{"runId",
            "directory", "trajectoryPath", "endedAt", "label",
            "isBatch", "configSummary", "statistics"}` — the first six
            keys identical to `list_recent_runs`'s own shape.
            `configSummary` is `_run_config_summary`'s own `{"N", "d",
            "m", "mu", "mutation_model", "seed"}`, or `None` if
            `RecentRun.manifest` was unavailable (a hand-built row in a
            test) or its own parameters no longer validate. `statistics`
            is `None` if the row's own `report.json`/`summary.json`
            could not be read; otherwise one entry per `_RESULT_
            STATISTIC_NAMES` name — a `format_statistic`-formatted
            string for a scalar run, or `{"mean", "low", "high",
            "sampleCount"}` (`format_statistic`-formatted mean/low/
            high, matching `webui/meters.js`'s own `buildCiMeter`
            input shape exactly) for a batch.
        """
        digits = self._significant_digits
        rows: list[dict[str, Any]] = []
        for run in recent_runs.list_recent_runs():
            config_summary: dict[str, str] | None = None
            if run.manifest is not None:
                try:
                    config_summary = _run_config_summary(run.manifest.params())
                except ValueError:
                    config_summary = None
            statistics: dict[str, Any] | None = None
            if run.is_batch:
                raw_summary = _read_json_object(run.directory / "summary.json")
                if raw_summary is not None:
                    statistics = {
                        name: {
                            "mean": format_statistic(interval["mean"], digits),
                            "low": format_statistic(interval["low"], digits),
                            "high": format_statistic(interval["high"], digits),
                            "sampleCount": interval["sample_count"],
                        }
                        for name, interval in raw_summary.items()
                        if name in _RESULT_STATISTIC_NAMES
                    }
            else:
                raw_report = _read_json_object(run.directory / "report.json")
                if raw_report is not None:
                    statistics = {
                        name: format_statistic(
                            cast("float | None", raw_report.get(name)), digits
                        )
                        for name in _RESULT_STATISTIC_NAMES
                        if name in raw_report
                    }
            rows.append(
                {
                    "runId": run.run_id,
                    "directory": str(run.directory),
                    "trajectoryPath": (
                        None
                        if run.is_batch
                        else str(run.directory / "trajectory.jsonl")
                    ),
                    "endedAt": run.ended_at,
                    "label": run.label,
                    "isBatch": run.is_batch,
                    "configSummary": config_summary,
                    "statistics": statistics,
                }
            )
        return rows

    @_log_bridge_call
    def get_batch_replicate_summary(self, directory: str) -> dict[str, Any]:
        """List one persisted batch's own replicates, statistics only.

        Home enrichment design doc `20260909-claude-sonnet-5-home-
        enrichment-design.md` (`selby/restricted`), approach B1: fetched
        lazily, only the first time a batch row's own expand control is
        clicked (`webui/screens/open-run.js`'s own `toggleBatchRow`),
        not folded into `list_home_runs` itself — a `results/` directory
        with many old batches would otherwise pay this cost for every
        batch shown, expanded or not. Each replicate's own directory is
        `batch_runner.replicate_output_directory`'s own naming
        convention (`replicate-NNN`, recovered from `replicate_run_id`),
        the same helper `start_batch_run`'s own live "Open replicate"
        already uses — no second naming scheme.

        Args:
            directory: The batch's own output directory (`RecentRun.
                directory`/`Api.list_home_runs`'s own `"directory"`),
                the parent of its `manifest.json`.

        Returns:
            `{"ok": True, "replicates": [{"replicateId", "trajectoryPath",
            "statistics"}, ...]}`, in `BatchManifest.replicate_run_ids`'s
            own stored order — `statistics` is `None` for any one
            replicate whose own `report.json` could not be read (the
            same graceful-degradation `list_home_runs` already applies,
            one row deeper: a batch's own manifest and most other
            replicates are still worth showing even if one replicate's
            file is missing). `{"ok": False, "message": ...}` if
            `directory` names no readable batch manifest at all.
        """
        try:
            manifest = read_batch_manifest(Path(directory) / "manifest.json")
        except (OSError, ValueError) as error:
            return {"ok": False, "message": str(error)}
        digits = self._significant_digits
        replicates: list[dict[str, Any]] = []
        for replicate_run_id in manifest.replicate_run_ids:
            replicate_directory = batch_runner.replicate_output_directory(
                Path(directory), manifest.run_id, replicate_run_id
            )
            raw_report = _read_json_object(replicate_directory / "report.json")
            statistics = (
                {
                    name: format_statistic(raw_report.get(name), digits)
                    for name in _RESULT_STATISTIC_NAMES
                    if name in raw_report
                }
                if raw_report is not None
                else None
            )
            replicates.append(
                {
                    "replicateId": replicate_run_id,
                    "trajectoryPath": str(replicate_directory / "trajectory.jsonl"),
                    "statistics": statistics,
                }
            )
        return {"ok": True, "replicates": replicates}

    @_log_bridge_call
    def browse_for_trajectory(self) -> dict[str, Any]:
        """Browse for a `trajectory.jsonl` via the OS's own native file picker.

        `window.create_file_dialog(...)`, not an HTML `<input
        type="file">`: a better native-feel win than even Tk's own
        `filedialog.askopenfilename`, since pywebview's dialog is the
        OS's own file picker on every platform.

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

    @_log_bridge_call
    def open_run(self, values: dict[str, str]) -> dict[str, Any]:
        """Re-analyze a persisted trajectory, matching `fim stats`'s own semantics.

        Reached from the recent-runs picker (a row, or a browsed path)
        or "Open replicate" on a batch's own results table — the exact
        same operation over one replicate's own `trajectory.jsonl`. The
        returned payload is deliberately shaped exactly like
        `_drain_run_messages`'s own `"done"` payload, so the caller can
        hand it straight to the already-built `window.fim.showResults`
        — opening a run re-renders the unified run view's own
        `completed` state unchanged (§5.2), realized here as literal
        reuse, not a second rendering path.

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
            "outputDirectory", "trajectoryPath", "generationCount",
            "demeCount", "sigmaBand", "equilibrium",
            "identityRecovery"}` on success — `trajectoryPath` echoes
            this call's own resolved `trajectoryPath` input, so the
            Results card's own re-analysis controls (item 6) can re-
            issue this same call with a different `generation`/
            `differentiationOrders` against whichever run is currently
            showing, reopened or live-just-finished (`_drain_run_
            messages`'s own `"done"` payload carries the identical key
            for that second case); `sigmaBand` is `_sigma_band_payload`'s
            own result (sigma-band GUI design doc `20260910-claude-
            sonnet-5-gui-sigma-band-design.md`, `selby/restricted`,
            slice 4), `None` for a run that never requested one;
            `equilibrium`/`identityRecovery`
            are `_equilibrium_reference_payload`'s/`_identity_recovery_
            reference_payload`'s own results (botanist GUI design doc
            §6.2's two predicted-trajectory overlays), computed fresh
            from this reopened run's own manifest params, `None` when
            those params are not all plain scalars. `{"ok": False,
            "message": ...}` if no trajectory was given, the
            generation/q-sweep fields do not parse, or `fim.reanalyze.
            reanalyze_trajectory` itself raises (a trajectory-integrity
            failure, an edited file, or a generation that does not
            exist) — `message` is shown verbatim, matching `fim
            stats`'s own wording.
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
            # stats`'s wording" case from this bridge
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
            "effectiveAlleles": _effective_allele_summary(
                report, self._significant_digits
            ),
            "outputDirectory": str(trajectory_path.parent),
            "trajectoryPath": str(trajectory_path),
            "generationCount": reanalyzed.manifest.generation_count,
            "demeCount": reanalyzed.params.d,
            # Sigma-band GUI design doc `20260910-claude-sonnet-5-gui-
            # sigma-band-design.md` (`selby/restricted`) slice 4,
            # approach B1: `_sigma_band_payload` reused unchanged from
            # the live-run "done" push (`_drain_run_messages`) — a
            # reopened run's own manifest already carries this, no new
            # file read. Unlike a live run, a reopened run has no
            # `convergenceGenerations`/`convergenceHistories` of its
            # own to draw a curve from at all (this bridge method's own
            # docstring, above, already names that as a real, separate
            # scope boundary) — `run-view-completed.js`'s own
            # `renderTrajectory` shows the band alone, axes sized to
            # its own trailing window, rather than requiring a curve
            # that does not exist just to show a band that does.
            "sigmaBand": _sigma_band_payload(
                reanalyzed.manifest, self._significant_digits
            ),
            # The trajectory panel's own predicted-equilibrium overlay
            # (design doc §6.2, `_equilibrium_reference_payload`'s own
            # docstring) — computed fresh from this reopened run's own
            # manifest params (`_run_config_summary`'s own established
            # pattern of reading a manifest's params for a derived display
            # value), the same reasoning as a live run's own recompute at
            # `start_run` time: a pure function of `(N, m, mu, d)`, never
            # stale, and cheap enough to not bother caching.
            "equilibrium": _equilibrium_reference_payload(
                reanalyzed.params, self._significant_digits
            ),
            # The trajectory panel's own identity-recovery curve overlay
            # (design doc §6.2, `_identity_recovery_reference_payload`'s
            # own docstring) — same reasoning as `equilibrium` immediately
            # above: computed fresh from this reopened run's own manifest
            # params, a pure function of `(N, m)`.
            "identityRecovery": _identity_recovery_reference_payload(reanalyzed.params),
        }

    @_log_bridge_call
    def compare_runs(self, trajectory_paths: list[str]) -> dict[str, Any]:
        """Overlay two or more previously completed runs (design doc §8).

        Covers both halves of the Compare workspace: the small-multiples
        scatter (one final-state deme-1-vs-2 panel per run, reusing
        `reanalyze_trajectory`/`scatter_panels` exactly as `open_run`
        already does) and the trajectory-over-generations overlay
        (`fim.gui.trajectory_history.sampled_statistic_history`, one
        run's own evenly-spaced sample of every persisted generation,
        the same sampling density `get_animation_frames` already uses)
        — plus a legend naming which configuration field(s) actually
        differ across the selection. "No new engine computation"
        (design doc's own resolution-ledger entry for this workspace):
        every number here is already what a plain "open a run" or the
        animation screen's own frame sampler already computes.

        Args:
            trajectory_paths: Two or more `trajectory.jsonl` paths,
                typically `webui/screens/compare.js`'s own checked
                rows from the recent-runs list.

        Returns:
            `{"ok": True, "runs": [{"runId", "trajectoryPath", "panel",
            "statistics", "configSummary", "generations", "histories"},
            ...], "differingFields": [...]}` — `histories` is one
            formatted-string array per statistic (`format_statistic`'s
            own display shape, matching the live run view's identical
            `onRunProgress` convention exactly, so the same client-side
            `Number(...)`/`Number.isFinite` filter handles both),
            already the same length as `generations`, in the same
            order. `differingFields` names every `_run_config_summary`
            key whose value is not identical across every run, in that
            function's own fixed key order, so the page can render
            exactly those rows highlighted without recomputing the
            comparison itself. `{"ok": False, "message": ...}` if fewer
            than two paths were given, or any one trajectory/manifest
            cannot be read — the whole compare fails together rather
            than silently dropping the unreadable run, since a
            comparison missing a run the user explicitly picked would
            be misleading, not merely incomplete.
        """
        if len(trajectory_paths) < _COMPARE_MINIMUM_RUNS:
            return {"ok": False, "message": "select at least two runs to compare"}
        runs: list[dict[str, Any]] = []
        summaries: list[dict[str, str]] = []
        for path_text in trajectory_paths:
            try:
                reanalyzed = reanalyze_trajectory(Path(path_text))
                history = sampled_statistic_history(Path(path_text))
            except (OSError, ValueError) as error:
                return {"ok": False, "message": f"{path_text}: {error}"}
            summary = _run_config_summary(reanalyzed.params)
            summaries.append(summary)
            report = reanalyzed.report
            runs.append(
                {
                    "runId": reanalyzed.manifest.run_id,
                    "trajectoryPath": path_text,
                    "panel": scatter_panels(reanalyzed.state)[0],
                    "statistics": {
                        name: format_statistic(
                            cast("float | None", report[name]),
                            self._significant_digits,
                        )
                        for name in _RESULT_STATISTIC_NAMES
                    },
                    "configSummary": summary,
                    "generations": history.generations,
                    "histories": {
                        name: [
                            format_statistic(value, self._significant_digits)
                            for value in history.histories[name]
                        ]
                        for name in _RESULT_STATISTIC_NAMES
                    },
                }
            )
        differing_fields = [
            key
            for key in summaries[0]
            if len({summary[key] for summary in summaries}) > 1
        ]
        return {"ok": True, "runs": runs, "differingFields": differing_fields}

    @_log_bridge_call
    def get_animation_frames(self, output_directory: str) -> dict[str, Any]:
        """Sample and ship every animation frame for one run, in a single call.

        Loads the whole sampled set up front (`doc/fim-gui-design.md`
        §8), as raw
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
            (`fim.viz.scatter.panels_from_points`, `doc/fim-gui-design.md`
            §12): the page is responsible for any further reduction a
            high deme count needs. `demeCount` rides along for the
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

    @_log_bridge_call
    def get_animation_deme_pair_frames(
        self, output_directory: str, first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Recompute one explicit deme-pair panel for every sampled animation frame.

        `get_animation_frames`'s own "Compare demes directly" counterpart
        — the same choice the completed-run view already offers between
        the default view (a small-multiples pairwise grid for
        `d <= scatter.PAIRWISE_MAX_DEMES`, one Deme-1-vs-Deme-2 panel
        above it) and one explicit raw deme pair, extended to the whole
        animated trajectory rather than one static state. Still just
        one call: `webui/screens/animation.js` fires this once, when
        the user picks a pair, not once per frame or once per playback
        tick — zero further Python calls happen during playback, the
        same as for `get_animation_frames` itself, since the whole
        sampled set for that one pair comes back together, the same
        shape the default view's own frames already arrived in.

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

    @_log_bridge_call
    def get_deme_pair_panel(
        self, output_directory: str, first_deme: int, second_deme: int
    ) -> dict[str, Any]:
        """Recompute one explicit deme-pair 2-D panel for a completed run (Screen 3).

        The large-`d` counterpart to the completed-run view's own
        default panel (`showResults`'s own `panels[0]`, drawn whenever
        `d > scatter.PAIRWISE_MAX_DEMES` — Deme 1 vs. Deme 2 by
        default): this bridge method lets the user switch to any other
        specific raw deme pair instead, on demand,
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
            demes are out of range. `first_deme == second_deme` is a
            deliberate self-comparison, not an error
            (`deme_pair_panel`'s own docstring).
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

    @_log_bridge_call
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
            read, or the requested demes are out of range.
            `first_deme == second_deme` is a deliberate self-comparison,
            not an error (`deme_pair_panel`'s own docstring).
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

    @_log_bridge_call
    def ping(self) -> str:
        """Prove the basic JS-to-Python bridge round trip (Milestone W1).

        The walking skeleton's first proof: nothing about pywebview's
        window/bridge wiring is broken in this exact packaging/hosting
        context, before any real screen is built on top of it.
        """
        return "pong"

    @_log_bridge_call
    def ping_from_worker(self) -> str:
        """Prove a trivial picklable callable survives a real cross-process round trip.

        Deliberately proven this early, before `LiveProgressStore`/
        `batch_runner`'s own parallel plumbing (`doc/fim-gui-design.md`
        §7.2), both already built and tested, are ever reached through
        this specific pywebview-hosted process: `ProcessPoolExecutor`
        working at all from inside a GUI application process, not just
        from a plain CLI process, is an assumption worth its own direct
        check rather than only discovering a failure three layers away
        inside a real batch run.
        """
        with ProcessPoolExecutor(max_workers=1) as executor:
            return executor.submit(_worker_ping).result()

    @_log_bridge_call
    def open_external_link(self, url: str) -> None:
        """Open `url` in the OS default browser (doc/fim-gui-design.md §11).

        Every Tier 2 doc link and every bare `http(s)://` link inside a
        Tier 1 doc routes here, never to native `<a>` navigation — an
        unhandled link click inside a pywebview window can navigate the
        *whole application window* away from `index.html`, not open a
        new tab. `webbrowser.open`, best-effort, no return value the
        caller needs — the same `_reveal_in_file_browser` precedent this
        module already follows for another OS-dispatched action.
        """
        webbrowser.open(url)

    @_log_bridge_call
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

    @_log_bridge_call
    def get_about_info(self) -> dict[str, str]:
        """Return the static "About fim" facts the Help menu shows.

        No bridge state, no network call — `fim.__version__` and the
        project's own already-declared URLs, the same values `pyproject.
        toml`'s `[project.urls]` and `fim --version` already report.
        `organization`/`organization_url` credit Marie Selby Botanical
        Gardens, the institution this simulator was built for — shown
        alongside the reserved orchid mark (`branding/selby-orchid-logo.
        jpeg`, exposed to the web UI through its `assets/` link) that
        `screens/config-modals.js`'s own `modal-about`
        renders, not text-only attribution.
        """
        return {
            "version": fim_version,
            "repository": _REPOSITORY_URL,
            "license": "GNU Affero General Public License v3 or later (AGPLv3+)",
            "organization": "Marie Selby Botanical Gardens",
            "organization_url": "https://selby.org/botany/",
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
    equilibrium: dict[str, str] | None = None,
    identity_recovery: dict[str, float] | None = None,
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

    `equilibrium`/`identity_recovery` are `_start_scalar_run`'s own
    already-computed `_equilibrium_reference_payload`/`_identity_
    recovery_reference_payload` results (design doc §6.2's two
    predicted-trajectory overlays — see each function's own docstring
    for what they mean and why they are two separate things) — carried
    through to the eventual `"done"` push unchanged, the identical
    values the page already cached at run-start (`Api.start_run`'s own
    return), not a second, independent computation.
    """
    logger.debug("run message-drain thread started: %s", output_directory)
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
                # rather than blank until the run finishes.
                "statistics": {
                    name: format_statistic(message[4][name], digits)
                    for name in _RESULT_STATISTIC_NAMES
                },
            }
            pair = live_deme_pair()
            if pair is not None:
                first_deme, second_deme = pair
                # Out of range for this run's own `d` — a stale
                # selection from a previous run with a different `d`,
                # since the selector itself only ever offers `1..d` for
                # the run actually in progress. `first_deme ==
                # second_deme` is a deliberate self-comparison, not an
                # error (`deme_pair_panel`'s own docstring), so it never
                # reaches this `suppress` at all; skip this one tick's
                # `pairPanel` rather than drop the whole progress push
                # over the genuinely out-of-range case.
                with contextlib.suppress(ValueError):
                    progress_payload["pairPanel"] = deme_pair_panel(
                        message[3], first_deme - 1, second_deme - 1
                    )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("run progress: generation=%s", message[1])
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
                "effectiveAlleles": _effective_allele_summary(result.report, digits),
                "outputDirectory": str(output_directory),
                # The Results card's own re-analysis controls (Home/results
                # design item 6: "Generation"/"Differentiation-q sweep,"
                # relocated here from the old open-run screen) need a
                # trajectory to re-analyze even for a just-finished live
                # run, not only a reopened one -- `Api.open_run`'s own
                # return payload carries the identical key for that
                # second case. Every scalar run's own trajectory is
                # always at this fixed path (`Api.list_home_runs`'s own
                # established `directory / "trajectory.jsonl"` join).
                "trajectoryPath": str(output_directory / "trajectory.jsonl"),
                "generationCount": result.manifest.generation_count,
                "demeCount": deme_count,
                # The trajectory panel (botanist GUI design doc
                # `20260907-claude-sonnet-5-botanist-gui-redesign.md`
                # §6.2) — `RunResult` already computes both, for free,
                # as a byproduct of the run's own `ConvergenceMonitor`;
                # never sent to this page before this. `Api.open_run`'s
                # own payload carries neither key at all (a re-analyzed
                # run has no live monitor to have recorded a history
                # from), which is exactly how `run-view-completed.js`'s
                # own `renderTrajectory` already knows to hide the panel
                # rather than needing an explicit "not available" flag.
                "convergenceGenerations": result.convergence_generations,
                "convergenceHistories": result.convergence_histories,
                # Sigma-band GUI design doc `20260910-claude-sonnet-5-
                # gui-sigma-band-design.md` (`selby/restricted`) slice
                # 3, approach B1: already in memory on `result.manifest`
                # (the engine's own already-shipped extension-run logic,
                # `8615614`/`8c8da68`) — no new computation, no extra
                # file read.
                "sigmaBand": _sigma_band_payload(result.manifest, digits),
                # The trajectory panel's own predicted-equilibrium overlay
                # (design doc §6.2, `_equilibrium_reference_payload`'s own
                # docstring) — `_start_scalar_run`'s own already-computed
                # value, threaded through unchanged rather than recomputed
                # a second time here.
                "equilibrium": equilibrium,
                # The trajectory panel's own identity-recovery curve
                # overlay (design doc §6.2, `_identity_recovery_reference_
                # payload`'s own docstring) — same reuse, not recomputed.
                "identityRecovery": identity_recovery,
            }
            logger.info("run done: %s", output_directory)
            window.evaluate_js(f"fim.onRunDone({json.dumps(payload)})")
            if on_message is not None:
                on_message(message)
            return
        elif message[0] == "cancelled":
            logger.info("run cancelled: %s", output_directory)
            window.evaluate_js(f"fim.onRunCancelled({json.dumps(message[1])})")
            if on_message is not None:
                on_message(message)
            return
        else:
            logger.warning("run error: %s", message[1])
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
    — the same parsing rule, moved from a private Tk screen method to
    a module-level function here.

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
    `_parse_differentiation_orders` — unchanged.

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
    initial_states: dict[str, ModelState] | None = None,
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
    than blank until the batch finishes. Naturally
    empty (`{}`) for the first tick or two, before a second replicate
    has reported anything to summarize yet — `reports_summary` never
    raises for that, unlike `replicate_summary`.

    `meanReportedGeneration`, when present (omitted while no replicate
    has reported anything yet), is the live batch trajectory panel's
    own x-axis for this tick (batch trajectory panel design `20260912-
    claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/
    restricted`) — `run-view-running.js`'s own `accumulateLiveBatch
    Trajectory` appends it, paired with `statistics`' own per-name
    `mean`, to the same client-side trajectory accumulator a scalar
    run's own progress push already feeds.

    `initialStatistics` is the identical pooled-interval shape as
    `statistics`, but always for generation 0 specifically, across
    every replicate ever seen reporting so far (not only this tick's
    own currently-reporting subset — a replicate's own generation-0
    state never changes, so once read it stays counted even after that
    replicate moves on or finishes). Without this, the live trajectory
    panel's own x-axis could only ever start wherever the *first*
    two-or-more-replicates tick happened to land — plausibly generation
    10+ for a fast-running batch whose replicates had already outrun
    `_BATCH_POLL_INTERVAL_SECONDS`'s own first tick before this
    function ever got to look — silently misrepresenting how much of
    the run's own early history was actually skipped, not shown.
    `initial_states` is the cross-tick cache that makes this cheap: read
    once per replicate (`read_live_state(..., generation=0, ...)`, safe
    to call at any later generation too, since `trajectory.jsonl` is
    append-only and generation 0's own rows are never overwritten),
    never re-read on a later tick.

    Args:
        window: See other bridge-pushing functions in this module.
        params: This batch's own validated configuration.
        run_id: This batch's own id (not any one replicate's).
        working_directory: Where replicates are writing, mid-run.
        live_deme_pair: See this function's own body, below.
        digits: `Api._significant_digits`, formatting every statistic
            this call sends exactly like every other bridge push does.
        initial_states: A cache this function both reads and writes,
            expected to be the *same* dict object across every tick of
            one batch's own poll loop (`_drain_batch_messages` creates
            one and passes it to every one of its own `_push_batch_
            progress` calls) — `None` (the default) is only for a
            caller that does not care about paying the one-time
            generation-0 read cost again on every call, such as a test
            exercising a single tick in isolation.
    """
    if initial_states is None:
        initial_states = {}
    states: list[ModelState] = []
    for index in range(1, params.n_replicates + 1):
        replicate_run_id = f"{run_id}-r{index:03}"
        directory = batch_runner.replicate_output_directory(
            working_directory, run_id, replicate_run_id
        )
        sidecar = read_progress_sidecar(directory / ".progress")
        if sidecar is None:
            continue
        trajectory_path = directory / "trajectory.jsonl"
        state = read_live_state(
            trajectory_path,
            replicate_run_id,
            sidecar["generation"],
            params.loci,
        )
        if state is not None:
            states.append(state)
        if replicate_run_id not in initial_states:
            initial_state = read_live_state(
                trajectory_path, replicate_run_id, 0, params.loci
            )
            if initial_state is not None:
                initial_states[replicate_run_id] = initial_state
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
    raw_initial_summary = reports_summary(
        [
            report_for_state(
                state,
                params,
                run_id=run_id,
                converged=False,
                reason="initial conditions",
            )
            for state in initial_states.values()
        ]
    )
    initial_statistics = {
        name: {
            "mean": format_statistic(interval["mean"], digits),
            "low": format_statistic(interval["low"], digits),
            "high": format_statistic(interval["high"], digits),
            "sampleCount": interval["sample_count"],
        }
        for name, interval in raw_initial_summary.items()
    }
    progress_payload: dict[str, object] = {
        "replicateCount": params.n_replicates,
        "reportedReplicateCount": len(states),
        "panels": panels,
        "demeCount": params.d,
        "statistics": statistics,
        "initialStatistics": initial_statistics,
    }
    if states:
        # The live batch trajectory panel's own x-axis (batch trajectory
        # panel design `20260912-claude-sonnet-5-batch-trajectory-panel-
        # design.md`, `selby/restricted`, approach A): no single tick
        # has one shared "generation" the way a scalar run's own
        # progress push does, since replicates report at different
        # generations by construction -- the *mean* across whichever
        # replicates have reported this tick gives a meaningful, if
        # approximate, sense of "how far along," without a fastest-
        # replicate outlier dragging it forward the way a *max* would.
        # Rounded to the nearest integer for a clean axis tick; omitted
        # entirely (not `0`) when no replicate has reported yet, so the
        # client's own accumulator (`accumulateLiveBatchTrajectory`)
        # knows to skip this tick rather than plot a bogus generation 0.
        progress_payload["meanReportedGeneration"] = round(
            sum(state.generation for state in states) / len(states)
        )
    pair = live_deme_pair()
    if pair is not None and pooled_points is not None:
        first_deme, second_deme = pair
        # Same defensive fallback as `_drain_run_messages`'s own
        # identical case: only a stale, out-of-range selection ever
        # reaches this `suppress` — a self-comparison is not an error.
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
    """Build `fim.onBatchDone`'s own payload from a batch's final results.

    `replicates` is the row data for the completed view's own batch
    table (one row per *published* replicate — `results`' own length,
    not necessarily `params.n_replicates`: an adaptive
    `replicate_tolerance` stop can end a batch short of its own cap),
    including each row's own `trajectoryPath` — joined here, in Python
    (`batch_runner.replicate_output_directory`), rather than the page
    concatenating `outputDirectory` and a replicate directory name
    itself (the same Windows mixed-separator risk `list_recent_runs`'
    own docstring names) — for "Open replicate" to hand
    straight to `Api.open_run` with no path logic of its own. `summary`
    is `replicate_summary`'s own per-statistic confidence interval,
    pre-formatted server-side (`format_statistic`, matching every
    other statistic this bridge ever sends the page: the client never
    reimplements Python's own display formatting, for a batch's own
    results the same as a scalar run's) — omitted entirely (an empty
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

    `pooledConvergenceHistories` is `pooled_convergence_histories`'s
    own result, pre-formatted (batch trajectory panel design `20260912-
    claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/
    restricted`, commit 2) — deliberately a different key from a scalar
    "done" payload's own `convergenceHistories` (bare per-generation
    floats, one shared generation list) rather than reusing that name
    for a differently-shaped value: each point here already carries its
    own `generation` alongside `mean`/`low`/`high`/`sampleCount`, since
    different statistics can have different generation coverage once
    replicates start dropping out (that function's own docstring).
    Empty (`{}`), the same as `summary` immediately above, if
    `pooled_convergence_histories` itself has too few results to define
    even one generation's own interval from.
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
    try:
        raw_pooled_histories = pooled_convergence_histories(results)
    except ValueError:
        raw_pooled_histories = {}
    pooled_convergence_histories_payload = {
        name: [
            {
                "generation": point["generation"],
                "mean": format_statistic(point["mean"], digits),
                "low": format_statistic(point["low"], digits),
                "high": format_statistic(point["high"], digits),
                "sampleCount": point["sample_count"],
            }
            for point in points
        ]
        for name, points in raw_pooled_histories.items()
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
        "pooledConvergenceHistories": pooled_convergence_histories_payload,
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
    way a scalar run's is, since it is entirely file-mediated
    (`doc/fim-gui-design.md` §7.2).

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
    logger.debug("batch message-drain thread started: %s", working_directory)
    # One dict, for this batch's entire poll loop -- `_push_batch_
    # progress`'s own `initial_states` parameter, read once per
    # replicate (the first tick that sees it reporting at all) and
    # never re-read on a later tick.
    initial_states: dict[str, ModelState] = {}
    while True:
        try:
            message = message_queue.get(timeout=_BATCH_POLL_INTERVAL_SECONDS)
        except queue.Empty:
            _push_batch_progress(
                window,
                params,
                run_id,
                working_directory,
                live_deme_pair,
                digits,
                initial_states,
            )
            continue
        if message[0] == "done":
            payload = _batch_done_payload(
                params, run_id, output_directory, message[1], digits
            )
            logger.info("batch done: %s", output_directory)
            window.evaluate_js(f"fim.onBatchDone({json.dumps(payload)})")
        elif message[0] == "cancelled":
            cancelled_payload = {"replicateIndex": message[1], "generation": message[2]}
            logger.info("batch cancelled: %s", output_directory)
            window.evaluate_js(f"fim.onBatchCancelled({json.dumps(cancelled_payload)})")
        else:
            logger.warning("batch error: %s", message[1])
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


def _configure_macos_native_about_panel() -> None:
    """Populate the version/copyright/icon the native About FIM panel shows.

    macOS's app menu always carries its own "About FIM" item, wired by
    `pywebview`'s Cocoa backend straight to the standard Cocoa selector
    `orderFrontStandardAboutPanel:` (`webview.platforms.cocoa._add_app_
    menu`) -- a completely separate, OS-native dialog from `fim`'s own
    Help > "About fim" `MenuAction`, which instead opens the branded
    HTML `modal-about` dialog (`screens/config-modals.js`'s
    `showAboutModal`). The two are never the same code path: a user
    who opens the native one by way of the "FIM" app menu (its
    standard, expected macOS position) sees whatever this function last
    set on `NSBundle.mainBundle()`'s info dictionary and `NSApplication.
    sharedApplication().applicationIconImage`, regardless of the HTML
    dialog's own content.

    Before this ran, that native panel showed only the bare app name
    over a generic folder icon: `_set_macos_application_name` sets
    `CFBundleName` alone, and an unbundled `python3` process supplies
    none of `CFBundleShortVersionString`, `CFBundleVersion`, or
    `NSHumanReadableCopyright` for the standard panel to read, nor any
    icon for `NSApplication.applicationIconImage` to fall back to. This
    fills in the same facts `get_about_info` already gives the HTML
    dialog -- version and Marie Selby Botanical Gardens attribution --
    plus the shipped orchid mark as the app icon, so both About
    surfaces agree rather than one being blank.

    A no-op everywhere except macOS, and tolerant of `AppKit`/icon-file
    absence for the same reason `_set_macos_application_name` is: a
    cosmetic About panel is never worth failing application startup
    over.
    """
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSBundle, NSImage  # noqa: PLC0415
        except ImportError:
            return
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleShortVersionString"] = fim_version
            info["CFBundleVersion"] = fim_version
            info["NSHumanReadableCopyright"] = (
                "Marie Selby Botanical Gardens (https://selby.org/botany/)"
            )
        logo_path = _branding_directory() / "selby-orchid-logo.jpeg"
        if logo_path.is_file():
            image = NSImage.alloc().initWithContentsOfFile_(str(logo_path))
            if image is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(image)


def _set_macos_application_name(name: str) -> None:
    """Rename this process's own app identity for the macOS menu bar.

    Only macOS shows a per-process app name at all, as the bold,
    leftmost item of the menu bar — sourced from the running process's
    own bundle `CFBundleName`, never from anything `webview.create_
    window`'s own `title` controls (that only ever reaches the window's
    *title bar*, a separate piece of text — see `_WINDOW_TITLE`). A
    plain, unbundled `python3` process's bundle is Python's own
    framework bundle (`CFBundleName == "Python"`), which is exactly why
    an unmodified `fim`/`fim-gui` run shows "python" there instead of
    naming this application at all — nothing wrong with the window
    itself, just an unrelated, unbundled process's own borrowed
    identity leaking into a completely different piece of system UI.

    Mutating `NSBundle.mainBundle()`'s own live info dictionary before
    any window or menu is created is the documented workaround for
    exactly this case (an unbundled pyobjc app): `webview.platforms.
    cocoa` (imported the moment `webview.create_window` runs on macOS)
    already mutates two *other* keys on this same dictionary object at
    import time, for its own, unrelated reasons — confirming the same
    live-mutation technique already works reliably for it, not a
    speculative trick tried here for the first time.

    A no-op everywhere except macOS (`AppKit` is a macOS-only optional
    dependency `pywebview` pulls in only there — `pyproject.toml`'s own
    platform markers), and tolerant of it being unavailable for any
    other reason: a cosmetic menu-bar rename is never worth failing the
    whole application over. The platform check is a positive `if
    sys.platform == "darwin":` wrapping the whole body, not an early
    `if sys.platform != "darwin": return` — matching `fim.launcher`'s
    own `win32`-only branch (its own comment there for why): mypy
    treats a `sys.platform` comparison specially and does not flag
    statements *inside* a not-taken platform branch as unreachable,
    but flags everything *after* an early return that mypy can prove
    always fires under whichever platform it is itself running on
    (Linux in CI, unlike the macOS this was written and tested on) —
    the early-return form failed exactly that check in CI, on this
    file's every earlier revision.
    """
    if sys.platform == "darwin":
        try:
            from AppKit import NSBundle  # noqa: PLC0415 -- macOS-only
        except ImportError:
            return
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name


def create_window(*, api: Api | None = None, hidden: bool = False) -> webview.Window:
    """Build, but do not show, fim's one pywebview window over `webui/index.html`.

    Separate from `main` specifically so tests can drive the window
    themselves via `webview.start(callback)` without ever calling the
    real, blocking `main` — construct real widgets, drive them
    synchronously, never call the real blocking entry point without a
    controlled exit: the same testing discipline this package has
    always followed (`doc/fim-gui-test-plan.md`), now against
    pywebview's own API instead of Tk's.

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
    _set_macos_application_name(_MACOS_APPLICATION_NAME)
    _configure_macos_native_about_panel()
    created = webview.create_window(
        _WINDOW_TITLE,
        url=str(_webui_directory() / "index.html"),
        js_api=api if api is not None else Api(),
        width=900,
        height=700,
        hidden=hidden,
    )
    if created is None:
        raise RuntimeError("pywebview did not create a window")
    logger.debug("window created (hidden=%s)", hidden)
    return created


def _build_menu(window: webview.Window) -> list[Menu]:
    """Build the native File/Run/Help menu bar.

    (Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
    redesign.md` §3.3, "What deliberately stops being a menu item.")

    Edit and Window are deliberately left out: every text field already
    gets native Cut/Copy/Paste from the WebView engine itself, and a
    Window menu has no app-specific value for a single-window tool.
    Configure and View — a six-section menu plus a fourth-menu "System
    digits" submenu, in this project's own pre-redesign shape — are
    gone entirely, not merely renamed: every field either menu used to
    reach (including the three former quick-toggle leaves, "Deme
    weighting"/"Mutation model"/"Convergence statistic") now lives
    directly on the always-visible Configure screen the rail reaches
    (`webui/index.html`'s own two-panel `screen-configure`), reachable
    the same way regardless of how quick a toggle it happens to be —
    design principle 1's own "one model, one view," applied to the
    whole menu bar, not only the four headline parameters. Significant
    digits is the one setting that was never a `SimulationParams` field
    at all (`Api.set_significant_digits`'s own docstring); it now lives
    on that same screen as an ordinary, non-form field
    (`config-modals.js`'s own `wireSignificantDigitsField`).

    Every item except Quit is a thin closure calling `window.evaluate_js(
    "fim.menu.X()")` — the identical "no-op stub, overridden by whichever
    screen owns the real behavior" convention `app.js` already
    established for `onRunProgress`/`onBatchDone`/etc., extended to a
    `fim.menu` namespace so no new Python business logic exists anywhere
    in this menu: every item reuses an `Api` method or JS-side screen
    state a screen already tracks — a second entry point into logic
    that already exists, not new logic. Quit
    alone needs no JS round trip — `window.destroy()` is a window-level
    call, not app state.
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
            MenuAction("Load example…", dispatch("fim.menu.loadExample()")),
            MenuSeparator(),
            MenuAction("Open run…", dispatch("fim.menu.openRun()")),
            MenuAction(
                "Reveal output folder", dispatch("fim.menu.revealOutputFolder()")
            ),
            MenuSeparator(),
            MenuAction("Quit fim", window.destroy),
        ],
    )
    # No "Animate" item (`doc/fim-gui-design.md` §5.1): the
    # time slider is simply part of `completed`'s own view now, not a
    # second trigger reachable from a menu.
    run_menu = Menu(
        "Run",
        [
            MenuAction("Run simulation", dispatch("fim.menu.runSimulation()")),
            MenuAction("Cancel run", dispatch("fim.menu.cancelRun()")),
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
    return [file_menu, run_menu, help_menu]


def shutdown_timeout() -> float:
    """Return how long ordinary GUI shutdown is allowed to take.

    Reads `FIM_GUI_SHUTDOWN_TIMEOUT` (seconds) and falls back to
    `_SHUTDOWN_DEADMAN_SECONDS`. A malformed value falls back rather than
    raising: this is a safety net, and refusing to start -- or crashing
    on exit -- because its own timeout was mistyped would be a worse
    outcome than the hang it guards against.

    Args:
        None

    Returns:
        The timeout in seconds. Zero or negative disables the deadman.
    """
    raw = os.environ.get("FIM_GUI_SHUTDOWN_TIMEOUT")
    if raw is None:
        return _SHUTDOWN_DEADMAN_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "ignoring malformed FIM_GUI_SHUTDOWN_TIMEOUT=%r; using %gs",
            raw,
            _SHUTDOWN_DEADMAN_SECONDS,
        )
        return _SHUTDOWN_DEADMAN_SECONDS


def _shutdown_dump_streams() -> list[TextIO]:
    """Collect every destination the shutdown thread dump should reach.

    Always includes `sys.stderr` (useful when `fim` was launched from a
    terminal) and additionally every open `fim` log file stream, which is
    the only destination that survives a windowed launch with no console
    attached.

    Resolving the log streams is itself wrapped: this runs on a path where
    ordinary shutdown has already failed, so a failure to enumerate
    handlers must degrade to "stderr only" rather than propagate.

    Args:
        None

    Returns:
        Destinations in write order, stderr first, never empty.
    """
    streams: list[TextIO] = [sys.stderr]
    try:
        streams.extend(logging_setup.log_file_streams())
    except Exception:
        # Deliberately broad: this runs on a path where ordinary shutdown
        # has already failed, so failing to enumerate handlers must
        # degrade to "stderr only" rather than propagate.
        logger.debug("could not resolve log file streams for shutdown dump")
    return streams


def _start_shutdown_deadman(timeout_seconds: float) -> None:
    """Guarantee the process actually exits after the window closes.

    A closed window that leaves the process alive is, from the user's
    point of view, indistinguishable from a crash with none of a crash's
    honesty: the app is gone from the screen, nothing can be clicked, and
    yet `fim` is still running -- holding its port, its lock, and its
    place in the dock or task manager. Reopening it may silently do
    nothing, and the only cure is Force Quit or Task Manager, which no
    part of this application's own documentation should ever have to
    ask a user to do.

    That failure is not hypothetical here. `create_window` already
    carries one fix for this exact class (pywebview's own HTTP handler
    threads, forced to `daemon_threads` so they cannot outlive a closed
    window), and this suite's own history (`test/gui/conftest.py`) has
    several more, each an in-flight bridge call or leftover thread
    wedging `Py_FinalizeEx -> wait_for_thread_shutdown` indefinitely.
    Every one of those was found and fixed, but each was found *after*
    reaching a real build; the mechanism that produces them -- one
    non-daemon thread outliving the GUI -- is a property of the
    libraries involved, not of any one bug, so the next one is a matter
    of when.

    A GUI has nothing legitimate to do after its window closes: no
    unsaved state (a run's own output is written as it goes, by the
    worker that owns it), no network flush, no user waiting on a result.
    Shutdown is expected to be effectively instantaneous, which is
    exactly what makes a deadman timer safe here -- the timeout is orders
    of magnitude longer than any honest shutdown, so it can only ever
    fire on a genuine hang. `os._exit` is deliberate and is the whole
    point: it terminates immediately without running finalization, which
    is precisely the step that is stuck.

    The daemon timer thread cannot itself delay shutdown, so on every
    healthy exit this function is invisible: the process is gone long
    before the timer would fire, and nothing is printed.

    Args:
        timeout_seconds: How long to allow for ordinary shutdown before
            forcing exit. Values at or below zero disable the deadman
            entirely, which `FIM_GUI_SHUTDOWN_TIMEOUT=0` exposes as a
            documented escape hatch for anyone who needs to debug a hang
            rather than have it terminated out from under them.

    Returns:
        None
    """
    if timeout_seconds <= 0:
        logger.debug("shutdown deadman disabled")
        return

    def force_exit() -> None:
        # Reached only when ordinary shutdown has already failed, so
        # this reports rather than exiting mutely -- a user who runs
        # `fim` from a terminal, and any log file, gets a real
        # explanation instead of an unexplained hard exit.
        message = (
            f"fim: shutdown did not complete within {timeout_seconds:g}s; forcing exit"
        )
        logger.error("shutdown deadman fired after %gs; forcing exit", timeout_seconds)
        print(message, file=sys.stderr, flush=True)
        # Dump every thread's stack first: this is the one moment the
        # offending thread can still be identified, and without it a
        # forced exit would trade a diagnosable hang for a silent one.
        #
        # The dump goes to the log file as well as stderr, and the log
        # file is what actually matters here. A GUI launched from Finder,
        # a dock icon, or a Start-menu shortcut has no terminal attached,
        # so its stderr is discarded -- and that is exactly the user least
        # able to reproduce this under a terminal on request. Writing only
        # to stderr would reliably lose the traceback for everyone except
        # the developers who need it least.
        #
        # `faulthandler` writes to a file object rather than through
        # `logging`, so the handler's own stream is borrowed directly. A
        # marker line is logged first so the raw dump that follows it in
        # the file is attributable rather than looking like corruption.
        for stream in _shutdown_dump_streams():
            try:
                faulthandler.dump_traceback(file=stream)
                stream.flush()
            except Exception:
                # Deliberately swallowed, and deliberately broad: this
                # runs while the interpreter is already wedged, and a
                # best-effort diagnostic that raised here would leave the
                # process in the very hang the next line exists to end.
                # Losing one dump destination is always better.
                continue
        # `os._exit`, not `sys.exit`: `sys.exit` raises an exception that
        # unwinds into the very interpreter finalization that is stuck,
        # so it would join the hang rather than end it.
        os._exit(_SHUTDOWN_DEADMAN_EXIT_CODE)

    def wait_then_force_exit() -> None:
        time.sleep(timeout_seconds)
        force_exit()

    # `daemon=True` is essential: a non-daemon timer would itself become
    # a thread blocking shutdown -- the exact bug this guards against.
    timer = threading.Thread(
        target=wait_then_force_exit,
        name="fim-shutdown-deadman",
        daemon=True,
    )
    timer.start()
    logger.debug("shutdown deadman armed (%gs)", timeout_seconds)


def main() -> int:
    """Launch the GUI and block until the window closes.

    Configures logging from `FIM_LOG_LEVEL`/`FIM_LOG_OPTIONS`
    (`doc/fim-logging-design.md` §5) again here, independently of
    `fim.launcher.main`'s own call — reached only via `fim.launcher`'s
    GUI branches in practice, where the environment was already
    validated once, but this keeps the function independently correct
    for any future caller that reaches it another way.

    Returns:
        0 on an ordinary close — `webview.start()` returning means the
        user closed the window, not an error condition to report
        differently — or 2 if `FIM_LOG_LEVEL`/`FIM_LOG_OPTIONS` is
        malformed. A hung shutdown never returns from here at all: the
        deadman terminates the process with
        `_SHUTDOWN_DEADMAN_EXIT_CODE` instead (see
        `_start_shutdown_deadman`).
    """
    try:
        logging_setup.configure(
            os.environ.get("FIM_LOG_LEVEL", "warning"),
            logging_setup.parse_log_options(os.environ.get("FIM_LOG_OPTIONS")),
        )
    except ValueError as error:
        print(f"fim: error: {error}", file=sys.stderr)
        return 2
    logger.info("starting fim %s", fim_version)
    window = create_window()
    webview.start(menu=_build_menu(window))
    logger.info("window closed")
    # Armed only now, after the window has closed: the timer's budget
    # covers shutdown alone, never the arbitrarily long time a user may
    # legitimately leave the application open.
    _start_shutdown_deadman(shutdown_timeout())
    return 0


def _branding_directory() -> Path:
    """Return the reserved top-level branding directory.

    Source checkouts keep branding outside `src/` so the repository's
    AGPL-covered application source and its reserved identity assets are
    unambiguous. Distribution builds materialize the web UI's asset links,
    so an installed or frozen application reads the packaged copy there.
    """
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return _webui_directory() / "assets"

    project_branding = Path(__file__).resolve().parents[3] / "branding"
    if project_branding.is_dir():
        return project_branding
    return _webui_directory() / "assets"


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
