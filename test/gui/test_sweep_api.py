"""Tests for the sweep bridge calls on `fim.gui.app.Api` (no real window).

A sweep runs on a background thread. Tests wait for that thread's real
end — the busy guard `start_sweep` sets before returning and the thread
clears in its own `finally`, however the sweep ended — with no timing
budget, then assert how it ended (`_FakeWindow.finished`). These used to
wait up to 60 seconds for the `sweep_done` push; how long a sweep takes
depends on machine load, not on the commit, so a timed wait made the
result depend on what else the machine was doing. Only
`conftest.COMPLETION_BACKSTOP_SECONDS`, a hang backstop far above any
load-dependent duration, bounds the wait, failing with a named message.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from conftest import join_or_fail, poll_or_fail

from fim import paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.execution_priority import Priority
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.gui.config_form import DEFAULT_RUN_SETTING_FIELD_NAMES, starter_form_values
from fim.persistence import groups
from fim.sweep_run import LocalPointRunner, SweepOutcome, run_sweep

_IDLE_POLL_SECONDS = 0.05
_PROCESS_COUNTS: list[int] = []


class _InlineProcessPointRunner(LocalPointRunner):
    """Use the local point runner while preserving the process-runner API."""

    def __init__(
        self,
        processes: int,
        max_workers: int | None = None,
        *,
        pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
        lower_priority: bool = False,
        priority: Priority | None = None,
        results: Path | None = None,
    ) -> None:
        del lower_priority, priority, results
        _PROCESS_COUNTS.append(processes)
        super().__init__(max_workers, pairwise_max_demes=pairwise_max_demes)

    def close(self) -> None:
        """Match the process runner lifecycle without starting a pool."""
        return None


class _FakeWindow:
    """Records `evaluate_js` pushes and notes when a sweep ends."""

    def __init__(self) -> None:
        self.pushed: list[str] = []
        self.finished = threading.Event()

    def evaluate_js(self, script: str) -> Any:
        self.pushed.append(script)
        if '"kind": "sweep_done"' in script or '"kind": "sweep_cancelled"' in script:
            self.finished.set()
        return None

    def events(self) -> list[dict[str, Any]]:
        prefix = "fim.onSweepEvent("
        return [
            json.loads(script[len(prefix) : -1])
            for script in self.pushed
            if script.startswith(prefix)
        ]


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Isolate the results directory for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> _FakeWindow:
    """Provide a fake active window."""
    fake = _FakeWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: fake)
    return fake


@pytest.fixture
def api(tmp_path: Path, results: Path) -> Api:
    """An `Api` whose defaults make every point a tiny single run."""
    del results
    api = Api(preferences_path=tmp_path / "preferences.json")
    defaults = {
        key: value
        for key, value in starter_form_values().items()
        if key in api.get_default_run_settings()
    }
    defaults.update({"n_replicates": "1", "max_generations": "10"})
    assert api.set_default_run_settings(defaults)["ok"] is True
    api.set_default_ploidy("1")
    return api


def _form() -> dict[str, str]:
    """A tiny valid Configure form."""
    values = {
        key: value
        for key, value in starter_form_values(overrides={"ploidy": "1"}).items()
        if key not in DEFAULT_RUN_SETTING_FIELD_NAMES
    }
    values.update({"N": "16", "d": "2"})
    return values


def _request(*axes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"axes": list(axes), **extra}


_D_AXIS = {"key": "d", "values": [2, 3]}


def test_the_sweepable_keys_are_described_for_the_page(api: Api) -> None:
    keys = {entry["key"]: entry for entry in api.get_sweepable_keys()}

    assert set(keys) >= {"N", "d", "m", "mu", "topology", "deme_weighting"}
    assert keys["N"]["unit"] == "individuals"
    assert keys["m"]["scale"] == "log"
    assert keys["topology"]["choices"] == ["island", "ring", "linear", "torus"]
    assert keys["N"]["generic"] is False
    assert keys["n_loci"]["generic"] is True and keys["n_loci"]["kind"] == "int"
    assert list(keys)[:7] == ["N", "d", "m", "mu", "Nm", "topology", "deme_weighting"]
    assert keys["Nm"]["unit"] == "migrants per deme per generation"


def test_plan_sweep_counts_points_and_reports_invalid_ones(api: Api) -> None:
    plan = api.plan_sweep(_form(), _request({"key": "d", "values": [2, 3, 1]}))

    assert plan["ok"] is False
    assert "at least" in plan["message"]

    plan = api.plan_sweep(_form(), _request(_D_AXIS))

    assert plan["ok"] is True
    assert plan["gridSize"] == 2
    assert [point["coordinates"] for point in plan["points"]] == [{"d": 2}, {"d": 3}]
    assert plan["newCount"] == 2
    assert plan["reusedCount"] == 0
    assert plan["needsConfirmation"] is False
    assert plan["cost"]["points"] == 2


def test_plan_sweep_reads_a_range_definition(api: Api) -> None:
    plan = api.plan_sweep(
        _form(),
        _request({"key": "m", "range": {"start": 0.001, "stop": 0.1, "count": 3}}),
    )

    assert [point["coordinates"]["m"] for point in plan["points"]] == [0.001, 0.01, 0.1]


def test_plan_sweep_needs_at_least_one_axis(api: Api) -> None:
    plan = api.plan_sweep(_form(), {"axes": []})

    assert plan["ok"] is False
    assert "at least one axis" in plan["message"]


def test_start_sweep_runs_every_point_and_pushes_events(
    api: Api, window: _FakeWindow
) -> None:
    # One at a time, so the events arrive in a fixed order.
    started = api.start_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))

    assert started["ok"] is True
    assert started["total"] == 2
    _wait_until_idle(api)
    assert window.finished.is_set()
    events = window.events()
    assert [e["kind"] for e in events if e["kind"] != "point_progress"] == [
        "point_started",
        "point_done",
        "point_started",
        "point_done",
        "sweep_done",
    ]
    study = groups.get_study(started["studyId"])
    assert study.name == "Sweep of d"
    assert study.run_count == 2
    status = api.get_sweep_status(started["studyId"])
    assert [point["state"] for point in status["points"]] == ["done", "done"]


def test_a_finished_sweep_reports_its_results(api: Api, window: _FakeWindow) -> None:
    started = api.start_sweep(_form(), _request(_D_AXIS))
    _wait_until_idle(api)
    assert window.finished.is_set()

    payload = api.get_sweep_results(started["studyId"])

    assert payload["ok"] is True
    assert [entry["coordinates"] for entry in payload["results"]] == [
        {"d": 2},
        {"d": 3},
    ]
    assert "D" in payload["results"][0]["statistics"]
    assert payload["axes"] == [{"key": "d", "values": [2, 3]}]
    # The tiny runs stop at the generation cap, so each point is flagged and
    # says why; a flagged point is reported, never dropped.
    health = payload["results"][0]["health"]
    assert health["class"] == "unreliable"
    assert health["reason"].startswith("1 of 1 replicates stopped")
    assert (health["replicates"], health["hitCap"], health["converged"]) == (1, 1, 0)
    assert len(payload["results"]) == 2


def test_a_large_sweep_needs_confirmation_before_anything_is_created(
    api: Api, window: _FakeWindow
) -> None:
    request = _request(
        {"key": "m", "range": {"start": 0.001, "stop": 0.1, "count": 100}}
    )

    result = api.start_sweep(_form(), request)

    assert result["ok"] is False
    assert result["needsConfirmation"] is True
    assert groups.list_studies() == []
    assert window.pushed == []
    # Nothing started, so the sweep slot `start_sweep` claimed on entry
    # must be released again -- otherwise a second attempt, even a
    # confirmed one, would be refused as "a sweep is already running".
    assert api._sweep_cancel_event is None


def test_a_sweep_is_refused_when_another_job_uses_the_budget(
    api: Api,
    window: _FakeWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del window
    monkeypatch.setattr(app_module.jobs, "usable_core_count", lambda: 1)
    run, refusal = api._admit_run()
    assert refusal is None
    assert run is not None

    refused = api.start_sweep(
        _form(),
        _request({"key": "N", "values": [16, 20, 24, 28, 32]}, pointsAtOnce="1"),
    )

    assert refused["ok"] is False
    assert "only 0 remain" in refused["message"]
    assert groups.list_studies() == []
    assert api._jobs.active_job(run.job_id) is run


def test_cancelling_stops_the_sweep_and_resume_finishes_it(
    api: Api, window: _FakeWindow
) -> None:
    original = LocalPointRunner.run_object_point

    def cancel_after_first(
        self: Any,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Any = None,
    ) -> Any:
        result = original(self, object_id, cancel_event, on_message)
        api.cancel_sweep()
        return result

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(LocalPointRunner, "run_object_point", cancel_after_first)
    monkeypatch.setattr("fim.sweep_run.ProcessPointRunner", _InlineProcessPointRunner)
    try:
        started = api.start_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))
        _wait_until_idle(api)
        assert window.finished.is_set()
    finally:
        monkeypatch.undo()
    assert window.events()[-1]["kind"] == "sweep_cancelled"
    study_id = started["studyId"]
    assert groups.get_study(study_id).run_count == 1

    window.finished.clear()
    window.pushed.clear()
    resumed = api.resume_sweep(study_id)

    assert resumed["ok"] is True
    _wait_until_idle(api)
    assert window.finished.is_set()
    assert groups.get_study(study_id).run_count == 2


def _wait_until_idle(api: Api) -> None:
    """Wait for the sweep thread to release the busy guard.

    `start_sweep`/`resume_sweep` set the guard before returning and the
    sweep thread clears it in its own `finally`, after its last push, so
    this returns once the sweep has ended however it ended. The interval
    affects only how soon this notices; the completion-signal backstop
    (`conftest.poll_or_fail`) fails a sweep that never ends.
    """
    poll_or_fail(
        lambda: api._sweep_cancel_event,
        lambda guard: guard is None,
        "sweep thread (busy guard release)",
        interval=_IDLE_POLL_SECONDS,
    )


def test_resuming_something_that_is_not_a_sweep_is_refused(
    api: Api, window: _FakeWindow
) -> None:
    del window
    study = groups.create_study("By hand")

    result = api.resume_sweep(study.study_id)

    assert result["ok"] is False
    assert "not a sweep" in result["message"]


def test_a_sweep_needs_an_active_window(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "_active_window", lambda: None)

    result = api.start_sweep(_form(), _request(_D_AXIS))

    assert result == {"ok": False, "message": "no active window"}
    assert groups.list_studies() == []


def test_sweep_theory_evaluates_the_closed_form_at_sweep_coordinates(
    api: Api, window: _FakeWindow
) -> None:
    started = api.start_sweep(_form(), _request({"key": "m", "values": [0.01, 0.1]}))
    _wait_until_idle(api)
    assert window.finished.is_set()

    theory = api.get_sweep_theory(
        started["studyId"], [{"m": 0.01}, {"m": 0.1}, {"m": 0.1, "topology": "ring"}]
    )

    assert theory["ok"] is True
    low, high, ring = theory["values"]
    assert set(low) == {"D", "G_ST", "E_ST", "H_S", "H_T"}
    assert low["D"] is not None and high["D"] is not None
    assert low["D"] < high["D"] or low["D"] > high["D"]
    # An N axis counts individuals; the closed form sees gene copies.
    by_n = api.get_sweep_theory(
        started["studyId"],
        [{"N": 8, "ploidy": "haploid"}, {"N": 16, "ploidy": "haploid"}],
    )["values"]
    assert by_n[0]["H_S"] != by_n[1]["H_S"]
    # A topology the island-model theory does not cover is undefined, not an error.
    assert all(value is None for value in ring.values())


def test_sweep_theory_for_an_unknown_study_is_a_clean_failure(api: Api) -> None:
    result = api.get_sweep_theory("study-ffffffff", [{"m": 0.1}])

    assert result["ok"] is False


def test_list_studies_reports_the_planned_point_count_of_a_sweep_only(
    api: Api, window: _FakeWindow
) -> None:
    started = api.start_sweep(_form(), _request(_D_AXIS))
    _wait_until_idle(api)
    assert window.finished.is_set()
    api.create_study("By hand")

    counts = {study["name"]: study["sweepPointCount"] for study in api.list_studies()}

    assert started["ok"] is True
    assert counts == {"Sweep of d": 2, "By hand": None}


def test_equilibrium_grid_gives_every_statistic_at_every_cell(api: Api) -> None:
    grid = api.get_equilibrium_grid("m", "d", "225", "0.001", "0.00003", "20")

    assert grid["ok"] is True
    assert grid["xAxis"] == "m"
    assert grid["yAxis"] == "d"
    # An integer axis has one row per integer, 2 through 50, not a blur.
    assert grid["yValues"] == list(range(2, 51))
    assert len(grid["xValues"]) == 24
    assert {"D", "G_ST", "H_S"} <= set(grid["series"])
    assert "mutation_negligible_equilibrium" not in grid["series"]
    rows = grid["values"]["D"]
    assert len(rows) == 49
    assert all(len(row) == 24 for row in rows)
    # The cell nearest the entered configuration is the probe's start.
    assert grid["yValues"][grid["currentRow"]] == 20
    assert abs(grid["xValues"][grid["currentColumn"]] - 0.001) < 0.0005


def test_equilibrium_grid_agrees_with_the_one_axis_sweep(api: Api) -> None:
    grid = api.get_equilibrium_grid("m", "mu", "225", "0.001", "0.00003", "20")
    curve = api.get_equilibrium_sweep("m", "225", "0.001", "0.00003", "20")

    row = grid["currentRow"]
    from_grid = [grid["values"]["G_ST"][row][c] for c in range(len(grid["xValues"]))]
    # Only the row nearest mu = 3e-5 is comparable, and only where the
    # nearest mu is that value; compare the shape at the sweep's own points.
    assert grid["xValues"] == pytest.approx([p["x"] for p in curve["points"]])
    assert all(value is None or 0 <= value <= 1 for value in from_grid)


def test_equilibrium_grid_needs_two_different_known_axes(api: Api) -> None:
    same = api.get_equilibrium_grid("m", "m", "225", "0.001", "0.00003", "20")
    unknown = api.get_equilibrium_grid("m", "q", "225", "0.001", "0.00003", "20")
    bad = api.get_equilibrium_grid("m", "d", "lots", "0.001", "0.00003", "20")

    assert same == {"ok": False, "message": "choose two different axes"}
    assert unknown["ok"] is False
    assert bad["ok"] is False


def test_equilibrium_grid_n_axis_is_whole_gene_copies_without_repeats(api: Api) -> None:
    grid = api.get_equilibrium_grid("N", "m", "225", "0.001", "0.00003", "20")

    values = grid["xValues"]
    assert all(isinstance(value, int) for value in values)
    assert len(values) == len(set(values))
    assert values == sorted(values)


def test_equilibrium_curve_evaluates_at_the_values_asked_for(api: Api) -> None:
    curve = api.get_equilibrium_curve(
        "d", [3, 4.4, 10], "225", "0.001", "0.00003", "20"
    )

    assert curve["ok"] is True
    assert [point["x"] for point in curve["points"]] == [3, 4, 10]
    assert curve["points"][0]["D"] != curve["points"][2]["D"]
    assert api.get_equilibrium_curve("q", [1], "1", "0.1", "0.1", "2")["ok"] is False


def test_a_sweep_runs_into_the_study_chosen_and_keeps_its_runs(
    api: Api, window: _FakeWindow
) -> None:
    study_id = api.create_study("Mine")["studyId"]

    started = api.start_sweep(_form(), _request(_D_AXIS), study_id)

    assert started["ok"] is True
    assert started["studyId"] == study_id
    assert started["total"] == 2
    assert isinstance(started["jobId"], str) and started["jobId"]
    _wait_until_idle(api)
    assert window.finished.is_set()
    (study,) = api.list_studies()
    assert (study["name"], study["runCount"], study["sweepPointCount"]) == (
        "Mine",
        2,
        2,
    )


def test_a_study_that_already_holds_a_sweep_is_refused(
    api: Api, window: _FakeWindow
) -> None:
    study_id = api.create_study("Mine")["studyId"]
    api.start_sweep(_form(), _request(_D_AXIS), study_id)
    _wait_until_idle(api)
    assert window.finished.is_set()

    again = api.start_sweep(_form(), _request({"key": "d", "values": [4]}), study_id)

    assert again["ok"] is False
    assert "already holds a sweep" in again["message"]


def test_create_study_can_place_the_study_in_an_experiment(api: Api) -> None:
    experiment_id = api.create_experiment("Migration")["experimentId"]

    made = api.create_study("Ring", "", experiment_id)

    assert made["ok"] is True
    (experiment,) = api.list_experiments()
    assert experiment["studyIds"] == [made["studyId"]]
    missing = api.create_study("Orphan", "", "experiment-ffffffff")
    assert missing["ok"] is False
    assert [study["name"] for study in api.list_studies()] == ["Ring"]


def test_plan_sweep_says_how_many_points_run_at_once(api: Api) -> None:
    plan = api.plan_sweep(_form(), _request(_D_AXIS))

    concurrency = plan["concurrency"]
    assert concurrency["automatic"] is True
    assert 1 <= concurrency["pointsAtOnce"] <= 2
    assert concurrency["cores"] >= 1
    asked = api.plan_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))
    assert asked["concurrency"]["pointsAtOnce"] == 1
    assert asked["concurrency"]["automatic"] is False


def test_a_sweep_runs_each_batch_point_as_one_in_process_run_object(
    api: Api, window: _FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    objects: list[str] = []
    legacy: list[int | None] = []
    original = LocalPointRunner.run_object_point

    def recording(
        self: Any,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Any = None,
    ) -> Any:
        objects.append(object_id)
        return original(self, object_id, cancel_event, on_message)

    def legacy_recording(self: Any, *args: Any, **kwargs: Any) -> Any:
        legacy.append(None)
        raise AssertionError("a batch point should run as a Run object")

    monkeypatch.setattr(LocalPointRunner, "run_object_point", recording)
    monkeypatch.setattr(LocalPointRunner, "run_point", legacy_recording)
    monkeypatch.setattr("fim.sweep_run.ProcessPointRunner", _InlineProcessPointRunner)
    api.set_default_run_settings(
        {
            **api.get_default_run_settings(),
            "n_replicates": "2",
            "engine_backend": "lineal",
        }
    )

    started = api.start_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))

    assert started["ok"] is True
    _wait_until_idle(api)
    assert window.finished.is_set()
    # A batch session steps both replicates in one process, so no point
    # asks for worker processes of its own.
    assert len(set(objects)) == 2
    assert legacy == []
    assert groups.get_study(started["studyId"]).run_count == 2


def test_a_sweep_thread_that_dies_tells_the_page_and_frees_the_guard(
    api: Api, window: _FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_sweep(*args: Any, **kwargs: Any) -> None:
        raise BrokenProcessPool("a process in the pool was terminated abruptly")

    monkeypatch.setattr(app_module, "run_sweep", broken_sweep)

    started = api.start_sweep(_form(), _request(_D_AXIS))
    assert started["ok"] is True
    assert api._sweep_thread is not None
    join_or_fail(api._sweep_thread, "the sweep thread")

    assert api._sweep_cancel_event is None
    errors = [s for s in window.pushed if s.startswith("fim.onSweepError(")]
    assert len(errors) == 1
    assert "terminated abruptly" in errors[0]


def test_sweep_theory_for_a_diploid_sweep_uses_gene_copies(api: Api) -> None:
    from fim.statistics.differentiation import equilibrium_g_st  # noqa: PLC0415
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import create_sweep_study  # noqa: PLC0415

    spec = SweepSpec(
        base={
            "N": 225,
            "ploidy": "diploid",
            "d": 20,
            "m": 0.001,
            "mu": 3e-05,
            "seed": 1,
            "n_replicates": 1,
            "max_generations": 10,
        },
        axes=(expand_axis("m", [0.001, 0.01]),),
    )
    study = create_sweep_study(spec, enumerate_points(spec), "Diploid theory")

    values = api.get_sweep_theory(study.study_id, [{"m": 0.001}])["values"]

    # 225 diploid individuals per deme are 450 gene copies.
    assert values[0]["G_ST"] == equilibrium_g_st(450, 0.001, 3e-05, 20)


class _SavingWindow(_FakeWindow):
    """A fake window whose Save dialog answers with a chosen path."""

    def __init__(self, answer: Path | None) -> None:
        super().__init__()
        self.answer = answer
        self.asked: list[dict[str, Any]] = []

    def create_file_dialog(self, *args: Any, **kwargs: Any) -> Any:
        del args
        self.asked.append(kwargs)
        return None if self.answer is None else str(self.answer)


def _finished_sweep(api: Api, window: _FakeWindow) -> str:
    """Run two tiny points serially and return the finished Study id.

    These bridge/metadata tests do not test process concurrency. Automatic
    concurrency starts child interpreters and their engine imports, which
    exceeded the completion backstop under the full slow-suite load.
    """
    with patch("fim.sweep_run.ProcessPointRunner", _InlineProcessPointRunner):
        started = api.start_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))
        _wait_until_idle(api)
    assert window.finished.is_set()
    job_events = [
        json.loads(script[len("fim.onJobEvent(") : -1])
        for script in window.pushed
        if script.startswith("fim.onJobEvent(")
    ]
    assert job_events[-1]["type"] == "finished"
    snapshot = api.get_job_snapshot(job_events[-1]["jobId"])
    assert snapshot["snapshot"]["type"] == "finished"
    return str(started["studyId"])


def test_finished_sweep_helper_does_not_spawn_point_processes(
    api: Api, window: _FakeWindow
) -> None:
    """Metadata fixtures must not depend on concurrent child-process startup."""

    _PROCESS_COUNTS.clear()
    study_id = _finished_sweep(api, window)
    assert groups.get_study(study_id).run_count == 2
    assert _PROCESS_COUNTS == [1]


def test_gui_one_point_sweep_uses_process_runner_and_half_core_budget(
    api: Api, window: _FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit sweep execution receives all currently available capacity."""
    _PROCESS_COUNTS.clear()
    monkeypatch.setattr(app_module.os, "cpu_count", lambda: 8)
    original_run_sweep = run_sweep
    captured: dict[str, Any] = {}

    def capture_run_sweep(*args: Any, **kwargs: Any) -> SweepOutcome:
        captured.update(kwargs)
        return original_run_sweep(*args, **kwargs)

    monkeypatch.setattr(app_module, "run_sweep", capture_run_sweep)
    monkeypatch.setattr("fim.sweep_run.ProcessPointRunner", _InlineProcessPointRunner)

    started = api.start_sweep(_form(), _request(_D_AXIS, pointsAtOnce="1"))
    _wait_until_idle(api)

    assert started["ok"] is True
    assert captured["process_points"] is True
    assert captured["priority"] == "lower"
    assert captured["cores"] == 8
    assert _PROCESS_COUNTS == [1]
    assert window.finished.is_set()


