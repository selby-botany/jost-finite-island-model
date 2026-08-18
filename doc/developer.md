# Developer and extension guide

This guide is for a maintainer extending the simulator without relying on
institutional knowledge. Start with the [project overview](../README.md), then
use the [generated API reference](../src/fim/API.md) for exact signatures.

## Contents

- [Architecture](#architecture)
- [Build environment](#build-environment)
- [Generation pipeline](#generation-pipeline)
- [Determinism](#determinism)
- [Persistence and reports](#persistence-and-reports)
- [Adding a new what-if](#adding-a-new-what-if)
- [Testing](#testing)
- [Documentation](#documentation)

## Architecture

| Package | Responsibility |
|---|---|
| `fim.model` | Allele/locus/state values, parameter validation, initialization, update operators |
| `fim.statistics` | Pure diversity and differentiation functions |
| `fim.convergence` | Trailing-window criterion and hard-cap monitor |
| `fim.persistence` | Store protocol, JSON Lines backend, replayable manifest |
| `fim.engine` | Public run loop and final report assembly |
| `fim.viz` | Headless scatter and diagnostic plots |
| `fim.cli` | YAML and command-line boundary |

The engine depends on these modules; none depends on the engine. Statistics can
analyze a frequency table without running a simulation, and persisted rows can
be re-analyzed through the CLI.

The scientific rationale is in the
[simulator design](fim-simulator-design.md). The
[detailed design](fim-simulator-detailed-design.md)
records implementation and release choices, and the
[test plan](fim-simulator-test-plan.md) maps each
requirement to evidence.

## Build environment

The supported maintainer environment is Unix-like and requires:

- Bash 3.2 or newer
- Git
- Python 3.12 or newer

The root `build` script and Git hooks assume Unix paths. They use Bash arrays,
`[[ ... ]]`, and `BASH_SOURCE`, so plain POSIX `sh` is not sufficient, but
they avoid modern-only Bash features and work with the Bash 3.2 bundled with
macOS. The Python build itself does not need Docker.

Create `.venv` with Python 3.12 or newer and install `.[dev]`. Shell activation
is optional: `build`, the Git hooks, and the commands in `bin/` automatically
select `.venv/bin/python`. A versioned `.venv-*` is accepted as a fallback.
`PYTHON=/path/to/python` overrides build selection, while
`FIM_PYTHON=/path/to/python` overrides the local command wrappers. Source
`include/dot-bashrc` to make those wrappers available as direct commands.

Docker Engine is required for the complete repository-file checks. It runs the
pinned ShellCheck, yamllint, markdownlint, gitleaks, and Homebrew validation
images. Source the local environment file before invoking those wrappers:

```console
. include/dot-bashrc
dev/bin/validate-repository
```

No tool or environment file is loaded from another checkout. Native Windows
development is not supported. The self-contained Windows executable is built
and smoke-tested by the tag-driven GitHub Actions release workflow.

## Generation pipeline

`fim.model.operators.step` composes:

1. **Migration:** deterministic all-other-deme blending, or a supplied
   row-stochastic matrix.
2. **Mutation:** a binomial number of distinct gene copies receive globally
   novel IDs.
3. **Drift:** each deme/locus is multinomially resampled to exactly `N` gene
   copies.

Every operator receives all changing inputs explicitly and returns a new
`ModelState`. `ModelState` enforces one normalized sparse frequency map per
deme/locus.

## Determinism

- Construct one `numpy.random.Generator(PCG64(seed))` per scalar run.
- Pass it into initialization and every stochastic operator.
- Do not call NumPy's global RNG, `random`, the wall clock, or the network from
  simulation logic.
- Preserve first-observed allele order; ordering has no biological meaning but
  stable iteration keeps byte output reproducible.
- Keep timestamps in manifest metadata and default directory names only.

Tests use a derandomized Hypothesis profile and literal PCG64 seeds. Statistical
tolerances are derived from sample size before a seed is selected.

## Persistence and reports

`TrajectoryStore` is the public backend contract:

- `write_generation(run_id, generation, rows)`
- `read(run_id)`

Add a new backend under `fim.persistence` without changing the engine,
statistics, or visualizations. The v1 JSON Lines backend flushes each
generation so an interrupted file retains every complete line.

Statistics are computed per locus, then arithmetic-mean aggregated in the final
report. Keep locus-specific analysis in pure statistics functions rather than
adding engine state.

## Adding a new what-if

| Requested change | Extension point |
|---|---|
| Unequal deme size | Pass per-deme `N`; operators and `E_ST` support it |
| Asymmetric migration | Pass a validated `d` by `d` matrix |
| Stepping-stone topology (1D) | `fim.model.topology`: `m: {topology: ring\|linear, rate}` or a hand-written sparse map |
| Spatial migration beyond 1D or a fixed matrix | 2D lattice topology, or a `MigrantPoolStrategy` interface — neither built yet |
| Per-locus mutation from length | Derive rates from `LocusSpec.length` in a mutation strategy |
| Selection | Add a pure `select` operator before drift |
| Stepwise mutation | Add a strategy behind mutation identity assignment |
| Several convergence statistics | Pass a list for `convergence_statistic` plus `convergence_combinator` |
| Large trajectories | Implement another `TrajectoryStore` |
| GUI | Call `fim.engine.fim`; do not duplicate model logic |

## Testing

Create a Python 3.12 environment and install `.[dev]`, then:

```console
pytest
pytest -o addopts="" -m statistical
./build --ci
```

Test locations mirror `src/fim`. Use exact golden values for formulas,
Hypothesis for algebraic identities, fixed-seed pre-banded checks for
stochastic behavior, and structural figure assertions instead of pixel diffs.
Never use the public internet or wall-clock values in an assertion.

The coverage gate is 90 percent branch coverage for `src/fim`, excluding
visualization rendering. Coverage is a floor; golden and invariant tests carry
the scientific proof.

## Documentation

Public functions require docstrings with purpose, arguments, returns, and
raised errors where relevant. Regenerate the API reference after source
changes:

```console
dev/bin/generate-api-docs
```

Run `dev/bin/check-doc-links` after moving headings or files. The pre-commit
hook refreshes API docs, the pre-push hook checks freshness, and CI repeats
both checks. See [source-tree orientation](../src/README.md) and
[repository-managed hooks](../dev/git-hooks/README.md).
