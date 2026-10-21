"""Resolve project, results, and log locations and publish output atomically.

The default results and log directories are relative to the project root.
Environment variables and explicit overrides can select other locations.
`atomic_directory` stages a complete output tree before publishing it so an
incomplete run directory is not exposed. See `doc/configuration.md` for path
configuration.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import fim

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)

# Module-level, settable overrides -- `20260918-claude-sonnet-5-
# configurable-storage-root-design.md` (`selby/restricted`). Each is
# `None` (meaning "no override") until one of the three real callers
# (`fim.cli`'s own `--root`/`--results-directory`/`--log-directory`
# flags, `fim.gui.app.main`'s own identical flags, or a GUI Settings
# save) sets it via the matching `set_*_override` function below, once,
# early in that entry point's own startup — the same "one thing,
# decided once, read from several unrelated places" shape `fim.gui.app`
# already uses for `_active_window`/`_cancel_event`, rather than a
# `root`/`results` parameter threaded through every caller of
# `project_root`/`results_directory`/`log_directory` that never asked
# for one. A test sets one of these directly (`monkeypatch.setattr
# (paths, "_root_override", tmp_path)`), which — unlike calling the
# public setter — pytest's own `monkeypatch` restores automatically at
# teardown, with no matching `set_*_override(None)` cleanup call needed.
_root_override: Path | None = None
_results_directory_override: Path | None = None
_log_directory_override: Path | None = None


def set_root_override(root: Path | None) -> None:
    """Override `project_root()`'s own resolution, or clear a previous one.

    The *bulk* override — "put results and logs both under this one
    place" — checked by `project_root()` itself, so it affects
    `results_directory()`/`log_directory()` (and everything derived
    from either) for free, with no code of its own. A more specific
    override (`set_results_directory_override`/`set_log_directory_
    override`, or that location's own environment variable) still wins
    over this one — see each function's own docstring.

    Args:
        root: The path to use in place of `project_root()`'s own
            three-case resolution, or `None` to remove the override.
    """
    global _root_override  # noqa: PLW0603
    _root_override = root


def root_override() -> Path | None:
    """The current `project_root()` override, if any set via `set_root_override`."""
    return _root_override


def set_results_directory_override(path: Path | None) -> None:
    """Override `results_directory()`'s own resolution, or clear a previous one.

    Args:
        path: The exact directory to use in place of `results_
            directory()`'s own computation, or `None` to remove the
            override. Unlike `set_root_override`, this is the results
            directory itself, not a project root to append `"results"`
            to — a user naming this specific location means exactly
            that location.
    """
    global _results_directory_override  # noqa: PLW0603
    _results_directory_override = path


def results_directory_override() -> Path | None:
    """The current `results_directory()` override, if any."""
    return _results_directory_override


def set_log_directory_override(path: Path | None) -> None:
    """Override `log_directory()`'s own resolution, or clear a previous one.

    Args:
        path: The exact directory to use in place of `log_directory()`'s
            own computation, or `None` to remove the override — the
            same "the location itself, not a root to append `logs` to"
            shape `set_results_directory_override` already documents.
    """
    global _log_directory_override  # noqa: PLW0603
    _log_directory_override = path


def log_directory_override() -> Path | None:
    """The current `log_directory()` override, if any."""
    return _log_directory_override


def _read_umask() -> int:
    """Return the process umask without leaving it changed.

    `os.umask` can only be read by setting it, so this sets it to zero and
    puts the old value straight back. It runs once, at import, before the
    program starts threads of its own.
    """
    current = os.umask(0)
    os.umask(current)
    return current


_UMASK = _read_umask()
"""The umask in force when `fim.paths` was imported."""


def _release_owner_lock(owner: contextlib.ExitStack, owner_lock: Path) -> None:
    """Delete `atomic_directory`'s owner lock so it is never published.

    On POSIX the file is unlinked while still locked: a reclaimer then
    either finds it held or finds it gone, and never deletes a staging
    directory about to be renamed into place. Windows cannot delete a
    locked file, so there it is released first; reclaiming then needs a
    build that outlived the grace period and a reclaim that lands in the
    instant before the rename.

    Args:
        owner: Holds the owner lock.
        owner_lock: The owner lock file.

    Returns:
        None; the lock is released and its file removed.
    """
    if sys.platform == "win32":
        owner.close()
        owner_lock.unlink(missing_ok=True)
    else:
        owner_lock.unlink(missing_ok=True)
        owner.close()


def default_file_mode() -> int:
    """Return the permission bits an ordinary new file gets (usually `0o644`).

    `tempfile.mkstemp` deliberately creates files private to the owner
    (`0o600`), which is right for a scratch file and wrong for the results
    and index files a botanist's other tools, backups and colleagues read.
    """
    return 0o666 & ~_UMASK


def default_directory_mode() -> int:
    """Return the permission bits an ordinary new directory gets (usually `0o755`)."""
    return 0o777 & ~_UMASK


def write_text_atomically(path: Path, text: str, *, prefix: str) -> None:
    """Write `text` to `path` so a reader sees the old file or the new one, never half.

    The text goes to a temporary file in the same directory, which is given
    the ordinary permissions a new file would have had (not the owner-only
    ones `mkstemp` picks), then replaces `path` in one rename. Shared by the
    Study and Experiment indexes, run metadata, the progress sidecar and the
    preferences file, which each used to repeat this idiom and each ended up
    unreadable to anyone but the owner.

    Args:
        path: The file to write. Its parent directory is created if needed.
        text: The complete new contents.
        prefix: Prefix for the temporary file's name, which shows what was
            being written if a crash leaves one behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=prefix)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(text)
        temp_path.chmod(default_file_mode())
        replace_with_retry(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


# Bounded, fixed retry for `replace_with_retry`: 10 attempts 20 ms apart,
# so a genuinely stuck target fails after about 0.2 s rather than hanging.
_REPLACE_ATTEMPTS = 10
_REPLACE_RETRY_DELAY_SECONDS = 0.02


def replace_with_retry(source: Path, target: Path) -> None:
    """Rename `source` over `target`, retrying briefly on `PermissionError`.

    `Path.replace` is atomic on POSIX regardless of who has `target`
    open. On Windows it is not: Python opens files without
    `FILE_SHARE_DELETE`, so replacing a file that another thread or
    process (the GUI polling a replicate's `.progress` sidecar, an
    antivirus scanner, the search indexer) has open at that instant
    raises `PermissionError` (`[WinError 5] Access is denied`). That is
    a transient collision, not a real refusal -- the reader closes the
    file within microseconds -- and it killed a 150-replicate batch on
    a botanist's first Windows run. Retrying a fixed, small number of
    times with a fixed delay rides it out; a target that stays locked
    for the whole window still raises the original error.

    Args:
        source: The completed temporary file (or directory) to publish.
        target: The path to replace.

    Raises:
        PermissionError: If every attempt was refused.
    """
    for attempt in range(1, _REPLACE_ATTEMPTS + 1):
        try:
            source.replace(target)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SECONDS)
        else:
            return


