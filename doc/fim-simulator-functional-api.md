# The externally accessible engine API

- [The externally accessible engine API](#the-externally-accessible-engine-api)
  - [Who this document is for](#who-this-document-is-for)
  - [What "externally accessible" means here](#what-externally-accessible-means-here)
  - [1. `fim.engine` — run a simulation](#1-fimengine--run-a-simulation)
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

Every package under `src/fim/` re-exports its own public surface through
its `__init__.py`'s own `__all__` — the names listed there (plus a
handful of single-file modules with no `__init__.py` of their own:
`fim.engine`, `fim.cli`, `fim.reanalyze`) are the whole of what "outside
code" (a test, the CLI, the desktop app, a future script) is meant to
import. Anything else — a name starting with `_`, or a submodule never
re-exported (`fim.model.operators` is the standing example: its own
package docstring states plainly that it is "called only from
`fim.engine`'s own run loop, never directly by outside code") — is an
implementation detail, free to change shape under a future core
refactor without that refactor counting as a breaking change to this
API.

`fim.gui` is a separate, GUI-specific API surface, not covered here —
see `doc/fim-gui-test-plan.md`.

## 1. `fim.engine` — run a simulation

The one entry point everything else in this project ultimately calls.

- **`fim(N, m, mu, d, *, params, store=None, run_id=None, clock=None,
  max_workers=None, store_factory=None, engine_backend=None, jit=None,
  auto_vector_min_d=None, auto_vector_max_capacity=None) -> RunResult |
  tuple[RunResult, ...]`**
  Runs the finite island model to convergence (or the hard generation
  cap), once per replicate. `N`, `m`, `mu`, `d` must equal the same
  fields already inside `params`; the four are repeated in the
  signature because a real call site names them directly (matching how
  a genetics paper would state a scenario) rather than reading them out
  of an opaque options object. Returns one `RunResult` when
  `params.n_replicates == 1`, otherwise a tuple of one per replicate (or
  fewer, if `params.replicate_tolerance` lets the batch stop early once
  every watched statistic's confidence interval has tightened enough).
  `engine_backend`/`jit`/`auto_vector_min_d`/`auto_vector_max_capacity`
  each default to `None` here, meaning "read `params.engine_backend`/
  `params.jit`/`params.auto_vector_min_d`/`params.auto_vector_max_
  capacity` instead" — real `SimulationParams` fields now (config-file/
  CLI reachable, `doc/configuration.md`), not duplicate-of-nothing
  keyword arguments; passing one of these four explicitly overrides
  `params` for that one call only, a pure override with no "must agree"
  requirement (unlike `N`/`m`/`mu`/`d` above). `engine_backend` selects
  which of this project's own engine
  implementations actually runs the batch — `"lineal"` (`params`'s own
  default too, every earlier release's own behavior, unchanged),
  `"generational"`
  (real thread-based replicate fan-out, bit-identical trajectory to
  `"lineal"` for the same seed), `"generational-vector"` (array-native,
  fused `migrate`/`mutate`/`drift`; matches `"lineal"` exactly, same
  seed, only for a **single-locus** run with migration off, and matches
  it statistically otherwise — same mean differentiation statistics
  across many seeds, not necessarily the same individual trajectory —
  whenever migration is active (its own dense-matrix migration blend
  and `"lineal"`'s own arithmetic are two different, equally valid
  floating-point paths to the same computation) or whenever two or more
  loci are tracked, migration on or off (`step_vectorized` fuses all
  three operators per locus, one whole locus at a time, while the
  dict-based backends run each stage across every locus first in a
  deme-major order — the two draw from the shared RNG stream in a
  different order the instant more than one locus is tracked)), or
  `"auto"` (picks between `"generational"` and
  `"generational-vector"` using `params.d`/`auto_vector_min_d` and
  every locus's own capacity against `params.auto_vector_max_capacity`,
  both below — never `"lineal"`).
  `"generational-vector"` is scoped to `params.mutation_model=
  "finite_alleles"` and `params.migrant_sampling="continuous"`; a
  direct `"generational-vector"` choice outside that scope raises
  `ValueError` naming the violated constraint (`"auto"` falls back to
  `"generational"` instead, silently, since it is choosing on the
  caller's behalf), and needs the optional `numba` dependency
  (`pip install fim[jit]`) unconditionally — it has no separate `jit`
  toggle of its own, so only `jit="off"` (the default) is accepted
  alongside it or alongside `"auto"` resolving to it. `max_workers`/
  `store_factory` opt `"lineal"` into running independent replicates
  across real OS processes rather than one at a time (`clock`/
  `store_factory` must be plain module-level functions, not closures or
  lambdas, whenever `max_workers` is set) — meaningful only under
  `"lineal"`; passing either alongside a different `engine_backend`
  raises `ValueError` rather than being silently ignored. `jit="numba"`
  JIT-compiles `"generational"`'s own `drift` step (bit-identical
  output; needs the optional `numba` dependency, `pip install
  fim[jit]`) — a real fix for a call-overhead regression an earlier
  internal attempt had, but not yet a demonstrated wall-clock win for
  `drift` as a whole (see the engine's own docstrings for the measured
  detail); `"lineal"` never accepts anything but `jit="off"`.
  `auto_vector_min_d` is the deme-count cutover `"auto"` uses to decide
  — irrelevant under every other `engine_backend`; `params.
  auto_vector_min_d`'s own default is `DEFAULT_AUTO_VECTOR_MIN_D` (35,
  defined in `fim.model.params`), a real benchmark-measured default
  with known cross-environment caveats (see that constant's own
  docstring). `auto_vector_max_capacity` is the per-locus capacity
  ceiling `"auto"` uses alongside it — every locus in `params.loci`
  must be at most this value for `"auto"` to pick `"generational-
  vector"`, regardless of `d`; `params.auto_vector_max_capacity`'s own
  default is `DEFAULT_AUTO_VECTOR_MAX_CAPACITY` (1024), likewise a real
  benchmark-measured default with the same caveats. Whichever backend
  actually ran — the resolved choice, not
  the literal string `"auto"` — and whether `jit` was on are both
  recorded on the returned result's own `manifest.engine_backend`/
  `manifest.jit`, so a saved run's own record always says what actually
  produced it, even when the caller asked for `"auto"`.
- **`build_engine_backend(engine_backend, *, jit="off", max_workers=None,
  store_factory=None, params=None,
  auto_vector_min_d=DEFAULT_AUTO_VECTOR_MIN_D,
  auto_vector_max_capacity=DEFAULT_AUTO_VECTOR_MAX_CAPACITY) ->
  EngineBackend`** — the factory `fim()` itself calls; usually reached
  through `fim()`'s own keywords above, not called directly, but
  available for a caller that wants a configured backend object without
  going through `fim()`'s own full public signature. `params` is
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
  `VectorizedAdvancer`, array-native fused `migrate`/`mutate`/`drift`
  scoped to finite-alleles/continuous-migration configs — what
  `engine_backend="generational-vector"` actually builds).
- **`run_batch(params, store, run_id, clock, advancer) -> tuple[RunResult, ...]`**,
  **`ReplicaLane`** — the generation-first driving loop `GenerationalBackend`
  calls, and the per-replica working-state object it advances one
  generation at a time; for the same seed, with `replicate_tolerance`
  unset, bit-identical to `LinealBackend`'s own trajectory under
  `SequentialAdvancer`/`ThreadedAdvancer` — not under `VectorizedAdvancer`
  in general, which matches exactly only for a single-locus run with
  migration off (see `engine_backend`'s own entry above for what
  "statistically" means the rest of the time, including every
  multi-locus case).
- **`FinalReport`** (a `TypedDict`) — the seven scalar numbers a finished
  run reports, averaged across every tracked locus: `run_id`,
  `generation`, `converged`, `converged_on`, `reason`, and the six
  differentiation/heterozygosity measures `G_ST` (`None` when every
  tracked locus has fixed, since the statistic is undefined with no
  variation left), `D`, `E_ST`, `K_ST`, `H_S`, `H_T`, `H_ST`.
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
- **`report_for_state(state, params, *, generation, converged, converged_on, reason) -> FinalReport`**
  Builds a `FinalReport` from any `ModelState`, not only a state `fim()`
  itself just finished producing — the same function `fim.reanalyze`
  uses to recompute a report at an earlier saved generation.
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
  configuration `fim()` takes: `N` (`PopulationSize`, an `int` gene-copy
  count or a per-deme tuple), `m` (`Migration`, a scalar rate or a dense
  `d`-by-`d` matrix), `mu` (`MutationRate`, a scalar or per-locus
  tuple), `d` (deme count), `seed`, `loci` (a tuple of `LocusSpec`),
  `initial_allele_count`, `initial_concentration`, `deme_weighting`,
  `convergence_statistic`/`convergence_combinator`/`convergence_window`/
  `convergence_tolerance`, `max_generations`, `n_replicates`,
  `replicate_tolerance`/`replicate_minimum`/`replicate_confidence`,
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
update rules `fim()` calls in a loop) is **not** part of this list —
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
  matrix `Migration` and `fim()` actually expect.

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
the same seven fields `FinalReport` (§1) reports per run, computed
directly from a frequency table instead of from a live simulation.

## 5. `fim.convergence` — deciding when a run is done

- **`ConvergenceMonitor`** — the stateful class driving a run's own
  stopping decision: accumulates a watched statistic's history
  generation by generation, asks its configured `ConvergenceCriterion`
  whether to stop, and separately enforces `SimulationParams.
  max_generations` as a hard safety cap.
- **`ConvergenceCriterion`** and its concrete rules —
  `TrailingWindowCriterion`/`trailing_window_stable` (has a statistic's
  own recent history stopped moving), `ConfidenceIntervalCriterion` (has
  a confidence interval across replicates tightened enough), and the two
  combinators `AllCriterion`/`AnyCriterion` for requiring several rules
  (or several watched statistics) to agree.
- **`ConvergenceOutcome`**, **`StopReason`** — what a monitor's own
  decision looked like: which generation, whether it converged or hit
  the cap, and why.

## 6. `fim.persistence` — reading and writing run results

- **`TrajectoryStore`** (the interface) and its two implementations,
  **`JSONLTrajectoryStore`** (the real, file-backed store — one
  generation written at a time, so a run's history survives an
  interruption) and **`InMemoryTrajectoryStore`** (a test double with
  the same interface, nothing touches disk). **`TrajectoryRow`** is one
  row of that per-generation history.
- **`RunManifest`**, **`read_manifest`**, **`write_manifest`** — a
  finished run's own bookkeeping (parameters, timing, software version,
  and a checksum of its trajectory file, used to detect the trajectory
  having since been edited, corrupted, or replaced). `engine_backend`/
  `jit` record which engine implementation and JIT setting actually
  produced the run — `None` for anything predating those two fields;
  `fim()` always resolves and records the real choice, never the
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
  functions. `fim()` is the documented way to run generations; nothing
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
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-03
generator-responsibility: revision
```
