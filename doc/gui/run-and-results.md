# Run and results

The run view shows one configuration's run in three states: initial,
running, and completed. **Run** and **Results** in the rail both open it.
A single run (`n_replicates: 1`) and a batch share the view; the batch
view adds replicate summaries.

## Contents

- [Before a run](#before-a-run)
- [While a run executes](#while-a-run-executes)
- [Graphs](#graphs)
- [Statistic trajectories](#statistic-trajectories)
- [Statistics table and hover readouts](#statistics-table-and-hover-readouts)
- [Completed single runs](#completed-single-runs)
- [Completed batches](#completed-batches)
- [Pause, save for later, and resume](#pause-save-for-later-and-resume)
- [Files](#files)

## Before a run

The initial state shows the founding state (generation 0) and the derived
convergence schedule beside **Run simulation**, for example:

> Convergence: burn-in 104,338 generations, cap 399,728 (derived; this
> model needs about 19,693 generations to forget its starting state)

Check the burn-in and cap against the model's timescale before you
start. See [why a run takes so long](../convergence.md#why-does-my-run-take-so-long).

**Run simulation** always starts from the current configuration, even
when a previous result is displayed. **Edit configuration** returns to
Configure.

## While a run executes

- Allele frequencies, statistic trajectories, and progress update live.
  Batch scatter plots pool the replicates in progress.
- Deme selectors (initially deme 1 against deme 2) apply to subsequent
  updates of the scatter plot and pairwise statistics.
- **Cancel** stops the run. Cancellation or an error keeps the last
  rendered view and adds a status banner.
- **Pause** appears while a saved run executes; it stops at a checkpoint.
- The run continues if you switch tabs or screens.

## Graphs

**Graphs** selects which graphs are visible. [Settings](settings.md)
controls how many sit side by side and the scatter-plot point style.

| Graph | Shows |
|---|---|
| Allele frequencies by deme pair | Each allele's frequency in one deme against another; axes span 0–1 |
| Statistic trajectories | Each shown statistic against generation, with references |
| Allele composition by deme | Stacked allele composition of each deme |
| Allele-frequency spectrum | Empirical frequency spectrum, with an optional Wright beta overlay |
| Isolation-by-distance identity decay | Pairwise identity against migration-graph distance |

The last three are drawn for completed results; batches pool them across
replicates. Double-click a graph to open it in a larger frame
with the scrubber and statistics table. The frame offers zoom (**−**,
**+**), **Fit**, **Reset view**, and **Export image…**. Escape or
**Close** returns it to the run view.

## Statistic trajectories

**References.** Single runs draw the analytic equilibrium line and,
where available, the expected trajectory for $D$, $G_{ST}$, $H_S$,
$H_T$, and $H_{ST}$. Expected trajectories start from the seeded
founding state and support unequal deme sizes and explicit migration
matrices up to 24 demes. They are omitted for per-locus mutation rates,
equilibrium-split founding, and larger matrices. They are model
expectations, not stochastic paths; with few loci, the mean of a ratio
can differ from the ratio of expectations. See
[`convergence_estimate`](../configuration.md#convergence_estimate).

**f₀ (identity recovery)** overlays Whitlock's (1992) closed-form
identity-by-descent recovery after a founder event, computed from this
configuration's N and m alone. It is a reference, not a measurement of
this run. It is off by default and hidden when N or m is not a plain
scalar. See [identity recovery](../statistics.md#identity-recovery).

**Show** selects **Every generation**, **Trailing mean**, or
**Cumulative mean**. **Band** selects ±1 or ±2 (the default) standard
errors for means. With **Every generation**, the band shows standard
deviations over the evidence window instead. Reference curves are
averaged over the same windows. Cumulative averaging begins at the
recorded averaging start, or after one convergence window when that
record is absent.

Solid colored lines with flags mark each statistic's averaging-window
start, and the windows are shaded. The scrubber's position is a dashed
gray line. Display choices are remembered and never alter computed or
saved results.

## Statistics table and hover readouts

The statistics table is also the trajectory legend: select a row to
show or hide its line. Select an underlined statistic name to open its
[reference entry](../statistics.md). **Choose…** in the table caption
opens [Statistics shown](settings.md#statistics-shown) in Settings, which
chooses the rows; hidden statistics are still in the saved report.

Hover a row for its window mean, standard error, generation range, and
standardized difference from the window-averaged prediction:

```text
mean 0.5506 ± 0.0048 over generations 1,399–174,966
(173,568 generations; 1 SE); predicted 0.5448, +1.2 SE
```

- At the final generation, the window is the monitor's stopping window.
  It can be longer than the configured convergence window, because the
  monitor widens it to assess precision.
- "Not yet noise-adequate" marks a mean that has not met the precision
  requirement.
- The first-order autoregressive standard error used here accounts for
  correlation between neighboring generations. It can underestimate
  uncertainty when relaxation has several time scales. The stopping rule
  itself uses Geyer's initial positive sequence; see
  [how precise is the reported value?](../convergence.md#how-precise-is-the-reported-value)
- The row also shows $\Delta_p$ (observed minus predicted at the
  selected generation) and the trajectory mean squared error, for example
  $\Delta D_p=+0.012$, $\mathrm{MSE}=4.1\times10^{-5}$. The prediction
  is the expected trajectory when supported, otherwise the equilibrium
  line. The tooltip names which reference applies.

## Completed single runs

- The report shows each statistic, the stop outcome (converged or
  reached the cap), and the precision achieved.
- With more than one retained generation, the scrubber plays back or
  scrubs through the saved trajectory. Thinned runs scrub only retained
  generations.
- Deme selectors update the scatter plot and pairwise statistics at the
  scrubbed generation. Pairs not saved in `pairwise.json` are recomputed
  from the trajectory on demand.

## Completed batches

- The scatter plot pools the final states of all replicates.
- A table lists each replicate's status, final generation, and
  statistics. **Open** on a replicate shows it as a single run.
- Intervals are labeled "uncertainty across N independent replicates".
  They are Student's-$t$ intervals over replicate window means; see
  [the across-replicate interval](../convergence.md#the-across-replicate-interval).
- A batch can stop before `n_replicates` when **stop the batch early**
  is on; the number of replicates shown is the number that ran.

## Pause, save for later, and resume

- **Save for later** keeps the configuration as a saved run under
  **Saved runs** on Home. An incomplete or invalid configuration is
  saved as a Draft.
- **Run simulation** creates and starts a saved run. After **Pause**,
  **Run simulation** in the same tab resumes it from its checkpoint.
- A resumed run reproduces an uninterrupted run. The desktop app runs
  one saved run at a time.

See [saved runs in the desktop app](../run-lifecycle.md#saved-runs-in-the-desktop-app)
for every state and action.

## Files

**Open output folder** (or **File > Reveal output folder**) shows the
run's files: `manifest.json`, `report.json`, `trajectory.tlog`,
`pairwise.json`, and, for a batch, `summary.json` plus one
`replicate-NNN/` folder per replicate. See
[output schemas](../usage.md#output-schemas).