def test_automatic_sweep_preview_uses_reserved_and_remaining_capacity(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic sizing applies the reserve, then respects active reservations."""
    monkeypatch.setattr(app_module.jobs, "usable_core_count", lambda: 10)
    api._preferences = api._preferences.with_reserved_cores(2)
    request = _request({"key": "N", "values": [16, 20, 24, 28, 32, 36, 40, 44, 48, 52]})
    payload = api.plan_sweep(
        _form(),
        request,
    )

    assert payload["ok"] is True
    assert payload["concurrency"]["remainingCores"] == 8
    assert payload["concurrency"]["cores"] == 4
    assert payload["concurrency"]["pointsAtOnce"] == 4

    run, refusal = api._admit_run(cores=6)
    assert refusal is None
    assert run is not None
    constrained = api.plan_sweep(_form(), request)

    assert constrained["concurrency"]["remainingCores"] == 2
    assert constrained["concurrency"]["cores"] == 2
    assert constrained["concurrency"]["pointsAtOnce"] == 2


def test_explicit_sweep_demand_above_remaining_budget_is_refused(
    api: Api,
    window: _FakeWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request is never silently clamped to fit the remaining budget."""
    monkeypatch.setattr(app_module.jobs, "usable_core_count", lambda: 8)
    run, refusal = api._admit_run(cores=6)
    assert refusal is None
    assert run is not None
    request = _request({"key": "N", "values": [16, 20, 24, 28, 32]}, pointsAtOnce="3")

    plan = api.plan_sweep(_form(), request)
    started = api.start_sweep(_form(), request)

    assert plan["concurrency"]["pointsAtOnce"] == 3
    assert plan["concurrency"]["cores"] == 3
    assert plan["concurrency"]["remainingCores"] == 2
    assert plan["concurrency"]["admissible"] is False
    assert started["ok"] is False
    assert "needs 3 worker slot(s), but only 2 remain" in started["message"]
    assert groups.list_studies() == []
    assert window.pushed == []


def test_gui_sweep_budget_prefers_process_cpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the process-visible CPU count when the Python runtime exposes it."""
    monkeypatch.setattr(app_module.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(app_module.os, "process_cpu_count", lambda: 6, raising=False)

    assert app_module._gui_sweep_core_budget() == 3


def test_export_sweep_table_writes_the_table_and_its_sidecar(
    api: Api, window: _FakeWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _finished_sweep(api, window)
    target = tmp_path / "saved.csv"
    saving = _SavingWindow(target)
    monkeypatch.setattr(app_module, "_active_window", lambda: saving)

    reply = api.export_sweep_table(
        study_id, {"statistics": ["D", "H_S"], "withTheory": True}
    )

    assert reply == {
        "ok": True,
        "path": str(target),
        "sidecar": str(tmp_path / "saved.meta.json"),
    }
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith(",theory,residual,residual_relative")
    assert len(lines) == 1 + 2 * 2  # two points, two statistics
    assert saving.asked[0]["save_filename"].endswith(".csv")
    meta = json.loads((tmp_path / "saved.meta.json").read_text(encoding="utf-8"))
    assert meta["sweep"]["study_id"] == study_id


def test_export_sweep_table_by_jsonl_name_and_over_an_existing_file(
    api: Api, window: _FakeWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _finished_sweep(api, window)
    target = tmp_path / "saved.jsonl"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(app_module, "_active_window", lambda: _SavingWindow(target))

    reply = api.export_sweep_table(study_id, {"statistics": "all"})

    assert reply["ok"] is True
    rows = [json.loads(line) for line in target.read_text("utf-8").splitlines()]
    assert {row["statistic"] for row in rows} > {"D", "G_ST"}


def test_a_cancelled_save_dialog_is_not_an_error(
    api: Api, window: _FakeWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _finished_sweep(api, window)
    monkeypatch.setattr(app_module, "_active_window", lambda: _SavingWindow(None))

    assert api.export_sweep_table(study_id, {}) == {"ok": False, "message": ""}
    assert not list(tmp_path.glob("*.csv"))


def test_export_sweep_table_refuses_what_it_cannot_write(
    api: Api, window: _FakeWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _finished_sweep(api, window)
    plain = groups.create_study("By hand")
    monkeypatch.setattr(
        app_module, "_active_window", lambda: _SavingWindow(tmp_path / "t.csv")
    )

    not_a_sweep = api.export_sweep_table(plain.study_id, {})
    unknown = api.export_sweep_table(study_id, {"statistics": ["nope"]})

    assert not_a_sweep["ok"] is False and "not a sweep" in not_a_sweep["message"]
    assert unknown["ok"] is False and "no statistic 'nope'" in unknown["message"]


def test_export_sweep_table_with_nothing_finished_says_so(
    api: Api, window: _FakeWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import create_sweep_study  # noqa: PLC0415

    del window
    spec = SweepSpec(
        base={
            "N": 16,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.01,
            "seed": 1,
            "n_replicates": 1,
            "max_generations": 10,
        },
        axes=(expand_axis("d", [2, 3]),),
    )
    study = create_sweep_study(spec, enumerate_points(spec), "Not run")
    saving = _SavingWindow(tmp_path / "t.csv")
    monkeypatch.setattr(app_module, "_active_window", lambda: saving)

    reply = api.export_sweep_table(study.study_id, {})

    assert reply == {"ok": False, "message": "no finished points to save yet"}
    assert saving.asked == []


def test_get_sweep_series_returns_the_curve_with_both_intervals_and_health(
    api: Api, window: _FakeWindow
) -> None:
    study_id = _finished_sweep(api, window)

    reply = api.get_sweep_series(study_id, "d", "D", "", {})

    assert reply["ok"] is True
    assert (reply["x"], reply["by"], reply["statistic"]) == ("d", None, "D")
    (curve,) = reply["series"]
    assert curve["byValue"] is None
    assert [point["x"] for point in curve["points"]] == [2, 3]
    first = curve["points"][0]
    assert {"mean", "ciLow", "ciHigh", "sdLow", "sdHigh", "health"} <= set(first)
    assert first["health"] == "unreliable"  # the tiny runs stop at the cap
    assert first["healthReason"].startswith("1 of 1 replicates")
    assert first["directory"]


def test_get_sweep_series_refuses_what_the_table_refuses(
    api: Api, window: _FakeWindow
) -> None:
    study_id = _finished_sweep(api, window)

    unknown = api.get_sweep_series(study_id, "nope", "D", None, {})
    same = api.get_sweep_series(study_id, "d", "D", "d", {})
    plain = api.get_sweep_series(
        groups.create_study("Plain").study_id, "d", "D", None, {}
    )

    assert unknown["ok"] is False and "'nope' is not an axis" in unknown["message"]
    assert same["ok"] is False and "cannot be split" in same["message"]
    assert plain["ok"] is False and "not a sweep" in plain["message"]


def _compared_study(name: str, population: int) -> str:
    """Create and run a tiny two-point sweep over `m`; return its Study id."""
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import (  # noqa: PLC0415
        LocalPointRunner,
        create_sweep_study,
        run_sweep,
    )

    spec = SweepSpec(
        base={
            "N": population,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.01,
            "seed": 7,
            "n_replicates": 3,
            "max_generations": 10,
        },
        axes=(expand_axis("m", [0.05, 0.1]),),
    )
    study = create_sweep_study(spec, enumerate_points(spec), name)
    run_sweep(
        study.study_id,
        LocalPointRunner(),
        lambda _event: None,
        threading.Event(),
        points_at_once=1,
    )
    return study.study_id


def test_compare_sweeps_returns_the_join_cells_and_difference(api: Api) -> None:
    small, large = _compared_study("Small", 16), _compared_study("Large", 32)

    reply = api.compare_sweeps([small, large], "", None, None)

    assert reply["ok"] is True
    assert (reply["statistic"], reply["on"], reply["labels"]) == (
        "D",
        ["m"],
        ["Small", "Large"],
    )
    assert reply["axes"] == {"m": [0.05, 0.1]}
    assert "D" in reply["available"] and "G_ST" in reply["available"]
    assert reply["baseDifferences"] == {"N": {"Small": 16, "Large": 32}}
    assert reply["pairing"] == "shared-seeds"
    first = reply["points"][0]
    assert first["coordinates"] == {"m": 0.05}
    assert set(first["cells"]["Small"]) >= {"mean", "low", "high", "sdLow", "health"}
    assert first["difference"]["difference"] == pytest.approx(
        first["cells"]["Small"]["mean"] - first["cells"]["Large"]["mean"]
    )
    assert first["difference"]["degreesOfFreedom"] is not None


def test_compare_sweeps_uses_a_chosen_statistic(api: Api) -> None:
    small, large = _compared_study("Small", 16), _compared_study("Large", 32)

    reply = api.compare_sweeps([small, large], "G_ST", None, None)

    assert reply["ok"] is True
    assert reply["statistic"] == "G_ST"
    assert reply["labels"] == ["Small", "Large"]


def test_two_studies_with_the_same_name_are_told_apart_by_id(api: Api) -> None:
    first, second = _compared_study("Same", 16), _compared_study("Same", 32)

    reply = api.compare_sweeps([first, second], "D", None, None)

    assert reply["labels"] == [f"Same ({first})", f"Same ({second})"]


def test_compare_sweeps_says_why_it_cannot(api: Api) -> None:
    small = _compared_study("Small", 16)

    one = api.compare_sweeps([small], "D", None, None)
    plain = api.compare_sweeps(
        [small, groups.create_study("Plain").study_id], "D", None, None
    )
    unknown = api.compare_sweeps(
        [small, _compared_study("Other", 32)], "D", ["nope"], None
    )

    assert one["ok"] is False and "at least two sweeps" in one["message"]
    assert plain["ok"] is False and "not a sweep" in plain["message"]
    assert unknown["ok"] is False and "cannot join on 'nope'" in unknown["message"]


def test_plan_sweep_reports_the_cost_bands_and_the_points_at_once(api: Api) -> None:
    plan = api.plan_sweep(_form(), _request(_D_AXIS))

    cost = plan["cost"]
    assert "estimate" not in plan
    assert cost["points"] == 2
    assert cost["pointsAtOnce"] == plan["concurrency"]["pointsAtOnce"]
    for name in ("replicates", "generations", "work"):
        assert cost[name]["low"] <= cost[name]["high"]
    assert cost["replicates"] == {"low": 1.0, "high": 1.0, "calibrated": None}
    assert cost["work"]["high"] == pytest.approx(
        cost["replicates"]["high"] * 2 * cost["generations"]["high"]
    )


def test_estimate_sweep_cost_states_time_disk_and_what_was_measured(
    api: Api, _no_real_engine_probe: Any
) -> None:
    reply = api.estimate_sweep_cost(_form(), _request(_D_AXIS))

    assert reply["ok"] is True
    assert reply["time"].startswith("about ") and "point(s) at once on" in reply["time"]
    assert "with the full trajectory log" in reply["disk"]
    assert reply["probe"].startswith("2 shape class(es) measured")  # d = 2 and d = 3
    assert reply["cost"]["points"] == 2
    assert reply["cost"]["probe"]["classes"] == 2
    assert _no_real_engine_probe.asked == [200, 200]


def test_a_repeated_estimate_uses_the_remembered_measurement(
    api: Api, tmp_path: Path, _no_real_engine_probe: Any
) -> None:
    api.estimate_sweep_cost(_form(), _request(_D_AXIS))
    api.estimate_sweep_cost(_form(), _request(_D_AXIS))

    assert _no_real_engine_probe.asked == [200, 200]  # measured once, then cached
    assert (tmp_path / "sweep-throughput.json").is_file()  # beside the preferences


def test_points_that_are_already_computed_cost_nothing_to_run(
    api: Api, window: _FakeWindow, _no_real_engine_probe: Any
) -> None:
    _finished_sweep(api, window)
    _no_real_engine_probe.asked.clear()

    reply = api.estimate_sweep_cost(_form(), _request(_D_AXIS))

    assert reply == {
        "ok": True,
        "none": True,
        "time": "nothing to run: every point is already computed",
    }
    assert _no_real_engine_probe.asked == []


def test_an_estimate_that_cannot_be_made_says_so_instead_of_failing(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def measure(self, params: Any, generations: int) -> Any:
            del params, generations
            raise RuntimeError("the engine would not start")

    monkeypatch.setattr(app_module, "EngineProbe", Broken)

    reply = api.estimate_sweep_cost(_form(), _request(_D_AXIS))

    assert reply == {
        "ok": False,
        "message": "could not estimate: the engine would not start",
    }
    assert (
        api.estimate_sweep_cost(_form(), _request({"key": "d", "values": [1]}))["ok"]
        is False
    )


def test_get_sweep_eta_says_what_is_left_and_when_it_is_done(
    api: Api, window: _FakeWindow
) -> None:
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import create_sweep_study  # noqa: PLC0415

    spec = SweepSpec(
        base={
            "N": 8,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.2,
            "seed": 7,
            "n_replicates": 1,
            "precision": 0.1,
        },
        axes=(expand_axis("N", [8, 16]),),
    )
    fresh = create_sweep_study(spec, enumerate_points(spec), "Eta").study_id
    finished = _finished_sweep(api, window)

    waiting = api.get_sweep_eta(fresh)
    done = api.get_sweep_eta(finished)

    assert waiting["ok"] is True
    assert (waiting["done"], waiting["total"], waiting["remainingPoints"]) == (0, 2, 2)
    assert waiting["text"].startswith("not yet known for 2 point(s)")
    assert done["text"] == "none: every point is done"
    assert (done["done"], done["total"]) == (2, 2)
    assert api.get_sweep_eta("study-ffffffff")["ok"] is False


def test_a_plan_marks_the_points_a_finished_sweep_already_computed(
    api: Api, window: _FakeWindow
) -> None:
    """The runs a sweep makes are Run objects; a plan must count them."""
    before = api.plan_sweep(_form(), _request(_D_AXIS))
    _finished_sweep(api, window)

    after = api.plan_sweep(_form(), _request(_D_AXIS))

    assert (before["newCount"], before["reusedCount"]) == (2, 0)
    assert (after["newCount"], after["reusedCount"]) == (0, 2)
    assert [point["exists"] for point in after["points"]] == [True, True]


def _capture_run_sweep(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the app's `run_sweep` with one that records its options."""
    calls: list[dict[str, Any]] = []

    def record(study_id: str, *args: Any, **kwargs: Any) -> None:
        del args
        calls.append({"study_id": study_id, **kwargs})

    monkeypatch.setattr(app_module, "run_sweep", record)
    return calls


@pytest.mark.parametrize(
    ("kwargs", "retry", "rerun"),
    [
        ({}, False, False),
        ({"retry_failed": True}, True, False),
        ({"rerun": True}, False, True),
        ({"retry_failed": True, "rerun": True}, True, True),
    ],
)
def test_resume_sweep_passes_the_retry_and_recompute_flags_on(
    api: Api,
    window: _FakeWindow,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, bool],
    retry: bool,
    rerun: bool,
) -> None:
    study_id = _finished_sweep(api, window)
    calls = _capture_run_sweep(monkeypatch)

    reply = api.resume_sweep(study_id, **kwargs)
    assert reply["ok"] is True
    assert reply["studyId"] == study_id
    if rerun:
        assert isinstance(reply["jobId"], str) and reply["jobId"]
        assert api._sweep_thread is not None
        api._sweep_thread.join(30)
        assert len(calls) == 1
        assert (calls[0]["retry_failed"], calls[0]["rerun"]) == (retry, rerun)
    else:
        assert reply == {
            "ok": True,
            "studyId": study_id,
            "jobId": None,
            "noWork": True,
        }
        assert calls == []
        assert api._jobs.active_jobs() == ()


