# Implementation conventions

This page lists the conventions that most often matter when you compare a
`fim` result with a published formula or another simulator. Each item
links to the authoritative reference. The desktop app and the command
line follow the same conventions.

## Contents

- [Population size and ploidy](#population-size-and-ploidy)
- [Migration](#migration)
- [Generation order](#generation-order)
- [Generation 0 and founding](#generation-0-and-founding)
- [Mutation](#mutation)
- [Statistics](#statistics)
- [When a run stops](#when-a-run-stops)
- [Batches and replicates](#batches-and-replicates)
- [Engines and seeds](#engines-and-seeds)

## Population size and ploidy

- The **N (individuals/deme)** field and the YAML key `N` count
  **individuals**. The engine uses $n = N \times c$ gene copies per deme
  and locus, where $c$ is the ploidy (haploid 1 through tetraploid 4).
  The conversion happens once, when the configuration is read.
- Every statistic, closed-form prediction, and derived timescale uses
  gene copies. Explore and sweeps also take `N` in individuals and apply
  the form's ploidy.
- `N: 225` with `ploidy: diploid` and `N: 450` with `ploidy: haploid`
  are the same genetic process. They are recorded as different
  population descriptions and receive different run IDs.
- Per-deme sizes are supported. Every operator and size-weighted option
  uses each deme's gene-copy count.

See [`N`](../configuration.md#n), [`ploidy`](../configuration.md#ploidy),
and [population unit](../migration-conventions.md#11-population-unit).

## Migration

- A scalar `m` is the fraction of a deme's parental pool drawn from the
  **other** $d-1$ demes. That incoming fraction is size-weighted across
  the other demes; with equal sizes each contributes $m/(d-1)$.
- Some papers instead redraw a fraction from all $d$ demes, including
  home. Convert with $m_{\mathrm{paper}} = m_{\mathrm{fim}}\,d/(d-1)$.
  For $d=4$, $m_{\mathrm{fim}}=0.06$ equals a redraw rate of $0.08$.
- A full matrix row $k$ gives destination $k$'s source weights. Rows must
  sum to 1. Matrix weights are authoritative and are **not** rescaled by
  deme size.
- A named topology's **rate** is each deme's total outgoing fraction,
  split evenly among its neighbors. Do not apply the island-model
  conversion above to a topology.
- **migrant sampling** `continuous` (the default) mixes frequencies
  deterministically. `stochastic` draws migrant gene-copy counts each
  generation; the mean is unchanged, and `N` sets the variance.

See [`m`](../configuration.md#m),
[`migrant_sampling`](../configuration.md#migrant_sampling), and
[migration source](../migration-conventions.md#12-migration-source).

## Generation order

One generation is **migrate → drift → mutate**: migration changes
parental frequencies, drift draws whole gene copies, and each new copy
mutates independently with probability μ. Papers that write
drift → migrate → mutate can describe the same process when the state is
translated at the generation boundary; do not add a one-generation lag.

See [operator order](../migration-conventions.md#14-operator-order-and-generation-boundaries)
and the [generation pipeline](../fim-simulator-design.md#34-the-generation-update-pipeline).

## Generation 0 and founding

- Generation 0 is the **initial conditions** draw: a continuous
  Dirichlet prior, an equilibrium split, an explicit $p_0$, or fixed
  alleles per deme. A Dirichlet or explicit $p_0$ need not lie on the
  $1/n$ lattice. Treat a generation-0 statistic as a property of the
  prior, not of a sampled population.
- Generation 1 is the first generation on the $1/n$ lattice.
- Generation 0 is never included in a convergence average.
- **Equilibrium split** simulates an ancestral population first and
  splits it. Its window, tolerance, and cap are separate from the main
  run's.

See [initial conditions](../configuration.md#initial-conditions).

## Mutation

- μ is a per-gene-copy probability per generation, applied after drift.
- **Derive from μb** sets each locus's μ from a per-base rate and the
  locus length. Edit μb, not the derived per-locus μ.
- **infinite_alleles** (the default) gives every mutation a new, globally
  unique allele ID. **finite_alleles** bounds a locus to $4^{\ell}$
  states for length $\ell$; mutations can recur to existing states.
- Founding alleles use locus-relative IDs.

See [`mu`](../configuration.md#mu),
[deriving `mu` from a per-base rate](../configuration.md#deriving-mu-from-a-per-base-rate),
and [mutation model](../configuration.md#mutation_model).

## Statistics

- Reported statistics are functions of the **population** frequencies,
  not finite-sample estimators. No sample-size correction is applied.
- Pooled frequencies use equal deme weights except where **deme
  weighting** applies. That setting affects only $E_{ST}$, $\delta_G$
  (`Delta`), and $I$ (`MI`).
- Across loci, `D` and $G_{ST}$ default to a ratio of mean
  heterozygosities (`locus_aggregation: ratio_of_means`). A mean of
  per-locus ratios is a different estimand.
- For time averages, **expected value** chooses between the mean of a
  statistic's values and the statistic of the mean heterozygosities.
  Every report carries both.
- An undefined value is not evidence of zero differentiation.
- The app shows mathematical symbols ($H_{ST}$, $\delta_G$, $I$).
  Configuration fields, saved files, and exports keep literal keys
  (`H_ST`, `Delta`, `MI`).

See [notation and aggregation](../statistics.md#notation-and-aggregation),
[`deme_weighting`](../configuration.md#deme_weighting),
[`locus_aggregation`](../configuration.md#locus_aggregation), and
[`convergence_estimate`](../configuration.md#convergence_estimate).

## When a run stops

- A run first discards a **burn-in**, then averages each watched
  statistic over an evidence window. It stops when each average's
  standard error meets **precision** at the chosen **confidence**, and
  the effective sample size reaches its floor.
- **convergence burn-in** and **max generations** default to `auto`,
  derived from the slowest locus's relaxation time. The derived values
  appear beside **Run simulation** before you start.
- With several watched statistics, **all** (the default) waits for every
  one. **any** stops at the first, so the others may not be converged.
- Reaching **max generations** is a valid, **non-converged** result. The
  report gives the precision achieved.
- A short run is not evidence of equilibrium.

See [convergence](../convergence.md#if-you-only-read-one-section) and the
[quick reference](../convergence.md#quick-reference).

## Batches and replicates

- **n_replicates** defaults to **200**, not 1. Set it to 1 for a single
  run; there is no separate batch-mode control.
- Replicate $i$ uses child $i$ of `numpy.random.SeedSequence(seed)`.
- With **stop the batch early** on (the default), a batch stops when
  every watched statistic's across-replicate Student's-$t$ interval has
  half-width at most **precision**, after at least **replicate minimum**
  replicates. **n_replicates** remains a hard cap.
- Replicates are considered in replicate order on every engine, not in
  completion order.
- Batch intervals describe uncertainty across independent replicates.
  The app labels them "uncertainty across N independent replicates".

See [`n_replicates`](../configuration.md#n_replicates),
[`stop_batch_early`](../configuration.md#stop_batch_early), and
[batches](../convergence.md#batches-replicates-that-average).

## Engines and seeds

- **execution engine** changes speed, not results. `lineal` (the
  reference), `generational`, and `generational-vector` produce the same
  trajectory, bit for bit, for the same seed on the same machine. `auto`
  chooses one from the deme count and locus capacity.
- Across machines, the compiled engines are reproducible statistically,
  not necessarily bit for bit.
- **max workers** affects only elapsed time. **max concurrent
  replicates** is recorded and can change adaptive-batch results.
- Run IDs are hashes of the configuration. Names and descriptions are
  not part of the ID.

See [`engine_backend`](../configuration.md#engine_backend) and
[reproduce a run](../usage.md#reproduce-a-run).
