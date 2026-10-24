# Sweeps

A sweep runs one configuration over a set of parameter values. Every
point is an ordinary run, filed in one Study. The desktop app and
`fim sweep` share the same specification, storage, and results; the
command-line equivalent of each action is given in parentheses. Full
semantics are in [sweep a parameter](../usage.md#sweep-a-parameter).

## Contents

- [Set up a sweep](#set-up-a-sweep)
- [Recording and disk](#recording-and-disk)
- [Seeds and reuse](#seeds-and-reuse)
- [Progress](#progress)
- [Results](#results)
- [Point health](#point-health)
- [Continue, retry, and recompute](#continue-retry-and-recompute)
- [Run names](#run-names)

## Set up a sweep

1. In Configure, tick **Sweep** and choose **Set up sweep…**.
2. Under **What varies**, choose a parameter and its values for each
   axis. **Add axis** adds another; there is no limit. Common parameters
   are listed first; every other sweepable setting is under **More
   parameters**. List- and group-valued settings (`p_0`, `mu_b`, `loci`,
   `expert`) and `seed` cannot be swept.
3. **Combine the axes**: **Every combination (a grid)**, with the first
   axis changing slowest, or **Together, value by value (equal
   counts)** (`strategy: zip`).
4. Check the live point count. **Show every point** lists them with
   their validity; an incompatible combination, such as a torus whose
   rows × columns differs from d, is listed with its reason
   (`fim sweep plan`).
5. Choose **Done**, then **Run**. The sweep runs in the Study selected at
   the bottom of Configure. A Study holds at most one sweep.

`N` on a sweep axis counts individuals per deme, as everywhere else. A
`Nm` axis sets $m = Nm/N$ with N in individuals and replaces `m`.

Explore's **Sweep this for real** fills the same setup; see
[Explore](explore.md#from-prediction-to-simulation).

## Recording and disk

**Keep** chooses what each point stores:

| Choice | YAML `retention` | Stores |
|---|---|---|
| Everything | `full` | Every generation |
| Every Nth generation | `thinned` | One generation in every stride after a start |
| Recent generations, thinning further back | `tapered` | The most recent generations in full, thinning further back; frames grow with the logarithm of run length |
| Statistics only | `statistics` | No trajectory log; report, summary, scatter plot, and a decimated history of at most 2,048 generations |

For **tapered**, **Recent generations kept in full** sets `keep_last`,
and **Keep the burn-in frames too** keeps the transient. **Statistic
history** is **Compact** (the sweep default: values rounded to about a
hundredth of the precision, which changes no reported answer) or
**Exact**.

The dialog shows **Time**, **Disk**, and what was **Measured** for the
points still to run. Ranges reflect unknown convergence times. The time
estimate comes from a brief probe of this computer's engine ("Measuring
the engine…"); results are cached in `sweep-throughput.json` beside your
preferences.

A point whose existing run kept less than the sweep requests is
recomputed; a run that kept more is reused.

## Seeds and reuse

- **Seeds**: **Independent points (recommended)** gives point $i$ the
  seed `base seed + i × n_replicates`, so no two points share a replicate
  seed. **The same seed at every point** (`seed_policy: same`) uses the
  base seed throughout.
- A point's run ID is a hash of its configuration. A sweep that overlaps
  an earlier one reuses identical points, including seed, made by the
  same software version.
- **Points at once** sets concurrency; **Automatic** fills the cores.
  Results do not depend on it.

## Progress

The progress view shows each point's state, the time remaining, and each
finished point's duration. Points with the longest estimated duration
start first; scheduling never changes a point's configuration or result.
A failed point is recorded and the sweep continues. A point that fails
for a transient reason is retried once. **Cancel sweep** stops the sweep
and pauses the points in flight; finished points stay in the Study.

A sweep locks its Study while it runs. Another process that tries to run
the same Study stops with a message instead.

## Results

**View results** opens the results card. **View** selects:

- **Final values:** the chosen **Statistic** **Across** one axis. To
  compare values of a second axis, set **and** to "(none: a line)" and
  choose **Curves by**. Each value gets a color, marker shape, error
  bars, and an optional theory curve. Two axes can also be drawn as a
  heat map, whose **Show** menu offers **Simulation**, **Theory**,
  **Simulation minus theory**, and the difference as a fraction of
  theory.
- **How each point got there:** the statistic against generation, one
  curve per **Curves by** value. The band is the interval of the mean
  over replicates still running; a dotted line marks the end of burn-in;
  a dashed stretch marks where fewer than half the replicates are still
  running; a hollow end marker marks a point whose replicates did not
  all settle. The x axis is logarithmic automatically for runs of 1,000
  generations or more (`fim sweep trajectories`).

**Error bars** show **Interval of the mean** (95%, the default),
**Spread (standard deviation)** across replicates, or **Both**.
**x scale** and **y scale** are **Automatic**, **Linear**, or **Log**;
Automatic uses a log axis for a geometric series. **Show theory** adds
the closed-form value for $D$, $G_{ST}$, $E_{ST}$, $H_S$, and $H_T$.

With the chart focused, the arrow keys move between points and Enter
opens a point's run. Click a curve or point to open its run.

- **Save table…** writes the results for every shown statistic, with
  the closed-form columns, plus a `.meta.json` file that records the
  sweep settings, software version, and column meanings
  (`fim sweep export --with-theory`).
- **Save chart…** saves the chart as drawn, as PNG.
- **Show in Home** selects the sweep's Study on Home.

## Point health

| Mark | Meaning | Drawn as |
|---|---|---|
| `healthy` | Every replicate settled and the interval meets `precision` | Filled marker |
| `degraded` | Some replicates reached the generation cap, or the interval is wider than `precision` | Hollow ring |
| `unreliable` | Every replicate reached the cap before settling | Ring with an X |

A point that is not healthy is flagged, never omitted. The table's
**health** column and the hover text give the reason.

## Continue, retry, and recompute

On Home, a Study that holds a sweep offers:

- **Continue sweep:** runs the points still missing
  (`fim sweep resume`).
- **Retry failed (N):** reruns only the failed points
  (`fim sweep resume --retry-failed`).
- **Recompute all…:** after confirmation, recomputes every finished point
  and compares it with its stored run, bit for bit
  (`fim sweep resume --rerun`). A match replaces the old run; a
  difference keeps both and writes `reproducibility.json`.

Each opens the sweep's progress view. To compare two or more sweeps, see
[compare sweeps](compare.md#compare-sweeps).

## Run names

Each point's run is named after the Study and its values, for example
`Migration and number of demes sweep m=0.001, d=8`. Its description
locates it in the sweep: `Point 8 of 21 of the sweep "Migration and
number of demes": m=0.001 of m=[0.0001..0.1] (7 values); d=8 of
d=[4, 8, 16].` A reused run keeps its existing name.
