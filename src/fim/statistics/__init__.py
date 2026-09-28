"""Pure diversity and differentiation statistics for finite-island models.

This package is the project's math library: every function in it takes
plain numbers in (allele frequencies, sample counts) and returns plain
numbers out (a diversity index, a confidence interval), with no
dependency on the simulator itself, the GUI, or how a run happens to be
stored on disk. That separation is deliberate — it means every formula
used to describe a population's genetic diversity lives in exactly one
place, reviewable and testable on its own, independently of the code
that produces the data or the code that displays it.

It is organized into three modules by subject:

- `fim.statistics.differentiation` — the actual diversity and
  differentiation formulas (`H_S`, `H_T`, `H_ST`, `G_ST`, Jost's `D`,
  `E_ST`, `K_ST`, and the general `differentiation_q` family that ties
  them all together). See that module's own docstring, and the
  [differentiation-measures guide](../../doc/jost-differentiation-measures.md),
  for the underlying population-genetics ideas.
- `fim.statistics.genetic_distance` — pairwise genetic distance and
  identity statistics between populations (Nei 1972 standard distance
  `D`, geometric/arithmetic distance `D'`, normalized identity `I`,
  cross identity `J_XY`, and founder-effect identity `I_0`).
- `fim.statistics.interval` — confidence intervals for a sample mean
  (the "± 3%" half of a "52% ± 3%"-style report) computed across a run's
  independent replicates. See that module's own docstring for what a
  confidence interval is and why the Student's-t method is used.

Every public name from all modules is re-exported here, so a caller
elsewhere in the project writes ``from fim.statistics import h_s,
jost_d, nei_d, confidence_interval`` rather than reaching into any module
by its own name directly.
"""

from .differentiation import (
    DifferentiationReport,
    allelic_distance,
    d_m,
    differentiation_q,
    e_st,
    effective_allele_count,
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_heterozygosity_isolated,
    equilibrium_heterozygosity_total,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_isolated,
    equilibrium_shannon_entropy_isolated_smm,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
    g_st,
    g_st_log,
    g_st_max,
    g_st_prime,
    gd,
    gregorius_delta,
    gs,
    h_s,
    h_st,
    h_t,
    heterozygosity,
    hill_number,
    identity,
    identity_recovery_equilibrium,
    identity_recovery_half_life,
    identity_recovery_rate,
    identity_recovery_trajectory,
    jost_d,
    k_st,
    mutation_negligible_equilibrium,
    mutation_negligible_transition,
    mutual_information,
    r_st,
    statistics_report,
    total_hill_number,
    within_hill_number,
)
from .genetic_distance import (
    cross_identity,
    nei_d,
    nei_d_prime,
    nei_founder_identity,
    nei_geometric_distance,
    nei_geometric_identity,
    nei_identity,
    nei_mean_distance,
    nei_standard_distance,
)
from .identity_recursion import (
    IDENTITY_STATISTIC_NAMES,
    MAXIMUM_MATRIX_DEMES,
    IdentityRecursion,
    identities_from_heterozygosities,
    identities_to_statistics,
    identity_matrix_from_frequencies,
    identity_recursion,
    matrix_identity_trajectory,
)
from .interval import ConfidenceInterval, confidence_interval, student_t_critical_value

__all__ = [
    "IDENTITY_STATISTIC_NAMES",
    "MAXIMUM_MATRIX_DEMES",
    "ConfidenceInterval",
    "DifferentiationReport",
    "IdentityRecursion",
    "allelic_distance",
    "confidence_interval",
    "cross_identity",
    "d_m",
    "differentiation_q",
    "e_st",
    "effective_allele_count",
    "equilibrium_d",
    "equilibrium_g_st",
    "equilibrium_heterozygosity_isolated",
    "equilibrium_heterozygosity_total",
    "equilibrium_shannon_differentiation",
    "equilibrium_shannon_entropy_isolated",
    "equilibrium_shannon_entropy_isolated_smm",
    "equilibrium_shannon_entropy_subpopulation",
    "equilibrium_shannon_entropy_total",
    "g_st",
    "g_st_log",
    "g_st_max",
    "g_st_prime",
    "gd",
    "gregorius_delta",
    "gs",
    "h_s",
    "h_st",
    "h_t",
    "heterozygosity",
    "hill_number",
    "identities_from_heterozygosities",
    "identities_to_statistics",
    "identity",
    "identity_matrix_from_frequencies",
    "identity_recovery_equilibrium",
    "identity_recovery_half_life",
    "identity_recovery_rate",
    "identity_recovery_trajectory",
    "identity_recursion",
    "jost_d",
    "k_st",
    "matrix_identity_trajectory",
    "mutation_negligible_equilibrium",
    "mutation_negligible_transition",
    "mutual_information",
    "nei_d",
    "nei_d_prime",
    "nei_founder_identity",
    "nei_geometric_distance",
    "nei_geometric_identity",
    "nei_identity",
    "nei_mean_distance",
    "nei_standard_distance",
    "r_st",
    "statistics_report",
    "student_t_critical_value",
    "total_hill_number",
    "within_hill_number",
]
