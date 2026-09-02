<!-- markdownlint-disable MD013 -->

# Finite island model simulator: detailed test plan

- [Finite island model simulator: detailed test plan](#finite-island-model-simulator-detailed-test-plan)
  - [Who this document is for](#who-this-document-is-for)
  - [1. The determinism contract for tests](#1-the-determinism-contract-for-tests)
  - [2. Test taxonomy](#2-test-taxonomy)
  - [3. Layout, tooling, and markers](#3-layout-tooling-and-markers)
  - [4. Unit tests, by module](#4-unit-tests-by-module)
    - [4.1 `model/allele.py`](#41-modelallelepy)
    - [4.2 `model/locus.py`](#42-modellocuspy)
    - [4.3 `model/topology.py`](#43-modeltopologypy)
    - [4.4 `model/state.py`](#44-modelstatepy)
    - [4.5 `model/params.py`](#45-modelparamspy)
    - [4.6 `model/initial.py`](#46-modelinitialpy)
    - [4.7 `model/operators.py`](#47-modeloperatorspy)
    - [4.8 `convergence/`](#48-convergence)
    - [4.9 `statistics/interval.py`](#49-statisticsintervalpy)
    - [4.10 `persistence/`](#410-persistence)
    - [4.11 `engine.py`](#411-enginepy)
    - [4.12 `cli.py`](#412-clipy)
  - [5. Property-based invariants for the statistics module](#5-property-based-invariants-for-the-statistics-module)
  - [6. Golden worked examples and focused statistics checks](#6-golden-worked-examples-and-focused-statistics-checks)
  - [7. Statistical and asymptotic tests](#7-statistical-and-asymptotic-tests)
    - [7.1 Deriving a tolerance band before choosing a seed](#71-deriving-a-tolerance-band-before-choosing-a-seed)
    - [7.2 Drift variance](#72-drift-variance)
    - [7.3 Equilibrium formulas](#73-equilibrium-formulas)
    - [7.4 Published-scenario fixtures](#74-published-scenario-fixtures)
    - [7.5 Literature-grounded scenarios beyond Dear-Nolan](#75-literature-grounded-scenarios-beyond-dear-nolan)
  - [8. Functional and end-to-end tests](#8-functional-and-end-to-end-tests)
  - [9. Visualization tests](#9-visualization-tests)
  - [10. Packaging smoke tests](#10-packaging-smoke-tests)
    - [10.1 Repository-tooling checks: git hooks, doc freshness, release notes](#101-repository-tooling-checks-git-hooks-doc-freshness-release-notes)
  - [11. Coverage targets and CI gating](#11-coverage-targets-and-ci-gating)
  - [12. Requirement traceability matrix](#12-requirement-traceability-matrix)
  - [Metadata](#metadata)
    - [Revisions](#revisions)

## Who this document is for

Written for whoever maintains or extends the simulator's tests — the
technical, secondary audience of this project's own two-document test
plan (see `doc/fim-simulator-test-plan.md`, the botanist-facing
companion this document assumes rather than repeats: what each test
means biologically, why it matters, and the citations behind it live
there, not here). This document organizes and tabulates; the full
detail behind any one test lives in that test's own docstring (pydoc),
not duplicated here.

Three companion references this document assumes:

- [The externally accessible engine API](fim-simulator-functional-api.md)
  — exactly what a **fim functional** test (§2) is allowed to import and
  call.
- [Desktop GUI test plan](fim-gui-test-plan.md) — the **fim-gui
  functional** category (§2) in full; not repeated here.
- The [detailed design](fim-simulator-detailed-design.md) (engineering,
  toolchain, release process) and the
  [design document](fim-simulator-design.md) (model, statistics,
  architecture), plus its own two companions, the
  [finite island model introduction](finite-island-model-introduction.md)
  and the
  [Jost differentiation-measures guide](jost-differentiation-measures.md)
  — every formula, golden value, and equilibrium relation cited here is
  sourced from these, not re-derived.

"Design §N" points into the design document; "detailed design §N" points
into the engineering reference; "Part N" points into the
differentiation-measures guide.

## 1. The determinism contract for tests

This is the governing rule for the whole suite, and it is not negotiable: a
test must be a pure function of the commit it runs against. Given the same
code, it produces the same result every time. A test whose outcome can
change while the code does not is broken — a defect of the same rank as a
test that asserts the wrong thing — regardless of how "unlucky" the failure
looks. This is doubly load-bearing here because the object under test is a
stochastic simulator, where the temptation to tolerate a random failure is
strongest and most wrong.

The contract, applied to every test in this plan:

1. **One seeded RNG, threaded explicitly.** Tests construct a
   `numpy.random.Generator(PCG64(seed))` with a fixed literal seed and pass
   it into the code under test. No test relies on the global NumPy RNG, the
   `random` module, `hash()` ordering, set iteration order, or wall-clock
   time.
2. **Bands derived before seeds are chosen.** For any statistical test
   (§7), the pass/fail tolerance is computed analytically from the sample
   size *first*; the seed is then fixed once and never reselected because
   it happens to pass. A test is never made green by seed-shopping.
3. **No network, ever.** The only network path in the product is
   `fim update --check` (detailed design §2.3); its test mocks the HTTP
   call entirely. Nothing else in the suite reaches out.
4. **No wall-clock in assertions.** Timestamps are asserted only for
   *presence and format* in the manifest, never for value.
5. **Order independence.** Tests pass in any order and under
   `pytest -p no:randomly` or with random ordering enabled; no test depends
   on state left by another. Shared fixtures are function-scoped unless
   provably immutable.
6. **Pinned toolchain.** pytest, Hypothesis, NumPy, and Matplotlib are
   version-pinned (detailed design §4) so the suite is a function of the
   commit, not of upstream's release schedule. Hypothesis runs with a fixed
   `derandomize=True` profile in CI so property tests are reproducible —
   [`test/test_hypothesis_profile.py`](../test/TESTS.md#test.test_hypothesis_profile) asserts `settings.default.derandomize`
   directly, so a future edit to [`test/conftest.py`](../test/TESTS.md#test.conftest)'s profile registration
   fails loudly instead of only showing up as an intermittently flipping
   property test months later.

A test that cannot be made deterministic is redesigned (stub the stochastic
dependency) rather than retried. Retry is reserved for cases where the
external interaction is itself the thing under test — of which this project
has none, because the simulator has no external interactions during a run.

## 2. Test taxonomy

Two taxonomies apply at once, answering two different questions. The
first (unchanged from earlier revisions of this document) is *what kind
of check is this* — unit, property, golden, statistical, and so on:

| Layer | Question it answers | Determinism source | Home |
|---|---|---|---|
| Unit | Does this function/value object behave exactly as specified? | Exact inputs, or seeded RNG | `test/<module>/` |
| Property-based | Do the algebraic identities hold for *all* valid inputs? | Hypothesis strategies, derandomized | `test/statistics/` |
| Golden-value | Do statistics match hand-checked reference values? | Fixed literal inputs | `test/statistics/`, `test/data/` |
| Statistical / asymptotic | Does the *model* match theory in the large-sample limit? | Fixed seeds + a-priori bands | `test/validation/` |
| Published-scenario | Does a run reproduce a literature-published worked example? | Fixed seeds + a-priori bands | `test/validation/` |
| Functional / end-to-end | Does the CLI/engine produce the documented artifacts? | Seeded run, fixed inputs | `test/cli/`, `test/engine/` |
| Packaging smoke | Does the shipped artifact start and run offline? | Fixed invocation | CI / `build` |

The first three layers are fully deterministic by construction. The middle
two are stochastic-but-reproducible under §1. The last two exercise
integration and shipping.

The second taxonomy is orthogonal to the first, and is the one that
matters most ahead of a future core-engine refactor: *what does this
test's own pass/fail criterion depend on*.

| Category | What it may call/assert against | Survives a core refactor? | Reference |
|---|---|---|---|
| **fim functional** | Only the public API surface: `fim.engine.fim`, `fim.model`'s re-exported names, `fim.statistics`, `fim.persistence`, `fim.reanalyze`, `fim.viz`, `fim.cli` — assertions compare that surface's output against a published value or a public closed-form formula | Yes, by construction | `fim-simulator-functional-api.md` |
| **fim-gui functional** | The above, plus `fim.gui`'s own bridge (`Api`) and the desktop page it drives | Yes (independent of core engine internals; still coupled to the GUI's own contract) | `fim-gui-test-plan.md` |
| **internal / deep** | Anything private (`_`-prefixed), any submodule never re-exported (`fim.model.operators`, most concretely), or a test-only helper built to mirror the engine's *current* internal mechanics precisely (the exact-recursion oracle in [`test/validation/test_simulator_equilibrium.py`](../test/TESTS.md#validation.test_simulator_equilibrium) is the standing example) | Not necessarily — expected to need re-deriving, not to signal a regression, once the mechanics it mirrors change | this document, §4 and §7 |

A single test file, or even a single test function, can straddle the
second taxonomy — several of §7.4/§7.5's own engine-level scenario tests
do exactly this, deliberately, each labeled `# FUNCTIONAL:`/`# INTERNAL:`
in place (see §7.5's own note on this). Every module-level unit test in
§4 is, by its nature, internal (it exercises one module's own concrete
implementation directly); §5-§9 are, with that one flagged exception,
fim functional; `test/gui/` is fim-gui functional in its entirety.

## 3. Layout, tooling, and markers

Tests live under `test/`, mirroring `src/fim/`, run by pytest with branch
coverage (detailed design §4). [`test/conftest.py`](../test/TESTS.md#test.conftest) provides the shared
scaffolding:

- `rng(seed)` — a factory returning `Generator(PCG64(seed))`; the only
  sanctioned RNG source in tests.
- tiny_params — a small, fast `SimulationParams` (`d=2`, `N=20`, one
  200bp locus) for end-to-end tests.

These two are the only fixtures `conftest.py` exports; they are the
sanctioned way to get a generator or a minimal parameter object, but they
are not the whole story for the property and golden layers below, which
each own their scaffolding locally rather than sharing it through
`conftest.py`:

- The property tests (§5) build their own bounded per-deme frequency-table
  Hypothesis strategy (frequency_tables() in
  [`test/statistics/test_properties.py`](../test/TESTS.md#statistics.test_properties)) rather than drawing on a shared
  fixture.
- The golden tests (§6) load the Part IV fixtures via a small local loader
  (`_fixture`/_frequency_table in [`test/statistics/test_differentiation.py`](../test/TESTS.md#statistics.test_differentiation))
  that reads the JSON files directly from `test/data/statistics/`.

Markers keep the fast/slow split explicit:

| Marker | Meaning | Runs in |
|---|---|---|
| (default) | Fast, fully deterministic | Every `pytest` |
| `@pytest.mark.statistical` | Many-replicate, seeded, banded (§7) | CI + `build --ci`; opt-in locally |
| `@pytest.mark.slow` | Long horizons | CI nightly / on demand |
| `@pytest.mark.packaging` | Requires a built wheel/exe | CI package job |
| `@pytest.mark.gui` | Opens a real pywebview window; needs a display | CI (under `xvfb-run`) + `build --ci`; opt-in locally |

The default `pytest` invocation (no markers — `pyproject.toml`'s own
`addopts` excludes `statistical`, `slow`, `packaging`, and `gui`) must
stay fast enough to run on every save. `build --ci` runs everything,
with no marker exclusion at all — see §10.1's own "Changelog-backed
release notes" item for the check enforcing this.

## 4. Unit tests, by module

### 4.1 `model/allele.py`

- AlleleRegistry.next_id() returns strictly increasing, never-repeating
  IDs across the whole run — the infinite-alleles guarantee reduces to this
  (design §3.2).
- Founding IDs (locus-relative `0..K-1`) and minted IDs occupy disjoint
  ranges and can never collide (design §3.3).
- `AlleleId` supports only equality — a test asserts that ordering/arithmetic
  is either absent or never used by the model code (enforced structurally by
  the `NewType` + `mypy --strict`, checked here at runtime for the equality
  contract).
- **`FiniteAlleleSpace`** (the opt-in finite-alleles mutation model, design
  §9): construction rejects a capacity too small for the initial IDs already
  in use; a mutation target is always different from its own source and
  never exceeds capacity; once every state is minted, every draw is a
  recurrence by construction; recurrence probability is checked at both
  extremes — a huge capacity underflows the recurrence chance to exactly
  `0.0` (infinite-alleles recovered exactly, not approximately), while a
  seeded many-draw sample at a known `(capacity, minted)` pair matches the
  closed-form recurrence rate `(minted - 1) / (capacity - 1)` within a
  pre-derived band (`@pytest.mark.statistical`, §7.1).
- **`FiniteAlleleRegistry`** dispatches each mutation event to the correct
  per-locus `FiniteAlleleSpace` by locus_id.

### 4.2 `model/locus.py`

- `LocusSpec` is immutable and hashable; `length` participates only as data
  by default (no statistic reads it directly — design §3.2), asserted by
  constructing two specs differing only in `length` and confirming
  identical statistical output downstream.
- finite_allele_capacity(length) returns `4 ** length` — the finite-alleles
  model's per-locus state-space size (design §9) — and both `LocusSpec` and
  finite_allele_capacity reject nonpositive values.

### 4.3 `model/topology.py`

Sparse/stepping-stone migration topology (design §9). Both functions are
tested directly, independent of `SimulationParams`'s config-sugar layer
(§4.5), against hand-derived matrices and structural invariants:

- stepping_stone_neighbors + dense_matrix_from_neighbors: a small ring's
  and a small bounded chain's dense matrices match independently
  hand-computed values exactly.
- Ring wraps its two end demes together; linear leaves them at `0.0` — the
  one property that actually distinguishes the two topologies.
- Both topologies are row-stochastic (`Σrow == 1`) for every `d` tested
  (`@pytest.mark.parametrize`, `d ∈ {2,3,4,10,25}`), including the linear
  chain's boundary case, where the two end demes send their whole `rate`
  to their single neighbor instead of splitting it.
- stepping_stone_neighbors rejects invalid `d`, an unrecognized
  `topology`, an out-of-range `rate`, and a ring smaller than 3 demes.
- dense_matrix_from_neighbors leaves a deme absent from the sparse map at
  the identity row, and rejects a deme/neighbor ID outside `1..d`, a deme
  listing itself as a neighbor, an out-of-range weight, and neighbor
  weights that sum to more than `1`.

### 4.4 `model/state.py`

- total_frequency() returns `1.0` within tolerance for every
  (deme, locus); a state that violates this is rejected/flagged.
- The support of any p<sub>k,t,l</sub> never exceeds `N` (design §3.1).
- Serialization round-trip: `state → rows → state` is exact for sparse
  states, including states with a single fixed allele and states at full
  `N`-allele support.
- Equality is value equality, independent of internal dict ordering.
- **Malformed-input validation** ([`test/model/test_state_validation.py`](../test/TESTS.md#model.test_state_validation),
  split out from the happy-path tests above): construction rejects empty
  `loci`, duplicate locus IDs, a negative `generation`, an empty deme list,
  and a `frequencies` shape that disagrees with `loci`'s length; a
  frequency map rejects an empty, negative, non-finite, or non-summing-to-1
  probability vector. ModelState.from_rows separately validates
  row-grouping invariants (no rows, more than one generation or run,
  non-1-based or non-contiguous demes, an unknown locus_id, a duplicate
  allele) and per-field types/presence (`generation` must be a real
  integer — not `bool` or a numeric string; run_id a nonempty string;
  `frequency` finite; every required field present, named individually
  when missing). to_rows("") and validate_support are checked directly
  for their own external-identifier and per-deme-support contracts.

### 4.5 `model/params.py`

- Valid scalar `(N, m, μ, d)` construct; `seed` is required (no default —
  design §4.3); invalid inputs raise with a clear message naming the
  offending field (`m ∉ [0,1]`, `μ ∉ [0,1]`, `d < 2`, `N < 1`, empty
  `loci`, unknown `P`-bag key, deme_weighting outside
  `{"equal","size"}`, convergence_window < 2, and the newer fields
  below), and an unknown top-level config key is rejected by name.
- Every `P`-bag default matches design §4.3's table exactly; a self-checking
  test parses the schema and asserts the documented defaults are the applied
  defaults (guards against silent drift between the two).
- Array-typed `N` and matrix-typed `m` are accepted and shape-validated
  (`N` length `d`; `m` `d×d`, each row summing to 1); initial_allele_count
  is bounded by the smallest deme `N`; the full mapping round-trips
  losslessly through to_dict()/from_mapping().
- **Multiple convergence statistics** (design §9): convergence_statistic
  accepts a string or a list of names, rejects an empty list, an unknown
  name, and a repeated name, and round-trips; a same-valued single-element
  list collapses to the scalar form (mirroring `N`/`m`/`mu`'s own
  collapse rule).
- **migrant_sampling** and **mutation_model**: both default to their
  documented value (`"continuous"`, "infinite_alleles") and round-trip;
  invalid values are rejected by name.
- **Finite-alleles capacity**: a configuration is rejected if
  initial_allele_count, or any allele ID an explicit p<sub>0</sub> uses, exceeds
  finite_allele_capacity(length) at *any* locus, not only the first one
  checked.
- **Per-locus mutation rate** (design §9): `mu` accepts an explicit list
  with one rate per locus; a list of equal values collapses to the scalar
  form. μ<sub>b</sub> (mutually exclusive with `mu`, and exactly one of the two is
  required) derives each locus's own `mu` from its `length` via the exact
  Eq. 5 relation and is itself validated as a probability.
- **Locus configuration shapes**: both the explicit `loci` list and the
  n<sub>loci</sub>/locus_lengths shorthand are accepted; malformed shapes are
  rejected; a scalar locus_lengths expands to every locus while explicit
  locus_ids round-trip, and a list of genuinely distinct per-locus
  lengths is preserved (not collapsed).
- **Migration config sugar** (design §9): the scalar and dense-matrix
  parsers reject wrong types/shapes as before; in addition, a
  `{topology, rate}` mapping expands through stepping_stone_neighbors +
  dense_matrix_from_neighbors (§4.3) into the equivalent dense matrix,
  and a hand-authored one-based sparse neighbor map (`{deme: {neighbor:
  weight}}`) is accepted directly; both malformed-shape variants are
  validated with field-naming errors.
- **Replicate-batch keys** (design §9): n<sub>replicates</sub> defaults to
  `200` and replicate_tolerance to `0.01` (matching convergence_tolerance's
  own default) — not `1`/unset — so an unconfigured run computes a real
  confidence interval by default rather than a single point estimate;
  replicate_minimum and replicate_confidence keep their own documented
  defaults (`10`, `0.95`). A zero replicate count, a negative or
  non-finite replicate_tolerance, a replicate_minimum below 2, and an
  unsupported confidence level are each rejected by name.
  replicate_tolerance always round-trips through to_dict() now
  (`null` for an explicit `None`, never omitted) — omitting it when
  `None` was safe only while `None` was also the field's own default;
  once it stopped being the default, an absent key and an explicit
  `null` stopped meaning the same thing, so `to_dict()` must say which
  one it is rather than collapsing them.
- **A stopping rule that can never fire**: convergence_window is
  rejected (_validate_stopping_rules) once it exceeds
  max_generations + 1 — the most generations a run can ever record
  (generation 0 plus max_generations steps) — since a trailing
  window that large could never fill before the generation cap ends
  the run. Covered by a parametrized case in this file's own
  scalar-contract table.
- **replicate_minimum above n<sub>replicates</sub> is clamped, not
  rejected**: previously a `_validate_stopping_rules` rejection
  (structurally the same shape as the convergence_window check above —
  the adaptive criterion could never be evaluated before the batch's
  own n<sub>replicates</sub> cap ends it) — changed once replicate_tolerance
  stopped defaulting to `None`: the identical combination now arises
  from nothing more deliberate than setting a small n<sub>replicates</sub>
  without separately thinking about replicate_minimum at all, found
  live when every GUI batch test that only ever sets n<sub>replicates</sub>
  failed exactly this way in CI (`jost-finite-island-model` run
  33656031751). `SimulationParams.__post_init__` now silently clamps
  replicate_minimum down to n<sub>replicates</sub> instead (a no-op at
  n<sub>replicates</sub> == 1, where the adaptive machinery is provably
  inert regardless). Covered by [`test_replicate_minimum_above_n_replicates_is_clamped_not_rejected`](../test/TESTS.md#model.test_params.test_replicate_minimum_above_n_replicates_is_clamped_not_rejected)
  and, end to end, by [`test/engine/test_engine.py::test_replicate_minimum_above_n_replicates_runs_to_completion`](../test/TESTS.md#engine.test_engine.test_replicate_minimum_above_n_replicates_runs_to_completion),
  which also documents the *legal* neighbor this behavior is
  distinguished from — [`test_replicate_tolerance_never_stops_on_a_permanently_undefined_statistic`](../test/TESTS.md#engine.test_engine.test_replicate_tolerance_never_stops_on_a_permanently_undefined_statistic),
  where the criterion is evaluable but never satisfied rather than
  never evaluable at all.
- **Explicit initial frequencies (p<sub>0</sub>)**: normalized and losslessly
  serialized; the parser names malformed inputs precisely; shape and
  probability-vector validation is exercised per deme/locus; support
  exceeding the deme's `N` is rejected.

### 4.6 `model/initial.py`

- Default Dirichlet generator: same seed ⇒ identical initial state; each
  (deme, locus) sums to 1; initial_concentration visibly changes evenness
  (a low-`α` draw is more skewed than a high-`α` draw, asserted via a
  Gini/entropy comparison at fixed seed).
- Explicit-p<sub>0</sub> override: the supplied distribution is used verbatim and
  validated (`Σp == 1`).
- Founding alleles use locus-relative IDs `0..K-1` (design §3.3).

### 4.7 `model/operators.py`

Each operator is `ModelState → ModelState` and tested against its closed
form.

- **migrate**: expectation-preserving — the migrant-pool blend conserves the
  total allele frequency across demes; `m=0` is identity; `m=1` fully
  replaces each deme with the pool (design §3.4). Matrix migration is
  checked against a naive reference implementation, applies each row's
  source weights (including a genuinely **asymmetric** matrix's
  per-row-directional weights), ignores population size (a matrix's rows
  are authoritative and never rescaled by `N`), and reduces to the scalar
  symmetric case exactly when the matrix is that case's dense form.
  Stepping-stone migration (ring and linear, §4.3) reaches only direct
  neighbors and a linear chain never wraps.
- **migrate, stochastic sampling** (migrant_sampling="stochastic", design
  §9): requires an explicit population size; a scalar-rate and a
  matrix-rate migrant count each match `Binomial(N, rate)` theory within a
  pre-derived band (`@pytest.mark.statistical`, §7.1); migrant pool
  *composition* is unaffected by the sampling mode (only how many copies
  move is stochastic, not what they carry); a matrix with self-weight `1`
  (no migration) matches the continuous path exactly.
- **mutate**: at rate `μ`, the expected count of mutated copies is `Nμ`;
  every mutated copy gets a fresh registry ID; `μ=0` is identity; a
  per-locus rate only mutates its own configured locus, and a uniform
  per-locus tuple matches the scalar-broadcast case. A seeded test checks
  the mutated-count mean over many draws against `Nμ` within a binomial
  band (§7.1).
- **mutate, finite alleles** (mutation_model="finite_alleles", design §9):
  respects each locus's own capacity independently; recurrent targets
  accumulate onto an existing allele's mass rather than always minting
  fresh; the default (mutation_model unset / "infinite_alleles")
  behavior is unchanged; the seeded recurrence rate matches
  `FiniteAlleleSpace`'s own theoretical rate (§4.1) end-to-end through the
  operator.
- **drift**: `Σp == 1` post-resample; support ≤ `N`; `μ=0` runs drive toward
  fixation (trailing-window variance → 0); the per-generation variance
  matches `p(1-p)/N` (§7.2), checked both with a shared `N` and per-deme
  when `N` is unequal across demes. The dense fast path and the sparse
  path produce identical results for a fixed-`K` no-mutation state at the
  same seed.
- **pipeline**: `step` composes `drift ∘ mutate ∘ migrate` in that order
  (design §3.4); same seed ⇒ identical next state; the invariant holds
  after every stage; population-size shape is validated once, at the
  operator boundary shared by all three stages.

### 4.8 `convergence/`

- Trailing-window criterion: a constant sequence is stable immediately once
  the window fills; a linearly drifting sequence is *not* stable; an
  oscillating sequence within tolerance *is* stable (checked via
  half-window means), one exceeding it is not.
- max_generations criterion always eventually fires regardless of the
  statistic's behavior (the safety valve — design §3.5).
- `ConvergenceMonitor.reason()` distinguishes "statistic converged" from
  "hit the cap"; a capped-unconverged run is a valid result, not an
  exception (design §5).
- **Several watched statistics with a combinator** (design §9): the
  monitor requires a mapping that covers every watched statistic; the
  `"all"` combinator requires every one stable before stopping, `"any"`
  stops as soon as one is; construction validates the statistic list and
  the combinator value together.
- **The confidence-interval criterion** (`ConfidenceIntervalCriterion`,
  the replicate-layer stopping rule — design §9): invalid configuration
  (tolerance, minimum count, confidence level) is rejected by name; a
  sample below the minimum count is never called stable regardless of its
  spread; a tight sample is stable and a loose one is not, at the same
  count; and the criterion composes with an unmodified
  `ConvergenceMonitor`, which is what makes the replicate layer reuse the
  within-run mechanism rather than duplicate it.
- Constructor and criteria validation: invalid trailing-window
  configuration, an incomplete window, invalid combinator children, and
  `any`/`all` short-circuiting on child results are all covered directly,
  as are a monitor rejecting invalid records and rejecting further records
  once already converged.

### 4.9 `statistics/interval.py`

Home: [`test/statistics/test_interval.py`](../test/TESTS.md#statistics.test_interval). The across-replicate Student's-t
interval (design §5, §9) is a self-contained numeric module, tested
without the engine:

- Every tabled degrees-of-freedom row matches published t-table values
  exactly, at each supported confidence level.
- An untabled degrees of freedom interpolates between its listed
  neighbors, and the interpolation matches the documented `1/df` formula
  rather than merely landing between them.
- **Interpolation error is bounded against an independent oracle**: for
  ten untabled degrees-of-freedom values spanning the
  whole table, the interpolated critical value is fed to a numerical
  quadrature of the closed-form Student's-t density — sharing no table,
  formula, or code path with `interval._interpolate` — and the tail
  probability that quadrature reports must land within `1e-4` of the
  target the critical value is supposed to produce. Verified sensitive
  by deliberately breaking the interpolation weight and confirming this
  test fails from its own, independently computed reference value —
  not merely because the formula-echo test above happened to catch the
  same break.
- Beyond the table's tail, the critical value is the exact standard-normal
  quantile, which is separately checked against well-known `z` values.
- An interval on a fixed sample matches a hand-computed reference; an
  all-identical sample gives a zero-width interval; and more replicates at
  the same spread give a strictly tighter one.
- The default confidence level is 0.95, and a sample of fewer than two
  values is rejected rather than reported with an undefined spread.

### 4.10 `persistence/`

- `TrajectoryStore` round-trip: write_generation then `read` returns rows
  byte-faithful to the schema, for a multi-generation, multi-deme,
  multi-locus run, including the in-memory store used by fast tests.
- `JSONLTrajectoryStore` appends incrementally (each generation is a
  flushed set of lines; a truncated file still parses every complete line
  and reports a missing or corrupt complete line precisely).
- Rows carry only nonzero frequencies (sparse — design §6).
- Manifest captures the full `SimulationParams` incl. seed, convergence
  outcome, and version; a test reconstructs `SimulationParams` from the
  manifest and confirms it re-runs to an identical trajectory (the replay
  contract — design §6), including a manifest that names **several**
  convergence statistics (design §9), not only the single-statistic case.
- **Artifact integrity** (design §6): schema_version and
  generation_count are required, positive-integer manifest fields;
  `artifacts` — a per-file `{sha256, bytes}` digest, keyed by artifact
  name — is `None` on a manifest as the engine constructs it (before any
  file is written to disk) and round-trips losslessly once populated;
  hash_file produces a digest matching an independent hash of the same
  bytes; every malformed digest shape (missing/empty `sha256`, non-integer
  or negative `bytes`) is rejected by name.
- **Validation** ([`test/persistence/test_validation.py`](../test/TESTS.md#persistence.test_validation)): row normalization
  rejects invalid values and reports missing/extra fields and context
  mismatches by name; stores reject empty generations and filter by
  run_id; the manifest constructor and its mapping-based reconstruction
  both validate identity fields and nested-field types strictly.

### 4.11 `engine.py`

- A tiny seeded `fim(...)` run is bit-reproducible across two invocations.
- Converged and capped runs both return a valid `RunResult` with the correct
  `reason`.
- The report computed live at `t=T` equals the report re-computed from the
  persisted trajectory (design §4.1 — statistics never depend on the
  engine).
- Replicate batching: n<sub>replicates</sub> runs are independent and each
  individually reproducible from the run seed; the scalar (`n=1`) case
  returns a single `RunResult`, not a one-element batch; an explicit
  caller-supplied run_id receives deterministic one-based suffixes
  (`"batch-r001"`, `"batch-r002"`, …).
- **Adaptive replicate batching** (replicate_tolerance, design §9): with
  the key unset, exactly n<sub>replicates</sub> replicates run; with a tolerance
  no bounded statistic can miss, the batch stops at replicate_minimum
  exactly — a deterministic stop, not a lucky one, since every reported
  statistic lies in `[0, 1]`; with an unreachable replicate_minimum, the
  n<sub>replicates</sub> cap still ends the batch. A replicate whose G<sub>ST</sub> is
  undefined (every tracked locus monomorphic) is dropped from G<sub>ST</sub>'s
  own stopping-criterion window for that round — never raised, never
  substituted — exactly the sample replicate_summary reports from; a
  batch watching only G<sub>ST</sub> where it stays undefined for every replicate
  runs to the full n<sub>replicates</sub> cap rather than reporting a fabricated
  early stop.
- **replicate_summary**: reports an interval for every statistic with at
  least two defined samples, each `low <= mean <= high` at the configured
  confidence, and rejects a batch of fewer than two results outright.
- **Parallel replicate execution** (max_workers, design §9): a batch run
  across workers produces replicate-for-replicate identical run_ids,
  final states, and reports to the same batch run sequentially — the
  property that makes the worker count a performance knob only. Adaptive
  stopping still applies under max_workers, overshooting the minimal
  count by at most max_workers - 1 because the decision is made once a
  whole concurrent batch completes; the test asserts that bound rather
  than an exact count. max_workers rejects a non-positive count and a
  shared `store`, and `store`/store_factory are mutually exclusive.
  store_factory gives every replicate its own store in either execution
  mode, and the fixture factory is a module-level function precisely
  because a worker process must be able to pickle it.
- The legacy positional `(N, m, mu, d)` arguments are validated against the
  parameter bag they must agree with, each mismatch reported by name.
- A manifest `clock` without an explicit timezone is rejected.
- A run watching only G<sub>ST</sub> at total shared fixation (every locus
  permanently monomorphic) never fabricates stability from a value that
  does not exist; its trailing window never fills, so the run falls back
  to max_generations rather than reporting a spurious immediate
  "converged". A locus that is monomorphic alongside a polymorphic one
  drops out of that generation's G<sub>ST</sub> average instead — see the
  differentiation-statistics golden-value tests (§6) for the same rule
  applied to the final report.
- **Several watched statistics** (design §9): the single-statistic report
  shape is the several-statistic combinator's one-element special case
  (converged_on is a bare string, not a one-item list); watching several
  statistics is itself reproducible and reports every statistic's
  convergence history; the `"any"` combinator is shown, on an identical
  seed and parameters differing only in convergence_combinator, to stop
  strictly earlier than `"all"`.
- Mutation IDs never collide with allele labels already supplied through
  an explicit p<sub>0</sub>, even when that label sits above the registry's own
  starting point.
- **Per-deme population sizes, asymmetric migration, and multi-locus runs**
  (design §9): an unequal-per-deme `N` run is reproducible and keeps every
  deme's support within its own `N`; deme_weighting="size" vs.
  `"equal"` on the same unequal-`N` run changes the reported E<sub>ST</sub> while
  leaving `D` unaffected (the config-level counterpart to §5/§6's
  statistics-primitive-level weighting checks); an asymmetric migration
  matrix run is
  reproducible; a report over several loci with equal deme weighting is
  shaped correctly; locus `length` does not affect the report (data only,
  per §4.2); a multi-locus run with genuinely unequal lengths is
  reproducible.
- **Stepping-stone topology, stochastic migrant sampling, and the
  finite-alleles model** (design §9): a run using topology sugar for `m`
  is reproducible; a stochastic-migrant-sampling run is reproducible, and
  the default (continuous) sampling is unaffected by the option's mere
  existence; a finite-alleles run is reproducible and keeps every locus
  within its own capacity, and the default (infinite-alleles) model is
  unaffected; a μ<sub>b</sub>-configured run matches the equivalent explicit
  per-locus `mu` run bit-for-bit, and μ<sub>b</sub> combines correctly with the
  finite-alleles model in the same run.

### 4.12 `cli.py`

Functional detail in §8.

- Config parsing maps every YAML key to the right `SimulationParams` field
  and rejects unknown keys with a message naming the key; loading a config
  whose YAML root is not a mapping is rejected.
- `fim --version` prints `version.txt`'s value, read from the single
  source of truth shared with packaging (detailed design §6.4).
- `fim update --check` is tested against a mocked Releases response only —
  newer, equal, and older tags each produce the right message, network
  failures (`HTTPError`, `URLError`) and a non-object payload are wrapped
  into the documented runtime-error contract, version comparison/format
  helpers are stable, and a non-semantic version string is rejected;
  **no test performs a live request** (§1). `fim update` without
  `--check` is rejected as an explicit-opt-in requirement.
- `fim init` refuses to overwrite an existing starter config unless
  forced.
- Batch invocation: `--sequential`, `--workers N`, and the default
  worker pool each drive the same documented artifact set (§8), and a
  batch refuses a non-empty output directory.
- Default output paths use the project's `results` directory, falling
  back to the working directory when no project root is found.
- **Atomic output publishing**: a scalar or batch run's
  entire output directory is built in a hidden temporary sibling and
  published with one atomic rename (_atomic_directory), only once every
  artifact is flushed and `manifest.json` — written last — records each
  sibling artifact's SHA-256 digest (_write_run_artifacts). `-o` now
  refuses *any* pre-existing target directory, empty or not (stricter than
  the prior any-artifact-file check). **Failure injection** at three
  boundaries — mid-trajectory (the write itself), the report write, and
  the plot render — each confirms the target directory does not exist
  afterward, at both the scalar and (a replicate write, sequential batch)
  batch layers.
- **Trajectory integrity verification**: `fim stats`
  recomputes the trajectory's SHA-256 digest and distinct generation count
  against the manifest's recorded values before reading a single row,
  refusing an edited, truncated, or replaced trajectory with a named error
  instead of silently re-analyzing it; a manifest predating this check
  (no `artifacts` recorded) is refused with its own distinct message.

## 5. Property-based invariants for the statistics module

Home: [`test/statistics/test_properties.py`](../test/TESTS.md#statistics.test_properties). Checked with Hypothesis over a
locally defined frequency_tables() strategy (derandomized in CI — §3).
These are the differentiation-measures guide's Part V identities, asserted
as properties rather than point cases:

- H<sub>T</sub> ≥ H<sub>S</sub> for every table.
- G<sub>ST</sub> ≤ 1 − H<sub>S</sub> — the ceiling identity (Part V).
- H<sub>T</sub> = H<sub>S</sub> + H<sub>ST</sub> − H<sub>S</sub> · H<sub>ST</sub> — the correct subadditive partition, with
  H<sub>ST</sub> equal to `D`'s own first bracket (Part V).
- `D ∈ [0, 1]`; E<sub>ST</sub>, K<sub>ST</sub>, and (when defined) G<sub>ST</sub> are also in
  `[0, 1]` for valid tables; `D = 1` for two fully disjoint demes and
  `D = 0` for two identical demes (the endpoints, asserted exactly).
- **Replication principle**: pooling two equally sized, equally diverse,
  completely disjoint groups exactly doubles ^HD<sub>T</sub> / ^HD<sub>S</sub> (Part V).

Deme-weighting is exercised at two levels, neither of them property-based:
the statistics module's own optional per-deme `weights` argument (E<sub>ST</sub>
accepts it, `D` never does — a focused check, §6), and the
SimulationParams.deme_weighting config setting's effect on an actual
engine report, where `"size"` vs. `"equal"` visibly changes E<sub>ST</sub> for the
same run while leaving `D` unaffected (§4.11).

## 6. Golden worked examples and focused statistics checks

Home: [`test/statistics/test_differentiation.py`](../test/TESTS.md#statistics.test_differentiation), fixtures in
`test/data/statistics/`. The golden values were independently recomputed
from first principles in the differentiation guide's Part IV, not copied
from the paper, and are asserted **exact** (`assertAlmostEqual` to 12
decimal places):

| Fixture file | Scenario | Selected expected values | Note |
|---|---|---|---|
| fixed_nine_one.json | Nine demes fixed for allele A, one for B | `D = 0.20`, G<sub>ST</sub> = 1.0 | |
| fixed_five_five.json | Five demes fixed for A, five for B | `D = 0.5556`, E<sub>ST</sub> = 0.3010, K<sub>ST</sub> = 0.1111 | The erratum's canonical case: documents `D = 0.5556` against the paper's printed `0.5`. |
| fixed_all_different.json | Ten demes, each fixed for its own private allele | `D = 1.0`, H<sub>T</sub> = 0.9 | |
| reversed_frequencies.json | Two demes, each 95% one allele and 5% spread over ten shared rare ones | `D = 0.9892`, K<sub>ST</sub> = 0.0 | `D` and K<sub>ST</sub> diverge sharply on the same table. |
| shared_all.json | Two demes with an identical 20-allele distribution | `D = 0.0`, G<sub>ST</sub> = 0.0 | The "demes are identical" endpoint, exercised as a golden value in addition to the property-test endpoint (§5). |
| shared_and_private.json | Two demes sharing half their alleles, each private in the rest | `D = 0.5`, G<sub>ST</sub> = 0.0476 | The "G<sub>ST</sub> hides differentiation" case: a near-zero G<sub>ST</sub> alongside a mid-range `D`. |
| shared_none.json | Two demes with disjoint 20-allele supports | `D = 1.0`, G<sub>ST</sub> = 0.0256 | G<sub>ST</sub> stays small even though the demes share nothing — the "98% within demes" trap in miniature. |

Each fixture's `expected` object lists every statistic the test checks for
that table, not just `D`; the loader (`_fixture`/_frequency_table, §3)
reads the JSON directly rather than through a shared conftest fixture.
The same file also carries focused (non-property, non-golden-fixture)
checks: `q = 0, 1, 2` Hill-number endpoints against `D`/E<sub>ST</sub>/K<sub>ST</sub> and
against `H`'s own partition; G<sub>ST</sub> reporting `None` (not `NaN`) at total
shared fixation; E<sub>ST</sub> accepting optional deme-size weights while `D`
stays equal-weighted regardless (the deme-weighting separation named in
§5); and malformed-input validation for frequency tables, weights, and
Hill-number orders.

## 7. Statistical and asymptotic tests

Marked `@pytest.mark.statistical`. These exercise the *model*, not just the
statistics module, and are the tests the determinism contract (§1) most
directly governs.

### 7.1 Deriving a tolerance band before choosing a seed

The procedure every test in this section follows, in order:

1. Fix the parameters `(N, m, μ, d)` and the replicate/sample count `R`.
2. Derive the expected value from the closed form (drift variance, or the
   Part VI equilibrium formulas) and the standard error of the sample
   estimate analytically from `R` (e.g. a binomial/normal band, or a
   `k·SE` band for a chosen `k` such as 4–5 σ).
3. *Then* fix one literal seed. Run once. The test asserts the sample
   estimate falls inside the pre-derived band.

The seed is never chosen to make the test pass; the band is wide enough,
by construction from `R`, that a correct implementation passes at
essentially any seed, and the fixed seed only makes the single realization
reproducible. This is the operational meaning of "deterministic given the
commit" for a stochastic check.

**Where step 2's standard error is empirical, not closed-form.** The
three [`test/validation/test_simulator_equilibrium.py`](../test/TESTS.md#validation.test_simulator_equilibrium)
equilibrium tests (§7.3, §7.4) use a `k·sigma/sqrt(R)` band whose `sigma`
has no known closed form (see that file's module docstring): the
per-replicate G<sub>ST</sub>/`D` estimate is a ratio-of-means statistic sampled
from a multi-generation stochastic recursion, and a closed-form variance
would need delta-method propagation through that recursion's higher
moments, not currently derived. `sigma` is instead set from a **versioned
empirical characterization pass** — `dev/bin/calibrate-statistical-bands`,
a maintainer tool deliberately not wired into `build` or `ci.yml` (a
characterization pass is itself stochastic by design, so it stays out of
the deterministic PR gate) — whose raw per-replicate output, seeds, and
environment fingerprint are retained in the
[calibration evidence document](statistical-calibration-evidence.md), not
only summarized in a code comment. This replaces an earlier, unretained
characterization pass whose
program, seeds, and environment were never recorded; re-running the new
script against the current deployed constants found two (_SIGMA<sub>PART</sub>_VI<sub>D</sub>,
_SIGMA<sub>DEAR</sub>_NOLAN<sub>LOW</sub>_G) narrower than a larger, retained sample supports,
and they were widened accordingly — the assertion tests continue to pass
at their existing fixed seeds either way, since a wider band only relaxes
the check.

### 7.2 Drift variance

Home: [`test/model/test_operators.py`](../test/TESTS.md#model.test_operators). A single-locus, single-deme drift
step starting from a known `p` has per-generation sampling variance
`p(1−p)/N` (design §3.1's gene-copy-count `N`). Over `R` seeded replicates
of one step, the sample variance of the resulting frequency is asserted
within its analytically derived band, checked both with a single shared
`N` and per-deme when `N` differs across demes (design §9's per-deme
population sizes). Drift is the pipeline's only unconditional source of
randomness, so this is the check everything statistical downstream rests
on (design §10).

### 7.3 Equilibrium formulas

Home: [`test/validation/test_equilibrium.py`](../test/TESTS.md#validation.test_equilibrium) (the closed-form formulas
directly) and [`test/validation/test_simulator_equilibrium.py`](../test/TESTS.md#validation.test_simulator_equilibrium) (the real
engine against the same oracle). Two independent oracles are used, per the
latter file's own module docstring, and are cross-checked against each
other before either is trusted against the engine:

1. The closed-form diffusion equilibria equilibrium_g_st/equilibrium_d
   (differentiation guide Part VI Eq. 2/Eq. 4) — `O(1/N)` approximations.
2. An exact per-generation identity recursion for the engine's own
   Migrate → Mutate → Drift pipeline, built from first principles rather
   than fitted to the simulator. This is the finite-`N` expectation of the
   very quantities the engine samples, so it is the correct center for a
   seeded many-replicate band, and it also supplies the fixed point used to
   construct the near-equilibrium starting states in §7.4.

[`test_identity_recursion_oracle_matches_formula_and_published`](../test/TESTS.md#validation.test_simulator_equilibrium.test_identity_recursion_oracle_matches_formula_and_published) confirms
the two oracles and the published Dear-Nolan values agree with each other
before either is used as a ruler. [`test_engine_reproduces_part_vi_equilibrium`](../test/TESTS.md#validation.test_simulator_equilibrium.test_engine_reproduces_part_vi_equilibrium)
then runs the real engine to stochastic equilibrium and checks its
sample-mean G<sub>ST</sub> and `D` land in a `k = 5`-standard-error band (derived
analytically from an independent replicate-spread characterization pass,
never tuned to a realized draw) around the identity-recursion oracle.

### 7.4 Published-scenario fixtures

The two haploid Dear-Nolan scenarios (design §4.3), asserted as
**tolerance-banded statistical oracles**, never exact equalities — they are
themselves simulation output from a colleague's independent tool, not
analytic values (design §10):

| Scenario | `N` | `d` | `m` | `μ` | Expected G<sub>ST</sub> | Expected `D` (observed) |
|---|---|---|---|---|---|---|
| Low migration, low mutation | 100 | 5 | 0.0001 | 0.000001 | 0.97 | 0.04 (0.04) |
| Higher migration, higher mutation | 2000 | 100 | 0.01 | 0.001 | 0.02 | 0.91 (0.90) |

Both plug in directly under the ploidy-neutral `N` convention (no
conversion — design §4.3). Each is checked at two levels:

- [`test_equilibrium.py`](../test/TESTS.md#validation.test_equilibrium) plugs the scenario's `(N, m, μ, d)` directly into
  the closed-form formulas and checks them against the published values.
- [`test_simulator_equilibrium.py`](../test/TESTS.md#validation.test_simulator_equilibrium) goes further: it runs the *real* engine,
  started from a derived near-equilibrium state built from the identity
  recursion (§7.3) — a 26-locus ensemble for the low-migration scenario,
  a direct derived start for the high-migration one — and shows the
  engine's own pooled G<sub>ST</sub> and `D` directly reproduce both published
  values, a stationarity property a biased operator would fail. The
  letter's own closed-form H<sub>S</sub>/H<sub>T</sub> approximations provide an
  additional independent analytic cross-check, used the same way.

### 7.5 Literature-grounded scenarios beyond Dear-Nolan

Five further sources, each closing a specific, identified gap rather
than adding coverage for its own sake — citations for all five are in
`doc/fim-simulator-test-plan.md`'s own references appendix; each
scenario test's own docstring carries the full worked derivation. This
section states only what each contributes and how it is tested.

- **Crow & Aoki (1984)**, Table 1's toroidal stepping-stone scenario
  ([`test_crow_aoki_torus_scenario_via_engine`](../test/TESTS.md#validation.test_simulator_equilibrium.test_crow_aoki_torus_scenario_via_engine), home file [`test/validation/test_simulator_equilibrium.py`](../test/TESTS.md#validation.test_simulator_equilibrium)) — a genuinely different
  migration topology (a 3-by-3 torus, four-nearest-neighbor migration)
  from every other scenario in this section (the symmetric island
  model), and from an author with no connection to the Jost/Chao
  citation cluster the rest of this section traces back to. **Fully
  fim functional**: the sole assertion compares the engine directly
  against the literal published `G_ST = 0.172`, with no internal-
  recursion oracle involved (this project's own exact-recursion
  oracle, extended to a general migration matrix
  (`_pairwise_identity_fixed_point`), was independently found not to
  match Crow & Aoki's own published number for this specific migration
  topology at this project's own established migration-rate
  convention — see the Open Issues appendix of `doc/
  fim-simulator-test-plan.md` for the full, still-unresolved finding,
  and this file's own [`test_pairwise_identity_recursion_applied_to_the_crow_aoki_torus`](../test/TESTS.md#validation.test_simulator_equilibrium.test_pairwise_identity_recursion_applied_to_the_crow_aoki_torus) for the internal side of that discrepancy).
- **Chao, Chiu, Jost, Sherwin & Rollins (2015)**, the Shannon-entropy
  equilibrium formulas (Eqs. 2A/5A/6/7D/10) — a genuinely independent
  *statistic family* (mutual-information-based Shannon differentiation,
  not heterozygosity-based `G_ST`/`D`) validated against the real engine
  for the first time in this project
  ([`test_chao_shannon_equilibrium_scenario_via_engine`](../test/TESTS.md#validation.test_simulator_equilibrium.test_chao_shannon_equilibrium_scenario_via_engine), same home
  file). **Fully fim functional**: compares the engine directly against
  the public closed-form `equilibrium_shannon_entropy_total`/
  `_subpopulation`/`equilibrium_shannon_differentiation`
  (`fim.statistics`), not any internal oracle. The closed forms
  themselves (`equilibrium_shannon_entropy_isolated`/`_isolated_smm`,
  a dependency-free digamma implementation) are unit-tested exactly
  against textbook closed forms in [`test/validation/test_equilibrium.py`](../test/TESTS.md#validation.test_equilibrium)
  — those unit tests are internal in the taxonomy sense (they exercise
  `_digamma` directly) but the engine-level scenario test above is not.
- **Nei (1973)** — `d_m`, `r_st` (Eqs. 10-11, an absolute, deliberately
  not-`[0, 1]`-bounded pairwise gene-diversity measure and its ratio to
  within-deme diversity) and `g_st_log` (the paper's own unnumbered
  "better estimate" of `G_ST` for strong differentiation). Pure closed-
  form additions to `fim.statistics`, unit-tested in [`test/statistics/test_differentiation.py`](../test/TESTS.md#statistics.test_differentiation) against the paper's own algebra — `g_st_log`'s
  own `[0, 1]` boundedness is proven, not just tested, from the same two
  facts `g_st` already relies on. No engine-level scenario test: the
  paper supplies no numeric worked example to reproduce, only formulas.
- **Whitlock (1992)** — `identity_recovery_rate`/`_equilibrium`/
  `_trajectory`/`_half_life`, a genuinely different *kind* of prediction
  from every other formula in `fim.statistics`: how fast a disturbed
  population's identity by descent returns toward equilibrium, not what
  value it settles to. Shown to be an exact algebraic reduction of this
  project's own `_iterate_identities` recursion in the `d -> infinity`,
  `mu = 0` limit — confirmed by a deterministic test at `d = 100,000`
  ([`test_identity_recursion_reduces_to_whitlock_infinite_island_trajectory`](../test/TESTS.md#validation.test_simulator_equilibrium.test_identity_recursion_reduces_to_whitlock_infinite_island_trajectory)), an internal-taxonomy test by construction (it is
  specifically about validating the internal recursion's own large-`d`
  limit, not the engine). No engine-level empirical convergence-speed
  test exists yet — see the Open Issues appendix of `doc/
  fim-simulator-test-plan.md`.
- **Kimura & Weiss (1964)**, cited via Whitlock & McCauley (1999) —
  stepping-stone differentiation is at least as large as the island
  model's, for the same per-deme migration rate.
  [`test_stepping_stone_differentiation_is_at_least_the_island_models`](../test/TESTS.md#validation.test_simulator_equilibrium.test_stepping_stone_differentiation_is_at_least_the_island_models)
  is a directional (metamorphic), fully deterministic check, reusing
  the already-validated exact-recursion oracles for both topologies —
  no calibration band needed, since it asserts an inequality, not a
  point value. Internal in the taxonomy sense (both sides of the
  comparison are this file's own oracle functions, not the real
  engine) — the same category as the migration/mutation monotonicity
  checks immediately below it in the same file.

Migration/mutation-rate monotonicity (`D`/`G_ST` non-increasing in `m`,
moving opposite ways in `mu`) is checked at two of §2's second-taxonomy
levels for the same underlying claim: a closed-form, Hypothesis-driven
property test directly on `equilibrium_d`/`equilibrium_g_st`
([`test/statistics/test_properties.py`](../test/TESTS.md#statistics.test_properties), fim functional — no engine or
oracle involved at all) and an exact-recursion version reusing the same
internal oracle as the scenarios above
([`test_identity_recursion_d_and_g_st_are_non_increasing_in_migration`](../test/TESTS.md#validation.test_simulator_equilibrium.test_identity_recursion_d_and_g_st_are_non_increasing_in_migration)/
`_move_opposite_ways_in_mutation`, internal). A third, real-engine-level
version of the same claim (replicated stochastic runs, properly
calibrated) remains a deferred opportunity — see `doc/
fim-simulator-test-plan.md`'s own deferred-opportunities appendix.

## 8. Functional and end-to-end tests

Home: `test/cli/`, `test/engine/`. These exercise the product as a
researcher uses it (design §12), on tiny_params-scale scenarios small
enough to finish in well under a second, driven through the CLI's own
config parsing (§4.12) rather than by constructing `SimulationParams`
directly. The desktop GUI's own end-to-end coverage (`test/gui/`) is
documented separately in `doc/fim-gui-test-plan.md`, not here.

- **`fim run` produces exactly the documented artifacts**: given a small
  YAML config and a fixed seed, the output directory contains
  `trajectory.jsonl`, `manifest.json`, `report.json`, and `scatter.png`,
  and `report.json` carries every scalar in design §12's example shape
  (requirement 6a). Two runs at the same seed produce identical
  `trajectory.jsonl` and `report.json`; a run that would collide with an
  existing output directory is rejected. Progress output describes the
  run's outcome.
- **A batch (n<sub>replicates</sub> greater than one) produces exactly the
  documented batch artifacts** (`doc/usage.md`): one `replicate-NNN/`
  subdirectory per replicate, each holding that same four-file scalar
  contract and nothing else, plus a batch `summary.json` whose
  sample_count matches the replicate count and a batch `manifest.json`
  carrying replicate_count and one replicate_run_ids entry per
  replicate. The set of directory entries is asserted exactly, so a
  stray or missing artifact fails rather than passing unnoticed. The
  default worker pool, an explicit `--workers`, and `--sequential` each
  produce that artifact set; a non-empty output directory is rejected
  before anything is written; and a replicate_tolerance no bounded
  statistic can miss writes exactly replicate_minimum replicates,
  proving the adaptive stop reaches the artifacts a user sees rather than
  only the library layer (§4.11).
- **Every design-§9 configuration surface is reachable end-to-end through
  a YAML config**, not just through `SimulationParams` directly (§4.5,
  §4.11): per-deme population sizes; an asymmetric migration matrix;
  loci with unequal lengths; several convergence statistics; stepping-stone
  topology sugar for `m`; stochastic migrant sampling; the finite-alleles
  mutation model; and a per-base mutation rate (μ<sub>b</sub>).
- **Config validation**: malformed configs fail with a message naming the
  offending key/value; a config whose YAML root is not a mapping is
  rejected; a valid config round-trips into `SimulationParams`.
- **`fim stats` re-analysis**: re-computing a statistic (including a swept
  `q`) from a persisted `trajectory.jsonl` reproduces the live report,
  without re-running the simulation (design §4.1); explicit-generation and
  JSON-output modes are covered, as are an empty trajectory and a request
  for an unknown generation.
- **Manifest replay**: reconstructing `SimulationParams` from a run's
  `manifest.json` and re-running yields an identical trajectory (design §6).
- **Cap vs. converge**: a config with a low max_generations and a tight
  tolerance returns a capped-but-valid result whose `report.json` says so
  plainly (design §5), not an error.
- **`fim init`**: first run drops the starter config into the run folder
  (design §12); refuses to overwrite an existing one unless forced.
- **Default paths**: output defaults to the project's `results` directory,
  falling back to the working directory when no project root is found.
- **`fim update --check`**: fully mocked HTTP; asserts the newer/equal/older
  messaging, error wrapping, and version-comparison/format helpers;
  `fim update` without `--check` is rejected; never performs a live
  request.

## 9. Visualization tests

Home: `test/viz/`. Rendered with the non-interactive `Agg` backend so they
are headless and reproducible.

- Plots are asserted by **structure, not pixels**: the figure exists, has
  the expected number of axes/panels, the axis labels name the demes, and
  the title carries the run's `N, m, μ, d` (the house style — design §8).
  Pixel-diffing is avoided because it is brittle across Matplotlib patch
  versions; structural assertions plus a pinned Matplotlib (detailed design
  §4) give reproducibility without fragility.
- `d ≤ 3` renders the direct scatter; `d > 3` dispatches to the pairwise
  matrix (moderate `d`) or one explicit deme pair, Deme 1 vs. Deme 2 by
  default (large `d` — design §8, corrected in that document's own
  revision history: an earlier version of this dispatch used a PCA
  projection instead). _plot_pca itself is unchanged and directly
  tested, just no longer reached automatically by `plot_frequency_
  scatter` at any `d`.
- Coincidence-count marker scaling and common/rare coloring appear when many
  points coincide (design §8).
- Diagnostics: the convergence-trace series has one point per recorded
  generation; the STRUCTURE-style bar chart has `d` stacked bars.
- **Error paths and file writing**: every documented
  `ValueError` guard (mismatched deme count, an undersized
  pairwise_max_demes, a length mismatch or empty history for a
  convergence trace, an out-of-range locus_index) is asserted by
  name; both diagnostic views are confirmed to actually write a
  non-empty PNG when given a `path`;
  the pairwise matrix's unused-grid-cell hiding is exercised at the
  specific `d` that reaches it; the PCA projection's single-point
  special case (skipping `numpy.linalg.svd` entirely) is exercised by
  calling the module-private projection function directly, since no
  public dispatch reaches it automatically at any `d` any longer;
  a locus with more alleles than MAX<sub>LEGEND</sub>_ALLELES renders without
  a legend. One guard in _frequency_points (an empty point matrix)
  is unreachable through any validly constructed `ModelState` — marked
  `# pragma: no cover` with the invariant it depends on stated inline,
  rather than covered by a contrived, non-representative fixture.

## 10. Packaging smoke tests

Marked `@pytest.mark.packaging`; run in the CI package and release jobs
(detailed design §5.2/§5.4), not in the default `pytest`.

- **Wheel entry point**: after `pip install` of the built wheel into a
  throwaway venv, `fim --version` prints `version.txt` and `fim --help`
  lists the subcommands.
- **One-file exe** (release workflow, `windows-latest`): `fim.exe --version`
  prints the tag's version; a bundled tiny config runs `fim.exe run`
  end-to-end **offline** (no network available on the runner for that step)
  and writes the four artifacts.
- **Version guard**: the release job fails if the tag ≠ `version.txt`
  (detailed design §5.4) — tested by construction (a mismatched tag fails
  the job).

### 10.1 Repository-tooling checks: git hooks, doc freshness, release notes

Home: `test/validation/` — sharing a directory with the scientific
statistical/asymptotic and published-scenario layers (§7) but a
different, non-scientific kind of check. These are fast, Docker-free
shell/pytest checks that obey the determinism contract (§1): no network,
no wall-clock, fully reproducible.

- **`commit-msg`** ([`test_git_hooks.py`](../test/TESTS.md#validation.test_git_hooks)): accepts valid Conventional
  Commit subjects (including `merge`, `revert`, `fixup!`, and `squash!`
  forms) and rejects malformed ones, driven by a table of
  subject/expected-result cases.
- **`pre-commit`** ([`test_git_hooks.py`](../test/TESTS.md#validation.test_git_hooks)): formats a deliberately messy
  staged Python file and confirms the re-staged content is `ruff`-clean;
  regenerates `src/fim/API.md` only when a staged `.py` changed (a staged
  docs-only or non-Python change leaves it untouched); rejects a newly
  added non-ASCII filename.
- **`pre-push`** ([`test_git_hooks.py`](../test/TESTS.md#validation.test_git_hooks)): detects a stale generated
  `src/fim/API.md` (the same doc-freshness gate `pre-commit` enforces,
  checked again at push time) and passes once it is regenerated.
- **Graceful degradation** ([`test_git_hooks.py`](../test/TESTS.md#validation.test_git_hooks)): each hook — including
  `pre-push` — is run in a fixture repository with the relevant tool (or
  `pyproject.toml`) absent and asserted to no-op with an informational
  message rather than error, so a fresh clone that has not yet installed
  the `dev` group is never blocked (detailed design §8.2).
- **Hook installer** ([`test_git_hooks.py`](../test/TESTS.md#validation.test_git_hooks)): `dev/git-hooks/install`
  symlinks every documented hook (`commit-msg`, `pre-commit`, `pre-push`)
  into a fixture repository's `.git/hooks/`.
- **Doc-freshness gate** ([`test_api_docs.py`](../test/TESTS.md#validation.test_api_docs)): every committed Python
  module receives an API section in the generated reference, the direct
  proof (alongside the `pre-commit`/`pre-push` hook coverage above) that a
  stale API reference cannot reach `main` (§8.1). [`test_test_docs.py`](../test/TESTS.md#validation.test_test_docs)
  is the identical check for `test/TESTS.md` (`dev/bin/generate-
  test-docs`): every committed test module receives its own anchor, and
  the generator's own `_GROUPS` list is independently checked to cover
  every real subdirectory of `test/` that actually holds `.py` files.
- **Doc-navigation checker** ([`test_doc_links.py`](../test/TESTS.md#validation.test_doc_links);
  `dev/bin/check-doc-links`, detailed design §8.3): a fixture set of small
  Markdown files exercises the pass and fail paths — a valid relative link,
  in-page anchor, GitHub-style em/en-dash anchor, and a code-span heading's
  literal underscore all pass; a link to a missing file, an anchor with no
  matching heading, and an orphan document each fail with a message naming
  the offending target. The checker is offline and deterministic (§1); it
  never resolves external `http(s)` URLs, so its result is a pure function
  of the tree.
- **Changelog-backed release notes** ([`test_release_notes.py`](../test/TESTS.md#validation.test_release_notes),
  `dev/bin/extract-release-notes`, detailed design §5.4): the extractor
  returns exactly one version's `CHANGELOG.md` section, excluding adjacent
  releases, and fails rather than publishing a blank body when the tag has
  no section; the release jobs are checked, by inspecting
  `.github/workflows/ci.yml`, to source their GitHub release notes from
  the extractor rather than GitHub's own auto-generated notes, and to use
  a `pyinstaller` work path that does not collide with the build script's
  own directories. A companion check confirms `build`'s CI-mode test
  invocation excludes only the `slow` marker — the authoritative release
  gate still runs every other layer, including `packaging`, `statistical`,
  and `gui` (§2's taxonomy), unlike the fast default `pytest` invocation
  (§3), which excludes all four for local iteration speed.
- **Release gating** ([`test_release_notes.py`](../test/TESTS.md#validation.test_release_notes), detailed design §5.4):
  `windows` and `publish` name `verify-tag` and/or `build` in their
  `needs:`, and `verify-tag` itself is checked to both `git rev-parse
  --verify "...^{tag}"` (rejecting a lightweight tag) and `git
  merge-base --is-ancestor` (rejecting a tag that is not reachable from
  `main`) — so a release cannot silently regress to publishing from an
  unverified or untested tag.
- **CI test-layer runtime budget** ([`test_ci_runtime_budget.py`](../test/TESTS.md#validation.test_ci_runtime_budget), detailed
  design §5.2): the fast marker-filtered step and the full `--ci` step
  are checked to be two separately named steps, in that order, each
  declaring its own `timeout-minutes` (the fast step's smaller than the
  full step's) — so the two cannot silently be re-folded back into one
  opaque, unbudgeted step. Separately, the `slow-tests` job is checked to
  run only on `schedule`/`workflow_dispatch` (never `push`/`pull_
  request`) and to call `./build --slow-only` under its own positive
  budget, while `build` and `homebrew` are each checked to skip those
  same two triggers — so a scheduled or dispatched run adds only
  `slow-tests`, never a duplicate of the ordinary per-push pipeline.
- **Statistical calibration provenance** ([`test_calibration_provenance.py`](../test/TESTS.md#validation.test_calibration_provenance),
  §7.1): `dev/bin/calibrate-statistical-bands` is checked
  to appear in `build` only inside the lint stage's static-analysis
  invocations, never as an executed step, and nowhere in `ci.yml` — the
  structural guarantee behind "characterization stays out of the
  deterministic PR gate."
  `test/validation/statistical-calibration-evidence.json` is
  checked to actually carry the retained evidence (every scenario's
  section, seed, replicate count, empirical sigma, environment, and the
  generator metadata block), not merely exist as an empty placeholder.
- **Repository-local Python tool resolution** ([`test_python_wrappers.py`](../test/TESTS.md#validation.test_python_wrappers)):
  `bin/ruff` and `build`'s lint step both work correctly in an environment
  with no activated virtual environment on `PATH`, confirming the
  repository's own wrapper scripts — not whatever Python happens to be
  first on a developer's `PATH` — resolve the pinned toolchain.

## 11. Coverage targets and CI gating

- Branch coverage gate: **90%** of `src/fim`, with every package
  measured — `viz/` (structurally tested by §9) carries real error-path
  and file-writing coverage and sits at 100%. `cli.py`'s
  `update --check` network wrapper is *not* omitted: its `urlopen` call is
  monkeypatched at the boundary (§4.12), so the surrounding logic is
  exercised and counted normally, and the coverage gate itself never
  performs a network request (§1).
- The gate runs in `build --ci` and `ci.yml` identically (detailed design
  §5.2/§7), so local and CI coverage cannot diverge.
- Coverage is a floor, not the goal: the golden, property, and statistical
  layers are what actually establish correctness; coverage only catches
  code no test exercises at all.

## 12. Requirement traceability matrix

Each design-document requirement (design §2) mapped to the tests that prove
it:

| Requirement | Proven by |
|---|---|
| 1. `fim(N,m,μ,d;P)` yields per-generation state; ends on convergence | §4.11 engine reproducibility; §4.8 convergence; §8 end-to-end run |
| 2. Alleles are identity-only (`same` = integer equality) | §4.1 registry uniqueness + equality-only contract |
| 3. Locus carries `length`; per-locus `L` is future-ready | §4.2 `LocusSpec`, finite_allele_capacity; §4.5 array-typed params |
| 4. Random initial conditions; convergence is a modeling assumption | §4.6 initial conditions; §4.8 stochastic-equilibrium detection |
| 5. Every generation persisted; converged final state reported | §4.10 persistence round-trip; §8 artifacts on disk |
| 6a. Final differentiation scalars | §6 golden values; §5 invariants; §8 `report.json` shape |
| 6b. Per-deme allele-frequency distributions + canonical scatter | §8 report table; §9 scatter structure |

Design §9.1's configuration-reachable variations each carry dedicated
coverage rather than riding along on the requirements above:

| Design §9.1 variation | Proven by |
|---|---|
| Per-deme population sizes (`N` as an array) | §4.5 shape validation; §4.7 per-deme drift variance; §4.11 unequal-`N` run |
| Asymmetric/matrix migration, incl. stepping-stone topology sugar and a hand-authored sparse map | §4.3 `topology.py`; §4.5 config-sugar parsing; §4.7 operator-level checks; §4.11 engine run; §8 CLI config |
| Stochastic migrant-count sampling | §4.5 migrant_sampling; §4.7 binomial-theory check; §4.11 engine run; §8 CLI config |
| Per-locus mutation rate, incl. μ<sub>b</sub> per-base derivation | §4.5 `mu`/μ<sub>b</sub> parsing; §4.7 per-locus mutate; §4.11 engine run; §8 CLI config |
| Finite-alleles (K-allele) mutation model | §4.1 `FiniteAlleleSpace`/`FiniteAlleleRegistry`; §4.5 capacity validation; §4.7 operator-level checks; §4.11 engine run; §8 CLI config |
| Several convergence statistics with a combinator | §4.5 convergence_statistic parsing; §4.8 monitor combinator; §4.11 engine run |
| Adaptive replicate batching on a confidence interval | §4.5 replicate_tolerance/replicate_minimum/replicate_confidence validation; §4.8 `ConfidenceIntervalCriterion`; §4.9 the interval itself; §4.11 adaptive stop and replicate_summary; §8 batch artifacts |
| Parallel replicate execution | §4.11 worker-count equivalence, argument validation, and per-replicate stores; §4.12 CLI flags; §8 batch artifacts under each execution mode |

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-18
generator-responsibility: primary
```

### Revisions

Documentation review. Corrected the claim that the CLI rejects a
multi-replicate configuration, and added the coverage that had no entry
here: the confidence-interval module
(§4.9), the confidence-interval criterion (§4.8), the replicate-batch
parameter keys (§4.5), adaptive stopping and parallel execution (§4.11,
§4.12), the batch artifact contract (§8), and two traceability rows
(§12).

```text
generator-name: Claude Code
generator-version: Claude Opus 5
generator-model-token: claude-opus-5
generator-provider: Anthropic
generation-date: 2026-08-18
generator-responsibility: revision
```

Corrected §9's own scatter-plot description to match design §8's own
correction: large-`d` no longer dispatches to a PCA projection by
default, and the single-point SVD-skip test now calls the projection
function directly rather than relying on a public dispatch path that no
longer reaches it.

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-23
generator-responsibility: revision
```

Split into two documents: this one (renamed from `fim-simulator-
test-plan.md`, developer-facing) and a new, separately authored `doc/
fim-simulator-test-plan.md` (rewritten from scratch, botanist-facing —
the primary audience for that document, this document's own readership
demoted to secondary there and made primary here instead). Extracted
two further companion references: `doc/fim-simulator-functional-api.md`
(the externally accessible engine API §2's "fim functional" category is
scoped to) and `doc/fim-gui-test-plan.md` (`test/gui/`'s own coverage,
previously undocumented in this document at all). Added §2's second,
orthogonal taxonomy (fim functional / fim-gui functional / internal-
deep) and the `gui` marker row missing from §3's own table. Added §7.5,
documenting five literature-grounded scenarios added since this
document's last revision (Crow & Aoki 1984, Chao et al. 2015, Nei 1973,
Whitlock 1992, Kimura & Weiss 1964 via Whitlock & McCauley 1999) that
had no entry here. Fixed a stale, out-of-repository doc reference in
`fim.gui`'s own package docstring (`src/fim/gui/__init__.py`), found
while auditing this document's own citation trail for references
outside this repository.

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-30
generator-responsibility: revision
```
