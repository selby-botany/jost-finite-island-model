# Jost's finite island model simulator

`fim` simulates migration, mutation, and genetic drift across a finite set
of demes. It preserves every generation's allele frequencies and reports
differentiation statistics against that known history.

## Contents

- [What this simulates](#what-this-simulates)
- [Quick start](#quick-start)
- [Outputs](#outputs)
- [Model contract](#model-contract)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## What this simulates

`fim` implements the **finite island model**, not the classic infinite-source
version of Wright's original island model:

- **Migrant source.** Migrants come from the other islands at their current
  allele frequencies, not from a fixed external reservoir. The finite set
  of islands forms a closed system.
- **Migration and randomness.** By default, migration deterministically
  blends each island's frequencies with a weighted average of the other
  islands' frequencies. Drift then resamples each island's gene copies from
  its post-migration frequencies. Mutation independently changes each new
  copy with probability μ, assigning a novel allele by default.
  `migrant_sampling: stochastic` also samples the number of migrating gene
  copies; see the
  [configuration reference](doc/configuration.md#migrant_sampling).

The [finite island model introduction](doc/finite-island-model-introduction.md)
explains the model without assuming genetics, statistics, or programming
knowledge. It also covers the stepping-stone (spatial) variant and the
connection to Jost's differentiation statistics.

## Quick start

Every packaged executable below supports both a **desktop GUI** (double-click
it, or run it with no arguments) and the **command line** (the same
executable with a command, e.g. `run`). Both read and write the same
`results/` folder, so a run started from one can be opened, re-analyzed, or
animated from the other. See the
[desktop GUI section of the command reference](doc/usage.md#desktop-gui-fim-gui)
for what each screen does.

### Windows release

1. Download the executable matching your processor and its `.sha256` file
   from the project's GitHub Releases page: `fim-windows-x64.exe` for an
   Intel/AMD machine, `fim-windows-arm64.exe` for an ARM64 machine (for
   example, a Windows-on-ARM laptop or VM). The examples below use the x64
   name; substitute `fim-windows-arm64.exe` if that is the one you
   downloaded.
2. Open PowerShell in the download folder and verify the checksum:

   ```powershell
   Get-FileHash .\fim-windows-x64.exe -Algorithm SHA256
   ```

3. Double-click `fim-windows-x64.exe` for the GUI, or use the command line:

   ```powershell
   .\fim-windows-x64.exe init
   .\fim-windows-x64.exe run `
       "results\example-run.yaml"
   ```

The executable is self-contained. A simulation does not use the network.
Windows SmartScreen may identify this unsigned research executable as an
unrecognized application; verify the checksum before selecting **Run anyway**.

### macOS release

1. Download `fim-macos-arm64.dmg` (Apple Silicon) or `fim-macos-x64.dmg`
   (Intel) and its `.sha256` file from the project's GitHub Releases page.
2. Verify the checksum, then open the `.dmg` and drag `fim.app` to
   Applications:

   ```console
   shasum -a 256 -c fim-macos-arm64.dmg.sha256
   ```

3. Double-click `fim.app` for the GUI. For the command line, the same
   binary lives inside the bundle:

   ```console
   /Applications/fim.app/Contents/MacOS/fim init
   /Applications/fim.app/Contents/MacOS/fim run results/example-run.yaml
   ```

macOS Gatekeeper may warn that this unsigned research application is from an
unidentified developer; verify the checksum, then allow it via **System
Settings → Privacy & Security**.

The first launch can take longer while macOS validates the unsigned app
bundle and the graphical toolkit builds its font index. These results are
cached. If the window initially appears blank, leave it open until
initialization finishes.

### Linux release

Install with one command — downloads, verifies, and installs `fim` and
`fim-gui` to `~/.local/bin`, no root required:

```console
curl -sSL https://raw.githubusercontent.com/selby-botany/jost-finite-island-model/main/install.sh | bash
```

Or download `fim-linux-x64` and its `.sha256` file from the Releases page
directly. `fim-gui` opens the same GUI as double-clicking the other two
platforms' executables; the desktop backend (WebKitGTK) is a system
package most desktop distributions already have — see
[installation alternatives](install/README.md) if `fim-gui` reports it is
missing.

### Python installation

Python 3.12 or newer is required:

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
fim init --output example-run.yaml
fim run example-run.yaml --output results/example
```

`pip install .` also provides `fim-gui`, the same desktop application every
packaged executable above bundles — launch it with:

```console
fim-gui
```

See [installation alternatives](install/README.md) and the
[complete command reference](doc/usage.md).

## Outputs

By default, `n_replicates: 200` sets an upper limit of 200 independent
replicates. With early stopping enabled, the batch stops when the confidence
interval meets `precision`, after at least 10 replicates. It writes:

| File | Purpose |
|---|---|
| `summary.json` | Each reported statistic's mean across the replicates and its confidence interval |
| `manifest.json` | The batch's parameters, seed, version, timestamps and the replicates that ran |
| `replicate-NNN/` | One subdirectory per replicate, each holding the files below |

A single run (`n_replicates: 1`, an explicit choice) writes the files below
directly, with no `summary.json`. A batch writes one set per replicate
subdirectory. Open a replicate in the desktop app to see it as a single run.

| File | Purpose |
|---|---|
| `trajectory.tlog` | Every nonzero `(generation, deme, locus, allele)` frequency, as a compact binary log |
| `equilibrium_trajectory.tlog` | The ancestral phase's own trajectory; written only by an equilibrium-split run |
| `convergence.jsonl` | One row per generation of each watched convergence statistic (a compact run writes `convergence.fhist` instead) |
| `history.json` | The statistic history at full precision for at most 2,048 generations; written instead of the two above by a statistics-only run |
| `manifest.json` | Replayable parameters, seed, version, timestamps, and stop outcome |
| `report.json` | Final `H_S`, `H_T`, `G_ST`, Jost's `D`, `E_ST`, and `K_ST` |
| `pairwise.json` | Every deme pair's Nei identities and pairwise `F_ST` at the final generation |
| `scatter.png` | Canonical allele-frequency scatter or labeled projection |

A run can keep less of itself: `trajectory_retention` chooses every generation (the
default), every Nth, the recent ones in full with the past thinned further back, or
statistics only, and `history_format: compact` rounds the statistic history to a
hundredth of the precision (see [the configuration reference](doc/configuration.md)).

The trajectory is stored as a compact binary log; `fim export` turns it into
long-format JSON Lines (`trajectory.jsonl`), which can be loaded without a
custom database (see [the trajectory log](doc/trajectory-log.md)). See
[n_replicates](doc/configuration.md#n_replicates) for how the batch decides
when to stop, and [output schemas](doc/usage.md#output-schemas) for the
files.

## Model contract

- YAML `N` is the **individual count per deme**. Required `ploidy`
  determines the gene-copy count: `N: 225` with `ploidy: diploid` gives
  450 gene copies. The engine and analytic formulas use gene-copy counts.
- Every run uses one explicitly seeded NumPy `PCG64` generator.
- A generation applies migration, then drift (drawing the new gene copies),
  then mutation (each new copy mutates independently with probability `mu`):
  the textbook Wright-Fisher island model.
- Generation 0 is a continuous Dirichlet prior (or an explicit `p_0`),
  not a state the model's `N` gene copies could themselves produce.
  Generation 1, after the first `drift` application, is the first generation
  on that `1/N` lattice. Treat a generation-0 statistic as describing
  the prior, not a sampled population.
- A run burns in, then averages, and has converged when the average of each
  watched statistic is known to the requested precision (its standard error
  over the evidence window is small enough). Reaching the hard cap is a valid,
  non-converged result, reported with the achieved precision.
- Founding alleles use locus-relative IDs. By default (the infinite-alleles
  model), every mutation receives a globally unique ID; the opt-in
  finite-alleles model instead bounds each locus to `4 ** length` states
  and allows a mutation to recur to one already present elsewhere in the
  run.
- `mu` (the mutation probability) can be a shared scalar, an explicit
  per-locus list, or derived per locus from a single per-base rate
  (`mu_b`) and each locus's `length`.
- `n_replicates` runs that many independently seeded replicates, and
  defaults to `200`, not `1` — set it explicitly to `1` for a single,
  run with no batching. With `stop_batch_early` on (the default,
  at `precision: 0.01`), a batch stops when every watched statistic's
  across-replicate confidence interval meets the requested precision.
- `engine_backend` chooses how fast a run goes, not what it computes:
  `lineal` (the reference), `generational` and `generational-vector` (an
  array engine that needs the optional `numba`, `pip install fim[jit]`)
  produce the same trajectory, bit for bit, for the same seed on the same
  machine, under both mutation models. `auto` picks the fast one for your
  configuration, and the array engine is typically tens to hundreds of
  times faster; see [the configuration
  reference](doc/configuration.md#engine_backend).

The [simulator design](doc/fim-simulator-design.md)
defines the complete scientific and architectural contract.

## Documentation

### Using the application

- [Command, workflow, and output reference](doc/usage.md)
- [Saved runs: pause, resume, and recover](doc/run-lifecycle.md)
- [Configuration reference](doc/configuration.md)
- [Statistics reference: definitions, interpretation, and implementation](doc/statistics.md)
- [Convergence defaults](doc/convergence.md)
- [Worked examples](doc/examples/README.md)
- [Installation alternatives](install/README.md)
- [Security model](SECURITY.md)
- [Known issues](ISSUES.md)
- [Release history](CHANGELOG.md)

### Understanding the science

- [Finite island model introduction](doc/finite-island-model-introduction.md)
- [Jost differentiation measures](doc/jost-differentiation-measures.md)
- [Nei distances in fim](doc/nei-distances.md): the two denominators, pair
  and all-demes forms, and how to read a negative value
- [Simulator design](doc/fim-simulator-design.md)
- [Test plan](doc/fim-simulator-test-plan.md) — scientific validation
  methods and evidence, without requiring programming knowledge
- [Migration and identity conventions](doc/migration-conventions.md) —
  mapping the simulator's conventions onto cited papers, for anyone
  comparing `fim`'s output against the literature

### Maintaining or extending the application

- [Developer and extension guide](doc/developer.md)
- [Detailed implementation design](doc/fim-simulator-detailed-design.md)
- [Detailed test plan](doc/fim-simulator-detailed-test-plan.md)
- [Using fim from Python](doc/python-api.md)
- [Externally accessible engine API](doc/fim-simulator-functional-api.md)
- [Engine backend benchmark results](doc/fim-engine-backend-benchmarks.md)
- [Desktop GUI design](doc/fim-gui-design.md)
- [Operational logging design](doc/fim-logging-design.md)
- [Desktop GUI test plan](doc/fim-gui-test-plan.md)
- [Source-tree orientation](src/README.md)
- [Generated API reference](src/fim/API.md)
- [Generated test-suite reference](test/TESTS.md)
- [Maintainer runbook](CONTRIBUTING.md)
- [Repository-managed hooks](dev/git-hooks/README.md)
- [Maintainer scripts](dev/bin/README.md)

## Development

The supported build environment is a Unix-like system with Bash, Git, and
Python 3.12 or newer. The scripts avoid modern-only Bash features; the Bash
3.2 bundled with macOS is sufficient. Docker is required only for the complete
repository-file and Homebrew validation commands. Native Windows development
is not supported; every packaged executable (Windows x64/ARM64, macOS
arm64/x64, Linux x64) is built and smoke-tested by the release workflow
regardless of which platform develops it.

```console
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
. include/dot-bashrc
bash dev/git-hooks/install
./build
```

The build and hook scripts prepend the repository's `bin/` directory and
automatically use a Python 3.12+ interpreter from `.venv` (or a versioned
`.venv-*` fallback). Activating the environment is optional. Thin local
wrappers provide `python3`, `ruff`, `mypy`, `pytest`, `pydoc-markdown`, and
`pyinstaller` consistently.

Run `./build --ci` for the same static, test, documentation, and package gates
used by continuous integration. See [CONTRIBUTING.md](CONTRIBUTING.md) before
cutting a release.

Optional repository-file checks are also self-contained. With Docker
available, activate the local wrappers and run them without sourcing files
from another repository:

```console
. include/dot-bashrc
dev/bin/validate-repository
```

## License

Copyright 2026 Marie Selby Botanical Gardens.

Except for the reserved assets identified in
[`branding/`](branding/README.md), the project is licensed under
AGPL-3.0-or-later. See [LICENSE.md](LICENSE.md).
