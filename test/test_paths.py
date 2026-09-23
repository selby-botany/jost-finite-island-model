"""Unit tests for shared project-root, results-directory, and
atomic-publish resolution."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import fim
from fim import paths


def test_project_root_falls_back_to_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed and frozen applications never write inside their package.

    Regression test carried over from `test/cli/test_cli.py` (Milestone
    G0, `doc/fim-gui-design.md` §12):
    `fim.paths.project_root` is anchored on the `fim` package's own
    `__init__.py` (`fim.__file__`) rather than the caller's own module
    file, so every caller — the CLI, the GUI, or any future front end —
    resolves the same root through one shared function. This test
    therefore patches `fim.__file__`, not `fim.cli.__file__` as it did
    before the extraction.
    """
    working_directory = tmp_path / "working"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(
        fim,
        "__file__",
        str(tmp_path / "installed" / "site-packages" / "fim" / "__init__.py"),
    )

    assert paths.project_root() == working_directory


def test_project_root_falls_back_to_home_when_frozen_and_uninstalled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A packaged GUI never inherits `cwd()` -- it has no real one.

    Regression test for the real "[Errno 30] Read-only file system:
    '/results'" failure hit on first GUI use: a Finder-launched macOS
    `.app` starts with `cwd() == "/"`, so the pre-fix fallback built an
    unwritable default results directory straight from the filesystem
    root. `sys.frozen` (PyInstaller's own flag, already checked
    elsewhere in this codebase -- `fim.launcher`, `fim.gui.app.
    _webui_directory`) is the signal used here instead of trusting
    whatever the OS happened to set `cwd()` to.

    `cwd()` is deliberately set to somewhere *other* than home, and
    still must not appear in the result -- proving the frozen branch
    really ignores it rather than merely happening to agree with it.
    """
    home_directory = tmp_path / "home"
    home_directory.mkdir()
    working_directory = tmp_path / "elsewhere"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(Path, "home", lambda: home_directory)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        fim,
        "__file__",
        str(tmp_path / "fim.app" / "Contents" / "Frameworks" / "fim" / "__init__.py"),
    )

    assert paths.project_root() == home_directory / "fim"


def test_project_root_finds_the_real_checkout() -> None:
    """A real checkout resolves to the directory containing pyproject.toml."""
    assert (paths.project_root() / "pyproject.toml").is_file()


def test_project_root_honors_the_root_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`set_root_override` wins over every other case `project_root` checks."""
    monkeypatch.setattr(paths, "_root_override", tmp_path)

    assert paths.project_root() == tmp_path


def test_project_root_honors_fim_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FIM_HOME` wins over the real-checkout/frozen/cwd fallback chain."""
    monkeypatch.setenv("FIM_HOME", str(tmp_path))

    assert paths.project_root() == tmp_path


def test_project_root_override_wins_over_fim_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The programmatic override outranks the environment variable."""
    monkeypatch.setenv("FIM_HOME", str(tmp_path / "from-env"))
    monkeypatch.setattr(paths, "_root_override", tmp_path / "from-override")

    assert paths.project_root() == tmp_path / "from-override"


def test_set_root_override_round_trips_through_the_getter(tmp_path: Path) -> None:
    """`set_root_override`/`root_override` is a plain setter/getter pair."""
    assert paths.root_override() is None
    try:
        paths.set_root_override(tmp_path)
        assert paths.root_override() == tmp_path
    finally:
        paths.set_root_override(None)
    assert paths.root_override() is None


def test_results_directory_defaults_to_project_root_slash_results() -> None:
    """`results_directory` appends `results` to the resolved project root."""
    assert paths.results_directory() == paths.project_root() / "results"


def test_results_directory_accepts_a_root_override(tmp_path: Path) -> None:
    """An explicit root bypasses `project_root` entirely."""
    assert paths.results_directory(tmp_path) == tmp_path / "results"


def test_results_directory_honors_its_own_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`set_results_directory_override` returns the path directly, unjoined."""
    monkeypatch.setattr(paths, "_results_directory_override", tmp_path)

    assert paths.results_directory() == tmp_path


def test_results_directory_honors_its_own_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FIM_RESULTS_DIRECTORY` returns the path directly, unjoined."""
    monkeypatch.setenv("FIM_RESULTS_DIRECTORY", str(tmp_path))

    assert paths.results_directory() == tmp_path


def test_results_directory_override_wins_over_its_own_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The programmatic override outranks the location's own environment variable."""
    monkeypatch.setenv("FIM_RESULTS_DIRECTORY", str(tmp_path / "from-env"))
    monkeypatch.setattr(
        paths, "_results_directory_override", tmp_path / "from-override"
    )

    assert paths.results_directory() == tmp_path / "from-override"


