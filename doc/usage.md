# Using `fim`

This guide covers every `fim` command and output, not what the simulator
models or why — see [what this simulates](../README.md#what-this-simulates)
for that first. For parameter types and defaults, use the
[configuration reference](configuration.md). Return to the
[project overview](../README.md) for installation and documentation links.

## Contents

- [Create a configuration](#create-a-configuration)
- [Run a simulation](#run-a-simulation)
- [Worked examples](#worked-examples)
- [Re-analyze a trajectory](#re-analyze-a-trajectory)
- [Check for updates](#check-for-updates)
- [Desktop GUI (`fim-gui`)](#desktop-gui-fim-gui)
- [Global flags](#global-flags)
- [Output schemas](#output-schemas)
- [Reproduce a run](#reproduce-a-run)
- [Troubleshooting](#troubleshooting)

## Create a configuration

```console
fim init [--output PATH] [--force]
```

`fim init` writes the documented development scenario. Without `--output`, the
path is `project-root/results/example-run.yaml`. Existing files are protected
unless `--force` is present.

## Run a simulation

```console
fim run CONFIG [-o DIRECTORY | --output DIRECTORY] [--quiet]
    [--workers N] [--sequential]
```

`CONFIG` is a YAML file described in
[configuration.md](configuration.md). Without `--output`, a timestamped
directory is created under `project-root/results/`. The timestamp affects only
the folder name and manifest metadata; it never affects the trajectory,
statistics, convergence decision, or deterministic run_id.

`--quiet` suppresses progress and artifact-path messages. Validation errors
name the offending key or value and return status 2. A run that reaches
max_generations also returns status 0 because it is a valid, inspectable
non-converged result.

### Batches (n<sub>replicates</sub> greater than one)

A config's n<sub>replicates</sub> (see [configuration.md](configuration.md#nreplicates))
controls whether one run or a whole batch executes:

- **n<sub>replicates</sub>: 1**: the four-file scalar-run contract below,
  directly in the output directory. Set this explicitly for a single,
  ordinary run with no batching at all.
- **n<sub>replicates</sub> greater than one (the default: `200`, with
  [replicate_tolerance](configuration.md#replicate_tolerance)'s own default
  of `0.01` usually stopping well short of it)**: each replicate gets its
  own `replicate-NNN/` subdirectory, keeping that same four-file contract,
  plus a batch-level `manifest.json` and `summary.json` — see
  [Output schemas](#output-schemas).

An unconfigured run — no `n_replicates` key at all — is therefore a batch,
not a single scalar run: this is a deliberate default (an unattended run
reports a real confidence interval instead of a single, uncertainty-free
point estimate) but it means every config below that wants the plain
scalar behavior this guide describes states `n_replicates: 1` explicitly,
the same way [`fim init`](#create-a-configuration)'s own starter
configuration does.

Batch replicates run in parallel by default, one worker per processor.
`--workers N` sets an explicit worker count; `--sequential` runs replicates
one at a time. Every replicate's trajectory, report, and statistics are
identical to running it alone with the same seed, so the worker count
affects only how long the batch takes.

With replicate_tolerance unset in the config, exactly n<sub>replicates</sub>
replicates run. With it set, the batch can stop earlier, once every watched
statistic's across-replicate confidence interval has tightened enough (see
[configuration.md](configuration.md#replicate_tolerance)) — the number of
`replicate-NNN/` subdirectories written can then be less than n<sub>replicates</sub>.

## Worked examples

Each example below is a complete config and the command that runs it: save
the YAML, run the command, and the reported values match those shown here,
because the same seed, parameters, and version always give the same
`report.json` (see [Reproduce a run](#reproduce-a-run)). Each example uses
a small `N`, `d`, and max_generations so it finishes in seconds, and a
seed distinct from [`fim init`](#create-a-configuration)'s starter config.
Each demonstrates one option, or one natural pair of options, from the
[configuration reference](configuration.md); a real study combines them
freely.

### Unequal island sizes with a migration hub

Four demes of very different size, connected by an explicit `d x d`
migration matrix rather than one shared rate — a small "hub" topology
where deme 4 is both the largest and the best-connected:

```yaml
N: [200, 200, 200, 800]
d: 4
m:
  - [0.95, 0.02, 0.02, 0.01]
  - [0.02, 0.95, 0.02, 0.01]
  - [0.02, 0.02, 0.95, 0.01]
  - [0.01, 0.01, 0.01, 0.97]
mu: 0.001
seed: 20260819
loci:
  - locus_id: 1
    length: 100
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 300
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run hub-island.yaml --output results/hub-island --quiet
```

Converges at generation 11 with D \sim 0.100. `manifest.json`'s `parameters.N`
and `parameters.m` record the exact per-deme sizes and matrix rows used —
compare them against a run with one shared `N/m` to see the effect of
unequal size and asymmetric connectivity on differentiation.

### Stepping-stone (spatial) migration

Six demes arranged on a ring, each migrating only with its two neighbors —
`fim.model.topology`'s compact sugar for a sparse migration matrix, instead
of hand-writing all 36 matrix entries:

```yaml
N: 150
d: 6
m:
  topology: ring
  rate: 0.05
mu: 0.001
seed: 20260819
loci:
  - locus_id: 1
    length: 100
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run stepping-stone.yaml --output results/stepping-stone --quiet
```

Converges at generation 10 with D \sim 0.124. Swap `topology: ring` for
`linear` to remove the wrap-around edge between deme 1 and deme 6.

### Stochastic migrant counts

By default, migration blends each deme's frequencies with an exact
`rate * N` fraction of its neighbors' — a deterministic step given that
generation's frequencies. migrant_sampling: stochastic instead draws the
migrant *count* from `Binomial(N, rate)`, adding a genuine, explicit source
of randomness some studies want counted:

```yaml
N: 100
d: 4
m: 0.05
mu: 0.001
seed: 20260819
migrant_sampling: stochastic
loci:
  - locus_id: 1
    length: 100
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run stochastic-migrants.yaml --output results/stochastic-migrants --quiet
```

Converges at generation 15 with D \sim 0.039. Re-run with migrant_sampling
removed (or set to `continuous`, the default) at the same seed to compare
against the deterministic-migration baseline directly.

### Finite-length alleles (the K-allele model)

By default (mutation_model: infinite_alleles), every mutation receives a
globally unique identity — the standard population-genetics idealization
for a locus long enough that two independent mutations essentially never
land on the same state. finite_alleles instead bounds a locus to
4<sup>length</sup> states and lets a mutation recur to one already present
elsewhere in the run — deliberately exercised here with a very short
3-base locus (only 4<sup>3</sup> = 64 states) and a high `mu` so recurrence is
actually likely within the run, not just theoretically possible:

```yaml
N: 100
d: 3
m: 0.02
mu: 0.02
seed: 20260819
mutation_model: finite_alleles
initial_allele_count: 2
loci:
  - locus_id: 1
    length: 3
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run finite-alleles.yaml --output results/finite-alleles --quiet
```

Converges at generation 12 with D \sim 0.207. See
[configuration.md](configuration.md#mutation_model) for how this differs
from a distance-based (stepwise) mutation model, which `fim` does not
implement.

### Per-base mutation rate across unequal locus lengths

μ<sub>b</sub> (mutually exclusive with `mu`) is a single per-base-pair mutation
probability; each locus derives its own `mu` from μ<sub>b</sub> and its own
`length` via mu = 1 - (1 - μ<sub>b</sub>)<sup>length</sup> — so two loci of very
different lengths do not silently mutate at the same rate:

```yaml
N: 150
d: 3
m: 0.02
mu_b: 0.00002
seed: 20260819
loci:
  - locus_id: 1
    length: 50
  - locus_id: 2
    length: 500
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run mu-b.yaml --output results/mu-b --quiet
```

Converges at generation 15 with D \sim 0.090. `results/mu-b/manifest.json`'s
`parameters.mu` records the two derived rates — `0.0009995` for the
50-base locus and `0.0099503` for the 500-base one — the expanded,
canonical form μ<sub>b</sub> is sugar for; μ<sub>b</sub> itself is never stored.

### Several convergence statistics

Watch more than one statistic and decide whether stopping needs every one
of them stable (convergence_combinator: all, the default) or just one
(`any`):

```yaml
N: 150
d: 3
m: 0.02
mu: 0.001
seed: 20260819
loci:
  - locus_id: 1
    length: 100
convergence_statistic: [D, G_ST]
convergence_combinator: any
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run multi-statistic.yaml --output results/multi-statistic --quiet
```

Converges at generation 16, with `report.json`'s converged_on recording
["D", "G<sub>ST</sub>"] — both were watched, and `any` means only one needed to
stabilize first.

### An adaptive replicate batch with a confidence interval

Rather than guessing how many replicate runs a confidence interval needs,
set n<sub>replicates</sub> well above the plausible requirement and let
[replicate_tolerance](configuration.md#replicate_tolerance) decide when
enough have run:

```yaml
N: 100
d: 5
m: 0.001
mu: 0.00003
seed: 20260819
loci:
  - locus_id: 1
    length: 100
convergence_statistic: D
convergence_window: 10
convergence_tolerance: 0.02
max_generations: 500
n_replicates: 50
replicate_minimum: 10
replicate_tolerance: 0.05
```

```console
fim run adaptive-batch.yaml --output results/adaptive-batch --sequential --quiet
```

Stops at exactly 10 replicates (replicate_minimum) — `D`'s 95% confidence
interval is already `0.218 +/- 0.048`, tighter than the requested `0.05`
half-width, so the remaining 40 possible replicates were never needed.
`results/adaptive-batch/summary.json` reports every statistic's own
interval; `results/adaptive-batch/replicate-001/` through `replicate-010/`
each hold the ordinary four-file scalar-run contract for that one
replicate. Drop `--sequential` to run the same batch across a worker
process per CPU instead — the computed numbers are identical either way
(see [Batches](#batches-nreplicates-greater-than-one)); only the wall-clock
time differs.

## Re-analyze a trajectory

```console
fim stats TRAJECTORY [--manifest PATH] [--generation N]
    [--q ORDER ...] [-o PATH | --output PATH]
```

The default manifest is `manifest.json` beside `TRAJECTORY`. The final
generation is analyzed unless `--generation` selects another persisted
generation. Repeat `--q` to evaluate several Hill-number differentiation
orders without re-running the simulation:

```console
fim stats run/trajectory.jsonl --q 0 --q 1 --q 2
```

JSON is printed to standard output. `--output` also writes the same result.
`--q 0` and `--q 2` always match the report's own K<sub>ST</sub> and `D`; `--q 1`
matches E<sub>ST</sub>, including the run's own deme_weighting setting — a
size-weighted E<sub>ST</sub> and an equal-weighted Differentiation_1 would
otherwise silently disagree on the same trajectory.

## Check for updates

```console
fim update --check
```

This explicit command queries the latest GitHub Release and prints its download
page when a newer version exists. It does not download or modify anything.
This is the application's only network path; `run`, `stats`, and `init` are
offline.

## Desktop GUI (`fim-gui`)

```console
fim-gui
```

A desktop application — three screens over a small, static local web page
(`fim`'s own bundled `webview` renderer, the OS's native web view; no
browser, no server beyond the one opt-in release check below) — that runs
the same simulations and reads the same `results/` folder as the commands
above. Install it alongside `fim` (`python -m pip install .` already
provides both console scripts), or launch it from a packaged executable by
opening it with no arguments — for example by double-clicking
`fim-windows-x64.exe`/`fim.app`, or running `fim-gui`/`fim --graphical` from
a Linux install. n<sub>replicates</sub> in the configuration is the only thing that
decides whether a run goes through the scalar or the batch path — there is
no separate "batch mode" toggle. Every screen calls the identical underlying
function this guide already documents; nothing here is a second
implementation.

The run view — where a configuration is built, a run proceeds, and its
result is shown — is one screen in exactly one of three states at a time,
not three separate screens reached by navigating away from each other:
building/editing a configuration ("initial"), a run in progress
("running"), and a finished run's own summary ("completed"). "Run
simulation" moves from initial to running from wherever it is clicked,
including straight from a previous run's own completed view — the same
button starts the next run, reusing whatever the form already holds, with
no separate "New run" step. Cancelling a run, or a run ending in an error,
freezes the view exactly as it last rendered, with a banner on top,
rather than switching anywhere else:

A persistent rail (Home, Configure, Explore, Run, Results, Compare, and —
set apart at the bottom — Help) is always visible along the window's own
left edge, current destination highlighted, reachable from any screen
including mid-run; Run and Results both point at the same run view
described in the table below (a run in progress shows through either one,
a finished run's own summary too — splitting them into two genuinely
distinct layouts is still on this project's own GUI roadmap). A read-only
parameter strip beneath the title bar always shows the current N/d/m/mu;
clicking any of the four jumps straight to Configure. A native File/Run/Help
menu bar duplicates the everyday actions a mouse-and-rail user already has,
for keyboard-shortcut users: File covers configuration/run file-system
actions (New/Open/Save configuration, Open run…, Reveal output folder,
Quit); Run covers the simulation lifecycle (Run simulation, Cancel run);
Help covers this guide and the [configuration reference](configuration.md)
(rendered in-app — see the Help screen row below), a link to the full
documentation on GitHub, Check for updates, and About. Every menu item
reuses the exact same action the matching on-screen control already
performs.

| Screen/state | What it does | Same as |
|---|---|---|
| Home | A recent-runs list (newest first), each row carrying a config-summary and a final-statistics/outcome column read from that run's own `report.json`/`summary.json` — a batch row's outcome is its own confidence interval, and is expandable to its individual replicates, each independently reachable for re-analysis. Two shortcut cards, "New run" and "Explore," sit above the list. A recent-runs row, or browsing for a `trajectory.jsonl` directly, re-renders its summary and scatter (and, for a multi-generation run, its own scrubber) at any persisted generation, with the same optional differentiation-`q` sweep. Reachable from the rail's own Home button, or the File menu's "Open run…", from any screen | [Re-analyze a trajectory](#re-analyze-a-trajectory) |
| Configure | Two always-visible, independently scrollable panels: FIM parameters (N — scalar or a per-deme table, d, m, mu, seed — the five values that together are "the finite island model") and Structure (initial conditions, migrant sampling, mutation model, deme weighting, loci, the full convergence group, the full batch/replicate group, a light/dark override, and significant digits) — one per [configuration reference](configuration.md) section, no dialog to open for any of them; every field and mode-selector group has a hover/focus tooltip. "Load configuration…"/"Save configuration…" read and write the exact YAML file format above, "Load example…" opens the Presets picker (each preset also viewable as plain YAML, with a copy-to-clipboard action, and a loaded preset can be duplicated under a new name), and "▶ Run"/"🔮 Explore" jump to those destinations with the configuration exactly as shown. An invalid field on "Run simulation" (from anywhere) navigates here and marks the specific field, not only the section it lives in | [Create a configuration](#create-a-configuration) |
| Run view — running | A live scatter plot of the run's own current-generation frequencies (or, for a batch, every replicate's frequencies pooled onto one plot, filling in as replicates advance), with a generation progress indicator and a "Cancel" button — the window stays responsive throughout; the same axis selectors `completed` (below) has, live — picking a pair affects every subsequent push for the rest of the run, not just a one-time snapshot. For a scalar run, a statistic-vs-generation trajectory panel grows alongside the scatter as the run advances, plotting all six report statistics, each beside its own predicted-equilibrium reference line (D, G<sub>ST</sub>, E<sub>ST</sub> only — the three with a closed-form prediction). Cancelling, or the run ending in an error, leaves this same view showing exactly as it last rendered, with a banner on top | `run`'s own progress/error output, on one screen instead of terminal lines |
| Run view — completed | A scalar run's summary (all six named statistics, convergence outcome, each shown as a meter against the same `[0, 1]` scale the confidence-interval bars below use) beside the canonical scatter plot and the same trajectory panel described above — replaced, once the run finishes, by the real persisted trajectory, and showing the within-run σ band (a shaded region plus its own `mean [lower, upper]` caption) whenever [sigma_band_multiplier](configuration.md#sigma_band_multiplier) was set — or — for a batch — a pooled scatter across every replicate's final state beside a replicate table (status, final generation, every named statistic) and each statistic's across-replicate confidence interval as a meter, explicitly labeled "uncertainty across N independent replicates" so it is never confused with the within-run σ band; either way, one panel (Deme 1 vs. Deme 2 by default) with a labeled, numbered `0.0`-`1.0` probability scale on both axes; axis selectors on the plot choose which two demes to compare directly, and selecting Deme 1 vs. Deme 2 again returns to the default panel; a scalar run with more than one persisted generation auto-populates a play/pause-and-scrub time slider over the persisted trajectory in the background, with no separate button to reach it; each batch replicate row's own "Open" button reaches this same view for that one replicate; "Open output folder" reveals the run's own artifacts (a batch's own `summary.json` and every replicate subdirectory, for a batch) | [Output schemas](#output-schemas), [Batch `summary.json` and `manifest.json`](#batch-summaryjson-and-manifestjson) |
| Explore | Four fields (N, d, m, mu) and a theoretical-prediction table (D, G<sub>ST</sub>, E<sub>ST</sub>, and Whitlock's identity-recovery half-life) that update the instant a field is committed — no simulation ever runs, so this never takes measurable time regardless of N or d. A sweep curve plots the selected field's predicted D/G<sub>ST</sub> across a fixed range, with the current configuration marked. Reachable from the rail's own Explore button, or "🔮 Explore" on Configure, from any screen; "Back" returns to whichever screen was showing, not a fixed default | No CLI equivalent — a direct `fim.statistics` call from Python or a script is the closest terminal equivalent |
| Compare | Pick two or more previously completed runs from a recent-runs list, then overlay their final-state scatter panels as small multiples with a legend naming whichever configuration field(s) actually differ across the selection, plus a trajectory-over-generations overlay (one statistic at a time, one color per run, selectable from the same six named statistics) — "how does the conclusion change as I vary this one knob," on real simulated runs, no re-run needed. Reachable from the rail's own Compare button from any screen; "Back" returns to whichever screen was showing | No CLI equivalent — comparing several `trajectory.jsonl`/`report.json` files by hand is the closest terminal equivalent |
| Help | This guide and the [configuration reference](configuration.md), rendered in-app with working cross-links; every other doc opens on GitHub in the OS default browser instead. Reachable from the rail's own Help button, or the Help menu, from any screen; "Back" returns to whichever screen was showing, not a fixed default | No CLI equivalent — the terminal reads these same two files directly |

A GUI-authored run with the same parameters and seed produces byte-identical
`trajectory.jsonl`/`report.json` to the same configuration run from the
terminal — see [Reproduce a run](#reproduce-a-run). The GUI performs network
access only for the same explicit, opt-in release check the terminal's `fim
update --check` performs (Help menu → "Check for updates") — otherwise, like
the CLI, none at all.

On Linux, the GUI needs WebKitGTK, a system package most desktop
distributions already have installed — see
[installation alternatives](../install/README.md) if `fim-gui` reports it
is missing rather than opening a window.

Launched with no flags of its own, the GUI's own operational log is
configured from two environment variables instead — `FIM_LOG_LEVEL` and
`FIM_LOG_OPTIONS`, the exact equivalents of `-l`/`-L` below, set before
launching (a modified shortcut's own "Target" field, or a wrapper
script). See [operational logging design](fim-logging-design.md) §5.

### If the window closes but `fim` keeps running

Closing the window should end the program immediately. If something
inside it fails to stop, `fim` guards against being left running
invisibly — with no window to click and no obvious way to quit — by
forcing itself to exit about 20 seconds after the window closes. When
that happens it prints a line explaining why, so the cause can be
reported rather than guessed at:

```text
fim: shutdown did not complete within 20s; forcing exit
```

Your results are unaffected. Everything a run produces is written to its
output directory as the run proceeds, so a forced exit after the window
is already closed cannot lose any of it.

A diagnostic report is written at the same time, to `logs/fim.log` as well
as to the terminal — so it is kept even when `fim` was started from an icon
or shortcut with no terminal attached. If this happens to you, please report
it: the section of that file beginning `shutdown deadman fired` names what
failed to stop, and is what makes the cause findable. See
[known issues](../ISSUES.md#intermittent-hang-during-gui-shutdown).

If you are investigating such a shutdown yourself and need the process to
stay alive rather than be terminated, set `FIM_GUI_SHUTDOWN_TIMEOUT` to
the number of seconds to allow, or to `0` to wait indefinitely:

```console
FIM_GUI_SHUTDOWN_TIMEOUT=0 fim --graphical
```

### Saved preferences

The GUI remembers four things between launches: Configure's own Significant
digits setting, the light/dark override (absent/`null` means "follow the
OS," the default), whether the first-launch welcome panel has already been
dismissed, and the last configuration you successfully clicked "Run
simulation" with — a fresh launch's own Run view starts from that
configuration rather than from the built-in starter values, and a genuinely
first launch (welcome not yet dismissed) shows the welcome panel offering
"Try a worked example…" or "Start from scratch." File menu → "New
configuration" always resets to the built-in starter values regardless of
what is saved; it is the one action that ignores the saved configuration on
purpose.

Nothing scientific is stored here: a run's own configuration, seed, and
results always live in that run's own `manifest.json`/`trajectory.jsonl`
under `results/`, exactly as described throughout this guide. This file
holds only the GUI conveniences above.

It lives in the platform's normal per-user settings location — you do not
need to find or edit it for ordinary use:

| Platform | Location |
|---|---|
| macOS | `~/Library/Application Support/fim/preferences.json` |
| Windows | `%APPDATA%\fim\preferences.json` |
| Linux | `$XDG_CONFIG_HOME/fim/preferences.json` (usually `~/.config/fim/preferences.json`) |

If this file is ever unreadable — edited by hand into invalid JSON, or left
over from an incompatible future version — the GUI notices on the next
launch, shows a dismissible banner naming the problem, and starts from
built-in defaults instead of failing to open or guessing at a broken value.
The unreadable file is never deleted: it is renamed alongside itself with a
timestamp (`preferences.invalid-<timestamp>.json`) so nothing is lost, and a
fresh, working file is written in its place. Deleting the whole `fim`
folder shown above is always a safe way to reset both saved preferences
from scratch; it never touches anything under `results/`.

## Global flags

```console
fim --help
fim --version
fim -l debug run myrun.yaml
fim -l info -L file=none run myrun.yaml
```

`--version` comes from the same `version.txt` value used by packages,
manifests, and release tags.

`-l`/`--log LEVEL` (`debug`, `info`, `warn`, `error`, or `critical`;
default `warning`) and `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`
control this program's own operational log — separate from, and
unaffected by, `run`'s own `--quiet` and progress/artifact messages
above. Every run already writes a rotated log file at
`project-root/logs/fim.log` by default, whether or not `-l`/`-L` is
given at all; `-l debug` raises what reaches it (and the terminal),
`-L file=none` turns the file off entirely. Both flags must appear
*before* the subcommand name (`fim -l debug run ...`, not
`fim run ... -l debug`). Full flag reference, every `-L` key, and where
each log call in the source lives:
[operational logging design](fim-logging-design.md).

## Output schemas

### `trajectory.jsonl`

One JSON object is appended for every nonzero frequency:

```json
{"allele_id":0,"deme":1,"frequency":0.5,"generation":0,"locus_id":1,"run_id":"run-example"}
```

| Field | Type | Meaning |
|---|---|---|
| run_id | string | Deterministic identity for one parameter set and seed |
| `generation` | integer | Generation, beginning at 0 |
| `deme` | integer | One-based deme number |
| locus_id | integer | Configured positive locus identifier |
| allele_id | integer | Opaque identity-only allele label |
| `frequency` | number | Positive allele frequency |

### `manifest.json`

The manifest records the complete parameter mapping, seed, software version,
UTC start/end timestamps, generation, watched statistic, and whether
convergence or the hard cap ended the run. It also carries:

- schema_version: the manifest's own shape version.
- convergence.generation_count: how many distinct generations the run
  actually wrote to `trajectory.jsonl` (`convergence.generation + 1` for
  every run, since no generation is ever skipped — recorded explicitly
  rather than left implicit).
- `artifacts`: the SHA-256 digest and byte count of `trajectory.jsonl`,
  `report.json`, and `scatter.png` as they existed at the moment the run
  finished writing and flushing them. `fim stats` recomputes and checks the
  trajectory's digest (and its generation count) before reading a single
  row, so a trajectory edited, truncated, or replaced after the run
  completed is refused with a clear error rather than re-analyzed
  silently.

output_directory (the whole four-file set for a scalar run, or the whole
`replicate-NNN/` plus `summary.json`/`manifest.json` tree for a batch) is
built in a hidden temporary location beside the target path and published
with a single atomic rename only once every file in it is flushed and
`manifest.json` has been written last with its `artifacts` digests. An
interrupted run — an exception, `^C`, or a killed process — therefore never
leaves a partial directory at the target path: it is either not there at
all, or complete. This guarantee covers process-level interruption, not
an unclean power loss: nothing in the write path calls `fsync`, so on
power loss a directory can look complete (the rename itself is atomic)
while some file inside it has content that never reached physical disk.

**Compatibility with `fim` 1.0.0 output.** `fim` 1.0.0, the only version
released before schema_version and `artifacts` existed, wrote manifests
with neither field. `fim stats` refuses such a manifest — it has no
digest to verify the trajectory against — with an error naming both the
cause and that there is no automated migration: re-run the same
configuration (the manifest's own `parameters`) with the current `fim`
to get a manifest this version can read and verify.

### `report.json`

The final report contains:

- run identity, generation, convergence flag, watched statistic, and reason;
- H<sub>S</sub>, H<sub>T</sub>, and the correctly partitioned H<sub>ST</sub>;
- G<sub>ST</sub> (`null` only when *every* tracked locus is fixed for the same
  allele in every deme; with several loci, a locus that is fixed on its
  own does not blank out the others — it is dropped and the remaining,
  genuinely polymorphic loci are averaged);
- Jost's `D`, entropy differentiation E<sub>ST</sub>, and allele-number
  differentiation K<sub>ST</sub>.

Multiple loci are independent repeats. H<sub>S</sub>, H<sub>T</sub>, H<sub>ST</sub>, E<sub>ST</sub>,
and K<sub>ST</sub> are always each locus's own arithmetic mean. `D` and G<sub>ST</sub>
instead follow [locus_aggregation](configuration.md#locus_aggregation):
by default (`ratio_of_means`), H<sub>S</sub>/H<sub>T</sub> are pooled across loci
first and one `D`/G<sub>ST</sub> is computed from those pooled values, not
averaged per-locus ratios; the opt-in `mean_of_ratios` restores the
per-locus-ratio-then-average behavior. `D` and K<sub>ST</sub> always use equal
deme weighting. deme_weighting affects E<sub>ST</sub>.

### `scatter.png`

- `d = 2`: direct Deme 1 versus Deme 2 scatter with a diagonal reference.
- `d = 3`: direct three-dimensional scatter.
- `4 <= d <= 6`: every pairwise deme projection.
- `d > 6`: direct Deme 1 versus Deme 2 scatter, same as `d = 2` — not a
  PCA projection.

One point represents one `(locus, allele)` pair. Coincident points are enlarged
and annotated.

#### What the marker colors mean

Points are colored to answer one question at a glance: which allele is the
most common one?

| Color | Meaning |
|---|---|
| Blue | The most frequent allele in either of the two demes shown |
| Orange | Every other allele |

Two blue points is normal and correct. Each of the two demes on the plot
gets its own most frequent allele marked, and they are often different
alleles — that difference is frequently the interesting result. You will see
a single blue point when both demes happen to agree on the same allele.

If two or more alleles are exactly tied for most frequent, only the first is
marked, so that one plot never implies more "most frequent" alleles than it
has demes. The choice is stable: the same data always marks the same allele,
and the legend states the rule.

Blue does not mean "important", "significant", or "above a cutoff". It marks
a maximum, and a maximum exists in every data set — including one where all
the alleles are rare and nearly equal.

### Batch `summary.json` and `manifest.json`

Written only for n<sub>replicates</sub> greater than one, alongside the
`replicate-NNN/` subdirectories, each of which holds the four scalar-run
files above.

`summary.json` maps each reported statistic name (`D`, G<sub>ST</sub>, E<sub>ST</sub>,
K<sub>ST</sub>, H<sub>S</sub>, H<sub>T</sub>, H<sub>ST</sub>, G<sub>s</sub>, G<sub>d</sub>) to its across-replicate
confidence interval:

```json
{
  "D": {
    "mean": 0.643,
    "half_width": 0.021,
    "low": 0.622,
    "high": 0.664,
    "sample_std": 0.133,
    "sample_count": 40,
    "confidence": 0.95
  }
}
```

`half_width` and `sample_std` answer two different questions, and it is
worth keeping them apart:

- `half_width` is how precisely the **average** is known — the "± 0.021"
  half of a "0.643 ± 0.021" report. Running more replicates shrinks it.
- `sample_std` is how much the **replicates themselves** differ from one
  another. Running more replicates does not shrink it, because it
  describes the model's own run-to-run variability rather than your
  measurement of it.

`sample_std` can also be `null`, meaning "this interval has no single
honest value to report here." That happens only for an interval built by
resampling rather than by the ordinary Student's-t formula (see
`fim.engine.bootstrap_replicate_summary`), whose two sides are not
generally the same width — for such an interval, read `low` and `high`
as the real bounds rather than treating `mean` ± `half_width` as the
whole story. Every interval the command line writes today reports a real
number.

A `summary.json` written by an older version of this program has no
`sample_std` field at all. Nothing needs converting: every tool here
reads such a file as though the value were `null`.

G<sub>ST</sub> can have a smaller sample_count than the other statistics: a
replicate whose locus is monomorphic across every deme reports G<sub>ST</sub> as
`null` in its own `report.json`, and that replicate is excluded from
G<sub>ST</sub>'s interval rather than papered over with a substitute value. A
statistic left with fewer than two defined replicates is omitted from
`summary.json` entirely.

The batch's own `manifest.json` (distinct from each replicate's own) records
schema_version, the batch run_id, every replicate_run_ids entry,
replicate_count, the shared `parameters`, batch start/end timestamps, and
software_version — not a per-run convergence outcome, since each replicate
has its own. Like a scalar run's manifest, it also carries `artifacts`: the
SHA-256 digest and byte count of `summary.json` and of each replicate's own
`manifest.json` (keyed `replicate-NNN`), recorded once every one of them is
flushed, so an edited, truncated, or replaced batch-level artifact is
detectable the same way a scalar run's is. Under parallel execution — the
CLI default — an adaptive replicate_tolerance stop can leave a worker that
had already started its own `replicate-NNN/` directory before the stop was
decided; that directory is pruned before publishing, so the `replicate-*`
subdirectories actually present always equal replicate_run_ids exactly.

## Reproduce a run

1. Copy `manifest.json`.
2. Use its `parameters` object as a new YAML config.
3. Run the same `fim` version shown in software_version.
4. Compare `trajectory.jsonl` and `report.json` byte for byte.

Given the same version, parameters, and seed, those files are identical.
Manifest timestamps may differ.

## Troubleshooting

- **Unknown key:** compare the named key with
  [configuration.md](configuration.md); typos are never ignored.
- **Output already exists:** select a new output directory. `fim` refuses
  to publish into one that already exists at all, even if empty — never
  appended, overwritten, or reused.
- **Reached the cap:** inspect the trajectory and report, then increase
  max_generations, relax the tolerance, increase the window, or select
  another convergence statistic based on the study's needs.
- **Windows warning:** verify the release checksum before running the unsigned
  executable. See [SECURITY.md](../SECURITY.md).
