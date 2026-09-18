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
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from math import exp, isfinite
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Final, Protocol, TextIO, cast

import webview
import yaml
from webview.menu import Menu, MenuAction, MenuSeparator

from fim import __version__ as fim_version
from fim import engine as engine_module
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
from fim.gui.animation import pre_render_batch_frames, pre_render_frames
from fim.gui.config_form import (
    DEFAULT_RUN_SETTING_FIELD_NAMES,
    field_for_error,
    form_values_to_payload,
    m_from_params,
    mu_from_params,
    params_to_form_values,
    payload_to_yaml_text,
    starter_form_values,
    tab_for_error,
)
from fim.gui.literature_visuals import (
    literature_visual_payload,
    pooled_literature_visual_payload,
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
from fim.persistence import groups
from fim.persistence.manifest import RunManifest, read_batch_manifest, read_manifest
from fim.persistence.run_metadata import run_metadata_path
from fim.reanalyze import reanalyze_trajectory
from fim.statistics import (
    effective_allele_count,
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_heterozygosity_isolated,
    equilibrium_heterozygosity_total,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
    identity_recovery_equilibrium,
    identity_recovery_half_life,
    identity_recovery_rate,
    mutation_negligible_equilibrium,
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

# The differentiation/statistics set shown by the Results view and
# Compare summary. `H_ST` joined the engine after the first GUI milestone
# and is now shown with the other bounded statistics; `A_CGD`/`Delta`/
# `MI` (the literature-derived supplemental measurements) joined here
# too once `track_expensive_statistics` started tracking them
# per-generation like `E_ST`/`K_ST` (`fim.engine._EXPENSIVE_OPT_IN_
# STATISTICS`) — they retired the separate "Supplemental statistics"
# panel/payload (`_literature_statistic_summary`, removed) that used to
# show them final-report-only, since they now flow through the exact
# same live/trajectory pipeline as every other watched-or-tracked
# statistic.
_RESULT_STATISTIC_NAMES: Final = (
    "D",
    "G_ST",
    "E_ST",
    "K_ST",
    "H_S",
    "H_T",
    "H_ST",
    "A_CGD",
    "Delta",
    "MI",
)

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


def _interval_payload(interval: Mapping[str, Any], digits: int) -> dict[str, Any]:
    """Reshape one `ConfidenceInterval` into `buildCiMeter`'s own input.

    The single source of the `{mean, low, high, sampleCount}` shape every
    batch-statistics payload this module sends the page uses — the live
    batch progress push, the batch "done" payload, and the Home screen's
    own per-row summary all route through here rather than each repeating
    the same four `format_statistic` calls (which is how they were
    written before the sample-standard-deviation field existed, and what
    made adding it a four-place edit).

    `halfWidth` and `sampleStd` are present **only** for an interval that
    has an honest symmetric summary, and absent together otherwise
    (sample-standard-deviation tooltip design `20260912-claude-sonnet-5-
    sample-std-dev-tooltip-design.md`, `selby/restricted`, approaches A1
    and B1). `sample_std` is `None` exactly when the interval came from
    `fim.engine._bootstrap_interval`, whose own docstring says its
    `half_width` is "a symmetrized summary kept only for display
    consistency" and not the authoritative interval shape — so stating
    *either* number for such an interval would state something its own
    constructor disclaims. One decision, made once here, rather than the
    page re-deriving "is this interval symmetric" from the numbers.

    A `summary.json` written before `sample_std` existed has no such key
    at all, which `.get` reads as `None` — so reopening an older batch
    renders the same shorter tooltip, with no migration and no invented
    number.

    Args:
        interval: A `ConfidenceInterval`, or the equivalent mapping read
            back from a persisted `summary.json`.
        digits: `Api._significant_digits`, formatting every number here
            exactly like every other statistic this bridge sends.

    Returns:
        The page-facing dict, `halfWidth`/`sampleStd` included only when
        `interval` carries a real `sample_std`.
    """
    payload: dict[str, Any] = {
        "mean": format_statistic(interval["mean"], digits),
        "low": format_statistic(interval["low"], digits),
        "high": format_statistic(interval["high"], digits),
        "sampleCount": interval["sample_count"],
    }
    sample_std = interval.get("sample_std")
    if sample_std is not None:
        payload["halfWidth"] = format_statistic(interval["half_width"], digits)
        payload["sampleStd"] = format_statistic(sample_std, digits)
    return payload


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


# `replicate_summary`'s own Student's-t interval is symmetric around the
# sample mean and knows nothing about `H_S`/`H_T`'s own true domain
# (`effective_allele_count`'s own docstring: a real number in `[0, 1)`,
# never `1.0` exactly) -- a small, high-variance sample can genuinely
# produce a `low`/`high` edge outside that domain, the identical "wild
# band" `computeBatchTrajectoryValueDomain`'s own docstring already
# documents for `D` (a thin-sample tail reaching `low: -2.98, high:
# 3.54` for a statistic that never otherwise leaves roughly `[0, 1]`).
# `effective_allele_count` raises outright rather than accepting such a
# value — confirmed live, a real 2-replicate batch's own `H_S` interval
# reached a `high` at or past `1.0`, crashing `_drain_batch_messages`'s
# own background thread the first time this function was exercised
# against a genuine batch rather than a hand-built interval. `H_S`/`H_T`
# cannot actually lie outside `[0, 1)` regardless of what the interval's
# own symmetric formula computed, so clamping each endpoint to that
# domain before the transform reports the best still-honest reading
# available for an admittedly poorly-estimated interval, not a
# fabricated number the transform was never defined for.
_EFFECTIVE_ALLELE_UPPER_BOUND: Final = 1.0 - 1e-9


def _clamped_effective_allele_count(heterozygosity_value: float) -> float:
    """`effective_allele_count`, after clamping its own input to `[0, 1)`.

    See `_EFFECTIVE_ALLELE_UPPER_BOUND`'s own comment for why this
    clamp exists. Only `_effective_allele_interval_summary`'s own
    `low`/`high` interval edges ever need it in practice
    (`replicate_summary`'s own interval construction is symmetric and
    domain-unaware); a real report's own `H_S`/`H_T` — and therefore an
    interval's own `mean`, an average of such values — is already a
    valid heterozygosity by construction, so clamping it here too is
    purely defensive, never expected to change the result.
    """
    return effective_allele_count(
        min(max(heterozygosity_value, 0.0), _EFFECTIVE_ALLELE_UPPER_BOUND)
    )


def _effective_allele_interval_summary(
    raw_summary: Mapping[str, Mapping[str, Any]], digits: int
) -> dict[str, Any]:
    """Return `H_S`/`H_T`'s own effective-allele-count confidence interval, batch case.

    The batch counterpart to `_effective_allele_summary` immediately
    above (a single run's own point transform): `raw_summary` is
    `replicate_summary`'s own per-statistic cross-replicate confidence
    interval — or `{}`, this module's own established stand-in for "too
    few results to define one" (`_batch_done_payload`'s own docstring,
    `summary`). `H_S`/`H_T` are always either both present or both
    absent (`replicate_summary`'s own docstring: unlike `G_ST`, every
    replicate's `H_S`/`H_T` are always defined floats), so checking one
    for absence is a complete check for both.

    The transform (`_clamped_effective_allele_count`, `1 / (1 - H)`
    after clamping to `H`'s own domain — see that function's own
    docstring for why the clamp is necessary at all) is applied directly
    to each interval's own `mean`/`low`/`high`. Clamping aside, the
    transform is strictly increasing over `H`'s own domain, so it
    carries an interval's own ordering (and, for an edge that did not
    need clamping, its own exact confidence coverage) across the
    transform. `sample_count` is copied unchanged; `half_width`/
    `sample_std` are deliberately not carried across the transform at
    all — an `H`-scale plus-or-minus does not become the equivalent
    `D`-scale plus-or-minus by simple carry-over — so `_interval_payload`
    omits both here exactly like it already does for an interval that
    came from `fim.engine._bootstrap_interval`.

    Args:
        raw_summary: `replicate_summary`'s own return value (or `{}`).
        digits: The GUI's own configured display precision.

    Returns:
        `{}` if `raw_summary` has no `"H_S"` (equivalently `"H_T"`);
        otherwise `{"H_S": <interval payload>, "H_T": <interval
        payload>, "gStCaution": <bool>}` — the same shape
        `_effective_allele_summary` returns for a scalar run, with each
        readout an interval rather than a single point.
    """
    within_interval = raw_summary.get("H_S")
    total_interval = raw_summary.get("H_T")
    if within_interval is None or total_interval is None:
        return {}
    return {
        "H_S": _interval_payload(
            {
                "mean": _clamped_effective_allele_count(
                    cast("float", within_interval["mean"])
                ),
                "low": _clamped_effective_allele_count(
                    cast("float", within_interval["low"])
                ),
                "high": _clamped_effective_allele_count(
                    cast("float", within_interval["high"])
                ),
                "sample_count": within_interval["sample_count"],
            },
            digits,
        ),
        "H_T": _interval_payload(
            {
                "mean": _clamped_effective_allele_count(
                    cast("float", total_interval["mean"])
                ),
                "low": _clamped_effective_allele_count(
                    cast("float", total_interval["low"])
                ),
                "high": _clamped_effective_allele_count(
                    cast("float", total_interval["high"])
                ),
                "sample_count": total_interval["sample_count"],
            },
            digits,
        ),
        "gStCaution": cast("float", within_interval["mean"])
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


def _equilibrium_numeric_predictions(
    n: int, m: float, mu: float, d: int
) -> dict[str, float | bool | None]:
    """Evaluate every Explore prediction at one `(N, m, mu, d)` point.

    The single source of truth for *which* analytical quantities Explore
    knows about. Both presentations read from here: `_equilibrium_
    prediction_payload` formats these for the table, and `Api.get_
    equilibrium_sweep` collects one of these per swept point for the
    chart. Neither maintains its own second list of statistic names, so
    a quantity added here appears in both places at once.

    Every one of these is a closed-form function of `(N, m, mu, d)`
    alone, so every one of them is defined along every one of the four
    sweep axes. Several do not depend on every parameter -- `D` has no
    `N` term at all (`equilibrium_d`'s own docstring: only the
    migration-to-mutation ratio sets where it settles), `H_S` depends on
    `N` and `mu` only, and the whole Whitlock identity-recovery family
    depends on `N` and `m` only. Swept against a parameter they do not
    contain, those produce a flat line. That flat line is the point, not
    a degenerate case to suppress: `D` level across two orders of
    magnitude of `N` while `G_ST` falls away beneath it is this
    project's central claim, drawn rather than asserted.

    Args:
        n: Population size in gene copies per deme.
        m: Symmetric migration rate.
        mu: Infinite-alleles mutation rate.
        d: Number of equal demes.

    Returns:
        One entry per predicted quantity, as a plain number (or, for
        `mutation_negligible_equilibrium`, a bool). `None` marks a
        quantity this particular configuration leaves undefined -- every
        mutation-dependent entry at `mu == 0`, for instance. Callers
        plotting these must break the line at a `None` rather than
        reading it as a zero.
    """

    def value_of(function: Callable[..., float], *args: float | int) -> float | None:
        try:
            return function(*args)
        except ValueError:
            return None

    s_s_value = value_of(equilibrium_shannon_entropy_subpopulation, n, m, mu, d)
    s_t_value = value_of(equilibrium_shannon_entropy_total, n, m, mu, d)
    return {
        "D": value_of(equilibrium_d, m, mu, d),
        "G_ST": value_of(equilibrium_g_st, n, m, mu, d),
        "E_ST": value_of(equilibrium_shannon_differentiation, n, m, mu, d),
        "H_S": value_of(equilibrium_heterozygosity_isolated, n, mu),
        "H_T": value_of(equilibrium_heterozygosity_total, n, m, mu, d),
        "S_S": s_s_value,
        "S_T": s_t_value,
        # `exp` of the full-precision entropy, never of an already
        # display-rounded one -- `format_statistic` is applied to this
        # result, not to its input.
        "A_S": exp(s_s_value) if s_s_value is not None else None,
        "A_T": exp(s_t_value) if s_t_value is not None else None,
        "identity_recovery_rate": value_of(identity_recovery_rate, n, m),
        "identity_recovery_equilibrium": value_of(identity_recovery_equilibrium, n, m),
        "identity_recovery_half_life": value_of(identity_recovery_half_life, n, m),
        "mutation_negligible_equilibrium": mutation_negligible_equilibrium(m, mu, n),
    }


def _equilibrium_prediction_payload(
    n: int, m: float, mu: float, d: int, digits: int
) -> tuple[dict[str, str | bool], dict[str, str]]:
    """Build Explore's complete analytical prediction payload.

    Args:
        n: Population size in gene copies per deme.
        m: Symmetric migration rate.
        mu: Infinite-alleles mutation rate.
        d: Number of equal demes.
        digits: GUI display precision.

    Returns:
        Formatted predictions and qualification messages. Formula-specific
        ``ValueError`` results become the existing ``"undefined"`` value.
    """
    values = _equilibrium_numeric_predictions(n, m, mu, d)
    predictions: dict[str, str | bool] = {
        name: value if isinstance(value, bool) else format_statistic(value, digits)
        for name, value in values.items()
    }
    qualifications = {
        "S_S": (
            "Approximate; less reliable when d = 2"
            if d == _MINIMUM_EQUILIBRIUM_DEMES
            else "Approximate equilibrium subpopulation entropy"
        )
    }
    return predictions, qualifications


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


def _home_run_row(run: recent_runs.RecentRun, *, digits: int) -> dict[str, Any]:
    """Build one Home row for `run`.

    Factored out of `Api.list_home_runs` so `Api.get_study_run_summary`
    can build pixel-for-pixel identical rows for a Study's own lazily
    fetched runs, rather than a second, independently maintained
    enrichment path (`20260917-claude-sonnet-5-run-study-experiment-
    hierarchy-design.md`, `selby/restricted`).

    Returns:
        `{"runId", "directory", "trajectoryPath", "endedAt", "label",
        "isBatch", "configSummary", "statistics", "name"}`.
        `configSummary` is `_run_config_summary`'s own `{"N", "d", "m",
        "mu", "mutation_model", "seed"}`, or `None` if `run.manifest`
        was unavailable or its own parameters no longer validate.
        `statistics` is `None` if the row's own `report.json`/
        `summary.json` could not be read; otherwise one entry per
        `_RESULT_STATISTIC_NAMES` name. `name` is the run's own
        `metadata.json` name (`fim.persistence.run_metadata`), or
        `None` if it was never set.
    """
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
                name: _interval_payload(interval, digits)
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
    raw_metadata = _read_json_object(run_metadata_path(run.directory))
    run_name = raw_metadata.get("name") if raw_metadata is not None else None
    return {
        "runId": run.run_id,
        "directory": str(run.directory),
        "trajectoryPath": (
            None if run.is_batch else str(run.directory / "trajectory.jsonl")
        ),
        "endedAt": run.ended_at,
        "label": run.label,
        "isBatch": run.is_batch,
        "configSummary": config_summary,
        "statistics": statistics,
        "name": run_name if isinstance(run_name, str) else None,
    }


def _recent_run_at_directory(directory: Path) -> recent_runs.RecentRun | None:
    """Build one `RecentRun` for an arbitrary directory, not a `list_recent_runs` scan.

    A Study's own `run_directories` (`fim.persistence.groups.
    study_run_directories`) can reference a directory anywhere, not
    only a direct child of `results_directory()` — the one case `fim.
    gui.recent_runs.list_recent_runs`'s own `root.glob("*/manifest.
    json")` scan cannot reach by construction. A small local copy of
    that module's own `_recent_run_from_file` try-scalar-then-batch
    logic, rather than importing its private name across packages.
    """
    manifest_path = directory / "manifest.json"
    try:
        manifest = read_manifest(manifest_path)
    except (OSError, ValueError, KeyError):
        pass
    else:
        return recent_runs.RecentRun(
            run_id=manifest.run_id,
            directory=directory,
            ended_at=manifest.ended_at,
            label=manifest.stop_reason,
            is_batch=False,
            manifest=manifest,
        )
    try:
        batch_manifest = read_batch_manifest(manifest_path)
    except (OSError, ValueError, KeyError):
        return None
    n_replicates = batch_manifest.params().n_replicates
    return recent_runs.RecentRun(
        run_id=batch_manifest.run_id,
        directory=directory,
        ended_at=batch_manifest.ended_at,
        label=f"batch ({batch_manifest.replicate_count}/{n_replicates})",
        is_batch=True,
        manifest=batch_manifest,
    )


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
        on_batch_progress: Callable[[dict[str, object]], None] | None = None,
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
            on_batch_progress: Test-only hook, called with `_push_batch_
                progress`'s own `progress_payload` dict, right after
                that tick's own `window.evaluate_js(fim.onBatchProgress
                (...))` push — a batch's own per-generation progress is
                never a `BatchMessage` (`_drain_batch_messages`'s own
                docstring: "nothing here is possible... it is entirely
                file-mediated"), so `on_message` alone cannot observe
                it. Exists so a test can wait for a specific real
                condition (commonly: the first tick whose own
                `payload["statistics"]` is non-empty, guaranteed once
                two or more replicates have reported) via a `threading.
                Event`, the same "push, not poll" shape `on_message`
                already gives a scalar or terminal batch message,
                without a test-side `window.evaluate_js` poll loop
                racing this same background thread's own pushes — see
                `test/gui/test_running_screen.py`'s own module
                docstring for why that race is a real, previously
                diagnosed defect, not a theoretical one.
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
        self._on_batch_progress = on_batch_progress
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
    def start_run(
        self, values: dict[str, str], study_id: str | None = None
    ) -> dict[str, Any]:
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
            study_id: An existing Study to add this run to once it
                finishes (`run-view-controls.js`'s own `run-study-
                select`, "No study" mapping to `None`) — the GUI
                counterpart to `fim run --study <id>` (`fim.cli`); see
                `20260917-claude-sonnet-5-run-study-experiment-workflow-
                ergonomics.md` (`selby/restricted`), item 3. Validated
                here, before anything starts, so a stale id (a Study
                deleted moments ago in another window) fails the launch
                outright rather than silently producing an unattached
                run the botanist thought they had organized.

        Returns:
            `{"ok": True, "isBatch": ..., "equilibrium": ...,
            "identityRecovery": ...}` once the run has *started* — not
            once it finishes; the real outcome arrives via the pushed
            calls above. `isBatch` is `params.n_replicates > 1`, the
            identical "batch or scalar" toggle `_start_batch_run`'s own
            dispatch above already used — `run-view-controls.js`'s own
            `onRunClicked` reads it back to pick which running-state
            sub-view to show, since `values` alone no longer carries
            `n_replicates` (moved to Settings; Configure's own `<form>`
            never submits it). `equilibrium` is
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
            the form does not validate, `study_id` does not name an
            existing Study, or the output directory cannot be allocated.
        """
        if study_id is not None:
            try:
                groups.get_study(study_id)
            except ValueError as error:
                return {"ok": False, "message": str(error)}
        values = self._merge_default_run_settings(values)
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
        is_batch = params.n_replicates > 1
        result = (
            self._start_batch_run(params, output_directory, values, study_id)
            if is_batch
            else self._start_scalar_run(params, output_directory, study_id)
        )
        # `n_replicates` is no longer a field Configure's own `<form>`
        # submits (moved to Settings) -- `run-view-controls.js`'s own
        # `onRunClicked` can no longer infer "batch or scalar" from the
        # values it already has in hand before this call, so the real,
        # validated `params.n_replicates` this method computed is
        # reported back explicitly instead.
        result["isBatch"] = is_batch
        return result

    def _start_scalar_run(
        self,
        params: SimulationParams,
        output_directory: Path,
        study_id: str | None = None,
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
                study_id,
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
        study_id: str | None = None,
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
        except (FileExistsError, ValueError) as error:
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
                self._on_batch_progress,
                study_id,
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

    def _starter_form_values_for_this_session(self) -> dict[str, str]:
        """`starter_form_values`, overlaid with any saved Settings defaults.

        Falls back to the true, un-overlaid starter values if the saved
        overlay no longer validates (a range this project tightened
        since it was saved, say) — the same "a stale saved value is
        discarded wholesale, never applied partially" policy `get_
        initial_form` already applies to a stale `form_values` restore,
        below.
        """
        if self._preferences.default_run_settings is None:
            return starter_form_values()
        try:
            return starter_form_values(overrides=self._preferences.default_run_settings)
        except ValueError:
            return starter_form_values()

    def _merge_default_run_settings(self, values: dict[str, str]) -> dict[str, str]:
        """Fill in Configure-absent execution-default fields before using a submission.

        Configure's own `<form>` no longer collects `config_form.
        DEFAULT_RUN_SETTING_FIELD_NAMES`' fields, or `max_workers`, at
        all — moved to Settings (`2026-09-16` revision) — so `webui/
        screens/config-modals.js`'s own `collectFormValues` (a
        `FormData` scan of `#input-form`) never produces any of them.
        Every bridge call that expects an `all_fields()`-complete values
        dict from Configure's live form merges them in here, from
        whatever Settings currently holds (`get_default_run_settings`'s
        own fallback chain) — the value *at the moment of this call*,
        not one captured whenever Configure's form happened to last
        load, the more correct semantic for a field with no per-run
        override. Only fills a key `values` does not already have, so a
        future per-run override (none exists today) is never silently
        clobbered.
        """
        merged = dict(values)
        for key, value in self.get_default_run_settings().items():
            merged.setdefault(key, value)
        return merged

    def _sync_default_run_settings_from_loaded_config(
        self, values: Mapping[str, str]
    ) -> None:
        """Update Settings' own execution defaults to match a just-loaded configuration.

        A real, reported request: loading a preset or a hand-picked YAML
        file that names a specific `engine_backend`/`n_replicates`/etc.
        makes that configuration's own values the session's new
        execution defaults too — otherwise a submitted run would
        silently ignore what was just loaded in favor of whatever
        Settings already held (`_merge_default_run_settings`, above,
        which only ever reads from Settings, since Configure's own
        `<form>` no longer submits any of these fields itself). This is
        the one, deliberate exception to that function's own "no per-run
        override" rule: loading a named, curated configuration is
        exactly the moment a user's intent for *this* execution shape is
        most explicit, so it is allowed to change the session default
        rather than being silently discarded. `max_workers` is left
        untouched — not a `SimulationParams` field, so a loaded
        configuration never has an opinion on it.

        Args:
            values: An already-validated form-values dict (`params_to_
                form_values`'s own output, or an equivalently-shaped
                saved preset) — never re-validated here, since every
                caller has already confirmed it round-trips through
                `SimulationParams.from_mapping`.
        """
        saved = self._preferences.default_run_settings
        max_workers = (saved or {}).get("max_workers", "")
        subset = {key: values[key] for key in DEFAULT_RUN_SETTING_FIELD_NAMES}
        subset["max_workers"] = max_workers
        self._preferences = self._preferences.with_default_run_settings(subset)
        save_preferences(self._preferences_path, self._preferences)

    @_log_bridge_call
    def get_starter_form(self) -> dict[str, str]:
        """Return a fresh form's default values.

        `config_form.starter_form_values` is the single source of "GUI
        defaults" — the identical values `fim.cli.STARTER_CONFIG` itself
        expands to — overlaid with any saved Settings-dialog defaults
        (`_starter_form_values_for_this_session`) for exactly the field
        set the user has moved there (execution engine, `n_replicates`,
        the convergence-selection group — a real, reported request:
        "the defaults can be applicable pretty universally"). `fim.menu.
        newConfiguration`'s own explicit, unconditional reset — distinct
        from `get_initial_form`, just below, which a fresh app launch
        calls instead.
        """
        return self._starter_form_values_for_this_session()

    @_log_bridge_call
    def get_initial_form(self) -> dict[str, str]:
        """Return the values a fresh app launch's own Input screen should show.

        Honors the user's startup behavior setting. `"restore"` prefers
        the last successfully submitted form (`GuiPreferences.form_
        values`, saved by `start_run` below) over `get_starter_form`'s
        own values. `"restart"` ignores the saved form and starts from
        the starter values (with any saved Settings defaults overlaid,
        exactly like `get_starter_form`). A restored form is re-
        validated through the exact same `form_values_to_payload`/
        `SimulationParams.from_mapping` path `start_run` itself uses: a
        saved form that no longer validates is discarded wholesale
        rather than applied partially. Deliberately does *not* apply
        the Settings-defaults overlay to a restored form — Settings
        only ever affects what a *fresh* configuration starts with,
        never an in-progress restored session, avoiding a second,
        competing precedence rule against this restore path.
        """
        if self._preferences.startup_behavior == "restart":
            return self._starter_form_values_for_this_session()
        values = self._preferences.form_values
        if values is None:
            return self._starter_form_values_for_this_session()
        try:
            SimulationParams.from_mapping(form_values_to_payload(values))
        except ValueError:
            return self._starter_form_values_for_this_session()
        return values

    @_log_bridge_call
    def get_startup_behavior(self) -> str:
        """Return how a fresh launch chooses its initial form values."""
        return self._preferences.startup_behavior

    @_log_bridge_call
    def set_startup_behavior(self, value: str) -> dict[str, Any]:
        """Set how a fresh launch chooses its initial form values.

        Args:
            value: `"restore"` to reuse the last valid form at startup,
                or `"restart"` to start from the starter form.

        Returns:
            `{"ok": True, "value": value}` on success; otherwise
            `{"ok": False, "message": ...}`.
        """
        if value not in ("restart", "restore"):
            return {
                "ok": False,
                "message": (
                    f"startup behavior must be 'restart' or 'restore': {value!r}"
                ),
            }
        self._preferences = self._preferences.with_startup_behavior(value)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True, "value": value}

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
        values = self._merge_default_run_settings(values)
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
        values = self._merge_default_run_settings(values)
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
        values = self._merge_default_run_settings(values)
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
            `{"ok": True, "predictions": {...}, "qualifications": {...}}`;
            numeric values are strings already formatted by
            `format_statistic`, while the regime diagnostic is a boolean.
            Formula-specific undefined values use `"undefined"`;
            `{"ok": False,
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

        predictions, qualifications = _equilibrium_prediction_payload(
            n_value, m_value, mu_value, d_value, digits
        )
        return {
            "ok": True,
            "predictions": predictions,
            "qualifications": qualifications,
        }

    @_log_bridge_call
    def get_equilibrium_sweep(
        self, axis: str, n: str, m: str, mu: str, d: str
    ) -> dict[str, Any]:
        """Sweep one of N/d/m/mu and return every prediction across it.

        Explore's own curve (design doc
        `20260907-claude-sonnet-5-botanist-gui-redesign.md` §5.2):
        `axis` sweeps across `_EQUILIBRIUM_SWEEP_DOMAINS[axis]`, a fixed
        display range independent of the other three fields' current
        values, which are held fixed at whatever `get_equilibrium_
        predictions` was just called with.

        Every quantity `_equilibrium_numeric_predictions` knows about is
        evaluated at every swept point, not the `D`/`G_ST`/`E_ST` subset
        that shares a `[0, 1]` domain. No statistic is privileged: each
        one is a closed-form function of `(N, m, mu, d)`, so each one has
        a real curve along each of the four axes, and the differing units
        (proportion, nats, effective alleles, generations) are a charting
        concern the client resolves by grouping series into unit families
        -- not a reason to withhold the numbers here.

        Carrying all of them also makes Explore's axis scrubber free: the
        client re-reads the prediction table straight out of `points` as
        the marker moves, with no extra bridge round trip per tick.

        Args:
            axis: Which field to sweep — one of `"N"`, `"d"`, `"m"`,
                `"mu"`.
            n: Population size, held fixed unless `axis == "N"`.
            m: Migration rate, held fixed unless `axis == "m"`.
            mu: Mutation rate, held fixed unless `axis == "mu"`.
            d: Deme count, held fixed unless `axis == "d"`.

        Returns:
            `{"ok": True, "axis": axis, "current": <parsed current value
            of axis>, "current_index": <index into points nearest that
            value>, "series": [<statistic name>, ...], "points": [{"x":
            ..., <statistic name>: ..., ...}, ...]}`. Every statistic is
            a plain number (plotting reads these as numbers, not
            formatted strings), a bool for `mutation_negligible_
            equilibrium`, or `None` wherever that point's own
            configuration leaves it undefined -- every mutation-dependent
            entry at `mu == 0`, for instance. `{"ok": False, "message":
            ...}` if `axis` is not one of the four names above, or if
            `n`/`d`/`m`/`mu` do not parse.
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
        swept_values: list[float | int] = []
        for raw_value in swept:
            value = value_at(raw_value)
            sweep_n = int(value) if axis == "N" else n_value
            sweep_d = int(value) if axis == "d" else d_value
            sweep_m = float(value) if axis == "m" else m_value
            sweep_mu = float(value) if axis == "mu" else mu_value
            swept_values.append(value)
            points.append(
                {
                    "x": value,
                    **_equilibrium_numeric_predictions(
                        sweep_n, sweep_m, sweep_mu, sweep_d
                    ),
                }
            )

        # Where the scrubber's marker starts: the swept point closest to
        # the configuration's own committed value on this axis. The
        # sweep spans a fixed display range that need not contain that
        # value exactly (`_geometric_sweep` lands on its own points, and
        # a committed value can even sit outside the domain), so this is
        # a nearest match rather than an exact lookup -- the same
        # "snap to the nearest sampled position" rule `run-view-
        # completed.js`'s own `nearestGeneration` uses for the
        # trajectory scrubber.
        #
        # Read from `swept_values` rather than back out of `points`: a
        # point also carries the predictions, several of which are
        # legitimately `None` or `bool`, so the merged mapping's value
        # type no longer says that `"x"` in particular is a number.
        current_index = min(
            range(len(swept_values)),
            key=lambda index: abs(swept_values[index] - current),
        )

        return {
            "ok": True,
            "axis": axis,
            "current": current,
            "current_index": current_index,
            # The client formats scrubbed values itself, straight out of
            # `points`, so it needs the same display precision the
            # committed table was formatted with -- otherwise moving the
            # scrubber onto the committed value would silently change how
            # many digits every row shows.
            "digits": self._significant_digits,
            "series": [name for name in points[0] if name != "x"],
            "points": points,
        }

    @_log_bridge_call
    def load_yaml(self) -> dict[str, Any]:
        """Browse for and load a YAML config, returning the form values it renders to.

        Routes through `fim.cli.load_config` — the identical function
        `fim run` uses (doc/fim-gui-design.md) — so a config that runs from the
        terminal loads identically here, error for error. Also syncs
        Settings' own execution defaults to match the loaded file
        (`_sync_default_run_settings_from_loaded_config`'s own
        docstring) — the same treatment `load_preset`, below, gives a
        loaded preset.

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
        self._sync_default_run_settings_from_loaded_config(values)
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
            "builtin": <bool>, "loadable": <bool>}, ...]}` — built-in
            presets first, in `doc/usage.md`'s own document order, then
            user-saved presets sorted by name. A built-in preset's own
            `id` is its bare slug (`get_preset_form_values` reads it
            directly); a user-saved preset's own `id` is
            `"user:<name>"` (`_USER_PRESET_ID_PREFIX`), so the two id
            spaces can never collide even if a user happens to choose
            a name matching a built-in slug. `loadable` is `False`
            exactly when `get_preset_form_values(id)["ok"]` would be
            `False` for that same id — design doc `20260913-claude-
            sonnet-5-gui-worked-example-loadability-design.md`
            (`selby/restricted`): computed here, once per `list_
            presets` call, so the page can label a preset it cannot
            actually apply *before* the user picks it, rather than
            picking it and hitting a load failure with no advance
            warning. A handful of already-in-memory YAML parses, not a
            network call — cheap enough to redo on every call rather
            than caching a result that could go stale if a preferences
            range constraint changes.
        """
        found = presets.list_presets(_webui_directory())
        result = [
            {
                "id": preset.preset_id,
                "title": preset.title,
                "builtin": True,
                "loadable": self.get_preset_form_values(preset.preset_id)["ok"],
            }
            for preset in found
        ]
        named_presets = self._preferences.named_presets or {}
        result.extend(
            {
                "id": f"{_USER_PRESET_ID_PREFIX}{name}",
                "title": name,
                "builtin": False,
                "loadable": self.get_preset_form_values(
                    f"{_USER_PRESET_ID_PREFIX}{name}"
                )["ok"],
            }
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
    def load_preset(self, preset_id: str) -> dict[str, Any]:
        """Load one preset into the form, syncing Settings to match it.

        The actual "apply this preset" bridge call
        (`screens/presets.js`'s own `applyPreset`) — distinct from
        `get_preset_form_values`, above, which `list_presets` also
        calls, once per preset, purely to compute each preset's own
        `loadable` flag. Syncing Settings on every such probe would
        silently overwrite the user's own saved defaults every time the
        picker opens, a real, surprising side effect `list_presets`'s
        own loadability check must never trigger — so the sync
        (`_sync_default_run_settings_from_loaded_config`'s own
        docstring) lives here, the one call site that means "the user
        actually chose this," not inside `get_preset_form_values`
        itself.

        Args:
            preset_id: A `preset_id` from a prior `list_presets` call.

        Returns:
            The identical shape `get_preset_form_values` returns.
        """
        result = self.get_preset_form_values(preset_id)
        if result["ok"]:
            self._sync_default_run_settings_from_loaded_config(result["values"])
        return result

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
        values = self._merge_default_run_settings(values)
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
        values = self._merge_default_run_settings(values)
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
    def get_engine_backend_availability(self) -> dict[str, bool]:
        """Report which optional dependencies the engine selector needs.

        Configure's own `engine_backend` `<select>` offers all four legal
        values, two of which need the optional `numba` dependency to run
        at all: `"generational-vector"` imports it unconditionally
        (`fim.model.vectorized`'s own mutate step has no pure-Python
        fallback), and `"auto"` — the selector's own recommended default
        — resolves to that backend for essentially every vector-eligible
        configuration at the shipped `auto_vector_min_d`/
        `auto_vector_max_capacity` defaults. A source checkout installed
        without the `[jit]` extra can therefore select either and watch
        the run fail with `build_engine_backend`'s own "needs the
        optional numba dependency" `ValueError`.

        Reported rather than hidden: the page relabels exactly those two
        options (`screens/config-modals.js`'s own
        `applyEngineBackendAvailability`) instead of removing them, so
        every legal value still round-trips through save/load — the same
        reason all four are listed in the first place (GUI engine-backend
        selector design doc `20260911-claude-sonnet-5-gui-engine-backend-
        selector-design.md`, approach A3 alongside B3).

        Delegates to `fim.engine._numba_is_available` through the module
        rather than importing the function by name, deliberately: that
        function is module-level "specifically so a test can monkeypatch
        it directly" (its own docstring), and a `from ... import` would
        bind a copy at import time and quietly defeat exactly that hook.

        Returns:
            `{"numba": True}` when the optional `numba` dependency can be
            imported in this install, `{"numba": False}` otherwise.
        """
        return {"numba": engine_module._numba_is_available()}

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
    def get_default_run_settings(self) -> dict[str, str]:
        """Return the Settings dialog's own execution-default field values.

        Seeds Settings' own fields on open. Falls back to `starter_
        form_values()`'s own values for exactly `config_form.DEFAULT_
        RUN_SETTING_FIELD_NAMES`' keys when nothing has been saved yet
        (`self._preferences.default_run_settings is None`), so the
        dialog never shows a blank field the first time it opens.
        `max_workers` is not a `SimulationParams` field and so has no
        entry in `starter_form_values()`'s own base dict at all — its
        fallback is the empty string (`webui`'s own "auto" placeholder),
        the identical default `_parse_max_workers("")` already treats
        as "let the batch runner choose."

        A saved `default_run_settings` is re-overlaid onto the starter
        values, exactly like `_starter_form_values_for_this_session`,
        rather than returned as-is: a dict saved under an earlier
        version of this field set (missing a key this version added, or
        carrying one a later revision dropped — `DEFAULT_RUN_SETTING_
        FIELD_NAMES`'s own docstring records one real, reported such
        revision already) must not silently propagate an incomplete or
        stale projection forward. A saved value that no longer overlays
        cleanly at all falls back to the full, un-overlaid starter
        subset, the same "discarded wholesale, never applied partially"
        policy every other stale-saved-value path in this module
        already follows.
        """
        saved = self._preferences.default_run_settings
        if saved is not None:
            try:
                merged = starter_form_values(overrides=saved)
                values = {key: merged[key] for key in DEFAULT_RUN_SETTING_FIELD_NAMES}
            except ValueError:
                starter = starter_form_values()
                values = {key: starter[key] for key in DEFAULT_RUN_SETTING_FIELD_NAMES}
            values["max_workers"] = saved.get("max_workers", "")
            return values
        starter = starter_form_values()
        values = {key: starter[key] for key in DEFAULT_RUN_SETTING_FIELD_NAMES}
        values["max_workers"] = ""
        return values

    @_log_bridge_call
    def set_default_run_settings(self, values: dict[str, str]) -> dict[str, Any]:
        """Validate and persist Settings' own execution-default field values.

        A real, reported request: fields describing "how the
        computation runs" (execution engine, replicate/generation
        budgets, convergence-loop timing, replicate confidence, JIT,
        the `auto` backend's own threshold pair, and worker/concurrency
        limits) move out of the per-run Configure form and into one
        global-default home here — `starter_form_values`'s own
        `overrides` parameter is what every subsequent fresh-form call
        (`get_starter_form`, `get_initial_form`) reads this back
        through, and `start_run`/`validate_form`'s own submission-time
        merge (below) fills the same fields into a run Configure's own
        form no longer submits at all.

        Args:
            values: One string per `config_form.DEFAULT_RUN_SETTING_
                FIELD_NAMES` entry, plus `max_workers` — Settings' own
                fields, collected by `settings.js`.

        Returns:
            `{"ok": True}` on success; `{"ok": False, "message": ...}`
            if `values`, overlaid on the starter config, does not
            validate — the identical wording any other invalid form
            submission already produces, since this goes through the
            same `starter_form_values`/`form_values_to_payload`/
            `SimulationParams.from_mapping` path. `max_workers` is not
            part of that validation (`_parse_max_workers` tolerates any
            text, including nonsense, by treating it as "auto") — saved
            verbatim alongside the validated subset.
        """
        try:
            merged = starter_form_values(overrides=values)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        subset = {key: merged[key] for key in DEFAULT_RUN_SETTING_FIELD_NAMES}
        subset["max_workers"] = values.get("max_workers", "")
        self._preferences = self._preferences.with_default_run_settings(subset)
        save_preferences(self._preferences_path, self._preferences)
        return {"ok": True}

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
            One dict per run/batch, newest first — see `_home_run_row`
            for the exact shape. Every returned run, including one
            already claimed by a Study, is included: the Run/Study/
            Experiment hierarchy design (`20260917-claude-sonnet-5-run-
            study-experiment-hierarchy-design.md`, `selby/restricted`,
            §6) has the client, not this method, decide which rows
            belong under "Unsorted" versus inside a Study — it already
            has `list_studies`'s own `runDirectories` for exactly that.
        """
        digits = self._significant_digits
        return [
            _home_run_row(run, digits=digits) for run in recent_runs.list_recent_runs()
        ]

    @_log_bridge_call
    def list_studies(self) -> list[dict[str, Any]]:
        """List every Study, oldest first (matching `groups.list_studies`'s own order).

        Returns:
            One dict per Study: `{"studyId", "name", "description",
            "runCount", "createdAt", "runDirectories"}`. `runDirectories`
            is each member run's directory resolved to the same string
            form `list_home_runs`'s own `"directory"` field uses, so the
            client can match a Home row to its Study by plain string
            equality (design doc §6: "which runs are already claimed by
            a Study" is decided client-side from this list, not by a
            second bridge round trip per row).
        """
        results = paths.results_directory()
        return [
            {
                "studyId": study.study_id,
                "name": study.name,
                "description": study.description,
                "runCount": study.run_count,
                "createdAt": study.created_at,
                "runDirectories": [
                    str(directory)
                    for directory in groups.study_run_directories(
                        study, results=results
                    )
                ],
            }
            for study in groups.list_studies(results=results)
        ]

    @_log_bridge_call
    def list_experiments(self) -> list[dict[str, Any]]:
        """List every Experiment, oldest first.

        Returns:
            One dict per Experiment: `{"experimentId", "name",
            "description", "studyCount", "createdAt", "studyIds"}`.
            `studyIds` lets the client find an Experiment's own member
            Studies directly from `list_studies`'s own already-fetched
            result — expanding an Experiment row needs no bridge call of
            its own (design doc §6).
        """
        results = paths.results_directory()
        return [
            {
                "experimentId": experiment.experiment_id,
                "name": experiment.name,
                "description": experiment.description,
                "studyCount": experiment.study_count,
                "createdAt": experiment.created_at,
                "studyIds": list(experiment.study_ids),
            }
            for experiment in groups.list_experiments(results=results)
        ]

    @_log_bridge_call
    def get_study_run_summary(self, study_id: str) -> dict[str, Any]:
        """List one Study's own member Runs, enriched exactly like `list_home_runs`.

        Fetched lazily, only the first time a Study row's own expand
        control is clicked (`webui/screens/open-run.js`'s own
        `expandStudyGroup`) — the same "approach B1" reasoning
        `get_batch_replicate_summary` already established one level
        down: a Study with many member runs should not make every
        *other*, still-collapsed Study or Experiment pay for resolving
        its own runs' config summaries and statistics.

        Returns:
            `{"ok": True, "runs": [...]}`, each entry `_home_run_row`'s
            own shape; `{"ok": False, "message": ...}` if `study_id`
            does not exist. A member directory that no longer exists on
            disk is silently skipped (`fim.persistence.groups.
            study_run_directories`'s own "one missing thing does not
            hide everything else" precedent), never a partial failure.
        """
        try:
            study = groups.get_study(study_id)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        digits = self._significant_digits
        runs = [
            run
            for directory in groups.study_run_directories(study)
            if (run := _recent_run_at_directory(directory)) is not None
        ]
        return {"ok": True, "runs": [_home_run_row(run, digits=digits) for run in runs]}

    @_log_bridge_call
    def create_study(self, name: str, description: str = "") -> dict[str, Any]:
        """Create a new, empty Study.

        Returns:
            `{"ok": True, "studyId": ...}` on success; `{"ok": False,
            "message": ...}` if `name` is blank after stripping.
        """
        try:
            study = groups.create_study(name, description or None)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "studyId": study.study_id}

    @_log_bridge_call
    def create_experiment(self, name: str, description: str = "") -> dict[str, Any]:
        """Create a new, empty Experiment. See `create_study`."""
        try:
            experiment = groups.create_experiment(name, description or None)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "experimentId": experiment.experiment_id}

    @_log_bridge_call
    def add_run_to_study(self, study_id: str, directory: str) -> dict[str, Any]:
        """Add one existing Run directory to a Study; idempotent.

        Returns:
            `{"ok": True}` on success; `{"ok": False, "message": ...}`
            if `study_id` does not exist.
        """
        try:
            groups.add_run_to_study(study_id, Path(directory))
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True}

    @_log_bridge_call
    def add_study_to_experiment(
        self, experiment_id: str, study_id: str
    ) -> dict[str, Any]:
        """Add one existing Study to an Experiment; idempotent.

        Returns:
            `{"ok": True}` on success; `{"ok": False, "message": ...}`
            if either id does not exist.
        """
        try:
            groups.add_study_to_experiment(experiment_id, study_id)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True}

    @_log_bridge_call
    def delete_study(self, study_id: str) -> dict[str, Any]:
        """Delete a Study and every Run it references.

        A deliberate, explicit product decision (`fim.persistence.
        groups.delete_study`'s own docstring) — the GUI never offers the
        `delete_runs=False` escape hatch that function itself still
        supports, matching the confirmed design.

        Returns:
            `{"ok": True, "deletedRunCount": N}` on success; `{"ok":
            False, "message": ...}` if `study_id` does not exist.
        """
        try:
            deleted = groups.delete_study(study_id)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "deletedRunCount": deleted.run_count}

    @_log_bridge_call
    def delete_experiment(self, experiment_id: str) -> dict[str, Any]:
        """Delete an Experiment, its Studies, and their Runs. See `delete_study`.

        Returns:
            `{"ok": True, "deletedStudyCount": N}` on success; `{"ok":
            False, "message": ...}` if `experiment_id` does not exist.
        """
        try:
            deleted = groups.delete_experiment(experiment_id)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "deletedStudyCount": deleted.study_count}

    @_log_bridge_call
    def copy_study(self, study_id: str, name: str) -> dict[str, Any]:
        """Copy a Study's own run list into a new, independent Study.

        Returns:
            `{"ok": True, "studyId": ...}` on success; `{"ok": False,
            "message": ...}` if `study_id` does not exist or `name` is
            blank.
        """
        try:
            copied = groups.copy_study(study_id, name=name)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "studyId": copied.study_id}

    @_log_bridge_call
    def copy_experiment(self, experiment_id: str, name: str) -> dict[str, Any]:
        """Copy an Experiment's own study list into a new, independent Experiment.

        Returns:
            `{"ok": True, "experimentId": ...}` on success; `{"ok":
            False, "message": ...}` if `experiment_id` does not exist or
            `name` is blank.
        """
        try:
            copied = groups.copy_experiment(experiment_id, name=name)
        except ValueError as error:
            return {"ok": False, "message": str(error)}
        return {"ok": True, "experimentId": copied.experiment_id}

    @_log_bridge_call
    def delete_runs(self, directories: list[str]) -> dict[str, Any]:
        """Delete every one of `directories` outright, skipping any already gone.

        The "Select/Delete/Delete all" idiom Home's own bulk-selection
        toolbar needs (a real, reported gap: thousands of Unsorted runs
        could not realistically be deleted one at a time through the
        GUI) — one round trip for the whole selection, rather than one
        per directory, matters once a selection reaches into the
        thousands.

        Returns:
            `{"ok": True, "deletedCount": N}` — `N` is how many of
            `directories` actually existed and were removed; a
            directory already gone (deleted out of band, or a stale
            selection from before a refresh) is not an error.
        """
        deleted_count = 0
        for directory in directories:
            path = Path(directory)
            if path.is_dir():
                shutil.rmtree(path)
                deleted_count += 1
        return {"ok": True, "deletedCount": deleted_count}

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
            "literatureVisuals": literature_visual_payload(
                reanalyzed.state, reanalyzed.params
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
                    "literatureVisuals": {
                        "alleleComposition": frame.allele_composition,
                        "frequencySpectrum": frame.frequency_spectrum,
                    }
                    if frame.allele_composition is not None
                    else None,
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
    def get_batch_animation_frames(self, output_directory: str) -> dict[str, Any]:
        """Sample and ship every pooled animation frame for a completed batch.

        `get_animation_frames`'s own batch counterpart (batch trajectory
        panel design `20260912-claude-sonnet-5-batch-trajectory-panel-
        design.md`, `selby/restricted`) -- the completed batch view's
        own scrubber wires to this exactly like a scalar run's own
        scrubber wires to `get_animation_frames`, both loading their
        whole sampled set up front so play/pause/scrub are pure
        client-side JavaScript afterward, zero further Python calls
        during playback.

        Reads the batch's own top-level manifest for `replicate_run_
        ids` (each replicate's own real id, needed to validate that
        replicate's own trajectory rows -- `fim.gui.animation.pre_
        render_batch_frames`'s own `replicates` argument) rather than
        re-deriving them from directory names, the same "read the
        published, atomic artifacts on disk" source of truth `list_
        recent_runs`/`open_run` already use.

        Args:
            output_directory: The batch's own top-level artifact
                directory (Screen 4's `outputDirectory`).

        Returns:
            `{"ok": True, "demeCount", "frames": [{"generation",
            "panels"}, ...]}` -- identical shape to `get_animation_
            frames`'s own return, so the page's existing scrubber
            machinery (`webui/screens/run-view-controls.js`'s own
            `setScrubberFrames`) needs no batch-specific branch at all.
            `{"ok": False, "message": ...}` if the batch manifest or
            any replicate's own trajectory cannot be read.
        """
        directory = Path(output_directory)
        try:
            manifest = read_batch_manifest(directory / "manifest.json")
            params = manifest.params()
            replicates = [
                (
                    replicate_run_id,
                    batch_runner.replicate_output_directory(
                        directory, manifest.run_id, replicate_run_id
                    )
                    / "trajectory.jsonl",
                )
                for replicate_run_id in manifest.replicate_run_ids
            ]
            frames = pre_render_batch_frames(replicates, params)
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
        renders as a copyright notice, not a "built for" credit: the
        orchid mark and the Selby name are exactly the "Branding
        Materials" `LICENSE.md`'s own branding-exclusion section
        reserves, all rights, separately from the AGPLv3+ code below —
        `copyright_year` matches `LICENSE.md`'s own copyright line and
        needs updating alongside it if that year is ever renewed.
        `branding_note` states that exclusion plainly next to the code
        license, rather than leaving a reader to assume the whole
        distribution (mark included) is as freely licensed as the code
        actually is.

        `license` itself still names only the code's own license
        (AGPLv3+, still exactly accurate for every file `LICENSE.md`'s
        own branding exclusion does not carve out) — `branding_note` is
        the one place that exclusion is disclosed, not a correction to
        this field.
        """
        return {
            "version": fim_version,
            "repository": _REPOSITORY_URL,
            "license": "GNU Affero General Public License v3 or later (AGPLv3+)",
            "branding_note": (
                "The Marie Selby Botanical Gardens name and orchid mark are "
                "proprietary and reserved — not covered by this license."
            ),
            "organization": "Marie Selby Botanical Gardens",
            "organization_url": "https://selby.org/botany/",
            "copyright_year": "2026",
        }


def _attach_finished_run_to_study(study_id: str | None, output_directory: Path) -> None:
    """Add a just-published run/batch to `study_id`, tolerating a since-deleted Study.

    The shared body of `_drain_run_messages`/`_drain_batch_messages`'s
    own identical `"done"`-branch step (`Api.start_run`'s own `study_id`
    argument, Run/Study/Experiment workflow-ergonomics design
    `20260917-claude-sonnet-5-run-study-experiment-workflow-ergonomics.
    md`, `selby/restricted`, item 3) — factored out only to keep each
    caller's own branch count under this project's own configured
    `PLR0912` limit, not because the two calls differ in any way. A
    no-op when `study_id` is `None` (the common, "no study chosen"
    case). A `ValueError` (the Study was deleted by another window, or
    from Home, while this run/batch was still in flight) is logged and
    swallowed, never raised into the caller's own `"done"` handling —
    the run itself already succeeded; this is a best-effort organizing
    step, not part of what "done" reports.
    """
    if study_id is None:
        return
    try:
        groups.add_run_to_study(study_id, output_directory)
    except ValueError as error:
        logger.warning(
            "could not add %s to study %s: %s", output_directory, study_id, error
        )


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
    study_id: str | None = None,
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

    `study_id` (`Api.start_run`'s own already-validated argument) is
    attached to `output_directory` only in the `"done"` branch, below,
    exactly mirroring `fim.cli._record_run_organization`'s own "only
    once the run has actually published successfully" ordering — never
    for a `"cancelled"` or error outcome, which publish nothing to
    attach in the first place.
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
                "literatureVisuals": message[5],
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
                "literatureVisuals": literature_visual_payload(
                    result.final_state, result.params
                ),
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
            _attach_finished_run_to_study(study_id, output_directory)
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
    on_progress: Callable[[dict[str, object]], None] | None = None,
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
    server-side (`_interval_payload`) exactly like `_batch_done_payload`'s
    own `summary` field
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
        on_progress: Test-only hook, called with this tick's own
            `progress_payload` right after it is pushed to the page —
            see `Api.__init__`'s own docstring for what it is for.
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
        name: _interval_payload(interval, digits)
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
        name: _interval_payload(interval, digits)
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
    if on_progress is not None:
        on_progress(progress_payload)


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
    pre-formatted server-side (`_interval_payload`, which wraps
    `format_statistic`, matching every other statistic this bridge ever
    sends the page: the client never reimplements Python's own display
    formatting, for a batch's own results the same as a scalar run's,
    and decides once — there rather than on the page — whether this
    interval has an honest symmetric summary to state at all) — omitted
    entirely (an empty
    `{}`) if `replicate_summary` itself has too few results to define
    an interval from, its own documented `ValueError` case, not
    something this bridge treats as a real error partway through an
    otherwise-successful batch.

    `effectiveAlleles` is `_effective_allele_interval_summary`'s own
    result — `summary`'s own `H_S`/`H_T` intervals, transformed to the
    effective-number-of-alleles scale (design doc §7.7, the same
    transform a scalar run's own `effectiveAlleles` payload already
    applies to a single point) — empty (`{}`) exactly when `summary`
    itself has no `"H_S"` entry, for the identical reason.

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

    `literatureVisuals` is `pooled_literature_visual_payload`'s own
    result, over every published replicate's own `final_state` — a
    real, reported gap, found live: a completed batch's own run view
    simply never sent this key at all, so `run-view-running.js`'s own
    `alleleCompositionCard`/`frequencySpectrumCard` stayed unconditionally
    hidden for any batch, scalar and batch runs otherwise being "the
    same abstraction... no matter how large the set is." The identical
    pooling `panels`, just above, already applies to the scatter panel.
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
        name: _interval_payload(interval, digits)
        for name, interval in raw_summary.items()
    }
    effective_alleles = _effective_allele_interval_summary(raw_summary, digits)
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
    final_states = [result.final_state for result in results]
    return {
        "runId": run_id,
        "outputDirectory": str(output_directory),
        "panels": pooled_scatter_panels(final_states, params.d),
        "replicates": replicates,
        "summary": summary,
        "effectiveAlleles": effective_alleles,
        "demeCount": params.d,
        "p0Statistics": p0_statistics,
        "pooledConvergenceHistories": pooled_convergence_histories_payload,
        "literatureVisuals": pooled_literature_visual_payload(final_states, params),
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
    on_progress: Callable[[dict[str, object]], None] | None = None,
    study_id: str | None = None,
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
    `on_progress` also rides along to `_push_batch_progress`, unchanged
    every tick — see `Api.__init__`'s own docstring for what it is for.

    `study_id` (`Api.start_run`'s own already-validated argument) is
    attached to `output_directory` only once the batch's own `"done"`
    message arrives, exactly mirroring `_drain_run_messages`'s own
    identical ordering one level down.
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
                on_progress,
            )
            continue
        if message[0] == "done":
            payload = _batch_done_payload(
                params, run_id, output_directory, message[1], digits
            )
            _attach_finished_run_to_study(study_id, output_directory)
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


# How long `await_bridge_threads` waits for pywebview's own in-flight
# bridge-call threads to finish delivering before giving up, here in
# production (`_settle_before_close`/`_build_menu`'s own "Quit fim"
# wrapper, both below). Deliberately short, unlike `test/gui/conftest.
# py`'s own separately-tracked, more generous 10.0s: this runs
# synchronously on the platform's own native UI-event thread (confirmed
# live -- design doc `20260912-claude-sonnet-5-shutdown-bridge-thread-
# settle-design.md`, `selby/restricted`), so a real user closing the
# window pays this bound directly as a perceptible pause if it is ever
# actually reached, not merely a test process's own teardown budget. A
# bridge call that is genuinely still running finishes in well under a
# second under ordinary load (`js_bridge_call`'s own round trip is a
# single `Api` method invocation, never a long-running operation run
# synchronously) -- low single digits gives real headroom over that
# without ever reading as "the app froze" in the overwhelmingly common
# case (nothing in flight at all), where this constant is never on the
# critical path to begin with.
_BRIDGE_SETTLE_TIMEOUT_SECONDS: Final[float] = 3.0
_BRIDGE_SETTLE_POLL_INTERVAL_SECONDS: Final[float] = 0.1


def in_flight_bridge_threads(
    candidates: Iterable[threading.Thread] | None = None,
) -> list[threading.Thread]:
    """Return the pywebview bridge-delivery threads that would block shutdown.

    Split out from `await_bridge_threads` so it can be tested against an
    explicit thread list — asserting against live interpreter state
    instead would make a test's own result depend on whichever other
    thread happens to be running in the same process at the moment,
    order-dependence this project treats as a defect rather than as
    flakiness.

    Args:
        candidates: Threads to examine (default: `threading.enumerate()`).

    Returns:
        Every candidate that is a live, non-daemon pywebview bridge thread.
    """
    return [
        thread
        for thread in (
            threading.enumerate() if candidates is None else list(candidates)
        )
        # Identified by target qualified name rather than by thread name:
        # pywebview numbers these threads (`Thread-1162 (_call)`), so the
        # number is meaningless, while the target's qualified name is
        # stable. Matched as a suffix so nesting depth does not matter.
        if not thread.daemon
        and thread.is_alive()
        and getattr(getattr(thread, "_target", None), "__qualname__", "").endswith(
            "js_bridge_call.<locals>._call"
        )
    ]


def await_bridge_threads(timeout: float = _BRIDGE_SETTLE_TIMEOUT_SECONDS) -> None:
    """Wait for pywebview's own in-flight bridge-call threads to finish.

    pywebview delivers each `window.pywebview.api.*` call on a
    *non-daemon* thread (`js_bridge_call.<locals>._call` in `webview.
    util`). A window destroyed — or, on most platforms, closed by the
    user — while one is still in flight leaves that thread running, and
    `threading._shutdown` then waits on it forever: `ISSUES.md`'s own
    "Intermittent hang during GUI shutdown" entry, first captured in CI
    on 2026-09-07 with this exact thread named in the diagnostic dump.

    Originally test-only (`test/gui/conftest.py`), moved here in full —
    design doc `20260912-claude-sonnet-5-shutdown-bridge-thread-settle-
    design.md` (`selby/restricted`) — so production code and the test
    suite share one implementation of the wait rather than two that can
    drift apart. Each individual instance of this leak found before this
    function existed was fixed at its own call site instead, by awaiting
    the call and polling a settle flag (`webui/screens/open-run.js`'s own
    `window.__fimOpenRunRecentRunsLoaded`, for one) — that works but is
    per-call-site and must be repeated for every new screen; this is the
    structural backstop for the ones nobody remembered to fix, or cannot
    practically fix that way at all (a user closing the window is not a
    call site this codebase controls the timing of).

    Deliberately does not fail or raise on timeout. A leaked bridge
    thread is a real defect worth surfacing on its own terms — `test/
    conftest.py`'s own `pytest_unconfigure` diagnostics name the thread
    precisely, when this runs inside the test suite — but this
    function's own job is only to give a genuinely in-flight call a real
    chance to finish before whatever comes next (a window destroy, a
    test's own teardown) proceeds regardless.

    Args:
        timeout: Total seconds to wait for all such threads to finish.

    Returns:
        None. Returns as soon as no bridge thread is alive, or when
        `timeout` elapses, whichever comes first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not in_flight_bridge_threads():
            return
        time.sleep(_BRIDGE_SETTLE_POLL_INTERVAL_SECONDS)


def _settle_before_close() -> None:
    """`window.events.closing` subscriber: let an in-flight bridge call finish.

    Registered once per window (`create_window`, below) rather than
    per-close: `events.closing` fires from the native close-request
    handler itself, synchronously, on every platform this project ships
    (`Event(should_lock=True)`, confirmed live against a real window —
    design doc `20260912-claude-sonnet-5-shutdown-bridge-thread-settle-
    design.md`, `selby/restricted`) — so running `await_bridge_threads`
    here means the platform's own native window teardown does not begin
    until any in-flight bridge call has had a real, bounded chance to
    finish delivering first.

    Takes no arguments deliberately, even though `pywebview`'s own
    `Event.set()` will pass the window through to a subscriber whose own
    signature names a `window` parameter (`webview/event.py`'s own
    introspection): `await_bridge_threads` needs no window-specific
    information — it enumerates every live bridge thread process-wide —
    and an unused parameter here would be dead weight this project's own
    lint gate (`ARG001`) already flags production code for carrying.

    On macOS specifically, `window.destroy()` does not fire this event
    at all (confirmed live — traced to `NSWindow.close` bypassing the
    `windowShouldClose_` delegate method entirely) — `_wrap_destroy_to_
    settle_first` (`create_window`, below) makes every direct `.destroy()`
    call settle on its own instead, independent of whether a given
    platform's own `destroy()` happens to reach this subscriber.

    Args:
        None

    Returns:
        None
    """
    await_bridge_threads()


def _wrap_destroy_to_settle_first(window: webview.Window) -> None:
    """Make `window.destroy()` itself settle in-flight bridge calls first.

    `events.closing` (registered on `window` just before this is called,
    `create_window`) already covers a platform-triggered close, but does
    not fire at all for a direct `window.destroy()` call on macOS
    (`_settle_before_close`'s own docstring). Design doc `20260912-
    claude-sonnet-5-shutdown-bridge-thread-settle-design.md` (`selby/
    restricted`) originally scoped that remaining gap to exactly one
    call site — `_build_menu`'s own "Quit fim" action — and wrapped that
    one closure by hand. That scoping was wrong, found only after the
    fix landed and the shutdown flake kept recurring anyway: `test/gui/`
    alone has upward of ninety direct `.destroy()` calls across some
    twenty test files, nearly all of them a raw `create_window(...)` in
    the file's own `_drive()`-style teardown, `window.destroy()` called
    directly with no settle step of its own — the identical unprotected
    shape "Quit fim" was, just multiplied ninety-fold instead of fixed
    once. Retrofitting `await_bridge_threads()` into each of those call
    sites individually would be exactly the "same fix repeated at every
    call site, drifting apart" risk this project already rejected once
    for `in_flight_bridge_threads`/`await_bridge_threads` themselves
    (that design doc's own decision 1).

    Rebinding the *instance's* own `destroy` attribute, not the class
    method, means every caller that already holds a reference to this
    exact `window` object — a test's own `finally: window.destroy()`,
    `_build_menu`'s own "Quit fim" action (now a bare `window.destroy`
    reference again, no closure of its own needed), or pywebview's own
    internal shutdown sweep over `webview.windows` — settles first with
    no change of its own, closing off the whole class of "forgot to
    settle before destroy" bugs structurally rather than requiring every
    call site, present or future, to remember it independently.

    Args:
        window: The window whose own `destroy` method to wrap in place.

    Returns:
        None. `window.destroy` is reassigned as a side effect.
    """
    original_destroy = window.destroy

    def destroy_after_settling() -> None:
        await_bridge_threads()
        original_destroy()

    # Deliberate: pywebview's own stubs type `destroy` as a plain bound
    # method, not a mutable attribute, but `Window` itself has no
    # `__slots__` (confirmed live) and Python allows shadowing a bound
    # method with an instance attribute freely; this is exactly that,
    # not a real type error.
    window.destroy = destroy_after_settling  # type: ignore[method-assign]


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
    # Settles an in-flight bridge call before the platform's own native
    # close proceeds — real, synchronous, and cross-platform (`_settle_
    # before_close`'s own docstring), but not, on its own, a close from
    # every reachable path: `window.destroy()` does not fire this event
    # at all on macOS (confirmed live, same docstring), so a direct
    # `.destroy()` call anywhere still bypasses it.
    created.events.closing += _settle_before_close
    # `_wrap_destroy_to_settle_first`'s own docstring has the full
    # reasoning: this closes that remaining gap structurally, for every
    # direct `.destroy()` caller (there turned out to be many more of
    # them than the one "Quit fim" menu action design doc `20260912-
    # claude-sonnet-5-shutdown-bridge-thread-settle-design.md` originally
    # scoped this to), rather than requiring each one to remember its own
    # settle call.
    _wrap_destroy_to_settle_first(created)
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
    that already exists, not new logic. Quit alone needs no JS round
    trip — `window.destroy()` is a window-level call, not app state —
    and needs no settle wrapper of its own any more either:
    `create_window`'s own `_wrap_destroy_to_settle_first` already rebinds
    this exact `window`'s own `destroy` attribute to settle first, so the
    bare method reference below already does.
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
