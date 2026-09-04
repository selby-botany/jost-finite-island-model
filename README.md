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
version of Wright's original island model. The distinction answers the two
questions a first-time reader usually has:

- **Where do migrants come from?** From the *other islands themselves* —
  their actual, currently drifting allele frequencies — not from a fixed,
  infinite external reservoir with a composition that never changes. That is
  what "finite" refers to: the whole set of islands is a closed system, with
  no outside population to draw on.
- **Is migration a random draw of individuals?** By default, no. Migration
  blends each island's frequencies with a weighted average of the other
  islands' current frequencies, a deterministic step given that generation's
  frequencies. The randomness is downstream, in drift: each island's next
  generation is formed by resampling its gene copies at random from its own
  post-migration frequencies. An optional third step, mutation, can also
  replace a small, randomly sampled number of gene copies with genuinely
  novel alleles. An opt-in `migrant_sampling: stochastic` setting adds a
  fourth random process — how many gene copies migrate, not just how many
  drift or mutate — for studies that want that source of variation counted
  explicitly; see the [configuration reference](doc/configuration.md#migrantsampling).

The [finite island model introduction](doc/finite-island-model-introduction.md)
is the full plain-language walkthrough — no genetics, statistics, or
programming background assumed — including how this differs from the
stepping-stone (spatial) variant and how it connects to Jost's
differentiation statistics.

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

The first launch after installing can take noticeably longer than every
launch after it — macOS validates every file in the (unsigned) app bundle
before running it, and the graphical toolkit builds a one-time system font
index. Both results are cached, so if the window opens blank at first, leave
it open rather than force-quitting; it fills in once that one-time work
finishes.

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

Each scalar CLI run (`n_replicates: 1`, the default) writes exactly four
artifacts:

| File | Purpose |
|---|---|
| `trajectory.jsonl` | Every nonzero `(generation, deme, locus, allele)` frequency |
| `manifest.json` | Replayable parameters, seed, version, timestamps, and stop outcome |
| `report.json` | Final `H_S`, `H_T`, `G_ST`, Jost's `D`, `E_ST`, and `K_ST` |
| `scatter.png` | Canonical allele-frequency scatter or labeled projection |

The trajectory is long-format JSON Lines and can be loaded without a custom
database. `n_replicates` greater than one runs a batch instead: one of the
above per replicate subdirectory, plus a batch-level `summary.json` — each
reported statistic's mean and confidence interval across replicates — and
its own `manifest.json`. See [output schemas](doc/usage.md#output-schemas).

## Model contract

- `N` is the **gene-copy count per deme**, not an individual count. For a
  diploid autosomal locus, pass twice the census number of individuals.
- Every run uses one explicitly seeded NumPy `PCG64` generator.
- A generation applies migration, mutation, then drift.
- Generation 0 is a continuous Dirichlet prior (or an explicit `p_0`),
  not a state the model's `N` gene copies could themselves produce.
  Generation 1, the first `drift` application, is the first generation
  on that `1/N` lattice — treat a generation-0 statistic as describing
  the prior, not a sampled population.
- Convergence means that a selected statistic's trailing-window half means are
  within a configured tolerance. Reaching the hard cap is a valid,
  non-converged result.
- Founding alleles use locus-relative IDs. By default (the infinite-alleles
  model), every mutation receives a globally unique ID; the opt-in
  finite-alleles model instead bounds each locus to `4 ** length` states
  and allows a mutation to recur to one already present elsewhere in the
  run.
- `mu` (the mutation probability) can be a shared scalar, an explicit
  per-locus list, or derived per locus from a single per-base rate
  (`mu_b`) and each locus's own `length`, so loci of different lengths
  need not mutate at an identical, hand-picked rate.
- `n_replicates` runs that many independently seeded replicates. With the
  opt-in `replicate_tolerance` set, a batch stops as soon as every watched
  statistic's across-replicate confidence interval is tight enough,
  rather than requiring a hand-guessed replicate count in advance.

The [simulator design](doc/fim-simulator-design.md)
defines the complete scientific and architectural contract.

## Documentation

### Using the application

- [Command, workflow, and output reference](doc/usage.md)
- [Configuration reference](doc/configuration.md)
- [Worked examples](doc/examples/README.md)
- [Installation alternatives](install/README.md)
- [Security model](SECURITY.md)
- [Release history](CHANGELOG.md)

### Understanding the science

- [Finite island model introduction](doc/finite-island-model-introduction.md)
- [Jost differentiation measures](doc/jost-differentiation-measures.md)
- [Simulator design](doc/fim-simulator-design.md)
- [Test plan](doc/fim-simulator-test-plan.md) — how this project knows
  its own numbers are trustworthy, written for a non-programmer
- [Migration and identity conventions](doc/migration-conventions.md) —
  mapping this project's own conventions onto cited papers, for anyone
  comparing `fim`'s output against the literature

### Maintaining or extending the application

- [Developer and extension guide](doc/developer.md)
- [Detailed implementation design](doc/fim-simulator-detailed-design.md)
- [Detailed test plan](doc/fim-simulator-detailed-test-plan.md)
- [Externally accessible engine API](doc/fim-simulator-functional-api.md)
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

AGPL-3.0-or-later; see [LICENSE.md](LICENSE.md).
