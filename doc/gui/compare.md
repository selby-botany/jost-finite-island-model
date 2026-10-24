# Compare runs and sweeps

Comparison reads saved results. Nothing is rerun, and nothing is saved:
reopening a comparison recomputes it.

## Contents

- [Compare runs](#compare-runs)
- [Compare sweeps](#compare-sweeps)

## Compare runs

1. Choose **Compare** in the rail.
2. Select two or more completed runs from the list (**Run**, **Ended**,
   **Outcome**), then choose **Compare**.

The screen shows two overlays:

- **Small multiples:** one final-state scatter panel per run, deme 1
  against deme 2. Panels name the parameters that differ between runs.
- **Trajectory overlay:** one statistic over generations, one color per
  run. **Statistic** selects $D$, $G_{ST}$, $E_{ST}$, $K_{ST}$, $H_S$,
  $H_T$, or $H_{ST}$; switching redraws without another computation.

Runs with different seeds, configurations, or retention are compared as
saved. Check that differences in N, ploidy, or migration convention are
intended before you interpret a difference; see
[implementation conventions](conventions.md).

## Compare sweeps

Start on Home, in either of two ways:

- **Compare sweeps…** on an Experiment's row compares the sweep Studies
  inside it.
- **Select**, tick sweep Studies from any Experiments, then choose
  **Compare selected sweeps…**.

The comparison screen draws one curve per sweep, each with a distinct
color and marker, over the axis you pick. Other shared axes are held at
a value. It has the error-bar, scale, and theory controls of the sweep
results screen; each theory curve uses its own sweep's settings.
Degraded and unreliable points stay flagged.

With exactly two sweeps, a second panel shows their difference (first
minus second) with a 95% Welch interval and a zero line. That interval
assumes the sweeps' replicates are independent. Two sweeps built from
the same seed and grid share seeds point for point; the command-line
`fim sweep compare` reports whether seeds were different, shared, or
partly shared, and does not adjust for sharing.

Sweeps can be compared only on axes they all vary, at values they
share, and with the same ploidy. Otherwise the comparison is refused
with the reason. See [sweep a parameter](../usage.md#sweep-a-parameter)
for `fim sweep compare`.
