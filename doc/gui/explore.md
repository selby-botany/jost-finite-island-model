# Explore

Explore evaluates closed-form predictions from N, d, m, and μ without
running a simulation. Use it to locate a regime, check a timescale, or
choose sweep points before you spend computation.

## Contents

- [Inputs and units](#inputs-and-units)
- [Predictions](#predictions)
- [Curves and surfaces](#curves-and-surfaces)
- [From prediction to simulation](#from-prediction-to-simulation)
- [Limits](#limits)

## Inputs and units

- **N (individuals/deme)** is in individuals. Explore applies the ploidy
  of the current configuration to obtain gene copies.
- **d (demes)**, **m (migration rate)**, and **μ (mutation rate)** use
  the scalar island-model conventions; see
  [migration](conventions.md#migration).
- A field commits when you leave it or press Enter. Predictions then
  update. Explore never changes Configure until you choose **Run this
  for real** or **Sweep this for real**.

## Predictions

The table groups predictions under **Statistics**, **Equilibrium
diversity**, and **Identity and regime**. It covers differentiation
measures, diversity, entropy, effective allele counts, identity-recovery
timescales, and whether mutation is negligible. Definitions are in the
[statistics reference](../statistics.md), including
[identity recovery](../statistics.md#identity-recovery) and
[mutation-negligible equilibrium](../statistics.md#mutation-negligible-equilibrium).

Predictions that do not depend on a parameter are flat along it. For
example, analytic $D$ is independent of N, and identity-recovery
metrics are independent of μ.

## Curves and surfaces

- **View** selects **Curve: one parameter** or **Surface: two
  parameters**. A surface uses **Rows** and **Columns** as its two axes.
- Select a statistic row to chart it over the chosen parameter range.
  Charts group compatible units (proportions, nats, allele counts,
  generations).
- The slider evaluates predictions along the range without changing the
  input fields.

## From prediction to simulation

- **Run this for real** copies the four inputs to Configure, selects a
  new Study, and starts from your Settings.
- **Choose a sweep…** picks sweep points for the displayed parameters:
  an interval, a count, and a spacing. The spacing is the axis scale
  (linear or log) or even steps of a predicted response chosen in
  **Place points by the response of** ($D$, $G_{ST}$, $E_{ST}$, $H_S$,
  or $H_T$). Response spacing crowds points where the prediction
  changes fastest, such as the migration threshold. The exact points and
  the planned sweep size are shown. Removing a point switches to an
  explicit list.
- **Sweep this for real** copies the inputs to Configure, ticks
  **Sweep**, and stores the axes. See [sweeps](sweeps.md).

Response spacing produces an explicit list of values, so the sweep stays
reproducible without the closed form.

## Limits

- Predictions assume equilibrium and the scalar finite island model.
  They do not represent topologies, matrices, unequal sizes, stochastic
  migrant sampling, or finite alleles.
- Typical-deme entropy is approximate, especially at $d=2$.
- Explore makes no claim about how long a run takes to reach the
  prediction. Use the derived burn-in shown before a run, and see
  [convergence](../convergence.md).
- For programmatic access, see the
  [Python statistics API](../fim-simulator-functional-api.md#4-fimstatistics--differentiation-and-diversity-measures).