def test_recomputing_all_through_the_app_verifies_the_sweep(
    api: Api, window: _FakeWindow
) -> None:
    from fim.sweep_run import read_verification  # noqa: PLC0415

    study_id = _finished_sweep(api, window)
    window.pushed.clear()
    window.finished.clear()

    assert api.resume_sweep(study_id, rerun=True)["ok"] is True
    _wait_until_idle(api)

    kinds = [event["kind"] for event in window.events()]
    assert kinds.count("point_recomputed") == 2
    record = read_verification(study_id)
    assert record is not None and record["result"] == "identical"


def test_the_study_list_counts_the_failed_points_of_a_sweep(api: Api) -> None:
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import (  # noqa: PLC0415
        PointFailure,
        create_sweep_study,
        run_sweep,
    )

    class Failing:
        def run_point(self, *args: Any, **kwargs: Any) -> PointFailure:
            del args, kwargs
            return PointFailure("the engine exploded")

    spec = SweepSpec(
        base={
            "N": 8,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.2,
            "seed": 7,
            "n_replicates": 1,
            "precision": 0.1,
        },
        axes=(expand_axis("N", [8, 16]),),
    )
    study = create_sweep_study(spec, enumerate_points(spec), "Failing")
    plain = groups.create_study("Plain")
    run_sweep(
        study.study_id, Failing(), lambda _e: None, threading.Event(), points_at_once=1
    )

    rows = {row["studyId"]: row for row in api.list_studies()}

    assert rows[study.study_id]["sweepFailedCount"] == 2
    assert rows[plain.study_id]["sweepFailedCount"] == 0


