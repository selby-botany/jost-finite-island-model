"""Desktop GUI front end: a pywebview consumer of the existing simulator engine.

Six screens, rendered as a static local `webui/` page (plain HTML/CSS/
JS) driven entirely through `fim.gui.app.Api` — the JS side's only way
into Python. Every screen calls `fim.engine.fim`, `fim.viz.scatter`, and
`fim.model.params.SimulationParams` — the same public API `fim.cli`
already uses — and never re-implements validation, statistics, or run
orchestration (`doc/developer.md`'s architecture table: "GUI: call
`fim.engine.fim`; do not duplicate model logic."). See `doc/
fim-gui-test-plan.md` for how this package's own test suite is
organized, and `doc/developer.md`'s own architecture table for where
this package sits relative to the rest of the project.
"""

from __future__ import annotations
