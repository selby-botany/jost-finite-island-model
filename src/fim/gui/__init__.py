"""Desktop GUI front end: a Tk consumer of the existing simulator engine.

Every screen here calls `fim.engine.fim`, `fim.viz.scatter`, and
`fim.model.params.SimulationParams` — the same public API `fim.cli`
already uses — and never re-implements validation, statistics, or run
orchestration (`doc/developer.md`'s architecture table: "GUI: call
`fim.engine.fim`; do not duplicate model logic."). See
`dev/doc/apps/selby/jost-finite-island-model/
20260819-claude-sonnet-5-graphical-interface.md` for the full design
and implementation plan this package follows.
"""

from __future__ import annotations
