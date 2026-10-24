# Settings and saved preferences

**Settings**, at the top of the window, holds preferences and defaults
for new configurations. Saved Settings never change existing results or
a configuration you have loaded. To change one configuration's run
settings, use [Run settings…](configure.md#run-settings) in Configure.

## Contents

- [Startup and display](#startup-and-display)
- [Statistics shown](#statistics-shown)
- [Background work](#background-work)
- [Storage location](#storage-location)
- [Defaults for new configurations](#defaults-for-new-configurations)
- [Where preferences are stored](#where-preferences-are-stored)

## Startup and display

- **At startup**: **Restore the last submitted configuration** opens the
  configuration last started with **Run simulation**; **Restart from the
  starter configuration** uses built-in model values. Both apply saved
  run settings, not those of the restored configuration.
- **Default ploidy** for new configurations; **Ask me each time**
  requires a choice in each configuration before it runs.
- **Run card graph columns** and **Scatter plot points** (marker style;
  alleles at the same coordinates are counted together).
- **significant digits** rounds displayed values only; saved files keep
  full precision.
- **appearance**: **Follow system**, **Light**, or **Dark**.

The first-launch welcome panel offers **Examples…** or **Start from
scratch**. **File > New configuration** uses starter model values and
saved run settings, ignoring the last submitted configuration.

## Statistics shown

**Statistics shown** selects statistics for the statistics panel,
results tables, trajectory chart, and sweep charts. Presets are
**Essentials**, **Differentiation**, **Nei distances**, and
**Everything**; a filter box and checkboxes select individually.
**Choose…** beside a statistics table opens this section directly. Final
saved results include hidden statistics.

Showing any of $E_{ST}$, $K_{ST}$, $A_{\mathrm{CGD}}$, $\delta_G$, or $I$
enables per-generation tracking of all five for new runs, which
increases run time, especially with many demes. Hiding all five
disables that tracking unless one is watched for convergence. See
[`track_expensive_statistics`](../configuration.md#track_expensive_statistics).

**Save every deme pair for runs with up to** N demes sets the
`pairwise.json` limit (default 1024). The file grows with $d^2$: about
0.5 MB at 100 demes and 52 MB at 1024, per run and per replicate. Above
the limit no matrix is saved; any single pair can still be recomputed
from the saved trajectory.

## Background work

These settings apply to jobs started after you save them.

- **Run priority**: **Normal**, **Lower**, or **Lowest**. Lowest can
  make simulations several times slower on macOS. The interface always
  keeps normal priority. Each job's effective priority appears in its
  tab.
- **Keep CPU cores free**: whole logical cores or a percentage, rounded
  up to whole cores. The worker budget is the remaining cores, at least
  one.
- **Keep memory free**: **Automatic** keeps the larger of 2 GiB and 10%
  of physical memory free; you can instead reserve GiB or a percentage.
  Before a job starts, the app estimates its added memory and asks if
  the estimate would cross the reserve. While work runs, a warning
  appears if available memory falls below the reserve; each listed job
  has a **Pause** button. Runs pause at a checkpoint; sweeps stop at a
  resumable point.

## Storage location

**Storage location** holds results, logs, and Study and Experiment
data. **Change…** takes effect the next time you open `fim`. When a
`--root` or `--results-directory` flag, or `FIM_HOME` or
`FIM_RESULTS_DIRECTORY`, sets the location, the field is locked; change
or unset that instead. See
[where results, logs, and preferences are written](../usage.md#where-results-logs-and-preferences-are-written).

## Defaults for new configurations

These are the defaults that **Run settings…** starts from:
**execution engine**, **n_replicates**, **max generations**,
**confidence**, **max workers**, and **max concurrent replicates**;
under **Convergence**, **convergence burn-in**, **precision**,
**expected value**, **precision method**, and **replicate averaging
window**; under **Expert: engine tuning**, **JIT**, **auto-vector min
d**, and **auto-vector max capacity**; and under **Storage**,
**trajectory retention**, **trajectory stride**, and **thinning
start**.

**Expert settings** control convergence policy, statistical
calculations, and storage. Their values are recorded in the manifest and
contribute to the run's identity. **Reset all to default** restores the
shipped values. See [`expert`](../configuration.md#expert).

Each control's meaning is in the
[configuration reference](../configuration.md); the correspondence is
listed in [run settings](configure.md#run-settings). **max workers**
describes this computer, not the run, and never changes results.

## Where preferences are stored

Preferences hold defaults and interface state. They are not the record
of a run; each run's configuration, seed, and results are in its own
folder under `results/`.

| Platform | Location |
|---|---|
| macOS | `~/Library/Application Support/fim/preferences.json` |
| Windows | `%APPDATA%\fim\preferences.json` |
| Linux | `$XDG_CONFIG_HOME/fim/preferences.json` (usually `~/.config/fim/preferences.json`) |

`--preferences-file` or `FIM_PREFERENCES_FILE` overrides the location.
If the file contains invalid JSON or an unsupported format, the next
launch shows a dismissible banner and uses built-in defaults. The invalid
file is kept as `preferences.invalid-<timestamp>.json`. Removing the
preferences folder resets Settings without changing `results/`.
