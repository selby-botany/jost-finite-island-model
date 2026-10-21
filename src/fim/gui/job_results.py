"""Pickle-safe completed results passed from GUI job workers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from fim.engine.records import FinalReport, RunResult
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import RunManifest


@dataclass(frozen=True, slots=True)
class GuiRunResult:
    """Completed run data needed by the GUI, without worker-local stores."""

    run_id: str
    params: SimulationParams
    final_state: ModelState
    report: FinalReport
    convergence_generations: tuple[int, ...]
    convergence_history: tuple[float, ...]
    convergence_histories: Mapping[str, tuple[float, ...]]
    manifest: RunManifest

    @classmethod
    def from_result(cls, result: RunResult) -> GuiRunResult:
        """Copy GUI-visible fields while excluding trajectory-store handles."""
        return cls(
            run_id=result.run_id,
            params=result.params,
            final_state=result.final_state,
            report=result.report,
            convergence_generations=result.convergence_generations,
            convergence_history=result.convergence_history,
            convergence_histories=result.convergence_histories,
            manifest=result.manifest,
        )
