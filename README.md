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
  explicitly; see the [configuration reference](doc/configuration.md#migrant_sampling).

The [finite island model introduction](doc/finite-island-model-introduction.md)
is the full plain-language walkthrough — no genetics, statistics, or
programming background assumed — including how this differs from the
stepping-stone (spatial) variant and how it connects to Jost's
differentiation statistics.

## Quick start

### Windows release

1. Download `fim-windows-x64.exe` and its `.sha256` file from the project's
   GitHub Releases page.
2. Open PowerShell in the download folder and verify the checksum:

   ```powershell
   Get-FileHash .\fim-windows-x64.exe -Algorithm SHA256
   ```

3. Create a starter configuration:

   ```powershell
   .\fim-windows-x64.exe init
   ```

4. Edit `results\example-run.yaml`, then run:

   ```powershell
   .\fim-windows-x64.exe run `
       "results\example-run.yaml"
   ```

The executable is self-contained. A simulation does not use the network.
Windows SmartScreen may identify this unsigned research executable as an
unrecognized application; verify the checksum before selecting **Run anyway**.

### Python installation

Python 3.12 or newer is required:

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
fim init --output example-run.yaml
fim run example-run.yaml --output results/example
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
- [Installation alternatives](install/README.md)
- [Security model](SECURITY.md)
- [Release history](CHANGELOG.md)

### Understanding the science

- [Finite island model introduction](doc/finite-island-model-introduction.md)
- [Jost differentiation measures](doc/jost-differentiation-measures.md)
- [Simulator design](doc/fim-simulator-design.md)

### Maintaining or extending the application

- [Developer and extension guide](doc/developer.md)
- [Detailed implementation design](doc/fim-simulator-detailed-design.md)
- [Test plan](doc/fim-simulator-test-plan.md)
- [Source-tree orientation](src/README.md)
- [Generated API reference](src/fim/API.md)
- [Maintainer runbook](CONTRIBUTING.md)
- [Repository-managed hooks](dev/git-hooks/README.md)

## Development

The supported build environment is a Unix-like system with Bash, Git, and
Python 3.12 or newer. The scripts avoid modern-only Bash features; the Bash
3.2 bundled with macOS is sufficient. Docker is required only for the complete
repository-file and Homebrew validation commands. Native Windows development
is not supported; the Windows executable is built and tested by the release
workflow.

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
