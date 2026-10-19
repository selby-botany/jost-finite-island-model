"""Rewrite a finished trajectory log so it holds only chosen generations.

`fim.persistence.retention` decides which generations a tapered run keeps;
this module applies that decision to a log that holds more. It reads the log
once, in order, and writes a new log with only the kept frames (the writer
stores a gap in the generation numbers as it does for a thinned run), then
replaces the original. Because the result is a function of the frames kept and
the writer options alone, tapering a full log gives the same bytes however the
run got there, and tapering a log that is already tapered changes nothing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fim.persistence import tlog


@dataclass(frozen=True, slots=True)
class TaperResult:
    """What a taper changed.

    Attributes:
        frames_before: Generations the log held before.
        frames_after: Generations it holds now.
        bytes_before: The log's size before.
        bytes_after: The log's size now.
    """

    frames_before: int
    frames_after: int
    bytes_before: int
    bytes_after: int


def taper_log(
    path: Path | str,
    kept: Collection[int],
    *,
    options: Mapping[str, Any] | None = None,
) -> TaperResult:
    """Keep only the generations in `kept`, in place.

    The new log is written beside the old one and renamed over it, so a
    failure leaves the original untouched.

    Args:
        path: The log to rewrite.
        kept: The generations to keep; each must be in the log.
        options: Keyword options for `tlog.LogWriter` (`key_every`,
            `block_generations`, `sync_seconds`), normally the run's own
            (`fim.persistence.binary_store.log_options`), so the new file is
            what a run that wrote only these frames would have written.

    Returns:
        The frame and byte counts before and after.

    Raises:
        tlog.TlogError: The log is unusable.
        ValueError: A generation in `kept` is not in the log.
    """
    target = Path(path)
    wanted = set(kept)
    scan = tlog.recover(target, truncate=False)
    header = scan.header
    temporary = target.with_name(target.name + ".taper")
    temporary.unlink(missing_ok=True)
    before = target.stat().st_size
    seen = 0
    written: set[int] = set()
    writer = tlog.LogWriter(
        temporary, header.run_id, header.layout, **dict(options or {})
    )
    try:
        for frame in tlog.iter_frames(target, scan=scan):
            seen += 1
            if frame.generation in wanted:
                writer.submit(frame)
                written.add(frame.generation)
        writer.close()
    except BaseException:
        with contextlib.suppress(Exception):
            writer.close()
        temporary.unlink(missing_ok=True)
        raise
    missing = sorted(wanted - written)
    if missing:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"{target.name} does not hold generation {missing[0]}, which the "
            "tapered log must keep"
        )
    temporary.replace(target)
    return TaperResult(seen, len(written), before, target.stat().st_size)
