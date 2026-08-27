"""Value objects and update operators for the finite island model.

This package defines the plain data (`SimulationParams`, `ModelState`,
`LocusSpec`, `AlleleRegistry`) that describes one finite-island
population at a single moment in time, plus the pure functions that
generate a starting population (`generate_initial_state`) and identify
its founding alleles (`founding_allele_ids`). The actual generation-by-
generation update rules (mutation, migration, drift) live in
`fim.model.operators`, not re-exported here since they are called only
from `fim.engine`'s own run loop, never directly by outside code.
"""

from fim.model.allele import AlleleId, AlleleRegistry, founding_allele_ids
from fim.model.initial import generate_initial_state
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState

__all__ = [
    "AlleleId",
    "AlleleRegistry",
    "LocusSpec",
    "ModelState",
    "SimulationParams",
    "founding_allele_ids",
    "generate_initial_state",
]