STAGING_LOCK_FILENAME = ".fim-staging.lock"
"""The owner lock `atomic_directory` holds inside its staging directory."""


@contextlib.contextmanager
def atomic_directory(target: Path) -> Iterator[Path]:
    """Build a directory in a hidden sibling, then publish it atomically.

    The staging directory is created beside `target` on the same
    filesystem. Successful exit renames it to `target`; an exception in
    the context body removes the staging directory. Termination without
    cleanup may leave staging behind. The rename prevents readers from
    seeing a partially populated target, but no `fsync` is performed,
    so this does not guarantee data durability after power loss.

    Args:
        target: The directory's final path. Must not already exist.

    Yields:
        The temporary directory to build the run's output inside.

    Raises:
        FileExistsError: If `target` already exists.
    """
    if target.exists():
        # Refuse up front rather than silently overwriting: a caller
        # that already has a real, completed run sitting at `target`
        # almost certainly does not want it quietly replaced.
        raise FileExistsError(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # The leading "." makes this a hidden folder on every platform this
    # project supports (Unix-like systems and Windows Explorer alike
    # treat a dot-prefixed name as hidden by convention), so a person
    # browsing the results folder while a run is still in progress does
    # not see what looks like a second, mysterious, incomplete run sitting
    # next to the real one.
    working_directory = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
    )
    # `mkdtemp` makes the folder private to its owner; a published run
    # folder should be as readable as any other folder the user creates.
    working_directory.chmod(default_directory_mode())
    logger.debug("building %s in temporary directory %s", target, working_directory)
    # Proof of life for orphan reclamation (`fim.persistence.staging`):
    # held while the caller builds, then removed so it is never published.
    from fim.persistence.object_lock import exclusive_lock  # noqa: PLC0415

    owner_lock = working_directory / STAGING_LOCK_FILENAME
    owner = contextlib.ExitStack()
    try:
        owner.enter_context(exclusive_lock(owner_lock, blocking=False))
        # Hand the temporary folder to the caller's own `with` block —
        # everything the caller writes during that block goes here, not
        # to `target` itself.
        yield working_directory
    except BaseException as error:
        # The caller's own code raised something -- discard everything
        # written so far and let the same exception continue propagating
        # unchanged (`raise` with no argument re-raises exactly what was
        # caught, rather than wrapping or replacing it), so a caller
        # further up still sees the real, original error.
        owner.close()
        logger.warning(
            "rolling back %s after %s: %s",
            target,
            type(error).__name__,
            error,
        )
        shutil.rmtree(working_directory, ignore_errors=True)
        raise
    else:
        # The `with` block finished without raising -- publish it. This
        # is the one atomic step this whole function exists to make
        # possible: from this point on, `target` either did not exist
        # (a moment ago) or exists fully complete (now), with no
        # observable moment in between.
        _release_owner_lock(owner, owner_lock)
        replace_with_retry(working_directory, target)
        logger.info("published %s", target)


