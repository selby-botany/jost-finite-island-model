"""Project-root, results-directory, and atomic-publish logic, shared by
every front end.

Extracted from `fim.cli` (design doc `20260819-claude-sonnet-5-graphical-
interface.md` §3.7) so `fim.gui`'s run orchestration resolves the exact same
`project-root/results/` layout, timestamped default folder naming, and
atomic-publish-or-nothing guarantee as `fim run`, rather than a second,
independently maintained copy of this logic. `project_root` is anchored on
the `fim` package's own `__init__.py` (`fim.__file__`), not the calling
module's `__file__`: every caller under `src/fim/`, regardless of how deep
it sits (`fim/cli.py`, `fim/gui/runner.py`, ...), resolves the identical
root this way, where anchoring on each caller's own `__file__` would need a
different `parents[N]` depth per caller and silently break the moment a new
caller sat at a different depth.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import fim

Clock = Callable[[], datetime]


@contextlib.contextmanager
def atomic_directory(target: Path) -> Iterator[Path]:
    """Build a directory's contents in a hidden temporary sibling, then
    publish it at `target` with one atomic rename.

    Regression fix for R7 (`cli.py`'s own history, predating this
    extraction): an interrupted run used to leave a partial output
    directory that was silently indistinguishable from a complete one.
    Every write inside the `with` block happens in a temporary sibling
    of `target` — on the same filesystem, since it is always created
    directly inside `target.parent`, which guarantees the final publish
    is a single atomic rename rather than a copy. If the block raises
    anything, the temporary directory is discarded and `target` is left
    completely untouched. `target` therefore either does not exist yet
    or exists complete; there is no third, partial state to observe
    from outside this function against process-level interruption — an
    uncaught exception, `^C`, or `kill -9` all skip the `except` cleanup
    but still cannot leave anything at `target` itself, only an
    orphaned temporary directory beside it.

    This guarantee is about the rename, not about physical durability
    (S11): nothing in this function calls `fsync`, so on an unclean
    power loss, a filesystem is free to have recorded the rename's
    metadata before every byte written into the temporary directory
    actually reached disk — a `target` that survives such an event can
    exist, look complete, and still contain corrupted or truncated file
    content. Treat this function's guarantee as "no partial directory
    is ever observable," not "every observed directory survived a
    power failure intact."

    Args:
        target: The directory's final path. Must not already exist.

    Yields:
        The temporary directory to build the run's output inside.

    Raises:
        FileExistsError: If `target` already exists.
    """
    if target.exists():
        raise FileExistsError(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    working_directory = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
    )
    try:
        yield working_directory
    except BaseException:
        shutil.rmtree(working_directory, ignore_errors=True)
        raise
    else:
        working_directory.replace(target)


def default_output_directory(
    results: Path | None = None,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> Path:
    """Return a timestamped output folder without affecting run data.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).
        clock: Injectable UTC clock, for deterministic tests.

    Returns:
        `results / f"run-{timestamp}"`. The timestamp names the folder
        only; it never enters any persisted scientific value.
    """
    base = results if results is not None else results_directory()
    stamp = clock().strftime("%Y%m%d-%H%M%S")
    return base / f"run-{stamp}"


def project_root() -> Path:
    """Return the source checkout root, falling back to a writable default.

    Returns:
        The checkout root containing `pyproject.toml`, if one is found
        above the installed `fim` package; otherwise `Path.home() / "fim"`
        for a packaged (`sys.frozen`) build, or the current working
        directory for a plain `pip install` run from a terminal.

        The frozen case cannot fall back to `Path.cwd()`: a packaged GUI
        has no terminal, and therefore no user-chosen working directory
        to inherit — the OS picks one instead, and on macOS a
        Finder-launched `.app` gets `cwd() == "/"`, the read-only
        filesystem root. `results_directory()` built straight from that
        (`/results`) failed outright with "[Errno 30] Read-only file
        system" on first real GUI use. A frozen CLI invocation (the same
        binary run from an actual terminal) loses nothing here either:
        every documented `fim run` example passes `--output` explicitly,
        never relying on this default. `Path.cwd()` remains correct for
        the non-frozen, no-checkout case (`pip install fim` run from a
        terminal): there, cwd is a real, user-chosen directory.
    """
    source_root = Path(fim.__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    if getattr(sys, "frozen", False):
        return Path.home() / "fim"
    return Path.cwd()


def results_directory(root: Path | None = None) -> Path:
    """Return the project-local results directory.

    Args:
        root: Optional project root override (default: `project_root()`).

    Returns:
        `root / "results"`.
    """
    return (root if root is not None else project_root()) / "results"
