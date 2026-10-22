"""The one place that resolves "how many replicates run at once" when unset.

Three independent mechanisms answer this question today, with different
resource semantics (`20261022-claude-sonnet-5-replicate-concurrency-
unification-design.md`, `selby/restricted`):

- `lineal`'s `max_workers` (`fim.engine.lineal`): OS processes, real
  parallelism scaling to the core count.
- `generational`'s own `ThreadedAdvancer` thread pool
  (`fim.engine.lanes`): OS threads, real (GIL-shaped) parallelism also
  scaling to the core count.
- `generational` and `generational-vector`'s shared lane window
  (`SimulationParams.max_concurrent_replicates`, consumed by
  `fim.engine.batch.run_batch`): in-process `ReplicaLane`s. Under
  `generational-vector` specifically, `VectorizedAdvancer.advance()`
  steps every active lane in one Python loop with no fan-out at all, so
  a wider window there buys no throughput, only memory and a slower
  adaptive stop.

`default_replicate_concurrency` is the single function every one of
those three call sites falls back on when the caller (a user, a saved
config, a CLI invocation) left its own concurrency setting unset,
instead of each computing its own answer independently.
"""

from __future__ import annotations

import os
from typing import Literal

ConcurrencyAwareBackend = Literal["lineal", "generational", "generational-vector"]


def default_replicate_concurrency(
    engine_backend: ConcurrencyAwareBackend,
    n_replicates: int,
    batch_width: int,
) -> int:
    """Return how many replicates run at once when the caller left it unset.

    Args:
        engine_backend: The resolved backend (never the literal
            `"auto"` — resolve it first).
        n_replicates: The batch's own replicate ceiling; the result
            never exceeds this, so a 4-replicate batch runs at most 4
            at once regardless of hardware or backend.
        batch_width: `SimulationParams.expert.batch_width` — the fixed
            width the adaptive-batch window-matching statistics already
            assume for this same "unset" case
            (`fim.engine.monitors.batch_window_planner`). Reusing it
            here, rather than inventing a second constant, is the
            point: the statistical assumption and the execution
            schedule agree by construction.

    Returns:
        The process-visible core count for `"lineal"` and
        `"generational"`, both of which get genuine, hardware-scaled
        fan-out from more concurrency (OS processes and OS threads
        respectively) — starving either below the core count leaves
        real parallelism idle for no benefit. `batch_width` for
        `"generational-vector"`, which has no fan-out mechanism at all.
        Either way, clamped to `n_replicates`.
    """
    width = batch_width if engine_backend == "generational-vector" else _core_count()
    return min(width, max(n_replicates, 1))


def _core_count() -> int:
    """Return the process-visible CPU count, falling back to the machine count.

    Mirrors `fim.gui.jobs.usable_core_count` exactly — not imported
    from it, since `fim.engine` must not depend on `fim.gui` — and for
    the reason that function's own docstring exists: plain
    `os.cpu_count()` reports the machine's total cores, not how many a
    cgroup/container limit actually leaves this process able to use
    (Python 3.13+'s `os.process_cpu_count`, when available, is the
    process-visible figure).
    """
    process_cpu_count = getattr(os, "process_cpu_count", None)
    count = process_cpu_count() if callable(process_cpu_count) else None
    return max(1, count or os.cpu_count() or 1)
