# Constants (`fim.config`)

Every named policy constant and numerical guard of the convergence and
statistics code lives here, one module per subject, so a reader finds the
value, the reason for it and its kind in one place. Code imports from
`fim.config.*`; nothing else under `src/fim` defines a policy constant.
`test/test_constants.py` scans the convergence, statistics, model-parameter
and engine modules for any module-level numeric constant outside this
package and fails on one that is not on its short allow list.

## The four kinds

- **Derivable.** Follows from mathematics, from IEEE double precision, or
  from other settings. Named and documented, never adjustable: changing it
  would make the code wrong, not different.
- **Numerical guard.** A threshold that keeps a computation finite and well
  conditioned. Derivable in principle from double precision; an internal
  constant, not a setting.
- **Convention.** Fixed by a published convention (Geweke's 10% and 50%
  segments). Named and documented, not adjustable.
- **Policy.** A choice a careful person could make differently. Documented
  with its evidence, and a regular setting or an Expert Setting.

## Modules

| Module | What it holds |
| --- | --- |
| `convergence.py` | Policy constants of the convergence rule |
| `limits.py` | Sizes beyond which a computation is refused or skipped |
| `numerics.py` | Numerical guards and derivable constants |
| `defaults.py` | Default values of regular settings |
| `display.py` | Limits that shape what the app draws |
| `statistics.py` | How a statistic is estimated from a run (the expected-value form) |
| `expert.py` | `ExpertSettings`: the policy constants a run's configuration may change in its `expert:` mapping |

## Constants

