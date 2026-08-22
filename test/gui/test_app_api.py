"""Unit tests for `fim.gui.app.Api`'s window-free bridge methods.

No display, no Tk import, no `gui` marker: `get_starter_form`,
`validate_form`, and `get_default_max_workers` never touch
`webview.windows[0]` — they call straight into `fim.gui.config_form`/
`fim.gui.batch_runner`, so they are exercised here as plain Python calls,
far cheaper and more direct than driving them through a real window and
`evaluate_js` (design doc §6.2's unit layer, applied to the bridge
itself). `load_yaml`/`save_yaml` do need a real window (a real file
dialog) and are covered instead in `test/gui/test_app.py`, marked `gui`.
"""

from __future__ import annotations

import json
import time as time_module
from dataclasses import replace
from pathlib import Path

import pytest

from fim import paths as paths_module
from fim.engine import RunResult, replicate_summary
from fim.engine import fim as engine_fim
from fim.gui import app as app_module
from fim.gui import batch_runner
from fim.gui.app import Api, format_statistic
from fim.gui.batch_runner import default_max_workers
from fim.gui.config_form import starter_form_values
from fim.gui.store import LiveProgressStore
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.viz.scatter import pooled_scatter_panels


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
    """
    results = engine_fim(
        batch_params.N,
        batch_params.m,
        batch_params.mu,
        batch_params.d,
        params=batch_params,
    )
    assert isinstance(results, tuple)
    return results


def test_batch_done_payload_carries_one_replicate_row_per_result(
    tmp_path: Path,
    batch_params: SimulationParams,
    batch_results: tuple[RunResult, ...],
) -> None:
    """`replicates` has one row per published result, 1-indexed, stats formatted."""
    payload = app_module._batch_done_payload(
        batch_params, "batch-run-1", tmp_path, batch_results
    )

    assert payload["runId"] == "batch-run-1"
    assert payload["outputDirectory"] == str(tmp_path)
    replicates = payload["replicates"]
    assert isinstance(replicates, list)
    assert [row["index"] for row in replicates] == [1, 2, 3]
    for row, result in zip(replicates, batch_results, strict=True):
        assert row["runId"] == result.run_id
        assert row["generation"] == result.report["generation"]
        assert row["statistics"]["D"] == format_statistic(result.report["D"])


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
    payload = app_module._batch_done_payload(
        batch_params, "batch-run-1", tmp_path, batch_results
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
    payload = app_module._batch_done_payload(
        batch_params, "batch-run-1", tmp_path, batch_results
    )

    expected = pooled_scatter_panels(
        [result.final_state for result in batch_results], batch_params.d
    )
    assert payload["panels"] == expected


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