def test_results_directory_explicit_root_wins_over_its_own_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `root` argument still outranks the results-specific override."""
    monkeypatch.setattr(paths, "_results_directory_override", tmp_path / "override")

    assert paths.results_directory(tmp_path / "root") == tmp_path / "root" / "results"


def test_results_directory_own_override_wins_over_the_bulk_root_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A location-specific override outranks the bulk `FIM_HOME`-style root override."""
    monkeypatch.setattr(paths, "_root_override", tmp_path / "bulk-root")
    monkeypatch.setattr(paths, "_results_directory_override", tmp_path / "specific")

    assert paths.results_directory() == tmp_path / "specific"


def test_fim_index_directory_defaults_to_results_directory_slash_dot_fim() -> None:
    """`fim_index_directory` sits inside `results_directory()`, not beside it."""
    assert paths.fim_index_directory() == paths.results_directory() / ".fim"


def test_fim_index_directory_accepts_a_results_override(tmp_path: Path) -> None:
    """An explicit results override bypasses `results_directory` entirely."""
    assert paths.fim_index_directory(tmp_path) == tmp_path / ".fim"


def test_studies_directory_sits_under_the_fim_index_directory(tmp_path: Path) -> None:
    """`studies_directory` is `fim_index_directory() / "studies"`."""
    assert paths.studies_directory(tmp_path) == tmp_path / ".fim" / "studies"


def test_experiments_directory_sits_under_the_fim_index_directory(
    tmp_path: Path,
) -> None:
    """`experiments_directory` is `fim_index_directory() / "experiments"`."""
    assert paths.experiments_directory(tmp_path) == tmp_path / ".fim" / "experiments"


def test_default_output_directory_uses_microsecond_timestamp(
    tmp_path: Path,
) -> None:
    """Default output names include microseconds to prevent same-second collisions."""

    def clock() -> datetime:
        return datetime(2026, 8, 27, 14, 22, 5, tzinfo=UTC)

    output = paths.default_output_directory(tmp_path, clock=clock)

    assert output == tmp_path / "run-20260827-142205-000000"


def test_default_output_directory_retries_existing_timestamped_name(
    tmp_path: Path,
) -> None:
    """An existing automatic name receives a deterministic numeric suffix."""

    def clock() -> datetime:
        return datetime(2026, 8, 27, 14, 22, 5, tzinfo=UTC)

    (tmp_path / "run-20260827-142205-000000").mkdir()

    assert paths.default_output_directory(tmp_path, clock=clock) == (
        tmp_path / "run-20260827-142205-000000-001"
    )


def test_default_output_directory_uses_results_directory_by_default() -> None:
    """Omitting `results` falls back to `results_directory()`."""
    output = paths.default_output_directory()
    assert output.parent == paths.results_directory()
    assert output.name.startswith("run-")


def test_log_directory_defaults_to_project_root_slash_logs() -> None:
    """`log_directory` appends `logs` to the resolved project root, beside `results`."""
    assert paths.log_directory() == paths.project_root() / "logs"


def test_log_directory_accepts_a_root_override(tmp_path: Path) -> None:
    """An explicit root bypasses `project_root` entirely."""
    assert paths.log_directory(tmp_path) == tmp_path / "logs"


def test_log_directory_honors_its_own_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`set_log_directory_override` returns the path directly, unjoined."""
    monkeypatch.setattr(paths, "_log_directory_override", tmp_path)

    assert paths.log_directory() == tmp_path


def test_log_directory_honors_its_own_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FIM_LOG_DIRECTORY` returns the path directly, unjoined."""
    monkeypatch.setenv("FIM_LOG_DIRECTORY", str(tmp_path))

    assert paths.log_directory() == tmp_path


