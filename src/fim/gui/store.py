"""Progress reporting and cancellation for a background run (design §3.4).

`fim.persistence.store.TrajectoryStore` is a `Protocol` (structural
typing, not an ABC), and `fim.engine._run_one`'s generation loop already
calls `store.write_generation(...)` unconditionally, every generation,
with no `try`/`except` around it — a clean, pre-existing extension point
`GuiProgressStore` decorates rather than a change to `fim.engine` itself.

Named `RunCancelledError`, not the design doc's illustrative
`RunCancelled` — ruff's `N818` (exception names end in `Error`) is part
of this project's lint gate; the design's code block is a decision
sketch, not a literal source requirement (§4's own "wireframes ... not
final visuals" framing applies here too).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from fim.persistence.store import TrajectoryRow, TrajectoryStore


class RunCancelledError(Exception):
    """Raised from `write_generation` to unwind an in-progress run.

    Args:
        run_id: The cancelled run's identifier.
        generation: The generation `write_generation` was about to write
            when cancellation was observed — one generation past the
            last one actually persisted.
    """

    def __init__(self, run_id: str, generation: int) -> None:
        super().__init__(run_id, generation)
        self.run_id = run_id
        self.generation = generation


class GuiProgressStore:
    """Decorate a `TrajectoryStore` with progress reporting and cancellation.

    Structurally satisfies `TrajectoryStore` (a `Protocol`), so it drops
    into `fim.engine.fim(..., store=...)` exactly where the real
    `JSONLTrajectoryStore` would — the run loop cannot tell the
    difference.
    """

    def __init__(
        self,
        inner: TrajectoryStore,
        *,
        on_generation: Callable[[int], None],
        cancel_event: threading.Event,
    ) -> None:
        """Wrap `inner`, reporting each write and honoring `cancel_event`.

        Args:
            inner: The real store every non-cancelled write delegates to.
            on_generation: Called with the generation number after each
                successful delegated write — never before, and never for
                a write that raised `RunCancelledError` instead.
            cancel_event: Set by the UI's Cancel button; checked before
                every write.
        """
        self._inner = inner
        self._on_generation = on_generation
        self._cancel_event = cancel_event

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Delegate one generation's write, or raise `RunCancelledError` instead.

        Checked before delegating, not after: a cancellation observed
        here never reaches the real store at all, so a cancelled run's
        `trajectory.jsonl` never gains the generation that triggered the
        cancellation — only the generations already written before it.
        """
        if self._cancel_event.is_set():
            raise RunCancelledError(run_id, generation)
        self._inner.write_generation(run_id, generation, rows)
        self._on_generation(generation)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Delegate straight to the wrapped store; nothing to decorate here."""
        return self._inner.read(run_id)
