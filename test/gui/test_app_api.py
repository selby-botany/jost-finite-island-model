"""Unit tests for `fim.gui.app.Api`'s window-free bridge methods.

No display, no Tk import, no `gui` marker: `get_starter_form`,
`validate_form`, and `get_default_max_workers` never touch
`webview.windows[0]` — they call straight into `fim.gui.config_form`/
`fim.gui.batch_runner`, so they are exercised here as plain Python calls,
far cheaper and more direct than driving them through a real window and
`evaluate_js`. `load_yaml`/`save_yaml` do need a real window (a real file
dialog) and are covered instead in `test/gui/test_app.py`, marked `gui`.
"""

from __future__ import annotations

import json
import queue
import time as time_module
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from webview.menu import Menu, MenuAction, MenuSeparator

from fim import __version__ as fim_version
from fim import cli, update
from fim import paths as paths_module
from fim.engine import (
    RunResult,
    deterministic_run_id,
    replicate_summary,
    report_for_state,
)
from fim.engine import fim as engine_fim
from fim.gui import app as app_module
from fim.gui import batch_runner
from fim.gui import recent_runs as recent_runs_module
from fim.gui import runner as runner_module
from fim.gui.app import Api, _save_dialog_path, format_statistic
from fim.gui.batch_runner import default_max_workers
from fim.gui.config_form import starter_form_values
from fim.gui.recent_runs import RecentRun
from fim.gui.store import LiveProgressStore
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import read_manifest
from fim.viz.scatter import frequency_points, pooled_scatter_panels


def test_get_starter_form_matches_config_form_directly() -> None:
    """The bridge method adds no logic of its own beyond `starter_form_values`."""
    assert Api().get_starter_form() == starter_form_values()


def test_get_default_max_workers_matches_batch_runner_directly() -> None:
    """The Batch tab's default is `batch_runner.default_max_workers`, not invented."""
    assert Api().get_default_max_workers() == default_max_workers()


def test_validate_form_accepts_the_starter_values() -> None:
    """The starter form is valid on its own — no field left in a rejecting state."""
    result = Api().validate_form(starter_form_values())

    assert result == {"ok": True}