def test_log_directory_override_wins_over_its_own_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The programmatic override outranks the location's own environment variable."""
    monkeypatch.setenv("FIM_LOG_DIRECTORY", str(tmp_path / "from-env"))
    monkeypatch.setattr(paths, "_log_directory_override", tmp_path / "from-override")

    assert paths.log_directory() == tmp_path / "from-override"


def test_log_directory_explicit_root_wins_over_its_own_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `root` argument still outranks the log-specific override."""
    monkeypatch.setattr(paths, "_log_directory_override", tmp_path / "override")

    assert paths.log_directory(tmp_path / "root") == tmp_path / "root" / "logs"


def test_default_log_file_is_fim_log_under_the_log_directory(tmp_path: Path) -> None:
    """`default_log_file` names `fim.log` inside `log_directory`."""
    assert paths.default_log_file(tmp_path) == tmp_path / "logs" / "fim.log"


def test_atomic_directory_rejects_an_existing_target(tmp_path: Path) -> None:
    """A pre-existing target is refused outright, regardless of its contents.

    Regression proof for Milestone G0 (`doc/fim-gui-design.md` §7.3): the
    relocated `atomic_directory` still requires the final path not to
    exist at all — the same stricter-than-filename-checking contract
    `cli._atomic_directory` established, reproduced here directly
    against `fim.paths` rather than only indirectly through
    `test/cli/test_cli.py`'s `cli.main(["run", ...])` integration tests
    (which keep passing unmodified, since the behavior itself did not
    change).
    """
    target = tmp_path / "output"
    target.mkdir()

    with (
        pytest.raises(FileExistsError, match="already exists"),
        paths.atomic_directory(target),
    ):
        pass


def test_atomic_directory_publishes_via_one_rename_on_success(
    tmp_path: Path,
) -> None:
    """A successful block's contents land at `target` via a single rename."""
    target = tmp_path / "output"

    with paths.atomic_directory(target) as working_directory:
        assert not target.exists()
        assert working_directory.parent == tmp_path
        (working_directory / "marker.txt").write_text("ok", encoding="utf-8")

    assert (target / "marker.txt").read_text(encoding="utf-8") == "ok"


def test_atomic_directory_discards_the_temporary_directory_on_failure(
    tmp_path: Path,
) -> None:
    """A raised exception leaves neither the target nor an orphaned temp dir."""
    target = tmp_path / "output"

    with (
        pytest.raises(RuntimeError, match="simulated failure"),
        paths.atomic_directory(target) as working_directory,
    ):
        (working_directory / "partial.txt").write_text("x", encoding="utf-8")
        raise RuntimeError("simulated failure")

    assert not target.exists()
    assert not list(tmp_path.glob(".output.*"))


class _RefusingSource:
    """A stand-in for `Path` whose `replace` refuses a fixed number of times.

    Models Windows refusing to rename over a file another thread has
    open (`[WinError 5] Access is denied`), deterministically: no real
    lock, no timing.
    """

    def __init__(self, refusals: int) -> None:
        self.refusals = refusals
        self.calls = 0

    def replace(self, _target: Path) -> None:
        self.calls += 1
        if self.calls <= self.refusals:
            raise PermissionError(5, "Access is denied")


def test_replace_with_retry_rides_out_transient_permission_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rename refused a few times, then allowed, succeeds."""
    monkeypatch.setattr("fim.paths.time.sleep", lambda _seconds: None)
    source = _RefusingSource(refusals=3)

    paths.replace_with_retry(source, tmp_path / "target")  # type: ignore[arg-type]

    assert source.calls == 4


def test_replace_with_retry_gives_up_after_a_bounded_number_of_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A target that stays locked raises the original error, not a hang."""
    monkeypatch.setattr("fim.paths.time.sleep", lambda _seconds: None)
    source = _RefusingSource(refusals=10**6)

    with pytest.raises(PermissionError):
        paths.replace_with_retry(source, tmp_path / "target")  # type: ignore[arg-type]

    assert source.calls == 10


def test_replace_with_retry_really_replaces_a_file(tmp_path: Path) -> None:
    """The happy path on a real filesystem: content is swapped in."""
    target = tmp_path / "target"
    target.write_text("old", encoding="utf-8")
    source = tmp_path / "source"
    source.write_text("new", encoding="utf-8")

    paths.replace_with_retry(source, target)

    assert target.read_text(encoding="utf-8") == "new"
    assert not source.exists()
