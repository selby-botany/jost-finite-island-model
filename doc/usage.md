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
statistics, convergence decision, or deterministic `run_id`.

`--quiet` suppresses progress and artifact-path messages. Validation errors
name the offending key or value and return status 2. A run that reaches
`max_generations` also returns status 0 because it is a valid, inspectable
non-converged result.

### Batches (`n_replicates` greater than one)

A config's `n_replicates` (see [configuration.md](configuration.md#n_replicates))
controls whether one run or a whole batch executes:

- **`n_replicates: 1`** (the default): the four-file scalar-run contract
  below, directly in the output directory.
- **`n_replicates` greater than one**: each replicate gets its own
  `replicate-NNN/` subdirectory, keeping that same four-file contract, plus
  a batch-level `manifest.json` and `summary.json` — see
  [Output schemas](#output-schemas).

Batch replicates run in parallel by default, one worker per processor.
`--workers N` sets an explicit worker count; `--sequential` runs replicates
one at a time. Every replicate's trajectory, report, and statistics are
identical to running it alone with the same seed, so the worker count
affects only how long the batch takes.

With `replicate_tolerance` unset in the config, exactly `n_replicates`
replicates run. With it set, the batch can stop earlier, once every watched
statistic's across-replicate confidence interval has tightened enough (see
[configuration.md](configuration.md#replicate_tolerance)) — the number of
`replicate-NNN/` subdirectories written can then be less than `n_replicates`.

## Worked examples

Each example below is a complete config and the command that runs it: save
the YAML, run the command, and the reported values match those shown here,
because the same seed, parameters, and version always give the same
`report.json` (see [Reproduce a run](#reproduce-a-run)). Each example uses
a small $N$, $d$, and $max_generations$ so it finishes in seconds, and a
seed distinct from [`fim init`](#create-a-configuration)'s starter config.
Each demonstrates one option, or one natural pair of options, from the
[configuration reference](configuration.md); a real study combines them
freely.

### Unequal island sizes with a migration hub

Four demes of very different size, connected by an explicit $d x d$
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
```

```console
fim run hub-island.yaml --output results/hub-island --quiet
```

Converges at generation 11 with $D \sim 0.100$. `manifest.json`'s `parameters.N`
and `parameters.m` record the exact per-deme sizes and matrix rows used —
compare them against a run with one shared $N/m$ to see the effect of
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
```

```console
fim run stepping-stone.yaml --output results/stepping-stone --quiet
```

Converges at generation 10 with $D \sim 0.124$. Swap `topology: ring` for
`linear` to remove the wrap-around edge between deme 1 and deme 6.

### Stochastic migrant counts

By default, migration blends each deme's frequencies with an exact
$rate * N$ fraction of its neighbors' — a deterministic step given that
generation's frequencies. `migrant_sampling: stochastic` instead draws the
migrant *count* from $Binomial(N, rate)$, adding a genuine, explicit source
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
```

```console
fim run stochastic-migrants.yaml --output results/stochastic-migrants --quiet
```

Converges at generation 15 with $D \sim 0.039$. Re-run with `migrant_sampling`
removed (or set to `continuous`, the default) at the same seed to compare
against the deterministic-migration baseline directly.

### Finite-length alleles (the K-allele model)

By default (`mutation_model: infinite_alleles`), every mutation receives a
globally unique identity — the standard population-genetics idealization
for a locus long enough that two independent mutations essentially never
land on the same state. `finite_alleles` instead bounds a locus to
$4^{length}$ states and lets a mutation recur to one already present
elsewhere in the run — deliberately exercised here with a very short
3-base locus (only $4^{3} = 64$ states) and a high $mu$ so recurrence is
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
```

```console
fim run finite-alleles.yaml --output results/finite-alleles --quiet
```

Converges at generation 12 with $D \sim 0.207$. See
[configuration.md](configuration.md#mutation_model) for how this differs
from a distance-based (stepwise) mutation model, which `fim` does not
implement.

### Per-base mutation rate across unequal locus lengths

$mu_{b}$ (mutually exclusive with $mu$) is a single per-base-pair mutation
probability; each locus derives its own $mu$ from $mu_{b}$ and its own
$length$ via $mu = 1 - (1 - mu_{b})^{length}$ — so two loci of very
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
```

```console
fim run mu-b.yaml --output results/mu-b --quiet
```

Converges at generation 15 with $D \sim 0.090$. `results/mu-b/manifest.json`'s
`parameters.mu` records the two derived rates — `0.0009995` for the
50-base locus and `0.0099503` for the 500-base one — the expanded,
canonical form $mu_{b}$ is sugar for; `mu_{b}` itself is never stored.

### Several convergence statistics

Watch more than one statistic and decide whether stopping needs every one
of them stable (`convergence_combinator: all`, the default) or just one
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
```

```console
fim run multi-statistic.yaml --output results/multi-statistic --quiet
```

Converges at generation 16, with `report.json`'s `converged_on` recording
$["D", "G_ST"]$ — both were watched, and `any` means only one needed to
stabilize first.

### An adaptive replicate batch with a confidence interval

Rather than guessing how many replicate runs a confidence interval needs,
set `n_replicates` well above the plausible requirement and let
[`replicate_tolerance`](configuration.md#replicate_tolerance) decide when
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

Stops at exactly 10 replicates (`replicate_minimum`) — $D$'s 95% confidence
interval is already $0.218 +/- 0.048$, tighter than the requested `0.05`
half-width, so the remaining 40 possible replicates were never needed.
`results/adaptive-batch/summary.json` reports every statistic's own
interval; `results/adaptive-batch/replicate-001/` through `replicate-010/`
each hold the ordinary four-file scalar-run contract for that one
replicate. Drop `--sequential` to run the same batch across a worker
process per CPU instead — the computed numbers are identical either way
(see [Batches](#batches-n_replicates-greater-than-one)); only the wall-clock
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
`--q 0` and `--q 2` always match the report's own $K_{ST}$ and $D$; `--q 1`
matches $E_{ST}$, including the run's own `deme_weighting` setting — a
size-weighted $E_{ST}$ and an equal-weighted `Differentiation_1` would
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

A desktop application — seven screens over a small, static local web page
(`fim`'s own bundled `webview` renderer, the OS's native web view; no
browser, no server beyond the one opt-in release check below) — that runs
the same simulations and reads the same `results/` folder as the commands
above. Install it alongside `fim` (`python -m pip install .` already
provides both console scripts), or launch it from a packaged executable by
opening it with no arguments — for example by double-clicking
`fim-windows-x64.exe`/`fim.app`, or running `fim-gui`/`fim --graphical` from
a Linux install. `n_replicates` in the configuration is the only thing that
decides whether a run goes through the scalar or the batch path — there is
no separate "batch mode" toggle. Every screen calls the identical underlying
function this guide already documents; nothing here is a second
implementation:

A native File/Configure/Run/View/Help menu bar is available from any screen,
including mid-run. File covers configuration/run file-system actions
(New/Open/Save configuration, Open run…, Reveal output folder, Quit);
Configure jumps straight to any one of six configuration sections
(Population/Migration/Mutation/Initial conditions/Convergence/Batch), each
opening as a small dialog over whatever screen is currently showing,
without navigating away from it; three fields that are quick, meaningful
choices on their own get a direct submenu instead — no dialog at all —
that sets the field immediately: Deme weighting (size/equal), Mutation
model (infinite_alleles/finite_alleles), and Convergence statistic, which
*adds or removes* one statistic from the active set on each click rather
than replacing the whole selection (checking two or more is exactly what
turns on the "combinator" choice in the Convergence dialog). Run covers the
simulation lifecycle (Run simulation, Cancel run,
Animate); View holds a Significant digits submenu (2/3/4/5/6/8, default 3)
that changes how many digits every displayed statistic rounds to — cosmetic
only, `trajectory.jsonl`/`report.json`/`manifest.json` always keep full
precision regardless of this setting; Help covers this guide and the
[configuration reference](configuration.md) (rendered in-app — see the Help
screen row below), a link to the full documentation on GitHub, Check for
updates, and About. Every menu item reuses the exact same action the
matching button already performs; File/Configure/Run items invoked from a
screen where they are not immediately actionable navigate to the screen
where they are first, rather than doing nothing.

| Screen | What it does | Same as |
|---|---|---|
| Model input | Build and validate a full configuration from six sections (Population, Migration, Mutation, Initial conditions, Convergence, Batch — one per [configuration reference](configuration.md) section), each opened as its own dialog from the Configure menu (above) rather than an on-screen tab strip; "Load YAML…"/"Save YAML…" read and write the exact file format above; "Open a run…" reaches the Open a run screen | [Create a configuration](#create-a-configuration) |
| Running | A live scatter plot of the run's own current-generation frequencies (or, for a batch, every replicate's frequencies pooled onto one plot, filling in as replicates advance), with a generation progress indicator and a "Cancel" button — the window stays responsive throughout; the same "Compare demes directly" pair selector as the Results/Batch results/Animated trajectory screens, live — picking a pair affects every subsequent push for the rest of the run, not just a one-time snapshot | `run`'s own progress/error output, on one screen instead of terminal lines |
| Results | A scalar run's summary (all six named statistics, convergence outcome, each shown as a meter against the same `[0, 1]` scale the confidence-interval bars below use) beside the canonical scatter plot — every deme pair at once as a small-multiples grid once `d` is 3 or more (up to 6 demes; a PCA projection above that), each panel with a labeled, numbered `0.0`-`1.0` probability scale on both axes; "Compare demes directly" (two dropdowns plus "Show pair"/"Show overview") swaps to any two chosen demes' own raw frequencies at full size and back, most useful once the default view is a PCA projection rather than a direct pair; "Open output folder" reveals the same four artifacts; "Animate" plays back the persisted trajectory (disabled for a single-generation run, which has nothing to animate) | [Output schemas](#output-schemas) |
| Batch results | A pooled scatter across every replicate's final state — the same small-multiples grid the Results screen uses once `d` is 3 or more — beside a replicate table (status, final generation, every named statistic) and each statistic's across-replicate confidence interval as a meter; the same "Compare demes directly" pair selector as the Results screen, pooled across every replicate; each row's own "Open" button reaches the Results screen for that one replicate; "Open batch folder" reveals `summary.json` and every replicate subdirectory | [Batch `summary.json` and `manifest.json`](#batch-summaryjson-and-manifestjson) |
| Open a run | Pick a previous run from a recent-runs list — a batch entry is labeled distinctly and opened one replicate at a time from its own Batch results screen, not from here — or browse for a `trajectory.jsonl` directly, then re-render its summary and scatter at any persisted generation, with the same optional differentiation-`q` sweep | [Re-analyze a trajectory](#re-analyze-a-trajectory) |
| Animated trajectory | Play back a completed run's persisted generations as a scatter animation, with a play/pause button and a scrub slider; the same "Compare demes directly" pair selector as the Results/Batch results screens, applied across the whole animation rather than one static state | No CLI equivalent — a GUI-only bonus view |
| Help | This guide and the [configuration reference](configuration.md), rendered in-app with working cross-links; every other doc opens on GitHub in the OS default browser instead. Reachable from the Help menu (above) from any screen; "Back" returns to whichever screen was showing, not a fixed default | No CLI equivalent — the terminal reads these same two files directly |

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

## Global flags

```console
fim --help
fim --version
```

`--version` comes from the same `version.txt` value used by packages,
manifests, and release tags.

## Output schemas

### `trajectory.jsonl`

One JSON object is appended for every nonzero frequency:

```json
{"allele_id":0,"deme":1,"frequency":0.5,"generation":0,"locus_id":1,"run_id":"run-example"}
```

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | Deterministic identity for one parameter set and seed |
| `generation` | integer | Generation, beginning at 0 |
| `deme` | integer | One-based deme number |
| `locus_id` | integer | Configured positive locus identifier |
| `allele_id` | integer | Opaque identity-only allele label |
| `frequency` | number | Positive allele frequency |

### `manifest.json`

The manifest records the complete parameter mapping, seed, software version,
UTC start/end timestamps, generation, watched statistic, and whether
convergence or the hard cap ended the run. It also carries:

- `schema_version`: the manifest's own shape version.
- `convergence.generation_count`: how many distinct generations the run
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

`output_directory` (the whole four-file set for a scalar run, or the whole
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
released before `schema_version` and `artifacts` existed, wrote manifests
with neither field. `fim stats` refuses such a manifest — it has no
digest to verify the trajectory against — with an error naming both the
cause and that there is no automated migration: re-run the same
configuration (the manifest's own `parameters`) with the current `fim`
to get a manifest this version can read and verify.

### `report.json`

The final report contains:

- run identity, generation, convergence flag, watched statistic, and reason;
- $H_{S}$, $H_{T}$, and the correctly partitioned $H_{ST}$;
- $G_{ST}$ (`null` only when *every* tracked locus is fixed for the same
  allele in every deme; with several loci, a locus that is fixed on its
  own does not blank out the others — it is dropped and the remaining,
  genuinely polymorphic loci are averaged);
- Jost's $D$, entropy differentiation $E_{ST}$, and allele-number
  differentiation $K_{ST}$.

Multiple loci are independent repeats; report scalars are their arithmetic
means ($G_{ST}$ as just described). $D$ and $K_{ST}$ always use equal deme
weighting. `deme_weighting` affects $E_{ST}$.

### `scatter.png`

- $d = 2$: direct Deme 1 versus Deme 2 scatter with a diagonal reference.
- $d = 3$: direct three-dimensional scatter.
- $4 <= d <= 6$: every pairwise deme projection.
- $d > 6$: a single plot explicitly labeled as a two-dimensional PCA
  projection.

One point represents one $(locus, allele)$ pair. Coincident points are enlarged
and annotated.

### Batch `summary.json` and `manifest.json`

Written only for `n_replicates` greater than one, alongside the
`replicate-NNN/` subdirectories, each of which holds the four scalar-run
files above.

`summary.json` maps each reported statistic name ($D$, $G_{ST}$, $E_{ST}$,
$K_{ST}$, $H_{S}$, $H_{T$}, $H_{ST}$) to its across-replicate confidence interval:

```json
{
  "D": {
    "mean": 0.643,
    "half_width": 0.021,
    "low": 0.622,
    "high": 0.664,
    "sample_count": 40,
    "confidence": 0.95
  }
}
```

$G_{ST}$ can have a smaller `sample_count` than the other statistics: a
replicate whose locus is monomorphic across every deme reports $G_{ST}$ as
`null` in its own `report.json`, and that replicate is excluded from
$G_{ST}$'s interval rather than papered over with a substitute value. A
statistic left with fewer than two defined replicates is omitted from
`summary.json` entirely.

The batch's own `manifest.json` (distinct from each replicate's own) records
`schema_version`, the batch `run_id`, every `replicate_run_ids` entry,
`replicate_count`, the shared `parameters`, batch start/end timestamps, and
`software_version` — not a per-run convergence outcome, since each replicate
has its own. Like a scalar run's manifest, it also carries `artifacts`: the
SHA-256 digest and byte count of `summary.json` and of each replicate's own
`manifest.json` (keyed `replicate-NNN`), recorded once every one of them is
flushed, so an edited, truncated, or replaced batch-level artifact is
detectable the same way a scalar run's is. Under parallel execution — the
CLI default — an adaptive `replicate_tolerance` stop can leave a worker that
had already started its own `replicate-NNN/` directory before the stop was
decided; that directory is pruned before publishing, so the `replicate-*`
subdirectories actually present always equal `replicate_run_ids` exactly.

## Reproduce a run

1. Copy `manifest.json`.
2. Use its `parameters` object as a new YAML config.
3. Run the same `fim` version shown in `software_version`.
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
  `max_generations`, relax the tolerance, increase the window, or select
  another convergence statistic based on the study's needs.
- **Windows warning:** verify the release checksum before running the unsigned
  executable. See [SECURITY.md](../SECURITY.md).
