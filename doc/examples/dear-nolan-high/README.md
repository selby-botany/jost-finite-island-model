# Dear-Nolan high-migration example

This example is a worked explanation of the exact high-migration botanical
scenario used in the validation suite. It shows how to move from a
configuration a person would naturally write to the final, research-grade
initialization used by the model. Like every other example, it runs with
`fim run` from its `config.yaml`; what is unusual is that a script derives the
file's starting state (section 6).

The target audience is a botanist or ecological researcher, not a software
engineer. The goal is clarity: what the model is trying to represent, why the
obvious configuration is not enough, and how the final setup matches the
biological equilibrium the paper is studying.

## 1. The starting point a user would naturally write

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

This is the natural, readable configuration for a finite-island model:

- 2000 individuals per patch
- 100 patches
- migration rate 0.01
- mutation rate 0.001
- a single locus
- a seed chosen for reproducibility

It is also the configuration that looks right to a reader of the docs. It is
how a sensible user would start.

The problem is that this configuration begins from the default founder state:
all demes are assumed to start with the same broad distribution of allele
frequencies, and then the simulation is allowed to drift and settle. In the
high-migration Dear-Nolan regime, that settling process is extremely slow.

This is not a failure of the model. It is a property of the biological
question: the equilibrium state for this scenario is a very specific
near-stationary population structure, not a routine random start.

## 2. Why the naive configuration does not reach the published equilibrium

The published scenario in the validation suite is not a `fim` failure; it is a
special biological equilibrium.

The model is trying to represent a system where:

- many alleles are shared across the whole metapopulation
- each patch also carries some private alleles
- the within-patch identity and between-patch identity are both at a
  fixed, balanced point

That equilibrium is a target state, not a random accident of starting values.

If you start from a generic initial condition, the model still runs, but it
must evolve toward that equilibrium from a less informative starting point.
For the high-migration Dear-Nolan case, this drifts over a long time and is
not a practical demonstration for a user.

This is why the validation test does not simply call `fim run` on a YAML
config and hope for the equilibrium to appear. Instead it constructs a
near-equilibrium initial state and asks whether the simulator preserves that
state.

In plain language: the scientific question is not "what does random starting
configuration do after 1800 generations?" The scientific question is "does the
simulator hold the known equilibrium state when the system starts exactly at
that fixed point?"

## 3. What the equilibrium state actually looks like

The core idea is simple and biologically meaningful:

- some alleles are common to nearly all patches
- the rest are patch-specific
- the common alleles contribute strongly to between-patch similarity
- the private alleles contribute to within-patch differentiation

The validation test builds this state deliberately. It chooses the allele
masses so that:

- the shared alleles reproduce the desired between-patch identity
- the private alleles reproduce the desired within-patch identity
- both identities match the fixed point of the recursion used by the model

This is not arbitrary.

The test computes the fixed point `(jw*, jb*)` of the identity recursion. That
value tells us the equilibrium within-patch and between-patch identity we
expect in the high-migration regime. The starting state is then built to match
that point as closely as possible.

## 4. The user-facing explanation of the final construction

The final state is best thought of as follows:

- imagine 100 patches
- each patch shares a set of common alleles with all other patches
- each patch also has a private set of alleles that only it carries
- the frequencies are chosen so the whole metapopulation sits at the
  equilibrium point rather than slowly wandering toward it

This is the crucial trick: instead of asking the model to discover the
equilibrium on its own, we begin the simulation at the equilibrium that the
math says should hold.

Once the state is built this way, the simulator is tested to see whether it
continues to hold the same statistics as before. That is a stationarity check,
not a fresh simulation from a random start.

## 5. The exact logic, written in plain English

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

The function is intentionally long and explicit because this is a scientific
initial condition, not an everyday parameter file.

## 6. From the derivation to `config.yaml`

The near-equilibrium state is too long to type by hand: 100 patches, each
with 63 allele frequencies. The script in this directory, `reproduce.py`,
derives it and writes it into `config.yaml` as an explicit `p_0` table (see
[p_0](../../configuration.md#p0) in the configuration reference). The
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
generations. A 30-generation cap with `precision: 0.0` (never reached) and a
burn-in of 1 makes that horizon fixed: no replicate stops early, and each one
ends "at the cap" by design. That is the right reading
here, because the question is whether the state *stays* at equilibrium, not
whether it settles.

The script never changes anything unless you ask it to:

```console
python3 doc/examples/dear-nolan-high/reproduce.py           # print the file
python3 doc/examples/dear-nolan-high/reproduce.py --check   # is it current?
python3 doc/examples/dear-nolan-high/reproduce.py --write   # rewrite it
```

## 7. What the output means biologically

The run writes `summary.json`, the mean of each statistic over the five
replicates with a 95% confidence interval:

| Statistic | Five-replicate mean | Predicted equilibrium |
|---|---|---|
| G<sub>ST</sub> | 0.02193 ± 0.00034 | 0.02196 |
| D | 0.9072 ± 0.0040 | 0.9088 |

Both predictions lie inside their intervals. This is the published
high-migration equilibrium: low differentiation between patches, but still
enough structure that Jost's D remains high. The fixed-point configuration
is therefore a realistic scientific test of the model's stationarity, not a
random drift measurement.

In more botanical terms, the high-migration scenario describes many patches
connected strongly by dispersal, where species-level differentiation is low,
but the identity-based differentiation measure still records a meaningful
signal of structure. That is precisely the subtle regime the test is designed
to examine.

## 8. The shortest, clearest summary

If someone asks, "why is this example different from the other examples?" the
answer is:

- the literature scenario is an equilibrium calculation, not a generic random
  initialization
- the validation case constructs a starting population already sitting at the
  fixed point of the recursion, and `config.yaml` carries that population as
  an explicit `p_0`
- the run therefore checks whether `fim` preserves that equilibrium under
  the model dynamics rather than waiting for the system to find it by chance

That is why the example is longer and more explanatory than the other worked
examples. The scientific question is more subtle, and the initialization is
reasonably specialized.

## 9. Reproducing it

From the repository root:

```console
fim run doc/examples/dear-nolan-high/config.yaml \
    --output results/dear-nolan-high --quiet
```

The five replicates run in parallel, one worker process per processor, and
take about two minutes on ordinary development hardware.
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