| Name | Module | Kind | Controls |
| --- | --- | --- | --- |
| `BURN_IN_MINIMUM_RELAXATION_TIMES` | `convergence.py` | policy | Fewest relaxation times `tau` the burn-in lasts: the floor of `k`. |
| `FIRST_CHECK_RELAXATION_TIMES` | `convergence.py` | policy | First check, in relaxation times after the burn-in ends. |
| `FIRST_CHECK_MINIMUM` | `convergence.py` | policy | Fewest generations after the burn-in before the first check. |
| `MINIMUM_EFFECTIVE_SAMPLE_SIZE` | `convergence.py` | policy | Smallest effective sample size an evidence window must hold to be trusted. |
| `CHECK_GROWTH` | `convergence.py` | policy | Factor by which the evidence window grows between checks (doubling). |
| `FRACTIONAL_BURN_IN` | `convergence.py` | policy | Share of a run discarded as burn-in when no relaxation time is available. |
| `CAP_RELAXATION_MULTIPLE` | `convergence.py` | policy | Default `max_generations`, in relaxation times, beyond the burn-in. |
| `MINIMUM_MAX_GENERATIONS` | `convergence.py` | policy | Smallest derived cap. |
| `ABSOLUTE_MAX_GENERATIONS` | `convergence.py` | policy (safety) | Ceiling on a derived cap, so a nearly isolated system stays finite. |
| `GEWEKE_FIRST_FRACTION` | `convergence.py` | convention (Geweke 1992: the first 10% against the last 50%) | Share of an evidence window, from its start, that Geweke's `z` compares. |
| `GEWEKE_LAST_FRACTION` | `convergence.py` | convention (Geweke 1992: the first 10% against the last 50%) | Share of an evidence window, from its end, that Geweke's `z` compares. |
| `START_DRIFT_ALERT_Z` | `convergence.py` | policy | Absolute Geweke `z` above which the report says the burn-in may be too short. |
| `MAXIMUM_RECURSION_DEMES` | `limits.py` | policy (run time) | Largest `d` for which the `d² by d²` eigenvalue route is used. |
| `MAXIMUM_LAG1_CORRELATION` | `numerics.py` | numerical guard | A lag-1 correlation this close to 1 makes `tau_int` (below) blow up numerically for a reason that is ... |
| `MINIMUM_WINDOW_VALUES` | `numerics.py` | derivable | `window_statistics` needs at least this many values to define a lag-1 correlation at all (two ... |
| `MINIMUM_CRITERION_WINDOW` | `numerics.py` | derivable | `MINIMUM_CRITERION_WINDOW`. |
| `MINIMUM_REPLICATE_COUNT` | `numerics.py` | derivable | `MINIMUM_REPLICATE_COUNT`. |
| `EXACT_SCALE_BITS` | `numerics.py` | derivable (IEEE 754) | Every finite double is an integer multiple of `2 ** -1074`. |
| `MINIMUM_DEMES` | `numerics.py` | derivable | The recursion needs a between-deme identity, so at least two demes. |
| `DEGENERACY_TOLERANCE` | `numerics.py` | numerical guard | Eigenvalues closer than this (relative to the larger) make the 2 by 2 eigenvector matrix numerically singular. |
| `SINGULAR_FIXED_POINT` | `numerics.py` | numerical guard | Below these, the fixed-point system or the eigenvector matrix is singular to double precision. |
| `SINGULAR_EIGENVECTORS` | `numerics.py` | numerical guard | `SINGULAR_EIGENVECTORS`. |
| `MAXIMUM_CONDITION` | `numerics.py` | numerical guard | The eigenvector matrix is trusted only while it is this well conditioned and reproduces the operator to ... |
| `RECONSTRUCTION_TOLERANCE` | `numerics.py` | numerical guard | `RECONSTRUCTION_TOLERANCE`. |
| `MINIMUM_SAMPLE_GENE_COPIES` | `numerics.py` | derivable | `MINIMUM_SAMPLE_GENE_COPIES`. |
| `DIFFERENTIATION_TOLERANCE` | `numerics.py` | numerical guard | `DIFFERENTIATION_TOLERANCE`. |
| `EULER_GAMMA` | `numerics.py` | derivable (a mathematical constant) | Euler-Mascheroni constant gamma = -psi(1), to full double precision (Abramowitz & Stegun 1972, table 1.1) ... |
| `DIGAMMA_ASYMPTOTIC_THRESHOLD` | `numerics.py` | numerical guard, derivable | Threshold above which `_digamma`'s asymptotic series (Abramowitz & Stegun 1972, formula 6.3.18 -- the same ... |
| `DEFAULT_LOCUS_LENGTH` | `defaults.py` | policy; a regular setting | `DEFAULT_LOCUS_LENGTH`. |
| `DEFAULT_AUTO_VECTOR_MIN_D` | `defaults.py` | policy; already an Expert Setting | `"auto"`'s own default deme-count cutover, below which it never picks `"generational-vector"` even when ... |
| `DEFAULT_AUTO_VECTOR_MAX_CAPACITY` | `defaults.py` | policy; already an Expert Setting | `"auto"`'s own default per-locus capacity ceiling for `"generational- vector"` — above it, `"auto"` picks ... |
| `DEFAULT_N_REPLICATES` | `defaults.py` | policy; a regular setting | How many independently seeded replicates a run tries by default. |
| `DEFAULT_PRECISION` | `defaults.py` | policy; a regular setting | Default `precision`: plus or minus this much, in each statistic's units. |
| `DEFAULT_PAIRWISE_MAX_DEMES` | `defaults.py` | policy; a regular setting | Largest deme count whose full all-pairs matrices are saved by default. |
| `GUI_ANIMATION_MAX_FRAMES` | `display.py` | policy (display) | `GUI_ANIMATION_MAX_FRAMES`. |
| `ESTIMATE_AUTO_DENOMINATOR` | `statistics.py` | policy | Denominator below which a generation counts as degenerate for `auto`. |
| `ESTIMATE_AUTO_FRACTION` | `statistics.py` | policy | Share of window generations that may be degenerate before `auto` switches. |

## Adding a constant

- Put it in the module whose subject it belongs to, with a docstring that
  says what it controls, why it has this value (the measurement or design
  section for a policy constant) and a `Kind:` line.
- Add its row to the table above; a test fails if a constant is missing.
- A policy constant that a user may reasonably change becomes a setting;
  keep its default here and its valid range beside it.
