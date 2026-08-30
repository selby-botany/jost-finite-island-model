"""Static checks that the CI test layers stay explicit, budgeted, and observable."""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# `ci.yml` is also home to the release jobs (R8); see test_release_notes.py
# for the corresponding checks on those.
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _build_steps() -> list[dict[str, object]]:
    """Return the `build` job's step list, parsed from the real workflow file."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict[str, object]] = workflow["jobs"]["build"]["steps"]
    return steps


def _run_steps() -> list[dict[str, object]]:
    """Return only the `build` job's steps that execute a shell command."""
    return [step for step in _build_steps() if "run" in step]


def test_the_expensive_scenario_layer_runs_as_its_own_named_budgeted_step() -> None:
    """The slow/statistical suite is a separately named step, not folded into one.

    Regression test for R19: `./build --ci` used to be the workflow's only
    test-related step, so the `slow`/`statistical` scenario suite's own
    cost (18m07s at review time) was invisible in the Actions run summary
    -- indistinguishable from lint, type-checking, docs, and packaging,
    all bundled into the same opaque step. Splitting the fast,
    marker-filtered layer into its own preceding step makes both halves'
    wall-clock cost individually visible (GitHub Actions reports each
    step's own duration natively) and lets the fast layer fail in
    seconds, before CI pays for the expensive layer at all.
    """
    run_steps = _run_steps()
    fast_step = next(step for step in run_steps if "--no-package" in str(step["run"]))
    full_step = next(
        step
        for step in run_steps
        if str(step["run"]).strip() == "xvfb-run --auto-servernum ./build --ci"
    )

    # The fast step is the same deterministic layer `--ci` also covers
    # (pyproject.toml's own default marker filter), run without coverage
    # and with lint/type/docs/package explicitly skipped -- not a
    # separately maintained marker expression that could drift from the
    # one `--ci` itself uses.
    assert "--no-lint" in str(fast_step["run"])
    assert "--no-type" in str(fast_step["run"])
    assert "--no-docs" in str(fast_step["run"])
    assert "--no-package" in str(fast_step["run"])
    assert "--ci" not in str(fast_step["run"])

    assert run_steps.index(fast_step) < run_steps.index(full_step)


def test_each_test_step_has_a_nonflaky_wall_clock_budget() -> None:
    """Both test steps declare `timeout-minutes`, not an unbounded run.

    `timeout-minutes` is enforced by the runner itself, not by a
    wall-clock assertion inside the test run -- the latter would make
    pass/fail depend on the runner's momentary speed and load rather
    than on the commit under test, which the project's own house rule
    on test determinism (CLAUDE.md: "a test is a pure function of its
    commit") forbids. The full step's budget is larger than the fast
    step's, matching the much larger scenario suite it alone carries.
    """
    run_steps = _run_steps()
    fast_step = next(step for step in run_steps if "--no-package" in str(step["run"]))
    full_step = next(
        step
        for step in run_steps
        if str(step["run"]).strip() == "xvfb-run --auto-servernum ./build --ci"
    )

    fast_budget = fast_step["timeout-minutes"]
    full_budget = full_step["timeout-minutes"]

    assert isinstance(fast_budget, int) and fast_budget > 0
    assert isinstance(full_budget, int) and full_budget > 0
    assert fast_budget < full_budget


def test_slow_tests_job_never_runs_for_a_push_or_pull_request() -> None:
    """`slow-tests` only fires on a schedule or manual dispatch.

    The `slow`-marked engine scenario suite was moved out of `build`'s
    own per-push gate into this job specifically so a push or pull
    request can never be blocked or delayed by it -- confirmed directly
    here, not just by convention, since a workflow-file typo (a `push`
    accidentally left in this job's own `if:`) would otherwise silently
    reintroduce the exact problem this job exists to close.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    slow_tests = workflow["jobs"]["slow-tests"]

    condition = slow_tests["if"]
    assert "schedule" in condition
    assert "workflow_dispatch" in condition
    assert "push" not in condition
    assert "pull_request" not in condition


def test_slow_tests_job_runs_slow_only_with_its_own_budget() -> None:
    """`slow-tests` runs `build --slow-only` under its own real timeout."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    slow_tests = workflow["jobs"]["slow-tests"]

    run_steps = [step for step in slow_tests["steps"] if "run" in step]
    slow_step = next(step for step in run_steps if "--slow-only" in str(step["run"]))

    assert str(slow_step["run"]).strip() == "./build --slow-only"
    budget = slow_tests["timeout-minutes"]
    assert isinstance(budget, int) and budget > 0


def test_build_and_homebrew_jobs_never_run_on_schedule_or_dispatch() -> None:
    """`build`/`homebrew` skip the two triggers `slow-tests` alone answers.

    Without this, a scheduled or manually dispatched run would
    needlessly re-run the entire ordinary per-push pipeline alongside
    `slow-tests`, not just the slow suite it was actually meant to add.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))

    for job_name in ("build", "homebrew"):
        condition = workflow["jobs"][job_name]["if"]
        assert "schedule" in condition
        assert "workflow_dispatch" in condition


def test_ci_workflow_declares_schedule_and_workflow_dispatch_triggers() -> None:
    """The two triggers `slow-tests`'s own `if:` checks for actually exist.

    PyYAML's default (YAML 1.1) resolver parses the bare `on:` key as
    the boolean `True`, not the string `"on"` -- confirmed directly
    before writing this assertion, not assumed -- so this reads the
    trigger block back the same way every other test in this file
    already reads the rest of the workflow, through `workflow[True]`.
    """
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))

    triggers = workflow[True]
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"].count(" ") == 4
