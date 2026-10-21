# Developer and extension guide

This guide describes the architecture, extension points, and validation
requirements. Start with the [project overview](../README.md), then use the
[generated API reference](../src/fim/API.md) for signatures.

## Contents

- [Architecture](#architecture)
- [Build environment](#build-environment)
- [Generation pipeline](#generation-pipeline)
- [Engine backends](#engine-backends)
- [Determinism](#determinism)
- [Persistence and reports](#persistence-and-reports)
- [Run objects and checkpoints](#run-objects-and-checkpoints)
- [Adding a statistic](#adding-a-statistic)
- [Adding a new what-if](#adding-a-new-what-if)
- [Testing](#testing)
- [Documentation](#documentation)

## Architecture

| Package | Responsibility |
|---|---|
| `fim.model` | Allele/locus/state values, parameter validation, initialization, update operators |
| `fim.statistics` | Pure diversity/differentiation functions, and across-replicate confidence intervals |
| `fim.config` | Every named policy constant and numerical guard of the convergence and statistics code, one module per subject — see its [README](../src/fim/config/README.md) |
| `fim.convergence` | Trailing-window and confidence-interval criteria, and the hard-cap monitor |
| `fim.persistence` | Store protocol, binary trajectory log, replayable manifest |
| `fim.engine` | Public run loop and final report assembly, behind three interchangeable backend implementations (`LinealBackend`/`GenerationalBackend` + `Advancer`) — see [Engine backends](#engine-backends) |
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
requirement to evidence. The
[the externally accessible engine API](fim-simulator-functional-api.md)
and the [desktop GUI test plan](fim-gui-test-plan.md) define
what a functional test may call. The [desktop GUI design](fim-gui-design.md)
covers why and how `fim.gui` itself is built, and the
[operational logging design](fim-logging-design.md) covers the `-l`/`-L`
flags and where log calls live. The plain-language
[test plan](fim-simulator-test-plan.md) explains scientific validation without
requiring programming knowledge.

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
pinned ShellCheck, yamllint, markdownlint, ESLint, Stylelint, HTMLHint,
gitleaks, and Homebrew validation images. Source the local environment file
before invoking those wrappers:

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
2. **Drift:** each deme/locus is multinomially resampled to exactly `N` gene
   copies from its post-migration frequencies.
3. **Mutation:** each of those `N` new gene copies mutates independently
   with its locus's probability (`mu`: a shared scalar, an explicit
   per-locus list, or one derived per locus from a per-base rate,
   `mu_b`), so an allele carried by $n$ copies loses
   $\mathrm{Binomial}(n,\mu)$ of them, with $\mu$ its mutation probability.
   By default (mutation_model:
   infinite_alleles) each mutant copy receives a globally novel ID; under
   the opt-in finite_alleles model, its target is drawn uniformly from the
   other states of its locus's bounded state space and can recur.

This is the textbook Wright-Fisher island model: two gene copies of the
next generation are identical only if their parental copies were and
neither mutated, so the identity recursions behind the closed-form
trajectories (`fim.statistics.identity_recursion`) and the
equilibrium-split burn-in (`fim.convergence.defaults.
panmictic_equilibration`) carry the factor $(1-\mu)^2$.
See [migration conventions](migration-conventions.md) for the identity
recursions and their relationship to published models.

Every operator receives all changing inputs explicitly and returns a new
`ModelState`. `ModelState` enforces one normalized sparse frequency map per
deme/locus.

## Engine backends

`fim.engine` offers three implementations of the generation pipeline,
behind one shared `EngineBackend` protocol (`run(params, store, run_id,
clock) -> RunResult | tuple[RunResult, ...]`), selected via `simulate_params()`'s
`engine_backend` setting (`build_engine_backend`, `fim.engine`). See
the [backend selection guide](fim-simulator-design.md#46-choosing-an-engine-backend)
for performance guidance; this section describes implementation constraints.

- **`LinealBackend`** (`"lineal"`, the default). Replica-first: one
  replicate runs to completion (or the configured process pool runs
  several in parallel — `max_workers`) before statistics/reports get
  assembled. It is the reference implementation against which other
  backends are checked; see
  [Determinism](#determinism) below.
- **`GenerationalBackend`** (`"generational"`/`"generational-vector"`,
  both share this one class). Generation-first instead: every
  still-active replicate in a batch (`ReplicaLane`) advances by exactly
  one generation before any of them moves to the next, via a pluggable
  `Advancer`. `SequentialAdvancer` does this with no new concurrency (a
  pure reshuffle, still bit-identical to `LinealBackend`);
  `ThreadedAdvancer` distributes per-generation work across
  threads (`ThreadPoolExecutor`, one pool per generation tick); this
  factory uses `ThreadedAdvancer` for
  `"generational"` — building `GenerationalBackend(SequentialAdvancer())`
  directly is supported but has no separate `engine_backend` string.
- **`VectorizedAdvancer`** (`"generational-vector"`). A third
  `Advancer`: keeps each replicate's state in one dense NumPy table
  (`fim.model.vector_block.VectorBlock`: `loci x demes x width`
  frequencies and counts, an allele-id array per locus) and advances a
  whole generation, every locus, with one compiled kernel call
  (`fim.model.vector_kernels.run_generation`) instead of one Python-level
  operator call per deme. Both mutation models and both migrant-sampling
  modes are supported. Under infinite
  alleles the table's columns represent currently present alleles, kept in
  ascending id order, appended for each new mutant, compacted when
  extinct, grown by doubling and shrunk after a long quiet stretch, with
  a per-replicate memory ceiling that fails early with the remedies
  named. Requires `numba`, provided by the `jit` extra in `pyproject.toml`.
  The kernel module is imported lazily, so `import fim` does not require it.
  The engine does not call the per-locus implementation in
  `fim.model.vectorized`.

**Backend parity requirements.** Changes to `fim.model.operators` must preserve
the following rules, checked by exact
tests (`test/model/test_vector_kernels.py`, `test_vector_block.py`,
`test/engine/test_vector_parity.py`), which compare complete ordered row
streams, never summaries:

1. Stage-major order across loci: migrate every locus, then draw drift
   for every `(deme, locus)` pair deme-major, then mutation counts (and
   finite-alleles targets) pair by pair in the same order.
2. Ascending allele-id order within a pair, for the drift categories and
   the mutation counts; an absent allele consumes no draw.
3. Migration arithmetic operation for operation: a sequential
   deme-ascending size-weighted mass, each pool value clamped at zero,
   each row divided by the `fsum` of its positive entries; a full matrix
   is one `fsum` per cell.
4. Drift normalization with NumPy's pairwise `ndarray.sum()` over the
   present probabilities (ported to the kernel).
5. The same inversion binomial, one uniform per nondegenerate draw.
6. Infinite alleles: identities handed out deme-major, locus-minor from
   one counter; finite alleles: the K-allele target draws of
   `FiniteAlleleSpace`, interleaved with the next pair's counts.

A change to `operators` that alters any of these (a summation order, a
clamp, a normalization, a draw order) makes the parity tests fail at the
first generation, which is the intent. The statistics `D`, `G_ST`, `H_S`,
`H_T` and `H_ST` are computed in the kernel and must equal
`statistics_report`'s bits for the same reason (a different last bit can,
rarely, move a convergence stop).

`fim.model.operators._inversion_binomial` uses mode-anchored inverse-CDF
sampling with one `rng.random()` per draw. A fixed uniform-draw count is
required for cross-backend RNG alignment; NumPy's `rng.binomial()` does not
provide that contract.

Finite-allele identity bookkeeping must persist across generations, including
identities no longer present in the population. The per-locus vector path
carries it in `ReplicaLane.vectorized_locus_states` through
`build_vectorized_state(previous_locus_states=...)`. Validate state
round-tripping with full multi-generation batches against `LinealBackend`;
isolated operator tests cannot establish end-to-end parity.

**Threading limits.** Python-level RNG work holds the GIL. A reference-scale
measurement at `d=60` found no speedup across 1–14 threads, with additional
overhead beyond 4–6 threads. `jit="numba"` improves some single-thread work
but does not remove GIL-bound migration and mutation calls.
`ThreadedAdvancer` also creates a `ThreadPoolExecutor` per generation.
Performance changes must preserve bit parity and be measured before and
after with `dev/bin/benchmark-engines --sweep d`; see
[maintainer scripts](../dev/bin/README.md#benchmark-engines).

## Determinism

- Construct one `numpy.random.Generator(PCG64(seed))` per scalar run.
- Pass it into initialization and every stochastic operator.
- Do not call NumPy's global RNG, `random`, the wall clock, or the network from
  simulation logic.
- Preserve first-observed allele order; ordering has no biological meaning but
  stable iteration keeps byte output reproducible.
- Keep timestamps in manifest metadata and default directory names only.
- A replicate batch's `SeedSequence` seed derivation and per-replicate PCG64
  generator, are unaffected by execution order or worker count: opt-in
  parallel replicate execution (`fim`'s max_workers) runs each replicate
  in a separate worker process and computes the same result as sequential
  execution; wall-clock timestamps can vary.
- `"lineal"` and `"generational"` are
  bit-identical for the same seed, always, by construction — different
  execution order over the identical dict-based arithmetic.
  `"generational-vector"` is bit-identical to both too, for the same
  seed *on the same machine*, under either mutation model, with or
  without migration, for any number of loci (the rules above). Its one
  limit is the compiled mathematics: Numba's `lgamma`, `log`, `log1p`
  and `exp` are verified against CPython's only on the development
  platform, the same caveat `jit="numba"` carries, so across machines
  results agree statistically, not row for row. Check
  `manifest.engine_backend` to know which engine produced an archived
  run. The
  [backend guide](fim-simulator-design.md#46-choosing-an-engine-backend)
  describes reproducibility limits and numerical conventions.

Tests use a derandomized Hypothesis profile and literal PCG64 seeds. Statistical
tolerances are derived from sample size before a seed is selected.

## Persistence and reports

`TrajectoryStore` is the public backend contract:

- write_generation(run_id, generation, rows)
- read(run_id)

Add a new backend under `fim.persistence` without changing the engine,
statistics, or visualizations. A store may also take a generation as a
`TrajectoryFrame` (flat arrays; `FrameStore`: `begin_run`, `write_frame`,
`wants_frames`), which is what makes a binary store fast: the engine hands
frames only to a store that asks for them (`wants_frames`) and keeps handing
rows to any other.

`BinaryLogStore` writes
`trajectory.tlog` (and `equilibrium_trajectory.tlog`): a chained-checksum
block log with sparse delta records, written by a background thread, read
back with random access (`LogReader`), exported to the canonical
`trajectory.jsonl` on request (`fim export`), and resumable from a
checkpoint. [The trajectory log](trajectory-log.md) describes the format, the
modules, the determinism rules and the tests. There is no other file-backed
store: `InMemoryTrajectoryStore` serves library calls and tests that need no
file, and the export's expected text in the tests is built from its rows with
plain `json.dumps`.

A store holds one run, so a batch gives every replicate a separate store
(`ReplicateFanoutStore` with a store factory). Its writer thread and file
descriptor live from the first frame to `close`, so the lifecycle rules matter:

- `close()` (also the `with` form) commits the last block and releases the
  writer and the ancestral-phase companion's. It is idempotent and ends the
  store's life: writing again raises. `read`, `frames` and `discard` do not
  need the writer; a reader sees every committed block while the run is in
  progress. `close` is optional on the protocol (`ClosableStore`,
  `fim.persistence.store.close_store`); `InMemoryTrajectoryStore` has a
  no-op one.
- The engine closes what it writes: `fim.engine.lineal.run_one` closes the store
  when a replicate ends (or raises), `ReplicateFanoutStore.close_run` closes
  a generational lane's store the moment it finishes (a batch holds open
  only its running lanes, so a large batch cannot exhaust file descriptors),
  and `GenerationalBackend` closes the rest at the end. The scalar run owners
  (`fim run`, the GUI runner) also close in a `with`/`closing` block.
- Close before the directory is renamed or removed. A run directory is built
  in a hidden sibling and renamed into place by `fim.paths.atomic_directory`;
  on POSIX an open descriptor follows the file through the rename, but Windows
  refuses to rename or delete a directory holding an open file. Any new owner
  of a store inside an `atomic_directory` block must close it before the
  block ends.
- A pickled store (a `RunResult.store` returned from a worker process) is
  sent closed, without its writer, and reads from the file in the receiving
  process.
- `discard(run_id)` closes the writer and removes the file.
- Entry-point tests verify that stores are closed with
  `conftest.tracked_log_stores` and `assert_none_open`
  (`test/persistence/test_store_lifecycle.py`).

A replicate batch needs one store *per replicate*, not one shared instance —
mandatory once max_workers is set, since a single store object cannot
cross a worker-process boundary. `fim`'s store_factory builds one given a
replicate's run_id; it must itself be picklable under max_workers (a
module-level function, or `functools.partial` over one — never a closure or
lambda). The CLI uses this to assign each replicate a separate
`replicate-NNN/trajectory.tlog`.

Statistics are computed per locus, then arithmetic-mean aggregated in the final
report. Keep locus-specific analysis in pure statistics functions rather than
adding engine state.

## Run objects and checkpoints

A Run object is a saved run that can be paused and resumed. The user
view is [saved runs](run-lifecycle.md). Every front end (`fim object`, the
desktop app's Saved runs, sweep points) goes through one service, so the
lifecycle rules live in one place:

| Module | Responsibility |
|--------|----------------|
| `fim.persistence.run_lifecycle` | `run.json` record, states, phases, the transition table, capabilities |
| `fim.persistence.run_objects` | Create, edit (Draft ↔ Planned), clone, list, delete objects under `results/objects/` |
| `fim.persistence.object_lock` | Cross-process advisory file locks |
| `fim.persistence.run_ownership` | Attempts and leases, fenced commits, pause/cancel requests, owner-loss reconciliation |
| `fim.persistence.run_checkpoint` | Atomic, digest-verified checkpoint publication and loading |
| `fim.persistence.scalar_snapshot` | Exact export/import of population, registries, and random state |
| `fim.persistence.run_receipt` | Completion receipt verification for recovering a lost completion |
| `fim.persistence.staging` | Find and reclaim orphan staging directories |
| `fim.execution` / `fim.batch_session` | `ScalarSession` / `BatchSession`: stepwise, checkpointable engine runs |
| `fim.run_service` | `execute_run_object`: the worker loop, checkpoint cadence, safe points, diagnostics |
| `fim.termination` | SIGTERM policies: pause a Run object; clean up a `fim run` |
| `fim.cli_objects` | `fim object` verbs |
| `fim.gui.object_bridge` / `fim.gui.object_runner` | Saved runs in the desktop app |
| `fim.sweep_run` / `fim.group_lifecycle` | Sweep points as objects; Study and Experiment summaries, close/reopen |

Rules a change must keep:

- **Exact continuation.** A resumed run must produce the bytes an
  uninterrupted run produces: trajectory log, report, convergence history.
  Anything a run carries between generations (random state, monitor state,
  registries, vector state) must be in the checkpoint. The tests in
  `test/engine/test_execution_checkpoint.py`, `test/engine/test_batch_session.py`,
  `test/test_run_service.py`, and `test/persistence/test_tlog_resume.py`
  compare resumed and uninterrupted runs.
- **The attempt lock proves ownership.** A live owner always holds it; a
  free lock proves the owner is gone. Never infer loss from a timeout.
- **Pointer last.** Write a checkpoint or artifact into a staging sibling,
  sync, rename it into place, and only then record it in `run.json`. A
  directory `run.json` does not name is garbage, never an alternative truth.
- **No checkpoint, no resume.** A run without a committed checkpoint cannot
  resume; a retry is a clone (`source_object_id`).
- **New checkpoint state means a schema bump.** Change
  `CHECKPOINT_SCHEMA_VERSION` when the checkpoint contents change; a
  checkpoint of another version is refused, not migrated.

## Adding a statistic

Every statistic is defined once, in `fim.statistics.catalog`. The engine's
tracked sets, the convergence choices, the GUI's result lists, the
statistics panel's rows, the results tables' columns, the convergence
checkboxes and the Settings dialog's "Statistics shown" list are all derived
from it, so a new statistic touches one entry plus its formula:

1. Write the formula as a pure function in `fim.statistics` (numbers in,
   numbers out), with tests against hand-computed values.
2. Add a `StatisticSpec` to the catalog: key, HTML and text labels, a
   one-sentence description, group, scope (`global` or `pair`), history
   policy, bounds, whether a run may stop on it, and whether a fresh
   install shows it.
3. Compute it where its scope says:
   - a global statistic: a `FinalReport` field filled in
     `fim.engine.report_for_state`, and, if it has no per-generation
     history, `history_free_statistic_values` so a scrubbed frame shows it;
   - a pair statistic: `fim.engine.pair_statistic_values`, and a matrix in
     `fim.persistence.pairwise` if every pair should be saved.
4. Regenerate the committed worked examples (`doc/examples/`): their
   `report.json` files are compared with a fresh run.

The GUI needs no edit: it receives the catalog through
`Api.get_statistics_catalog` and builds everything from it. Drawing
constants for the GUI's plots live in the
[`webui/config/` modules](../src/fim/gui/webui/config/README.md).

Showing a statistic is display only. Nothing that computes or saves results
may read the GUI's shown set; a test enforces that.

## Adding a new what-if

| Requested change | Extension point |
|---|---|
| Unequal deme size | Pass per-deme `N`; operators and $E_{ST}$ support it |
| Asymmetric migration | Pass a validated `d` by `d` matrix |
| Stepping-stone topology (1D) | `fim.model.topology`: `m: {topology: ring\|linear, rate}` or a hand-written sparse map |
| Spatial migration beyond 1D or a fixed matrix | 2D lattice topology, or a `MigrantPoolStrategy` interface — neither built yet |
| Random, rather than fixed, migrant counts | `migrant_sampling: stochastic`; `migrate()` accepts `rng` and draws $\mathrm{Binomial}(N_i,r_i)$, where $r_i$ is the migrant fraction |
| Per-locus mutation rate from length | `mu_b` derives each locus's `mu` from its `length`; alternatively, pass a per-locus `mu` list |
| Finite-length alleles (remove infinite-length artifacts) | mutation_model: finite_alleles; `LocusSpec.length` bounds each locus to `4 ** length` states, and mutation can recur |
| Selection | Add a pure `select` operator before drift |
| Stepwise (distance-based) mutation, e.g. for microsatellites | Add a strategy behind mutation identity assignment — a different, still-unbuilt model from the row above (§3.2 of the design doc explains why) |
| Several convergence statistics | Pass a list for convergence_statistic plus convergence_combinator |
| Adaptive replicate count | `precision` and `stop_batch_early` control stopping; `fim.engine.replicate_summary` and `summary.json` report achieved intervals |
| Faster replicate batches | max_workers (library) / `--workers`, `--sequential` (CLI): one worker process per replicate batch-slot, opt-in, changes nothing about what is computed |
| Large trajectories | Implement another `TrajectoryStore` |
| GUI | Call `fim.engine.simulate_params`; do not duplicate model logic |

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
the scientific proof. Every Backend-V-only test (`fim.model.vector_kernels`,
`fim.model.vector_block`, `VectorizedAdvancer`) starts with `pytest.importorskip("numba")` rather
than assuming it is installed, so `.[dev]` alone still runs cleanly —
those tests skip when the optional dependency is absent. The coverage-gated CI job installs
`.[dev,jit]` specifically, not `.[dev]`: skipped tests contribute no
coverage. Install `.[dev,jit]` locally to reproduce the 90 percent coverage
gate, including Backend V tests.

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

After changing `doc/usage.md`, `doc/configuration.md`, or `doc/statistics.md`,
regenerate the
GUI's Help screen content the same way:

```console
dev/bin/generate-help-html
```

The pre-commit hook refreshes it automatically when a source guide (or
the generator itself) is staged; the pre-push hook and CI verify freshness
the same way they do for the API reference above. anchor_for in
`dev/lib/docslug.py` is the one GitHub-compatible heading-anchor slugger
both this generator and `check-doc-links` share — change it there, not in
either caller.