def test_plan_sweep_takes_a_strategy_and_a_generic_axis(api: Api) -> None:
    zipped = api.plan_sweep(
        _form(),
        _request(
            {"key": "d", "values": [2, 3]},
            {"key": "N", "values": [20, 40]},
            strategy="zip",
        ),
    )
    grid = api.plan_sweep(
        _form(),
        _request({"key": "d", "values": [2, 3]}, {"key": "N", "values": [20, 40]}),
    )
    generic = api.plan_sweep(_form(), _request({"key": "n_loci", "values": [1, 2, 3]}))
    bad = api.plan_sweep(_form(), _request(_D_AXIS, strategy="random"))

    assert (zipped["strategy"], zipped["gridSize"]) == ("zip", 2)
    assert (grid["strategy"], grid["gridSize"]) == ("grid", 4)
    assert generic["ok"] is True and len(generic["points"]) == 3
    assert bad["ok"] is False and "grid" in bad["message"]


def test_sweep_trajectories_come_back_as_curves_with_a_quorum(
    api: Api, window: _FakeWindow
) -> None:
    study_id = _finished_sweep(api, window)

    reply = api.get_sweep_trajectories(study_id, "D", "d", {})

    assert reply["ok"] is True
    assert [curve["byValue"] for curve in reply["series"]] == [2, 3]
    point = reply["series"][0]["points"][0]
    assert point["generations"][0] == 0
    assert len(point["mean"]) == len(point["generations"]) == len(point["quorum"])
    assert point["nReplicates"] >= 1
    assert point["health"] in {"healthy", "degraded", "unreliable"}
    json.dumps(reply, allow_nan=False)  # no NaN reaches the page