def default_output_directory(
    results: Path | None = None,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> Path:
    """Return a collision-resistant timestamped output folder.

    Called whenever a run is started without the caller naming a
    specific output folder — `fim run` with no `--output`, or the
    desktop app's own default. Two different, unnamed runs started at
    different times get different folders. The microsecond timestamp avoids
    ordinary same-second collisions; a bounded numeric suffix resolves an
    existing name without replacing any run data.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).
        clock: Injectable UTC clock, for deterministic tests — a real
            caller never supplies this; only a test that wants to check
            the exact folder name a specific, fixed time would produce
            needs to.

    Returns:
        A non-existing path below `results`. The name never enters a
        persisted scientific value, so equivalent seeded runs remain
        scientifically identical regardless of their folder names.

    Raises:
        FileExistsError: If all bounded fallback names already exist.
    """
    base = results if results is not None else results_directory()
    stem = f"run-{clock().strftime('%Y%m%d-%H%M%S-%f')}"
    for suffix in range(1000):
        candidate = base / (stem if suffix == 0 else f"{stem}-{suffix:03d}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"could not allocate a unique output directory below: {base}")


def allocate_output_directory(
    results: Path | None = None,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> tuple[Path, Path]:
    """Reserve and return a unique `(output_directory, reservation)` pair.

    The output directory remains absent until its run is published. A
    neighboring hidden marker directory reserves its name in the meantime,
    preventing concurrent callers from receiving the same path. The caller
    must remove the reservation after publication or failure.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).
        clock: Injectable UTC clock for deterministic tests.

    Returns:
        The not-yet-created output directory and its reservation marker.

    Raises:
        FileExistsError: If all bounded fallback names are already used or
            reserved.
    """
    base = results if results is not None else results_directory()
    stem = f"run-{clock().strftime('%Y%m%d-%H%M%S-%f')}"
    for suffix in range(1000):
        name = stem if suffix == 0 else f"{stem}-{suffix:03d}"
        candidate = base / name
        if candidate.exists():
            continue
        reservation = base / f".reserve-{name}"
        try:
            reservation.mkdir(parents=True)
        except FileExistsError:
            continue
        return candidate, reservation
    raise FileExistsError(f"could not allocate a unique output directory below: {base}")


def project_root() -> Path:
    """Return the source checkout root, falling back to a writable default.

    Answers "where should output go by default, if nobody said
    otherwise" — see this module's own docstring, above. There are three
    genuinely different situations this has to handle, because `fim`
    itself can be run in three different ways, and "the project" means
    something different in each one:

    1. Running from a real, cloned copy of this repository (a
       "checkout") — the common case for anyone actively developing or
       reading the source. Here, "the project" plainly means that
       checkout, identified by walking up from wherever the installed
       `fim` package's own files live until a `pyproject.toml` is
       found — the file that marks the top of this specific project.
    2. Running a **packaged** build — a standalone application built by
       PyInstaller (the tool this project uses to produce a plain
       double-clickable `fim-gui` app, with no separate Python
       installation required), where there is no source checkout on
       disk to find at all. Python itself sets `sys.frozen` to `True`
       specifically to mark this situation, which is what the code below
       checks. Falls back to a `fim` folder inside the user's own home
       directory — a location that is essentially guaranteed to exist
       and be writable, regardless of which operating system or user
       account is running it.
    3. A plain `pip install fim`, run from an ordinary terminal, with no
       checkout and not packaged either — falls back to the current
       working directory, exactly as most ordinary command-line tools
       do.

    Checks `set_root_override`'s own current value first, then the
    `FIM_HOME` environment variable, before any of the three cases
    below — `20260918-claude-sonnet-5-configurable-storage-root-
    design.md` (`selby/restricted`). Both are the *bulk* override,
    lower precedence than `results_directory()`/`log_directory()`'s own
    more specific overrides — see each one's own docstring.

    Returns:
        The checkout root containing `pyproject.toml`, if one is found
        above the installed `fim` package; otherwise `Path.home() / "fim"`
        for a packaged (`sys.frozen`) build, or the current working
        directory for a plain `pip install` run from a terminal.

        The packaged case specifically cannot fall back to `Path.cwd()`
        (the current working directory) the way case 3 above does: a
        packaged desktop app was not launched from a terminal at all, so
        there is no user-chosen working directory for it to inherit —
        the operating system picks some directory on the app's behalf
        instead, and on macOS, an app launched by double-clicking it in
        Finder gets handed `cwd() == "/"`, the very root of the entire
        filesystem, which is read-only on modern macOS. Building
        `results_directory()` straight from that (`/results`) failed
        outright with a real "[Errno 30] Read-only file system" error the
        first time this was tried against an actual packaged build — not
        a hypothetical concern. A packaged *command-line* build (the same
        underlying program, built the same way, but invoked from an
        actual terminal instead of double-clicked) loses nothing from
        using the home-directory fallback either: every documented `fim
        run` example already passes `--output` explicitly, so it never
        relies on this default output location at all.
    """
    if _root_override is not None:
        return _root_override
    home = os.environ.get("FIM_HOME")
    if home:
        return Path(home)
    # `fim.__file__` is the path to this package's own `__init__.py`
    # (see this module's own docstring, above, for why anchoring on this
    # specific file rather than the calling module's own `__file__`
    # matters); `parents[2]` climbs up past `fim/` and `src/` to whatever
    # sits above both, which is the checkout root exactly when one
    # exists at all.
    source_root = Path(fim.__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    if getattr(sys, "frozen", False):
        return Path.home() / "fim"
    return Path.cwd()


def results_directory(root: Path | None = None) -> Path:
    """Return the project-local results directory.

    The one folder every unnamed run's own output lands under (see
    `default_output_directory`, just above, for how each individual
    run then gets its own timestamped subfolder inside this one).

    `root`, when given, is the single highest-precedence override —
    an explicit function argument always wins. Absent that, checks
    `set_results_directory_override`'s own current value, then the
    `FIM_RESULTS_DIRECTORY` environment variable, before falling back
    to `project_root() / "results"` — `20260918-claude-sonnet-5-
    configurable-storage-root-design.md` (`selby/restricted`) §8 spells
    out the full precedence across all three location-specific chains.

    Args:
        root: Optional project root override (default: `project_root()`).

    Returns:
        `root / "results"`, or the current override/`FIM_RESULTS_
        DIRECTORY` directly (not joined with `"results"` — see `set_
        results_directory_override`'s own docstring for why) when
        `root` is not given and one is set.
    """
    if root is not None:
        return root / "results"
    if _results_directory_override is not None:
        return _results_directory_override
    env_value = os.environ.get("FIM_RESULTS_DIRECTORY")
    if env_value:
        return Path(env_value)
    return project_root() / "results"


def log_directory(root: Path | None = None) -> Path:
    """Return the project-local logging directory.

    Sits beside `results_directory()` under the same resolved
    `project_root()` — one root-resolution rule for everything this
    program ever writes, including the frozen-app-with-no-writable-cwd
    fallback `project_root`'s own docstring documents, rather than a
    second, platform-specific rule invented for logs alone
    (`doc/fim-logging-design.md` §6). `root`, `set_log_directory_
    override`, and `FIM_LOG_DIRECTORY` compose in the identical order
    `results_directory`'s own docstring describes, one location over.

    Args:
        root: Optional project root override (default: `project_root()`).

    Returns:
        `root / "logs"`, or the current override/`FIM_LOG_DIRECTORY`
        directly when `root` is not given and one is set.
    """
    if root is not None:
        return root / "logs"
    if _log_directory_override is not None:
        return _log_directory_override
    env_value = os.environ.get("FIM_LOG_DIRECTORY")
    if env_value:
        return Path(env_value)
    return project_root() / "logs"


def default_log_file(root: Path | None = None) -> Path:
    """Return the default operational log file path.

    Args:
        root: Optional project root override (default: `project_root()`).

    Returns:
        `log_directory(root) / "fim.log"` — `fim.logging_setup.configure`'s
        own default `RotatingFileHandler` target unless `-L file=...`
        (or `FIM_LOG_OPTIONS`'s own `file=`) names a different path.
    """
    return log_directory(root) / "fim.log"


def fim_index_directory(results: Path | None = None) -> Path:
    """Return the hidden index directory holding Study/Experiment bookkeeping.

    Sits inside `results_directory()`, not beside it: a Study or
    Experiment is a small, separate JSON file referencing existing run
    directories by name (`20260917-claude-sonnet-5-run-study-experiment-
    hierarchy-design.md`, `selby/restricted`, §2) — never a change to
    where any individual run's own output lives.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).

    Returns:
        `(results or results_directory()) / ".fim"`.
    """
    root = results if results is not None else results_directory()
    return root / ".fim"


def studies_directory(results: Path | None = None) -> Path:
    """Return the directory holding every `StudyManifest` JSON file.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).

    Returns:
        `fim_index_directory(results) / "studies"`.
    """
    return fim_index_directory(results) / "studies"


def experiments_directory(results: Path | None = None) -> Path:
    """Return the directory holding every `ExperimentManifest` JSON file.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).

    Returns:
        `fim_index_directory(results) / "experiments"`.
    """
    return fim_index_directory(results) / "experiments"