def test_validate_form_rejects_and_locates_an_invalid_population_field() -> None:
    """An invalid `N` is rejected, named, and routed to the Population tab."""
    values = dict(starter_form_values())
    values["N"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "N"
    assert result["tab"] == "population"
    assert "N must be an integer" in result["message"]


def test_validate_form_rejects_and_locates_an_invalid_migration_rate() -> None:
    """An invalid scalar `m` rate routes to Migration via the composite selector."""
    values = dict(starter_form_values())
    values["m_mode"] = "scalar"
    values["m_rate"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["tab"] == "migration"


def test_validate_form_rejects_an_invalid_choice_field() -> None:
    """A "choice"-kind field's own error is located exactly like an "int" field's."""
    values = dict(starter_form_values())
    values["deme_weighting"] = "not-a-real-choice"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "deme_weighting"
    assert result["tab"] == "population"
    assert "deme_weighting" in result["message"]


def test_format_statistic_matches_the_cli_own_format_optional() -> None:
    """`format_statistic` is a direct parallel of `cli._format_optional`."""
    assert format_statistic(None) == "undefined"
    assert format_statistic(0.123456789) == "0.123457"
    assert format_statistic(1.0) == "1"


def test_format_statistic_honors_an_explicit_digits_count() -> None:
    """`digits` overrides the CLI-parity default the test above pins.

    The GUI's own configured precision (`Api._significant_digits`,
    `_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS`) is always passed explicitly
    by a real caller — this is the same rounding a fresh `Api()`
    actually produces for every displayed statistic today.
    """
    assert format_statistic(0.123456789, 3) == "0.123"
    assert format_statistic(None, 3) == "undefined"


def test_api_starts_with_the_default_significant_digits() -> None:
    """A fresh `Api()` starts at the GUI's own default, not the CLI's own six."""
    api = Api()

    assert (
        api.get_significant_digits() == app_module._DEFAULT_DISPLAY_SIGNIFICANT_DIGITS
    )


def test_set_significant_digits_changes_what_get_significant_digits_returns() -> None:
    """A valid `digits` value is accepted and immediately reflected back."""
    api = Api()

    result = api.set_significant_digits(5)

    assert result == {"ok": True, "digits": 5}
    assert api.get_significant_digits() == 5


@pytest.mark.parametrize("digits", [0, -1, 18, 100])
def test_set_significant_digits_rejects_values_outside_the_valid_range(
    digits: int,
) -> None:
    """Outside the valid digit range, `set_significant_digits` leaves it unchanged.

    `_MAX_SIGNIFICANT_DIGITS` (17) is not an arbitrary round number: a
    double-precision float carries roughly that many significant
    decimal digits, so anything past it would print noise a real
    `FinalReport` statistic never actually carries.
    """
    api = Api()

    result = api.set_significant_digits(digits)

    assert result["ok"] is False
    assert "message" in result
    assert (
        api.get_significant_digits() == app_module._DEFAULT_DISPLAY_SIGNIFICANT_DIGITS
    )


def test_api_starts_with_no_live_deme_pair_selected() -> None:
    """A fresh `Api()` starts with the Progress screen's own selector cleared."""
    api = Api()

    assert api.get_live_deme_pair() is None


def test_set_live_deme_pair_changes_what_get_live_deme_pair_returns() -> None:
    """A selected pair is reflected back immediately."""
    api = Api()

    result = api.set_live_deme_pair(2, 4)

    assert result == {"ok": True}
    assert api.get_live_deme_pair() == (2, 4)


def test_set_live_deme_pair_none_clears_the_selection() -> None:
    """ "Show overview" (`None, None`) clears back to no selection."""
    api = Api()
    api.set_live_deme_pair(2, 4)

    result = api.set_live_deme_pair(None, None)

    assert result == {"ok": True}
    assert api.get_live_deme_pair() is None


def test_open_output_folder_calls_the_injected_opener(tmp_path: Path) -> None:
    """`open_output_folder` reaches the injected opener, never a real one.

    Mirrors the Tk-era `ResultsScreen`'s own `open_folder` injection
    point (`test_results_screen_open_folder_invokes_the_injected_
    opener`), carried into `Api.__init__` unchanged in spirit.
    """
    opened: list[Path] = []
    api = Api(open_folder=opened.append)

    api.open_output_folder(str(tmp_path))

    assert opened == [tmp_path]


def test_resolve_available_output_directory_returns_a_free_path_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No collision, no retry: the first candidate is returned as-is."""
    free = tmp_path / "run-free"
    monkeypatch.setattr(paths_module, "default_output_directory", lambda: free)
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == free
    assert sleeps == []


def test_resolve_available_output_directory_retries_past_a_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-second collision (`paths.default_output_directory`'s own
    real, timestamp-based naming — see `_START_RUN_COLLISION_*`'s own
    comment) is retried until a free path is returned, not surfaced as
    a failure on the very first attempt.

    Direct regression coverage for the real, repeatedly-reproduced
    failure this fixed: several of this project's own `gui`-marked
    tests, each starting a real run within the same wall-clock second,
    previously received `{"ok": False, "message": "output directory
    already exists"}` for what was, from each test's perspective, an
    entirely fresh run — see `test/gui/test_running_screen.py`'s own
    module docstring for the full investigation.
    """
    colliding = tmp_path / "run-colliding"
    colliding.mkdir()
    free = tmp_path / "run-free"
    candidates = iter([colliding, colliding, free])
    monkeypatch.setattr(
        paths_module, "default_output_directory", lambda: next(candidates)
    )
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == free
    assert sleeps == [app_module._START_RUN_COLLISION_RETRY_INTERVAL_SECONDS] * 2


def test_resolve_available_output_directory_gives_up_after_the_max_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collision that never clears is returned anyway once the wait budget expires.

    `Api.start_run` is the one that turns a still-colliding directory
    into a real `{"ok": False, ...}` (via `runner.start_run`'s own
    `FileExistsError`) — this function's own job ends at "stop
    retrying," not at deciding what a persistent collision means.
    """
    colliding = tmp_path / "run-colliding"
    colliding.mkdir()
    monkeypatch.setattr(paths_module, "default_output_directory", lambda: colliding)
    sleeps: list[float] = []
    monkeypatch.setattr(time_module, "sleep", sleeps.append)

    result = app_module._resolve_available_output_directory()

    assert result == colliding
    expected_retries = round(
        app_module._START_RUN_COLLISION_MAX_WAIT_SECONDS
        / app_module._START_RUN_COLLISION_RETRY_INTERVAL_SECONDS
    )
    assert len(sleeps) == expected_retries


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4", 4),
        ("1", 1),
        ("", None),
        ("not-a-number", None),
        ("0", None),
        ("-1", None),
        ("3.5", None),
    ],
)
def test_parse_max_workers(raw: str, expected: int | None) -> None:
    """Only a positive integer parses; everything else falls back to the default.

    The Batch tab's own `max_workers` field has no validation UI of its
    own (it is not a `SimulationParams` field), so a blank or corrupted
    value must fail *silently* to the default, not block the run —
    `None` is that "use `batch_runner.default_max_workers()`" signal.
    """
    assert app_module._parse_max_workers(raw) == expected


@pytest.fixture
def batch_params(tiny_params: SimulationParams) -> SimulationParams:
    """A small, fast three-replicate batch configuration.

    Mirrors `test/gui/test_batch_runner.py`'s own identically-named
    fixture — a direct parallel, not a shared import, per this
    project's established per-test-file fixture convention.
    """
    return replace(tiny_params, n_replicates=3)


@pytest.fixture
def batch_results(batch_params: SimulationParams) -> tuple[RunResult, ...]:
    """Three real, completed replicates — the shape `_batch_done_payload` consumes.

    A real `fim.engine.fim(...)` batch call, not hand-built `RunResult`
    fakes — the same "construct the real thing" precedent `test/gui/
    test_batch_runner.py`'s own fixtures already established for this
    exact scale of batch.

    Passes `run_id=deterministic_run_id(batch_params)` explicitly,
    matching `batch_runner._batch_worker`'s own real call exactly
    (`batch_runner.py`'s own `run_id = deterministic_run_id(params)`
    then `fim(..., run_id=run_id)`): only then does each replicate's
    own `run_id` actually come out as `f"{batch_run_id}-r{index:03}"`
    — `fim()`'s own `run_id is None` branch gives each replicate an
    independent `deterministic_run_id`, unrelated to any batch run_id.
    """
    run_id = deterministic_run_id(batch_params)
    results = engine_fim(
        batch_params.N,
        batch_params.m,
        batch_params.mu,
        batch_params.d,
        params=batch_params,
        run_id=run_id,
    )
    assert isinstance(results, tuple)
    return results


def test_batch_done_payload_carries_one_replicate_row_per_result(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`replicates` rows per result; formatted stats; no `index`/`runId` fields.

    `replicateId` (`result.run_id` verbatim) is the one deliberate
    exception to that "no per-replicate identifier" policy, added after
    this test was first written: multiple replicates legitimately
    converging at the same generation are indistinguishable in the
    table without it (a real, reported gap, not the same "confusing
    run-numbering" concern `index`/`runId` were originally removed
    for -- `webui/screens/run-view-completed.js`'s own `replicateLabel`
    only ever surfaces a compact `#NNN` badge derived from it, never
    the raw id string, and it answers "which of this batch's own
    replicates is this row" rather than any global run-ordering claim).
    """
    # The real batch's own deterministic run_id, not an arbitrary
    # literal: `_batch_done_payload` uses this same value both as the
    # payload's own `"runId"` label and, via `replicate_output_
    # directory`, to recover each replicate's own ordinal from its
    # `run_id` prefix — only the real value both purposes agree with
    # what `batch_results` was actually run under.
    run_id = deterministic_run_id(batch_params)

    payload = app_module._batch_done_payload(
        batch_params, run_id, tmp_path, batch_results
    )

    assert payload["runId"] == run_id
    assert payload["outputDirectory"] == str(tmp_path)
    replicates = payload["replicates"]
    assert isinstance(replicates, list)
    assert len(replicates) == len(batch_results)
    # `index` and `runId` are no longer sent to the client (internal state
    # that added nothing user-facing and raised confusing questions about
    # run numbering).
    for row in replicates:
        assert "index" not in row
        assert "runId" not in row
    for row, result in zip(replicates, batch_results, strict=True):
        assert row["generation"] == result.report["generation"]
        assert row["statistics"]["D"] == format_statistic(result.report["D"])
        expected_directory = batch_runner.replicate_output_directory(
            tmp_path, run_id, result.run_id
        )
        assert row["trajectoryPath"] == str(expected_directory / "trajectory.jsonl")
        assert row["replicateId"] == result.run_id


def test_batch_done_payload_carries_p0_statistics(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`p0Statistics` carries the six seeded generation-0 statistics, formatted."""
    run_id = deterministic_run_id(batch_params)

    payload = app_module._batch_done_payload(
        batch_params, run_id, tmp_path, batch_results
    )

    p0 = payload["p0Statistics"]
    assert isinstance(p0, dict)
    assert set(p0) == set(app_module._RESULT_STATISTIC_NAMES)
    # Each value is a pre-formatted string (format_statistic), not a float.
    for name in app_module._RESULT_STATISTIC_NAMES:
        assert isinstance(p0[name], str)


def test_batch_done_payload_summary_matches_replicate_summary(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`summary`'s own numbers are `replicate_summary`'s, formatted server-side.

    The same "the client never reimplements Python's own display
    formatting" rule every other statistic this bridge sends the page
    already follows.
    """
    run_id = deterministic_run_id(batch_params)

    payload = app_module._batch_done_payload(
        batch_params, run_id, tmp_path, batch_results
    )

    expected = replicate_summary(batch_results)
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert set(summary) == set(expected)
    for name, interval in expected.items():
        assert summary[name]["mean"] == format_statistic(interval["mean"])
        assert summary[name]["sampleCount"] == interval["sample_count"]


def test_batch_done_payload_pools_every_replicate_final_state(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`panels` is the pooled scatter over every replicate's own final state."""
    run_id = deterministic_run_id(batch_params)

    payload = app_module._batch_done_payload(
        batch_params, run_id, tmp_path, batch_results
    )

    expected = pooled_scatter_panels(
        [result.final_state for result in batch_results], batch_params.d
    )
    assert payload["panels"] == expected


def test_batch_done_payload_honors_an_explicit_digits_count(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`digits`, `_start_batch_run`'s own snapshot of `Api._significant_digits`,
    reaches every formatted statistic in both `replicates` and `summary` —
    not only the bare-call default the tests above exercise.
    """
    run_id = deterministic_run_id(batch_params)

    payload = app_module._batch_done_payload(
        batch_params, run_id, tmp_path, batch_results, 2
    )

    replicates = payload["replicates"]
    assert isinstance(replicates, list)
    for row, result in zip(replicates, batch_results, strict=True):
        assert row["statistics"]["D"] == format_statistic(result.report["D"], 2)
    expected_summary = replicate_summary(batch_results)
    summary = payload["summary"]
    assert isinstance(summary, dict)
    for name, interval in expected_summary.items():
        assert summary[name]["mean"] == format_statistic(interval["mean"], 2)


class _FakeWindow:
    """A `webview.Window` stand-in exposing only `evaluate_js`.

    `_push_batch_progress`'s only "window" dependency is calling
    `.evaluate_js(script)` — this lets its own real file-reading logic
    (sidecar discovery, `read_live_state`, `pooled_scatter_panels`) be
    exercised directly, against real files under `tmp_path`, with no
    real `pywebview` window and no `gui` marker needed at all.
    """

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


def _one_call_payload(window: _FakeWindow, function_name: str) -> dict[str, object]:
    """Extract and parse the one JSON argument `evaluate_js` was called with."""
    assert len(window.scripts) == 1
    script = window.scripts[0]
    prefix = f"fim.{function_name}("
    assert script.startswith(prefix)
    assert script.endswith(")")
    result: dict[str, object] = json.loads(script[len(prefix) : -1])
    return result


def test_push_batch_progress_pushes_a_pooled_scatter_from_real_sidecars(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """Reads whichever replicates have reported so far; skips the rest silently.

    Writes one replicate's own real `.progress` sidecar and
    `trajectory.jsonl`, in exactly the file layout `LiveProgressStore`
    itself produces (`fim.gui.batch_runner._replicate_store_factory`'s
    own construction, mirrored here directly) — the second replicate's
    directory is never created at all, the "has not started yet" case
    `_push_batch_progress`'s own docstring names as normal, not an
    error.
    """
    params = replace(tiny_params, n_replicates=2)
    run_id = "batch-run-1"
    replicate_run_id = f"{run_id}-r001"
    directory = batch_runner.replicate_output_directory(
        tmp_path, run_id, replicate_run_id
    )
    directory.mkdir(parents=True)
    store = LiveProgressStore(
        JSONLTrajectoryStore(directory / "trajectory.jsonl"),
        progress_path=directory / ".progress",
        cancel_path=tmp_path / "cancel",
    )
    state = ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
        ),
    )
    store.write_generation(replicate_run_id, 0, state.to_rows(replicate_run_id))
    window = _FakeWindow()

    app_module._push_batch_progress(window, params, run_id, tmp_path)

    payload = _one_call_payload(window, "onBatchProgress")
    assert payload["replicateCount"] == 2
    assert payload["reportedReplicateCount"] == 1
    panels = payload["panels"]
    assert isinstance(panels, list)
    assert len(panels) == 1
    points = panels[0]["points"]
    assert isinstance(points, list)
    assert len(points) == 2
    # `reports_summary` needs at least two reporting replicates to
    # define any interval — one reporting replicate summarizes to
    # nothing yet, not an error (see `reports_summary`'s own docstring).
    assert payload["statistics"] == {}


def test_push_batch_progress_reports_nothing_before_any_replicate_starts(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """No sidecar anywhere yet still pushes a well-formed, empty progress payload."""
    params = replace(tiny_params, n_replicates=2)
    window = _FakeWindow()

    app_module._push_batch_progress(window, params, "batch-run-1", tmp_path)

    payload = _one_call_payload(window, "onBatchProgress")
    assert payload["replicateCount"] == 2
    assert payload["reportedReplicateCount"] == 0
    assert payload["panels"] == []


def test_push_batch_progress_includes_a_live_deme_pair_panel_when_selected(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """The Progress screen's own live selector reaches a real batch's own push.

    Same real sidecar/trajectory setup as `test_push_batch_progress_
    pushes_a_pooled_scatter_from_real_sidecars` above, plus a
    `live_deme_pair` that always returns one pair — the same bound-
    method shape `Api.get_live_deme_pair` gives a real background
    thread, here a plain `lambda` standing in for it.
    """
    params = replace(tiny_params, n_replicates=2)
    run_id = "batch-run-1"
    replicate_run_id = f"{run_id}-r001"
    directory = batch_runner.replicate_output_directory(
        tmp_path, run_id, replicate_run_id
    )
    directory.mkdir(parents=True)
    store = LiveProgressStore(
        JSONLTrajectoryStore(directory / "trajectory.jsonl"),
        progress_path=directory / ".progress",
        cancel_path=tmp_path / "cancel",
    )
    state = ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
        ),
    )
    store.write_generation(replicate_run_id, 0, state.to_rows(replicate_run_id))
    window = _FakeWindow()

    app_module._push_batch_progress(window, params, run_id, tmp_path, lambda: (1, 2))

    payload = _one_call_payload(window, "onBatchProgress")
    pair_panel = payload["pairPanel"]
    assert isinstance(pair_panel, dict)
    assert pair_panel["x_label"] == "Deme 1"
    assert pair_panel["y_label"] == "Deme 2"


def test_drain_run_messages_includes_a_live_deme_pair_panel_when_selected() -> None:
    """A `"progress"` push includes `pairPanel` once a live pair is selected.

    No test calls `_drain_run_messages` directly elsewhere in this file
    (its own docstring already notes why: production only ever reaches
    it via `_start_scalar_run`'s own thread) — this is the first, added
    specifically to prove the new `live_deme_pair` parameter without
    needing a real background thread or a `gui` marker.
    """
    state = ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
            ({AlleleId(0): 0.1, AlleleId(1): 0.9},),
        ),
    )
    points = frequency_points(state)
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.01,
        d=3,
        seed=20260814,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
    )
    report = report_for_state(
        state, params, run_id="run-1", converged=False, reason="in progress"
    )
    message_queue: queue.Queue[runner_module.RunMessage] = queue.Queue()
    message_queue.put(("progress", 3, [], points, report))
    # A terminal message right behind it: `_drain_run_messages`'s own
    # `while True` loop only returns once it sees one, and this test
    # cares only about the "progress" push's own first `evaluate_js`
    # call, not about how the (here, arbitrary) run actually ends.
    message_queue.put(("cancelled", 3))
    window = _FakeWindow()

    app_module._drain_run_messages(
        window,
        message_queue,
        max_generations=10,
        deme_count=3,
        output_directory=Path("/unused"),
        live_deme_pair=lambda: (1, 3),
    )

    progress_payload = json.loads(window.scripts[0][len("fim.onRunProgress(") : -1])
    pair_panel = progress_payload["pairPanel"]
    assert pair_panel["x_label"] == "Deme 1"
    assert pair_panel["y_label"] == "Deme 3"


def _write_run(tmp_path: Path, **overrides: object) -> Path:
    """Write a small config with several generations and return its output directory.

    Mirrors `test/gui/test_animation.py`'s own identically-named
    helper — a direct parallel, not a shared import, per this
    project's established per-test-file fixture convention.
    """
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 8,
        "convergence_tolerance": 1e-6,
        "max_generations": 12,
    }
    config.update(overrides)
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


@pytest.mark.parametrize(
    ("mode", "text", "expected"),
    [
        ("final", "", None),
        ("final", "5", None),
        ("choose", "7", 7),
        ("choose", " 3 ", 3),
    ],
)
def test_parse_generation_accepts_valid_input(
    mode: str, text: str, expected: int | None
) -> None:
    """ "final" always means `None`; "choose" parses its own entry."""
    assert app_module._parse_generation(mode, text) == expected


@pytest.mark.parametrize("text", ["", "not-a-number", "3.5"])
def test_parse_generation_rejects_invalid_choose_input(text: str) -> None:
    """ "choose" with an empty or non-integer entry is a real validation error."""
    with pytest.raises(ValueError, match="generation must be an integer"):
        app_module._parse_generation("choose", text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("   ", ()),
        ("1", (1.0,)),
        ("1 2 3", (1.0, 2.0, 3.0)),
        ("1, 2,3", (1.0, 2.0, 3.0)),
    ],
)
def test_parse_differentiation_orders_accepts_valid_input(
    text: str, expected: tuple[float, ...]
) -> None:
    assert app_module._parse_differentiation_orders(text) == expected


def test_parse_differentiation_orders_rejects_a_non_numeric_token() -> None:
    with pytest.raises(ValueError, match="space/comma-separated numbers"):
        app_module._parse_differentiation_orders("1 not-a-number 3")


def test_list_recent_runs_reshapes_every_recent_run_into_a_json_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge method adds little logic beyond calling `recent_runs.
    list_recent_runs` and joining each non-batch row's own trajectory
    path (in Python, not the page — a client-side string join would
    silently mix path separators on Windows).

    Injected via monkeypatch, not a real `results/` scan — `fim.gui.
    recent_runs`'s own test suite already covers the real scan directly.
    """
    canned = [
        RecentRun(
            run_id="run-1",
            directory=tmp_path / "run-1",
            ended_at="2026-08-22T00:00:00Z",
            label="statistic converged",
            is_batch=False,
        ),
        RecentRun(
            run_id="run-2",
            directory=tmp_path / "run-2",
            ended_at="2026-08-21T00:00:00Z",
            label="batch (14/20)",
            is_batch=True,
        ),
    ]
    monkeypatch.setattr(recent_runs_module, "list_recent_runs", lambda: canned)

    result = Api().list_recent_runs()

    assert result == [
        {
            "runId": "run-1",
            "directory": str(tmp_path / "run-1"),
            "trajectoryPath": str(tmp_path / "run-1" / "trajectory.jsonl"),
            "endedAt": "2026-08-22T00:00:00Z",
            "label": "statistic converged",
            "isBatch": False,
        },
        {
            "runId": "run-2",
            "directory": str(tmp_path / "run-2"),
            "trajectoryPath": None,
            "endedAt": "2026-08-21T00:00:00Z",
            "label": "batch (14/20)",
            "isBatch": True,
        },
    ]


def test_open_run_reanalyzes_the_final_generation_by_default(tmp_path: Path) -> None:
    """A bare "final" open reproduces the run's own terminal report."""
    output = _write_run(tmp_path)
    manifest = read_manifest(output / "manifest.json")

    result = Api().open_run({"trajectoryPath": str(output / "trajectory.jsonl")})

    assert result["ok"] is True
    assert result["runId"] == manifest.run_id
    assert result["report"]["generation"] == manifest.generation
    assert result["report"]["reason"] == manifest.stop_reason
    assert result["outputDirectory"] == str(output)
    assert result["generationCount"] == manifest.generation_count
    assert set(result["statistics"]) == {"D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"}
    assert isinstance(result["panels"], list)


def test_open_run_choose_reanalyzes_an_earlier_generation_as_re_analysis(
    tmp_path: Path,
) -> None:
    """A non-final generation reports "re-analysis", not the run's own reason."""
    output = _write_run(tmp_path)
    manifest = read_manifest(output / "manifest.json")
    earlier = max(manifest.generation - 1, 0)

    result = Api().open_run(
        {
            "trajectoryPath": str(output / "trajectory.jsonl"),
            "generationMode": "choose",
            "generation": str(earlier),
        }
    )

    assert result["ok"] is True
    assert result["report"]["generation"] == earlier
    assert result["report"]["reason"] == "re-analysis"
    assert result["report"]["converged"] is False


def test_open_run_runs_a_differentiation_q_sweep_when_requested(
    tmp_path: Path,
) -> None:
    output = _write_run(tmp_path)

    result = Api().open_run(
        {
            "trajectoryPath": str(output / "trajectory.jsonl"),
            "differentiationOrders": "0.5, 2",
        }
    )

    assert result["ok"] is True
    assert set(result["report"]["Differentiation_q"]) == {"0.5", "2.0"}


def test_open_run_rejects_no_trajectory_selected() -> None:
    assert Api().open_run({}) == {"ok": False, "message": "no trajectory selected"}


def test_open_run_rejects_an_invalid_generation_entry(tmp_path: Path) -> None:
    output = _write_run(tmp_path)

    result = Api().open_run(
        {
            "trajectoryPath": str(output / "trajectory.jsonl"),
            "generationMode": "choose",
            "generation": "not-a-number",
        }
    )

    assert result == {"ok": False, "message": "generation must be an integer"}


def test_open_run_reports_a_missing_trajectory_without_raising(tmp_path: Path) -> None:
    """A real regression: `reanalyze_trajectory`'s own manifest read raises
    `FileNotFoundError` (an `OSError`), not the `ValueError` this bridge
    method's exception handling originally caught alone."""
    result = Api().open_run(
        {"trajectoryPath": str(tmp_path / "never-written" / "trajectory.jsonl")}
    )

    assert result["ok"] is False
    assert "message" in result


def test_get_animation_frames_ships_client_ready_panels(tmp_path: Path) -> None:
    """Every sampled frame's points already arrive as `scatter_panels`-shaped panels."""
    output = _write_run(tmp_path)

    result = Api().get_animation_frames(str(output))

    assert result["ok"] is True
    assert result["demeCount"] == 2
    frames = result["frames"]
    assert isinstance(frames, list)
    assert len(frames) >= 2
    assert frames[0]["generation"] == 0
    for frame in frames:
        panels = frame["panels"]
        assert isinstance(panels, list)
        assert len(panels) == 1
        assert panels[0]["x_label"] == "Deme 1"


def test_get_animation_frames_reports_a_missing_run_without_raising(
    tmp_path: Path,
) -> None:
    result = Api().get_animation_frames(str(tmp_path / "never-written"))

    assert result["ok"] is False
    assert "message" in result


def test_get_animation_deme_pair_frames_names_the_pair_in_every_frame(
    tmp_path: Path,
) -> None:
    """The Animation screen's own pair view names its axes the same way, per frame.

    `d=4` past `scatter.PAIRWISE_MAX_DEMES`'s own small-`d` case would
    not matter here — this bridge method exists specifically for a
    caller-chosen pair regardless of `d` — but matches `test_get_deme_
    pair_panel_names_the_requested_pair`'s own choice for a direct,
    easy comparison between the two.
    """
    output = _write_run(tmp_path, d=4)

    default = Api().get_animation_frames(str(output))
    result = Api().get_animation_deme_pair_frames(
        str(output), first_deme=2, second_deme=4
    )

    assert result["ok"] is True
    frames = result["frames"]
    assert isinstance(frames, list)
    # `pre_render_frames` samples identically both times (same
    # trajectory, same `max_frames` default) — the two calls' own
    # generation lists agree exactly, the invariant `animation.js`'s
    # own frame-set swap relies on to keep `currentIndex` meaningful
    # across a "Show pair"/"Show overview" switch.
    assert [frame["generation"] for frame in frames] == [
        frame["generation"] for frame in default["frames"]
    ]
    for frame in frames:
        panel = frame["panel"]
        assert panel["x_label"] == "Deme 2"
        assert panel["y_label"] == "Deme 4"
        assert isinstance(panel["points"], list)


def test_get_animation_deme_pair_frames_rejects_an_out_of_range_deme(
    tmp_path: Path,
) -> None:
    output = _write_run(tmp_path, d=4)

    result = Api().get_animation_deme_pair_frames(
        str(output), first_deme=1, second_deme=5
    )

    assert result["ok"] is False
    assert "message" in result


def test_get_animation_deme_pair_frames_reports_a_missing_run_without_raising(
    tmp_path: Path,
) -> None:
    result = Api().get_animation_deme_pair_frames(
        str(tmp_path / "never-written"), first_deme=1, second_deme=2
    )

    assert result["ok"] is False
    assert "message" in result


def test_get_deme_pair_panel_names_the_requested_pair(tmp_path: Path) -> None:
    """The Screen 3 on-demand pair view names its axes by 1-based deme number."""
    output = _write_run(tmp_path, d=4)

    result = Api().get_deme_pair_panel(str(output), first_deme=2, second_deme=4)

    assert result["ok"] is True
    panel = result["panel"]
    assert panel["x_label"] == "Deme 2"
    assert panel["y_label"] == "Deme 4"
    assert isinstance(panel["points"], list)


def test_get_deme_pair_panel_rejects_an_out_of_range_deme(tmp_path: Path) -> None:
    output = _write_run(tmp_path, d=4)

    result = Api().get_deme_pair_panel(str(output), first_deme=1, second_deme=5)

    assert result["ok"] is False
    assert "message" in result


def test_get_deme_pair_panel_reports_a_missing_run_without_raising(
    tmp_path: Path,
) -> None:
    result = Api().get_deme_pair_panel(
        str(tmp_path / "never-written"), first_deme=1, second_deme=2
    )

    assert result["ok"] is False
    assert "message" in result


def test_get_batch_deme_pair_panel_pools_every_replicate(tmp_path: Path) -> None:
    """The large-`d` Screen 4 on-demand pair view pools every published replicate.

    `d=4` keeps `_write_run`'s own real `cli.main(["run", ...])` batch
    dispatch fast; `deme_pair_panel` itself does not care whether `d`
    is above or below `scatter.PAIRWISE_MAX_DEMES` -- this bridge
    method exists specifically for the case where it is, so the point
    is proving the pooling and directory-rediscovery, not `d`'s own
    size.
    """
    output = _write_run(tmp_path, d=4, n_replicates=3)

    result = Api().get_batch_deme_pair_panel(str(output), first_deme=1, second_deme=3)

    assert result["ok"] is True
    panel = result["panel"]
    assert panel["x_label"] == "Deme 1"
    assert panel["y_label"] == "Deme 3"
    assert isinstance(panel["points"], list)


def test_get_batch_deme_pair_panel_rejects_an_out_of_range_deme(
    tmp_path: Path,
) -> None:
    output = _write_run(tmp_path, d=4, n_replicates=3)

    result = Api().get_batch_deme_pair_panel(str(output), first_deme=1, second_deme=5)

    assert result["ok"] is False
    assert "message" in result


def test_get_batch_deme_pair_panel_reports_no_replicates_without_raising(
    tmp_path: Path,
) -> None:
    empty_directory = tmp_path / "no-replicates-here"
    empty_directory.mkdir()

    result = Api().get_batch_deme_pair_panel(
        str(empty_directory), first_deme=1, second_deme=2
    )

    assert result["ok"] is False
    assert "message" in result


class _FakeMenuWindow:
    """Minimal `webview.Window` stand-in for `_build_menu`'s own structural test.

    `_build_menu` only ever captures `window.evaluate_js` inside a
    closure (never calling it during construction itself) and reads
    `window.destroy` as a bare, uncalled attribute — a real `webview.
    Window` is not needed to prove the menu's own shape, matching this
    file's own established "exercised as plain Python calls" precedent
    for bridge logic that does not actually touch a live window.
    """

    def evaluate_js(self, script: str) -> None:  # noqa: ARG002 - duck-typed stand-in
        return None

    def destroy(self) -> None:
        return None


def test_build_menu_has_file_configure_run_view_and_help() -> None:
    """The menu bar has exactly the five menus `doc/fim-gui-design.md` §10 specifies.

    Configure is new alongside File/Run/View/Help (the input screen's
    own six-tab bar moving off-canvas) — this test's own name and
    assertions were updated alongside it rather than left describing a
    menu bar that no longer matches `_build_menu`'s real shape.
    """
    menus = app_module._build_menu(_FakeMenuWindow())  # type: ignore[arg-type]

    assert [menu.title for menu in menus] == [
        "File",
        "Configure",
        "Run",
        "View",
        "Help",
    ]
    file_items = [item.title for item in menus[0].items if hasattr(item, "title")]
    assert file_items == [
        "New configuration",
        "Open configuration…",
        "Save configuration…",
        "Open run…",
        "Reveal output folder",
        "Quit fim",
    ]
    configure_items = [item.title for item in menus[1].items if hasattr(item, "title")]
    assert configure_items == [
        "Population",
        "Migration",
        "Mutation",
        "Initial conditions",
        "Convergence",
        "Batch",
        "Deme weighting",
        "Mutation model",
        "Convergence statistic",
    ]
    deme_weighting_submenu = menus[1].items[7]
    assert isinstance(deme_weighting_submenu, Menu)
    assert [
        item.title for item in deme_weighting_submenu.items if hasattr(item, "title")
    ] == ["size", "equal"]
    mutation_model_submenu = menus[1].items[8]
    assert isinstance(mutation_model_submenu, Menu)
    assert [
        item.title for item in mutation_model_submenu.items if hasattr(item, "title")
    ] == ["infinite_alleles", "finite_alleles"]
    convergence_statistic_submenu = menus[1].items[9]
    assert isinstance(convergence_statistic_submenu, Menu)
    assert [
        item.title
        for item in convergence_statistic_submenu.items
        if hasattr(item, "title")
    ] == ["D", "Gₛₜ", "Eₛₜ", "Kₛₜ", "Hₛ", "Hₜ"]
    run_items = [item.title for item in menus[2].items if hasattr(item, "title")]
    # No "Animate" item (`doc/fim-gui-design.md` §5.1): the
    # time slider is simply part of `completed`'s own view now, not a
    # second trigger reachable from a menu -- see `_build_menu`'s own
    # comment on the Run menu.
    assert run_items == ["Run simulation", "Cancel run"]
    view_items = [item.title for item in menus[3].items if hasattr(item, "title")]
    assert view_items == ["Significant digits"]
    digits_submenu = menus[3].items[0]
    assert isinstance(digits_submenu, Menu)
    digit_items = [
        item.title for item in digits_submenu.items if hasattr(item, "title")
    ]
    assert digit_items == ["2", "3", "4", "5", "6", "8"]
    help_items = [item.title for item in menus[4].items if hasattr(item, "title")]
    assert help_items == [
        "Usage guide",
        "Configuration reference",
        "Documentation on GitHub",
        "Check for updates",
        "About fim",
    ]


def test_statistic_menu_label_renders_true_unicode_subscripts() -> None:
    """`_statistic_menu_label` matches every `CONVERGENCE_STATISTIC_NAMES` entry.

    Direct, focused coverage of the small pure function behind the
    Convergence statistic submenu's own labels — native
    menu items are plain text, so this is the closest equivalent to the
    `<sub>`-tagged labels `index.html`'s own static markup uses.
    """
    assert app_module._statistic_menu_label("D") == "D"
    assert app_module._statistic_menu_label("G_ST") == "Gₛₜ"
    assert app_module._statistic_menu_label("E_ST") == "Eₛₜ"
    assert app_module._statistic_menu_label("K_ST") == "Kₛₜ"
    assert app_module._statistic_menu_label("H_S") == "Hₛ"
    assert app_module._statistic_menu_label("H_T") == "Hₜ"


def _all_menu_titles(nodes: Sequence[Menu | MenuAction | MenuSeparator]) -> list[str]:
    """Recursively collect every `Menu`/`MenuAction` title in a menu tree.

    `MenuSeparator` carries no title at all -- skipped, not defaulted
    to an empty string, the same `hasattr` check every other structural
    test in this file already uses to tell the three node types apart.
    A `Menu` contributes both its own title and every title nested
    inside it, recursively -- the View menu's own "Significant digits"
    submenu is exactly this shape.
    """
    titles: list[str] = []
    for node in nodes:
        if not hasattr(node, "title"):
            continue
        titles.append(node.title)
        if isinstance(node, Menu):
            titles.extend(_all_menu_titles(node.items))
    return titles


def test_no_menu_title_contains_a_paren() -> None:
    """No `Menu`/`MenuAction` title anywhere contains `(` or `)`.

    A real, confirmed crash, not a hypothetical one: the GTK/Linux
    pywebview backend derives a native "detailed action name" straight
    from a menu item's own label text and hands it to `g_menu_item_
    set_detailed_action`, which parses anything after an opening paren
    as GVariant target syntax -- `"3 (default)"` (the View menu's own
    Significant-digits submenu, before this test existed) produced a
    fatal `GLib-GIO-ERROR` that aborted the whole process outright
    (`Trace/breakpoint trap (core dumped)`), invisible on macOS/Windows
    and caught only by CI's own `linux-beta-x64` smoke test. This
    guards the whole class, not just that one label, against every
    title anywhere in the tree -- top-level menus, nested submenus, and
    items alike -- so a future addition fails here, in milliseconds,
    rather than crashing a real Linux build the same way.
    """
    menus = app_module._build_menu(_FakeMenuWindow())  # type: ignore[arg-type]

    titles = _all_menu_titles(menus)

    offenders = [title for title in titles if "(" in title or ")" in title]
    assert offenders == []


def test_open_external_link_opens_the_os_default_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`open_external_link` is a thin `webbrowser.open` call, nothing more.

    The same `_reveal_in_file_browser` precedent this module already
    follows for OS-dispatched actions — no real browser opens in a test.
    """
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)

    Api().open_external_link("https://example.invalid/path")

    assert opened == ["https://example.invalid/path"]


def test_check_for_updates_reports_a_newer_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        update,
        "latest_release",
        lambda: ("v99.0.0", "https://example.invalid/releases/v99.0.0"),
    )

    result = Api().check_for_updates()

    assert result["ok"] is True
    assert result["available"] is True
    assert result["latest"] == "99.0.0"
    assert result["url"] == "https://example.invalid/releases/v99.0.0"


def test_check_for_updates_reports_current_when_not_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        update,
        "latest_release",
        lambda: (f"v{fim_version}", "https://example.invalid/current"),
    )

    result = Api().check_for_updates()

    assert result["ok"] is True
    assert result["available"] is False
    assert result["current"] == fim_version


def test_check_for_updates_reports_a_failed_check_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> tuple[str, str]:
        raise RuntimeError("update check failed: no network")

    monkeypatch.setattr(update, "latest_release", _raise)

    result = Api().check_for_updates()

    assert result["ok"] is False
    assert "no network" in result["message"]


def test_get_about_info_names_the_installed_version() -> None:
    info = Api().get_about_info()

    assert info["version"] == fim_version
    assert "selby-botany/jost-finite-island-model" in info["repository"]
    assert "AGPL" in info["license"]


# --- _save_dialog_path ---


def test_save_dialog_path_returns_none_for_none() -> None:
    assert _save_dialog_path(None) is None


def test_save_dialog_path_returns_none_for_empty_string() -> None:
    assert _save_dialog_path("") is None


def test_save_dialog_path_returns_none_for_empty_tuple() -> None:
    assert _save_dialog_path(()) is None


def test_save_dialog_path_treats_bare_string_as_full_path(tmp_path: Path) -> None:
    # macOS cocoa backend returns a bare str, not a tuple, for SAVE dialogs.
    # Indexing a string with [0] gives the first character ('/'), so the old
    # code silently resolved every save path to Path('/') and the is_dir()
    # guard swallowed it as a cancel.
    target = tmp_path / "config.yaml"
    result = _save_dialog_path(str(target))
    assert result == target


def test_save_dialog_path_treats_tuple_as_sequence(tmp_path: Path) -> None:
    # Non-macOS backends and OPEN dialogs return a tuple.
    target = tmp_path / "config.yaml"
    result = _save_dialog_path((str(target),))
    assert result == target


def test_save_dialog_path_returns_none_for_root_directory() -> None:
    # Older pywebview macOS versions returned ('/',) on cancel.
    assert _save_dialog_path(("/",)) is None


def test_save_dialog_path_returns_none_for_root_string() -> None:
    assert _save_dialog_path("/") is None


def test_main_returns_2_on_a_malformed_fim_log_level(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad `FIM_LOG_LEVEL` fails before any window is ever built.

    `main`'s own `configure()` call is deliberately the very first thing
    it does (`doc/fim-logging-design.md` §5) — this test relies on that
    ordering to call the real `main()` safely, with no window/`webview.
    start()` reached at all: a malformed value raises out of
    `configure()` before `create_window()` is ever called.
    """
    monkeypatch.setenv("FIM_LOG_LEVEL", "verbose")

    status = app_module.main()

    assert status == 2
    assert "unknown log level 'verbose'" in capsys.readouterr().err
