# The externally accessible engine API

- [The externally accessible engine API](#the-externally-accessible-engine-api)
  - [Who this document is for](#who-this-document-is-for)
  - [What "externally accessible" means here](#what-externally-accessible-means-here)
  - [1. `fim` — the front door](#1-fim--the-front-door)
  - [1b. `fim.engine` — the engine entry point](#1b-fimengine--the-engine-entry-point)
  - [2. `fim.model` — describe a simulation](#2-fimmodel--describe-a-simulation)
  - [3. `fim.model.topology` — build a migration matrix](#3-fimmodeltopology--build-a-migration-matrix)
  - [4. `fim.statistics` — differentiation and diversity measures](#4-fimstatistics--differentiation-and-diversity-measures)
  - [5. `fim.convergence` — deciding when a run is done](#5-fimconvergence--deciding-when-a-run-is-done)
  - [6. `fim.persistence` — reading and writing run results](#6-fimpersistence--reading-and-writing-run-results)
  - [7. `fim.reanalyze` — re-deriving statistics from a saved trajectory](#7-fimreanalyze--re-deriving-statistics-from-a-saved-trajectory)
  - [8. `fim.viz` — headless plotting](#8-fimviz--headless-plotting)
  - [9. `fim.cli` — the command line](#9-fimcli--the-command-line)
  - [What is deliberately not here](#what-is-deliberately-not-here)

## Who this document is for

Anyone writing or reviewing a **fim functional** test (see `doc/
fim-simulator-detailed-test-plan.md`'s own taxonomy) — a test whose job
is to check the simulator's documented, externally visible *behavior*,
not its internal machinery. This document lists exactly what that
external surface is: every module, class, and function a functional
test is allowed to import and call. A functional test that reaches past
this list — into `fim.model.operators`, a name starting with `_`, or any
module not listed here — has stopped being a functional test and become
an internal one (still potentially valuable, just a different kind; see
the detailed test plan).

This list is not a tutorial. For what each piece of biology and
statistics actually means, see the [finite island model
introduction](finite-island-model-introduction.md) and the [Jost
differentiation-measures guide](jost-differentiation-measures.md); for
how to install and run the simulator as an end user, see
[usage.md](usage.md) and [configuration.md](configuration.md). This
document only answers "what can code call."

## What "externally accessible" means here

The supported surface is what the public modules declare in `__all__`:
`fim` (the front door below), `fim.engine`, `fim.errors`, `fim.model`,
`fim.persistence`, `fim.results`, `fim.spec`, `fim.statistics`,
`fim.convergence`, and `fim.viz`, plus the single-file modules `fim.cli` and
`fim.reanalyze`. `test/test_public_surface.py` enforces it: each of those
modules must declare an importable `__all__`, and no module may import another
module's underscore-prefixed name. Anything else, a name starting with `_` or
a submodule never re-exported (`fim.model.operators` is the standing example),
is an implementation detail, free to change shape without counting as a
breaking change to this API. `fim` ships type information (`py.typed`).

`fim.gui` is a separate, GUI-specific API surface, not covered here —
see `doc/fim-gui-test-plan.md`.

## 1. `fim` — the front door

The names a Python user needs, loaded on first use so `import fim` stays
light. [Using fim from Python](python-api.md) is the guide with runnable
examples; this section is the reference.

- **`simulate(spec, *, execution=None, recording=None, clock=None) -> Result`**
  runs a `RunSpec` (or a `RunConfig`, whose own execution and recording the
  keywords override) and always returns a `Result`. With
  `recording.directory` set, the run is published there atomically in the
  layout `fim run` writes; with `execution.workers`, a `lineal` batch runs in
  worker processes (a directory is then required).
- **`RunSpec(model, seed, stopping, replication, analysis, expert)`** and its
  parts, all frozen, keyword-only dataclasses that report a bad value by its
  field path: `Model(demes, population, migration, mutation, loci,
  migrant_sampling, initial)` with `Population(individuals, ploidy)`,
  `Locus(id, length)`, `InfiniteAlleles(rate)` or `FiniteAlleles(rate)`, and
  an initial condition `FoundingAlleles`, `ExplicitFrequencies`, or
  `EquilibriumSplit`; `Stopping`; `Replication` (including `max_concurrent`);
  `Analysis`.
- **`Execution`** (backend, JIT, worker processes, the `auto` thresholds) and
  **`Recording`** (directory, trajectory retention) are passed beside the
  `RunSpec`. They never change a result and take no part in a run's identity.
- **`run_identity(spec) -> str`** — the run's ID, from the science only.
- **`load_config(path) -> RunConfig`**, **`RunConfig`** — a configuration
  file as grouped objects, with its labels.
- **`Result`** — `run_id`, `spec`, `replicates` (a tuple of **`Replicate`**),
  `summary` (statistic ID to confidence interval), `batch_stop`, `provenance`,
  and `only()`. A `Replicate` has `index`, `seed`, `run_id`, `stop`, `report`
  (a **`Report`**: a read-only mapping of catalog statistic IDs, with `D`,
  `G_ST`, `H_S`, `H_T` as attributes and `to_dict()` giving `report.json`),
  `histories`, `final_state`, and a read-only `trajectory` reader.
- **`open_run(directory) -> StoredRun`**, **`StoredRun`**,
  **`StoredReplicate`** — a saved run opened again, with the same report,
  stop, history, and summary types, `rows()`, `verify()`, and `reanalyze()`.
- **`Workspace(path)`**, **`RunHandle`** — a results directory of saved,
  checkpointed, resumable runs: `plan`, `run`, `handle`, `handles`, and on a
  handle `start`/`resume`, `pause`, `cancel`, `delete`, `state`, `open`.
- **`FimError`**, **`ConfigurationError`**, **`UnsupportedError`** — the
  exception root, a bad specification, and a valid one no available engine
  can run. `fim.errors.StoreError` covers damaged, closed, or read-only
  stores.

`fim.spec.to_params(spec, execution, recording)` compiles a specification to
the engine's `SimulationParams`, and `fim.spec.from_params` reads one back.

## 1b. `fim.engine` — the engine entry point

What `simulate` calls. Code that already holds `SimulationParams` can call it
directly.

- **`simulate_params(params, *, store=None, run_id=None, clock=None,
  max_workers=None, store_factory=None) -> RunResult | tuple[RunResult, ...]`**
  Runs the finite island model to convergence (or the hard generation cap),
  once per replicate. The configuration is stated once, in `params`; the
  backend (`engine_backend`, `jit`, and the `auto` thresholds) is read from
  it. Returns one `RunResult`, or a tuple with one per replicate (fewer than
  `n_replicates` when `stop_batch_early` lets the batch stop once every
  watched statistic's across-replicate interval has tightened enough).
  `max_workers` and `store_factory` opt `lineal` into running replicates in
  worker processes and give each replicate its own store; `clock` is a test
  seam. `engine_backend` selects the implementation: `"lineal"` (the
  reference), `"generational"` (thread-based replicate fan-out, bit-identical
  to `lineal` for the same seed), `"generational-vector"` (array-native; one
  compiled kernel call per generation, identical to `lineal` for the same seed
  on the same platform), or `"auto"` (picks between the last two: the vector
  backend when `numba` is importable, `jit` is `"off"`, the deme count is at
  least `auto_vector_min_d` and, under finite alleles, every locus's capacity
  is at most `auto_vector_max_capacity`; otherwise the generational one). A
  direct `"generational-vector"` choice without `numba` raises `ValueError`;
  a table over the per-replicate memory ceiling (2 GiB, or the
  `FIM_VECTOR_MEMORY_CEILING_BYTES` environment variable) raises
  `fim.model.vector_block.VectorMemoryCeilingError` with the remedies named.
  The backend that actually ran, never the literal `"auto"`, is recorded on
  each result's `manifest.engine_backend`.
- **`deterministic_run_id(params) -> str`** — the run ID, from
  `SimulationParams.identity_dict`, which leaves out the engine, JIT, trajectory
  retention, and storage settings.
- **`report_for_state(state, params, *, run_id, converged, reason, ...) -> FinalReport`**
  Builds a `FinalReport` from any `ModelState`, not only a state a run just
  finished producing — the same function `fim.reanalyze` uses to recompute a
  report at an earlier saved generation. Optional `window_statistics` and
  `converged_statistics` carry what only the run itself knows.
- **`reports_summary(reports) -> Mapping[str, float | None]`** and
  **`replicate_summary(reports) -> Mapping[str, tuple[float, ConfidenceInterval]]`**
  Aggregate several replicates' own `FinalReport`s into means (and, for the
  latter, confidence intervals) per statistic.
- **`build_engine_backend(engine_backend, *, jit="off", max_workers=None,
  store_factory=None, params=None,
  auto_vector_min_d=DEFAULT_AUTO_VECTOR_MIN_D,
  auto_vector_max_capacity=DEFAULT_AUTO_VECTOR_MAX_CAPACITY) ->
  EngineBackend`** — the factory `simulate_params()` itself calls; available for
  a caller that wants a configured backend object directly. `params` is
  required, and only actually read, when `engine_backend="auto"`.
- **`DEFAULT_AUTO_VECTOR_MIN_D`** — the module-level constant
  `auto_vector_min_d` defaults to; its own docstring has the full
  benchmark finding behind the default value and why it is a parameter,
  not a hardcoded constant.
- **`DEFAULT_AUTO_VECTOR_MAX_CAPACITY`** — the module-level constant
  `auto_vector_max_capacity` defaults to; its own docstring has the
  full benchmark finding behind the default value, the same "parameter,
  not a hardcoded constant" reasoning `DEFAULT_AUTO_VECTOR_MIN_D`
  already carries.
- **`EngineBackend`** (a `Protocol`), **`LinealBackend`**,
  **`GenerationalBackend`** — the common backend contract and its two
  current implementations. `LinealBackend` wraps today's own
  process-based dispatch unchanged; `GenerationalBackend` wraps
  `run_batch` (below), driven by an injectable `Advancer`
  (`SequentialAdvancer`, no new concurrency — what `GenerationalBackend()`
  defaults to when constructed directly rather than through
  `build_engine_backend`; `ThreadedAdvancer`, real thread-based fan-out —
  what `engine_backend="generational"` actually builds;
  `VectorizedAdvancer`, array-native, one compiled kernel call per
  generation, both mutation models, both migrant sampling modes — what
  `engine_backend="generational-vector"` actually builds).
- **`run_batch(params, store, run_id, clock, advancer) -> tuple[RunResult, ...]`**,
  **`ReplicaLane`** — the generation-first driving loop `GenerationalBackend`
  calls, and the per-replica working-state object it advances one
  generation at a time; for the same seed, bit-identical to
  `LinealBackend`'s own trajectory under
  `SequentialAdvancer`/`ThreadedAdvancer`/`VectorizedAdvancer` (the last
  on the same platform; see `engine_backend`'s own entry above).
- **`FinalReport`** (a `TypedDict`) — the scalar numbers a finished
  run reports, averaged across every tracked locus: `run_id`,
  `generation`, `converged`, `converged_on` (the watched statistic(s)
  that had settled when the run stopped; `None` for a run that hit its
  cap), `reason`, and the six
  differentiation/heterozygosity measures `G_ST` (`None` when every
  tracked locus has fixed, since the statistic is undefined with no
  variation left), `D`, `E_ST`, `K_ST`, `H_S`, `H_T`, `H_ST`, plus the
  literature-derived supplemental statistics `A_CGD` (Caballero-
  García-Dorado allelic distance), `Delta` (Gregorius δ), and `MI`
  (Sherwin mutual information).
- **`RunResult`** (a frozen dataclass) — everything one finished run
  produced: `run_id`, `params`, `final_state` (a `ModelState`, every
  deme's allele frequencies at every locus at the last generation),
  `report` (a `FinalReport`), `convergence_generations`/
  `convergence_history`/`convergence_histories` (the run's own
  generation-by-generation trajectory of whichever statistic(s) it
  watched), `manifest` (a `RunManifest`), and `store` (the
  `TrajectoryStore` the run wrote to, for reading its full
  per-generation history back later).
- **`deterministic_run_id(params) -> str`** — the same configuration
  always produces the same id; used when a caller does not supply its
  own `run_id`.
- **`report_for_state(state, params, *, run_id, converged, reason, ...) -> FinalReport`**
  Builds a `FinalReport` from any `ModelState`, not only a state `simulate_params()`
  itself just finished producing — the same function `fim.reanalyze`
  uses to recompute a report at an earlier saved generation. Optional
  `window_statistics` and `converged_statistics` (the statistics a run's
  own monitor saw pass, `ConvergenceMonitor.stable_statistics`) carry
  what only the run itself knows.
- **`reports_summary(reports) -> Mapping[str, float | None]`** and
  **`replicate_summary(reports) -> Mapping[str, tuple[float, ConfidenceInterval]]`**
  Aggregate several replicates' own `FinalReport`s into means (and, for
  the latter, confidence intervals) per statistic — what the CLI's own
  batch/stats output and the desktop app's batch-results screen are
  built from.

## 2. `fim.model` — describe a simulation

The plain data describing one finite-island population at a moment in
time, and the pure functions that build a starting one.

- **`SimulationParams`** (a frozen dataclass) — the full, validated
  configuration `simulate_params()` takes: `gene_copies` (`PopulationSize`, an `int` gene-copy
  count or a per-deme tuple), `m` (`Migration`, a scalar rate or a dense
  `d`-by-`d` matrix), `mu` (`MutationRate`, a scalar or per-locus
  tuple), `d` (deme count), `seed`, `loci` (a tuple of `LocusSpec`),
  `initial_allele_count`, `initial_concentration`, `deme_weighting`,
  `convergence_statistic`/`convergence_combinator`/`convergence_burn_in`/
  `precision`, `max_generations`, `n_replicates`,
  `stop_batch_early`/`replicate_minimum`/`confidence`,
  `migrant_sampling` (`"continuous"` or a stochastic mode),
  `mutation_model` (`"infinite_alleles"` or `"finite_alleles"`), and an
  optional `initial_frequencies` override. Constructing one validates
  every field; see `doc/configuration.md` for what each one means to a
  user filling in a form, and `fim-simulator-design.md` for the
  scientific rationale behind each default.
- **`ModelState`** — one simulated population at one generation: every
  deme's own allele-frequency mapping, at every locus. Its own public
  accessors (`frequency_map`, `deme_count`, `locus_count`, `support_sizes`,
  `total_frequency`, `to_rows`, `from_rows`) are how a functional test
  reads a run's output without knowing anything about how frequencies
  are stored internally.
- **`LocusSpec`** — one tracked locus's own configuration (sequence
  length, per-locus mutation rate override, and so on).
- **`AlleleId`**, **`AlleleRegistry`**, **`founding_allele_ids`** — how
  alleles are identified and how a population's *founding* alleles (the
  ones present at generation zero, before any mutation) are recovered
  from a state.
- **`generate_initial_state(params) -> ModelState`** — builds generation
  zero from a validated `SimulationParams`, following whichever starting
  distribution (`initial_allele_count`/`initial_concentration`, or an
  explicit `initial_frequencies` override) the parameters describe.

`fim.model.operators` (Migrate/Mutate/Drift, the actual per-generation
update rules `simulate_params()` calls in a loop) is **not** part of this list —
see "What is deliberately not here," below.

## 3. `fim.model.topology` — build a migration matrix

Helpers for the spatial (stepping-stone) migration case, exported
directly from `fim.model.topology` (not re-exported at the `fim.model`
top level, since most callers only ever need a plain scalar or
hand-built matrix for `m`):

- **`stepping_stone_neighbors(d, *, topology, rate) -> dict[int, dict[int, float]]`**
  A sparse one-based neighbor map for a 1-D ring or bounded-line
  arrangement of `d` demes.
- **`dense_matrix_from_neighbors(neighbors, d) -> tuple[tuple[float, ...], ...]`**
  Turns any sparse neighbor map (from the function above, or hand-built,
  as the Crow & Aoki torus scenario tests do for a 2-D lattice this
  project has no dedicated builder for) into the dense, row-stochastic
  matrix `Migration` and `simulate_params()` actually expect.

## 4. `fim.statistics` — differentiation and diversity measures

Pure functions computing a diversity or differentiation statistic from
a frequency table, or predicting one theoretically from a model's own
parameters — no dependency on the engine, persistence, or a run having
happened at all. Full formulas and citations:
[jost-differentiation-measures.md](jost-differentiation-measures.md).

*Computed from an actual (or simulated) frequency table:* `heterozygosity`,
`identity`, `hill_number`, `h_s`, `h_t`, `h_st`, `total_hill_number`,
`within_hill_number`, `g_st`, `g_st_log`, `jost_d`, `e_st`, `k_st`,
`differentiation_q`, `d_m`, `r_st`, `statistics_report`.

*Theoretical equilibrium predictions, from `(N, m, mu, d)` alone, no
frequency table needed:* `equilibrium_d`, `equilibrium_g_st`,
`equilibrium_shannon_entropy_isolated`/`_isolated_smm`/`_total`/
`_subpopulation`, `equilibrium_shannon_differentiation`.

*Convergence-speed prediction (Whitlock 1992), a different kind of
question from every formula above — how fast identity moves toward
equilibrium, not what value it settles to:* `identity_recovery_rate`,
`identity_recovery_equilibrium`, `identity_recovery_trajectory`,
`identity_recovery_half_life`.

*Confidence intervals for a sample mean across replicates:*
`ConfidenceInterval`, `confidence_interval`, `student_t_critical_value`.

`DifferentiationReport` is the `TypedDict` `statistics_report` returns —
the same scalar statistics `FinalReport` (§1) reports per run, computed
directly from a frequency table instead of from a live simulation.

## 5. `fim.convergence` — deciding when a run is done

- **`ConvergenceMonitor`** — the stateful class driving a run's own
  stopping decision: accumulates a watched statistic's history
  generation by generation, asks its configured `ConvergenceCriterion`
  whether to stop, and separately enforces `SimulationParams.
  max_generations` as a hard safety cap.
- **`ConvergenceCriterion`** and its concrete rules —
  `TrailingWindowCriterion`/`trailing_window_stable` (has a statistic's
  own recent history stopped moving) and `ConfidenceIntervalCriterion`
  (has a confidence interval across replicates tightened enough).
  Watching several statistics at once (requiring them to agree) is a
  `ConvergenceMonitor` feature — see `convergence_combinator` in
  `doc/configuration.md` — not a separate criterion class.
- **`ConvergenceOutcome`**, **`StopReason`** — what a monitor's own
  decision looked like: which generation, whether it converged or hit
  the cap, and why.

## 6. `fim.persistence` — reading and writing run results

- **`TrajectoryStore`** (the interface) and its two implementations,
  **`BinaryLogStore`** (the real, file-backed store — a compact binary
  log written by a background thread, so a run's history survives an
  interruption up to its last complete block; `close()` or a `with`
  block commits and releases it, and a closed store accepts no more
  writes) and **`InMemoryTrajectoryStore`** (a test double with
  the same interface, nothing touches disk). **`TrajectoryRow`** is one
  row of that per-generation history.
- **`RunManifest`**, **`read_manifest`**, **`write_manifest`** — a
  finished run's own bookkeeping (parameters, timing, software version,
  and a checksum of its trajectory file, used to detect the trajectory
  having since been edited, corrupted, or replaced). `engine_backend`/
  `jit` record which engine implementation and JIT setting actually
  produced the run — `None` for anything predating those two fields;
  `simulate_params()` always resolves and records the real choice, never the
  literal string `"auto"`.

## 7. `fim.reanalyze` — re-deriving statistics from a saved trajectory

- **`reanalyze_trajectory(...)`** and **`group_rows_by_generation(...)`**
  Read a trajectory a run already wrote to disk and recompute its
  statistics report at any earlier generation, not only the one the run
  actually stopped at.
- **`differentiation_q_for_state(state, order) -> float`** — the general
  `differentiation_q` family (§4), evaluated directly against a saved
  `ModelState` at a chosen order, for a "how does the conclusion change
  at a different `q`" sweep.
- **`ReanalyzedGeneration`** — one such recomputed generation's own
  result.

## 8. `fim.viz` — headless plotting

Matplotlib-based plotting functions with no GUI dependency (used by both
the CLI's own file output and the desktop app's screens):
`plot_convergence_trace`, `plot_frequency_bars`, `plot_frequency_scatter`.

## 9. `fim.cli` — the command line

- **`main(argv=None) -> int`** — the whole CLI's own entry point
  (`init`/`run`/`stats`/`update` subcommands); what `fim` on a shell
  actually calls, and what `test/cli/test_cli.py` drives directly rather
  than shelling out to a subprocess.
- **`load_config(path) -> SimulationParams`** — parses a YAML
  configuration file (see `configuration.md`) into a validated
  `SimulationParams`, the same parsing the `run`/`stats` subcommands use.

## What is deliberately not here

- **`fim.model.operators`** — the actual Migrate/Mutate/Drift step
  functions. `simulate_params()` is the documented way to run generations; nothing
  outside `fim.engine` calls these directly, by this project's own
  stated design (see `fim.model`'s own package docstring).
- Any name starting with `_` in any module, anywhere — always private,
  regardless of how useful it looks from outside.
- Test-only helpers that happen to mirror internal engine mechanics for
  cross-checking purposes (the exact-recursion oracle in
  `test/validation/test_simulator_equilibrium.py`, for instance) — these
  live in `test/`, are never imported by production code, and are
  themselves an example of the "internal/deep" test category the
  detailed test plan describes, not part of this API.
- `fim.gui` — a separate surface for the desktop application; see
  `doc/fim-gui-test-plan.md`.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5.5
generator-model-token: claude-sonnet-5-5
generator-provider: Anthropic
generation-date: 2026-10-15
generator-responsibility: revision
```
