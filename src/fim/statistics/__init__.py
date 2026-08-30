"""Pure diversity and differentiation statistics for finite-island models.

This package is the project's math library: every function in it takes
plain numbers in (allele frequencies, sample counts) and returns plain
numbers out (a diversity index, a confidence interval), with no
dependency on the simulator itself, the GUI, or how a run happens to be
stored on disk. That separation is deliberate — it means every formula
used to describe a population's genetic diversity lives in exactly one
place, reviewable and testable on its own, independently of the code
that produces the data or the code that displays it.

It is organized into two modules by subject:

- `fim.statistics.differentiation` — the actual diversity and
  differentiation formulas (`H_S`, `H_T`, `H_ST`, `G_ST`, Jost's `D`,
  `E_ST`, `K_ST`, and the general `differentiation_q` family that ties
  them all together). See that module's own docstring, and the
  [differentiation-measures guide](../../doc/jost-differentiation-measures.md),
  for the underlying population-genetics ideas.
- `fim.statistics.interval` — confidence intervals for a sample mean
  (the "± 3%" half of a "52% ± 3%"-style report) computed across a run's
  independent replicates. See that module's own docstring for what a
  confidence interval is and why the Student's-t method is used.

Every public name from both modules is re-exported here, so a caller
elsewhere in the project writes ``from fim.statistics import h_s,
jost_d, confidence_interval`` rather than reaching into either module
by its own name directly.
"""

from .differentiation import (
    DifferentiationReport,
    d_m,
    differentiation_q,
    e_st,
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_isolated,
    equilibrium_shannon_entropy_isolated_smm,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
    g_st,
    g_st_log,
    h_s,
    h_st,
    h_t,
    heterozygosity,
    hill_number,
    identity,
    jost_d,
    k_st,
    r_st,
    statistics_report,
    total_hill_number,
    within_hill_number,
)
from .interval import ConfidenceInterval, confidence_interval, student_t_critical_value

__all__ = [
    "ConfidenceInterval",
    "DifferentiationReport",
    "confidence_interval",
    "d_m",
    "differentiation_q",
    "e_st",
    "equilibrium_d",
    "equilibrium_g_st",
    "equilibrium_shannon_differentiation",
    "equilibrium_shannon_entropy_isolated",
    "equilibrium_shannon_entropy_isolated_smm",
    "equilibrium_shannon_entropy_subpopulation",
    "equilibrium_shannon_entropy_total",
    "g_st",
    "g_st_log",
    "h_s",
    "h_st",
    "h_t",
    "heterozygosity",
    "hill_number",
    "identity",
    "jost_d",
    "k_st",
    "r_st",
    "statistics_report",
    "student_t_critical_value",
    "total_hill_number",
    "within_hill_number",
]
