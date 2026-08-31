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
| `fim.statistics` | Pure diversity/differentiation functions, and across-replicate confidence intervals |
| `fim.convergence` | Trailing-window and confidence-interval criteria, and the hard-cap monitor |
| `fim.persistence` | Store protocol, JSON Lines backend, replayable manifest |
| `fim.engine` | Public run loop and final report assembly |
| `fim.viz` | Headless scatter and diagnostic plots |
| `fim.cli` | YAML and command-line front end |
| `fim.launcher` | Packaged single-executable dispatch: no arguments (or `--graphical`) launches `fim.gui`, anything else reaches `fim.cli` unchanged |
| `fim.gui` | pywebview desktop front end — six screens as a static local `webui/` page (plain HTML/CSS/JS) driven by an `Api` bridge class (`fim.gui.app.Api`), the JS side's only way into Python; calls `fim.engine`/`fim.viz`/`fim.persistence` directly, never duplicates model logic |

The engine depends on these modules; none depends on the engine. Statistics can
analyze a frequency table without running a simulation, and persisted rows can
be re-analyzed through either front end. `fim.cli` and `fim.gui` are peers —
two consumers of the same public API, not a case of one wrapping the other.

The scientific rationale is in the
[simulator design](fim-simulator-design.md). The
[detailed design](fim-simulator-detailed-design.md)
records implementation and release choices, and the
[detailed test plan](fim-simulator-detailed-test-plan.md) maps each
requirement to evidence — its own companions,
[the externally accessible engine API](fim-simulator-functional-api.md)
and the [desktop GUI test plan](fim-gui-test-plan.md), scope exactly
what a functional test may call. The [desktop GUI design](fim-gui-design.md)
covers why and how `fim.gui` itself is built, and the
[operational logging design](fim-logging-design.md) covers the `-l`/`-L`
flags and where log calls live. The plain-language
[test plan](fim-simulator-test-plan.md) is this project's own answer,
for a non-programmer, to "can this simulator's numbers be trusted."

## Build environment

The supported maintainer environment is Unix-like and requires:

- Bash 3.2 or newer
- Git
- Python 3.12 or newer

The root `build` script and Git hooks assume Unix paths. They use Bash arrays,
`[[ ... ]]`, and BASH<sub>SOURCE</sub>, so plain POSIX `sh` is not sufficient, but
they avoid modern-only Bash features and work with the Bash 3.2 bundled with
macOS. The Python build itself does not need Docker.

Create `.venv` with Python 3.12 or newer and install `.[dev]`. Shell activation
is optional: `build`, the Git hooks, and the commands in `bin/` automatically
select `.venv/bin/python`. A versioned `.venv-*` is accepted as a fallback.
`PYTHON=/path/to/python` overrides build selection, while
FIM<sub>PYTHON</sub>=/path/to/python overrides the local command wrappers. Source
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
2. **Mutation:** a binomial number of gene copies mutate at each locus's
   own rate (`mu`: a shared scalar, an explicit per-locus list, or one
   derived per locus from a per-base rate, μ<sub>b</sub>). By default
   (mutation_model: infinite_alleles) each mutating copy receives a
   globally novel ID; under the opt-in finite_alleles model, its target
   is drawn from its own locus's bounded state space and can recur.
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
- A replicate batch's `seed + i` derivation, and each replicate's own PCG64
  generator, are unaffected by execution order or worker count: opt-in
  parallel replicate execution (`fim`'s max_workers) runs each replicate
  in its own worker process, computes exactly the same result as running it
  alone, and only its own `RunResult`'s wall-clock timestamps can vary.

Tests use a derandomized Hypothesis profile and literal PCG64 seeds. Statistical
tolerances are derived from sample size before a seed is selected.

## Persistence and reports

`TrajectoryStore` is the public backend contract:

- write_generation(run_id, generation, rows)
- read(run_id)

Add a new backend under `fim.persistence` without changing the engine,
statistics, or visualizations. The JSON Lines backend flushes each
generation so an interrupted file retains every complete line.

A replicate batch needs one store *per replicate*, not one shared instance —
mandatory once max_workers is set, since a single store object cannot
cross a worker-process boundary. `fim`'s store_factory builds one given a
replicate's run_id; it must itself be picklable under max_workers (a
module-level function, or `functools.partial` over one — never a closure or
lambda), which is exactly how the CLI wires each replicate to its own real
`replicate-NNN/trajectory.jsonl`.

Statistics are computed per locus, then arithmetic-mean aggregated in the final
report. Keep locus-specific analysis in pure statistics functions rather than
adding engine state.

## Adding a new what-if

| Requested change | Extension point |
|---|---|
| Unequal deme size | Pass per-deme `N`; operators and E<sub>ST</sub> support it |
| Asymmetric migration | Pass a validated `d` by `d` matrix |
| Stepping-stone topology (1D) | `fim.model.topology`: `m: {topology: ring\|linear, rate}` or a hand-written sparse map |
| Spatial migration beyond 1D or a fixed matrix | 2D lattice topology, or a `MigrantPoolStrategy` interface — neither built yet |
| Random, rather than fixed, migrant counts | migrant_sampling: stochastic; `migrate()` accepts `rng` and draws Binomial(N<sub>i</sub>, rate) |
| Per-locus mutation rate from length | μ<sub>b</sub> (a per-base rate) derives each locus's own `mu` from its `length`; or pass `mu` as an explicit per-locus list directly |
| Finite-length alleles (remove infinite-length artifacts) | mutation_model: finite_alleles; `LocusSpec.length` bounds each locus to `4 ** length` states, and mutation can recur |
| Selection | Add a pure `select` operator before drift |
| Stepwise (distance-based) mutation, e.g. for microsatellites | Add a strategy behind mutation identity assignment — a different, still-unbuilt model from the row above (§3.2 of the design doc explains why) |
| Several convergence statistics | Pass a list for convergence_statistic plus convergence_combinator |
| How many replicate runs give a confidence interval | replicate_tolerance stops a batch once every watched statistic's across-replicate CI tightens to it, instead of a hand-guessed n<sub>replicates</sub>; fim.engine.replicate_summary / the CLI's `summary.json` report the realized interval |
| Faster replicate batches | max_workers (library) / `--workers`, `--sequential` (CLI): one worker process per replicate batch-slot, opt-in, changes nothing about what is computed |
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

After changing `doc/usage.md` or `doc/configuration.md`, regenerate the
GUI's Help screen content the same way:

```console
dev/bin/generate-help-html
```

The pre-commit hook refreshes it automatically when either source doc (or
the generator itself) is staged; the pre-push hook and CI verify freshness
the same way they do for the API reference above. anchor_for in
`dev/lib/docslug.py` is the one GitHub-compatible heading-anchor slugger
both this generator and `check-doc-links` share — change it there, not in
either caller.
