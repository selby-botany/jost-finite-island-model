# Dear-Nolan high-migration scenario

This example documents the high-migration botanical scenario used in the
validation suite. The configuration uses an initial state derived from the
model's equilibrium identity recursion. Section 6 describes the derivation
script and the resulting `p_0` table.

## 1. Baseline configuration

A researcher reading the usage docs would likely build a configuration like
this:

```yaml
N: 2000
ploidy: haploid
d: 100
m: 0.01
mu: 0.001
seed: 992000
loci:
  - locus_id: 1
    length: 200
convergence_statistic: G_ST
precision: 0.01
max_generations: 3000
```

This configuration specifies:

- 2000 individuals per patch
- 100 patches
- migration rate 0.01
- mutation rate 0.001
- a single locus
- a seed chosen for reproducibility

It begins from the default founder state:
all demes are assumed to start with the same broad distribution of allele
frequencies, and then the simulation is allowed to drift and settle. In the
high-migration Dear-Nolan regime, that settling process is extremely slow.

The equilibrium state for this scenario is a specific near-stationary
population structure rather than a generic random start.

## 2. Equilibrium initialization

The validation scenario represents a system where:

- many alleles are shared across the whole metapopulation
- each patch also carries some private alleles
- the within-patch identity and between-patch identity are both at a
  fixed, balanced point

The validation test supplies an explicit near-equilibrium initial state and
checks whether the simulator preserves its predicted statistics. A generic
founder state would instead require a long transient to approach equilibrium;
the explicit `p_0` makes this a stationarity check rather than a convergence
test from a generic start.

## 3. Equilibrium allele-frequency structure

The initial state has the following structure:

- some alleles are common to nearly all patches
- the rest are patch-specific
- the common alleles contribute strongly to between-patch similarity
- the private alleles contribute to within-patch differentiation

The validation test builds this state deliberately. It chooses the allele
masses so that:

- the shared alleles reproduce the desired between-patch identity
- the private alleles reproduce the desired within-patch identity
- both identities match the fixed point of the recursion used by the model

The test computes the fixed point `(jw*, jb*)` of the identity recursion.
These values specify the expected within-patch and between-patch identity
in the high-migration regime. The starting state is constructed to match
that point.

## 4. Interpretation of the initial state

The initial state has the following structure:

- 100 patches
- each patch shares a set of common alleles with all other patches
- each patch also has a private set of alleles that only it carries
- the frequencies are chosen so the whole metapopulation sits at the
  equilibrium point rather than slowly wandering toward it

The state is initialized at the recursion's predicted equilibrium. The
subsequent simulation tests whether the model maintains the predicted
statistics; it is a stationarity check rather than a simulation from a
random initial state.

## 5. Derivation procedure

The validation code uses the recursion fixed point:

```python
within_star, between_star = _identity_fixed_point(
    population_size=2000,
    m=0.01,
    mu=0.001,
    d=100,
)
```

This tells us the expected equilibrium values for the within-patch and
between-patch identities.

Then it builds the initial state with a helper called
`_dn2_equilibrium_start()`. That helper does three things:

1. chooses a group of alleles that are shared by every patch
2. gives those shared alleles a frequency that matches the desired
   between-patch identity exactly
3. adds a private set of alleles to each patch so that the within-patch
   identity also matches the fixed point

The function constructs this initial condition.

## 6. From the derivation to `config.yaml`

The near-equilibrium state is too long to type by hand: 100 patches, each
with 63 allele frequencies. The script in this directory, `reproduce.py`,
derives it and writes it into `config.yaml` as an explicit `p_0` table (see
[p_0](../../configuration.md#p_0) in the configuration reference). The
derivation is the same code the validation test uses:

```python
within_star, between_star = module._identity_fixed_point(
    population_size=2000, m=0.01, mu=0.001, d=100
)
start = module._dn2_equilibrium_start(
    within_fixed_point=within_star,
    between_fixed_point=between_star,
    d=100,
    shared_count=41,
)
```

The result is 41 alleles shared by every patch, each at frequency 0.00736,
and 22 alleles private to each patch, each at frequency 0.0317.

The rest of `config.yaml` sets up the validation experiment itself: five
independently seeded replicates (`n_replicates: 5`, with
`stop_batch_early: false` so all five always run), each run for exactly 30
generations. A burn-in of 1 and a fixed replicate averaging window of 29
(`replicate_averaging_window: 29`) make that horizon fixed: each replicate
averages generations 1 to 30 and stops, whatever its noise. That is the right
reading here, because the question is whether the state *stays* at
equilibrium, not whether it settles.

The script prints the derived configuration by default. Use `--check` to
compare it with the committed configuration or `--write` to replace that
file:

```console
python3 doc/examples/dear-nolan-high/reproduce.py           # print the file
python3 doc/examples/dear-nolan-high/reproduce.py --check   # is it current?
python3 doc/examples/dear-nolan-high/reproduce.py --write   # rewrite it
```

## 7. Interpreting the output

The run writes `summary.json`, the mean over the five replicates of each
replicate's average over its 30 generations, with a 95% confidence interval:

| Statistic | Five-replicate mean | Predicted equilibrium |
|---|---|---|
| G<sub>ST</sub> | 0.02204 ± 0.00011 | 0.02196 |
| D | 0.9081 ± 0.0021 | 0.9088 |

Both predictions lie inside their intervals. The result is consistent with
the high-migration equilibrium: differentiation between patches is low by
`G_ST`, while Jost's `D` remains high. The fixed-point configuration tests
whether the model preserves the predicted stationary statistics.

The scenario represents many patches connected by frequent dispersal. In
this regime, `G_ST` is low while the identity-based measure `D` retains a
larger value.

## 8. Summary

- the literature scenario is an equilibrium calculation, not a generic random
  initialization
- the validation case constructs a starting population already sitting at the
  fixed point of the recursion, and `config.yaml` carries that population as
  an explicit `p_0`
- the run therefore checks whether `fim` preserves that equilibrium under
  the model dynamics rather than waiting for the system to find it by chance

The specialized initialization makes the equilibrium-stationarity question
testable over a short run.

## 9. Reproducing it

From the repository root:

```console
fim run doc/examples/dear-nolan-high/config.yaml \
    --output results/dear-nolan-high --quiet
```

The five replicates run in parallel, with one worker process per processor,
and take about two minutes on the measured development system.
`results/dear-nolan-high/summary.json` matches `summary.json` in this
directory exactly, and each `replicate-NNN/report.json` matches its
committed copy.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration, written by `reproduce.py` |
| `reproduce.py` | Derives the near-equilibrium `p_0` and writes `config.yaml` |
| `manifest.json`, `summary.json` | The batch's metadata and five-replicate summary |
| `replicate-NNN/` | Each replicate's own `manifest.json` and `report.json` |
| `README.md` | This document |

For the mathematical definitions, see the
[differentiation-measures guide](../../jost-differentiation-measures.md)
and [migration-conventions reference](../../migration-conventions.md).
The validation helpers are
[`_identity_fixed_point()`](../../../test/validation/test_simulator_equilibrium.py)
and
[`_dn2_equilibrium_start()`](../../../test/validation/test_simulator_equilibrium.py).
