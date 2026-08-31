"""Project-root, results-directory, and atomic-publish logic, shared by
every front end.

This is where every part of `fim` (the command line, the desktop app,
every test) goes to answer three small but easy-to-get-wrong questions,
so each one is answered exactly once, the same way everywhere, rather
than reinvented slightly differently in each front end:

1. "Where does this project actually live on disk?" (`project_root`) —
   needed to find a sensible default place to write output, without
   requiring every command to be told an explicit path every time.
2. "Where should a run's output go if the user did not name a specific
   folder?" (`default_output_directory`, `results_directory`) — a
   single, predictable `results/` folder under the project root, with
   each unnamed run getting its own timestamped subfolder so two runs
   never collide by writing into the same place.
3. "How do we write a whole folder's worth of output files without ever
   leaving a half-written, broken folder behind if something goes wrong
   partway through?" (`atomic_directory`) — see that function's own
   docstring for the answer.

Extracted from `fim.cli` (`doc/fim-gui-design.md` §12) so `fim.gui`'s
run orchestration resolves the exact same `project-root/results/`
layout, timestamped default folder naming, and atomic-publish-or-nothing
guarantee as `fim run`, rather than a second, independently maintained
copy of this logic. `project_root` is anchored on
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

    The problem this solves: a simulation run writes several files
    (trajectory data, a report, a plot, a manifest) into its own output
    folder over the course of running, which can take anywhere from a
    second to hours. If that run is interrupted partway through — the
    process crashes, the computer loses power, or a person simply
    presses Ctrl-C — writing those files *directly* into the final
    folder would leave behind a folder that looks like a real,
    completed run's output (it exists, it has some files in it) but is
    actually missing whatever had not been written yet. Nothing about
    that folder's own name or existence would reveal it was actually
    incomplete — a real bug found in `cli.py` before this function was
    extracted from it, and one this function exists specifically to
    prevent.

    The fix follows the same idea a careful editor uses when saving a
    long document: write the whole new version to a *different* file
    first, and only once it is completely finished, replace the old file
    with the new one in a single step — never leaving a moment where the
    file exists but is only half-written. Concretely: every write inside
    the `with` block happens in a hidden temporary folder next to
    `target` (a dot-prefixed sibling, created directly inside
    `target.parent`, on the very same filesystem — required so the
    final step below can be one atomic rename rather than a slower,
    interruptible copy). "Atomic" here means the same thing it means in
    everyday English: indivisible — from the perspective of anything
    else looking at the filesystem, that rename either has not happened
    yet (nothing at `target`) or has completely finished (everything at
    `target`); there is no in-between moment where `target` exists but
    only holds some of the files. If the code inside the `with` block
    raises anything at all — an ordinary exception, `^C` from the
    keyboard, or the process being killed outright — the temporary
    folder is discarded and `target` is left completely untouched, never
    created in a broken state. `target` therefore either does not exist
    yet or exists fully complete; there is no third, partial state ever
    observable from outside this function.

    This guarantee is about the *rename* being all-or-nothing, not about
    surviving a total loss of power (a separate, harder guarantee this
    function does not attempt — internal tracking reference S11): nothing
    here calls `fsync` (the low-level operation that would force every
    written byte all the way out to physical disk before continuing), so
    on an actual, unclean power loss, the operating system and disk are
    still free to have recorded the rename itself before every byte
    written into the temporary folder had physically reached the disk —
    meaning a `target` that survives such an event can exist, look
    complete, and still contain corrupted or truncated file content in
    that specific, narrow scenario. Read this function's guarantee as
    "no half-written folder is ever visible to look at," not "every
    folder this function ever produced is guaranteed to have survived a
    power failure with perfect data integrity."

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
    try:
        # Hand the temporary folder to the caller's own `with` block —
        # everything the caller writes during that block goes here, not
        # to `target` itself.
        yield working_directory
    except BaseException:
        # The caller's own code raised something -- discard everything
        # written so far and let the same exception continue propagating
        # unchanged (`raise` with no argument re-raises exactly what was
        # caught, rather than wrapping or replacing it), so a caller
        # further up still sees the real, original error.
        shutil.rmtree(working_directory, ignore_errors=True)
        raise
    else:
        # The `with` block finished without raising -- publish it. This
        # is the one atomic step this whole function exists to make
        # possible: from this point on, `target` either did not exist
        # (a moment ago) or exists fully complete (now), with no
        # observable moment in between.
        working_directory.replace(target)


def default_output_directory(
    results: Path | None = None,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> Path:
    """Return a timestamped output folder without affecting run data.

    Called whenever a run is started without the caller naming a
    specific output folder — `fim run` with no `--output`, or the
    desktop app's own default. Two different, unnamed runs started at
    different times get two different folders this way (each one's own
    start time, encoded into the folder's name), so they can never
    collide by both trying to write into the exact same place.

    Args:
        results: Optional results-directory override (default:
            `results_directory()`).
        clock: Injectable UTC clock, for deterministic tests — a real
            caller never supplies this; only a test that wants to check
            the exact folder name a specific, fixed time would produce
            needs to.

    Returns:
        `results / f"run-{timestamp}"`. The timestamp names the folder
        only; it never enters any persisted scientific value — two runs
        with the exact same configuration and seed still produce
        identical scientific results regardless of which folder name
        each one happened to land in (see `fim.engine`'s own docstring
        for why that determinism matters).
    """
    base = results if results is not None else results_directory()
    stamp = clock().strftime("%Y%m%d-%H%M%S")
    return base / f"run-{stamp}"


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

    Args:
        root: Optional project root override (default: `project_root()`).

    Returns:
        `root / "results"`.
    """
    return (root if root is not None else project_root()) / "results"