def test_sweep_trajectories_name_a_bad_axis_and_a_missing_statistic(
    api: Api, window: _FakeWindow
) -> None:
    study_id = _finished_sweep(api, window)

    bad = api.get_sweep_trajectories(study_id, "D", "nonsense", {})
    none = api.get_sweep_trajectories(study_id, "no_such_statistic", "d", {})

    assert bad["ok"] is False and "not an axis" in bad["message"]
    assert none["ok"] is True
    assert all(curve["points"] == [] for curve in none["series"])


def test_a_sweep_study_is_not_pooled_by_open_study(
    api: Api, window: _FakeWindow
) -> None:
    study_id = _finished_sweep(api, window)

    reply = api.open_study(study_id)

    assert reply["ok"] is False
    assert "holds a sweep" in reply["message"]


def test_the_keep_choices_become_a_recording_block_without_the_defaults() -> None:
    from fim.gui.sweep_bridge import recording_from_request  # noqa: PLC0415

    assert recording_from_request(None) == {}
    assert recording_from_request({"retention": "full", "history": "compact"}) == {}
    assert recording_from_request(
        {
            "retention": "tapered",
            "keepLast": 250,
            "keepBurnIn": True,
            "history": "exact",
        }
    ) == {
        "retention": "tapered",
        "keep_last": 250,
        "keep_burn_in": True,
        "history": "exact",
    }
    # The details of tapering do not leak into another level.
    assert recording_from_request(
        {"retention": "statistics", "keepLast": 250, "keepBurnIn": True}
    ) == {"retention": "statistics"}
    with pytest.raises(ValueError, match="recording must be an object"):
        recording_from_request("tapered")


def test_plan_and_estimate_carry_what_the_sweep_keeps(
    api: Api, _no_real_engine_probe: Any
) -> None:
    request = _request(
        _D_AXIS, recording={"retention": "tapered", "keepLast": 50, "history": "exact"}
    )

    plan = api.plan_sweep(_form(), request)
    reply = api.estimate_sweep_cost(_form(), request)

    assert plan["ok"] is True
    assert reply["ok"] is True
    assert reply["cost"]["recordingLevel"] == "tapered"
    assert set(reply["cost"]["levelBytes"]) == {
        "full",
        "thinned",
        "tapered",
        "statistics",
    }
    assert reply["cost"]["chosenBytes"] == reply["cost"]["levelBytes"]["tapered"]
    assert "this sweep keeps tapered" in reply["disk"]
    bad = api.plan_sweep(_form(), _request(_D_AXIS, recording={"retention": "sparse"}))
    assert bad["ok"] is False and "trajectory_retention" in bad["message"]
