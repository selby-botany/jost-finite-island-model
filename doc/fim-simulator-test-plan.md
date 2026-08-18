<!-- markdownlint-disable MD013 -->

# Finite island model simulator: test plan

- [Finite island model simulator: test plan](#finite-island-model-simulator-test-plan)
  - [Who this document is for](#who-this-document-is-for)
  - [1. The determinism contract for tests](#1-the-determinism-contract-for-tests)
  - [2. Test taxonomy](#2-test-taxonomy)
  - [3. Layout, tooling, and markers](#3-layout-tooling-and-markers)
  - [4. Unit tests, by module](#4-unit-tests-by-module)
    - [4.1 `model/allele.py`](#41-modelallelepy)
    - [4.2 `model/locus.py`](#42-modellocuspy)
    - [4.3 `model/state.py`](#43-modelstatepy)
    - [4.4 `model/params.py`](#44-modelparamspy)
    - [4.5 `model/initial.py`](#45-modelinitialpy)
    - [4.6 `model/operators.py`](#46-modeloperatorspy)
    - [4.7 `convergence/`](#47-convergence)
    - [4.8 `persistence/`](#48-persistence)
    - [4.9 `engine.py`](#49-enginepy)
    - [4.10 `cli.py`](#410-clipy)
  - [5. Property-based invariants for the statistics module](#5-property-based-invariants-for-the-statistics-module)
  - [6. Golden worked examples](#6-golden-worked-examples)
  - [7. Statistical and asymptotic tests](#7-statistical-and-asymptotic-tests)
    - [7.1 Deriving a tolerance band before choosing a seed](#71-deriving-a-tolerance-band-before-choosing-a-seed)
    - [7.2 Drift variance](#72-drift-variance)
    - [7.3 Equilibrium formulas](#73-equilibrium-formulas)
    - [7.4 Published-scenario fixtures](#74-published-scenario-fixtures)
  - [8. Functional and end-to-end tests](#8-functional-and-end-to-end-tests)
  - [9. Visualization tests](#9-visualization-tests)
  - [10. Packaging smoke tests](#10-packaging-smoke-tests)
    - [10.1 Git-hook and doc-freshness checks](#101-git-hook-and-doc-freshness-checks)
  - [11. Coverage targets and CI gating](#11-coverage-targets-and-ci-gating)
  - [12. Requirement traceability matrix](#12-requirement-traceability-matrix)
  - [Metadata](#metadata)

## Who this document is for

Written for whoever implements or maintains the simulator's tests. It is
the companion to the
[detailed design](fim-simulator-detailed-design.md)
(which plans the code commit by commit) and the
[design document](fim-simulator-design.md) (which
settles the model, statistics, and architecture). Every formula, golden
value, and equilibrium relation cited here is sourced from the design
document and its two companions — the
[finite island model introduction](finite-island-model-introduction.md)
and the
[Jost differentiation-measures guide](jost-differentiation-measures.md)
— and is not re-derived.

"Design §N" points into the design document; "detailed design §N" points
into the implementation plan; "Part N" points into the differentiation-
measures guide. Each test group below names the milestone/commit in the
detailed design that ships it, so tests and code land together.

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
   `derandomize=True` profile in CI so property tests are reproducible.

A test that cannot be made deterministic is redesigned (stub the stochastic
dependency) rather than retried. Retry is reserved for cases where the
external interaction is itself the thing under test — of which this project
has none, because the simulator has no external interactions during a run.

## 2. Test taxonomy

| Layer | Question it answers | Determinism source | Home |
|---|---|---|---|
| Unit | Does this function/value object behave exactly as specified? | Exact inputs, or seeded RNG | `test/<module>/` |
| Property-based | Do the algebraic identities hold for *all* valid inputs? | Hypothesis strategies, derandomized | `test/statistics/` |
| Golden-value | Do statistics match hand-checked reference values? | Fixed literal inputs | `test/statistics/`, `test/data/` |
| Statistical / asymptotic | Does the *model* match theory in the large-sample limit? | Fixed seeds + a-priori bands | `test/validation/` |
| Published-scenario | Does a run reproduce Jost's own worked examples? | Fixed seeds + a-priori bands | `test/validation/` |
| Functional / end-to-end | Does the CLI/engine produce the documented artifacts? | Seeded run, fixed inputs | `test/cli/`, `test/engine/` |
| Packaging smoke | Does the shipped artifact start and run offline? | Fixed invocation | CI / `build` |

The first three layers are fully deterministic by construction. The middle
two are stochastic-but-reproducible under §1. The last two exercise
integration and shipping.

## 3. Layout, tooling, and markers

Tests live under `test/`, mirroring `src/fim/`, run by pytest with branch
coverage (detailed design §4). `test/conftest.py` provides the shared
scaffolding:

- `rng(seed)` — a factory returning `Generator(PCG64(seed))`; the only
  sanctioned RNG source in tests.
- `freq_tables` — a Hypothesis strategy producing valid per-deme frequency
  tables (`Σp == 1` per deme, arbitrary `d`, arbitrary allele support) for
  the property tests.
- `tiny_params` — a small, fast `SimulationParams` (e.g. `d=2`,
  `initial_allele_count=2`, one locus) for end-to-end tests.
- `golden` — loads the Part IV fixtures from `test/data/`.

Markers keep the fast/slow split explicit:

| Marker | Meaning | Runs in |
|---|---|---|
| (default) | Fast, fully deterministic | Every `pytest` |
| `@pytest.mark.statistical` | Many-replicate, seeded, banded (§7) | CI + `build --ci`; opt-in locally |
| `@pytest.mark.slow` | Long horizons | CI nightly / on demand |
| `@pytest.mark.packaging` | Requires a built wheel/exe | CI package job |

The default `pytest` invocation (no markers) must stay fast enough to run on
every save. `build --ci` runs everything.

## 4. Unit tests, by module

### 4.1 `model/allele.py`

Ships with detailed-design commit 1.1.

- `AlleleRegistry.next_id()` returns strictly increasing, never-repeating
  IDs across the whole run — the infinite-alleles guarantee reduces to this
  (design §3.2).
- Founding IDs (locus-relative `0..K-1`) and minted IDs occupy disjoint
  ranges and can never collide (design §3.3).
- `AlleleId` supports only equality — a test asserts that ordering/arithmetic
  is either absent or never used by the model code (enforced structurally by
  the `NewType` + `mypy --strict`, checked here at runtime for the equality
  contract).

### 4.2 `model/locus.py`

Ships with commit 1.2. `LocusSpec` is immutable and hashable; `length`
participates only as data (no statistic reads it — design §3.2), asserted by
constructing two specs differing only in `length` and confirming identical
statistical output downstream.

### 4.3 `model/state.py`

Ships with commits 1.3–1.4.

- `total_frequency()` returns `1.0` within tolerance for every
  (deme, locus); a state that violates this is rejected/flagged.
- The support of any `p_{k,t,l}` never exceeds `N` (design §3.1).
- Serialization round-trip: `state → rows → state` is exact for sparse
  states, including states with a single fixed allele and states at full
  `N`-allele support.
- Equality is value equality, independent of internal dict ordering.

### 4.4 `model/params.py`

Ships with commit 1.5.

- Valid scalar `(N, m, μ, d)` construct; `seed` is required (no default —
  design §4.3).
- Invalid inputs raise with a clear message: `m ∉ [0,1]`, `μ ∉ [0,1]`,
  `d < 2`, `N < 1`, empty `loci`, unknown `P`-bag key, `deme_weighting`
  outside `{"equal","size"}`, `convergence_window < 2`.
- Every `P`-bag default matches design §4.3's table exactly; a self-checking
  test parses the schema and asserts the documented defaults are the applied
  defaults (guards against silent drift, mirroring the sibling projects'
  self-validating-helper pattern).
- Array-typed `N`/`m` are accepted and shape-validated (`N` length `d`; `m`
  `d×d`) so the design §9 extensions are a data change, not a schema change.

### 4.5 `model/initial.py`

Ships with commit 1.6.

- Default Dirichlet generator: same seed ⇒ identical initial state; each
  (deme, locus) sums to 1; `initial_concentration` visibly changes evenness
  (a low-`α` draw is more skewed than a high-`α` draw, asserted via a
  Gini/entropy comparison at fixed seed).
- Explicit-`p_0` override: the supplied distribution is used verbatim and
  validated (`Σp == 1`).
- Founding alleles use locus-relative IDs `0..K-1` (design §3.3).

### 4.6 `model/operators.py`

Ships with commits 2.1–2.5. Each operator is `ModelState → ModelState` and
tested against its closed form.

- **migrate**: expectation-preserving — the migrant-pool blend conserves the
  total allele frequency across demes; `m=0` is identity; `m=1` fully
  replaces each deme with the pool (design §3.4).
- **mutate**: at rate `μ`, the expected count of mutated copies is `Nμ`;
  every mutated copy gets a fresh registry ID; `μ=0` is identity. A seeded
  test checks the mutated-count mean over many draws against `Nμ` within a
  binomial band (§7.1).
- **drift**: `Σp == 1` post-resample; support ≤ `N`; `μ=0` runs drive toward
  fixation (trailing-window variance → 0); the per-generation variance
  matches `p(1-p)/N` (§7.2). The dense fast path and the sparse path produce
  identical results for a fixed-`K` no-mutation state at the same seed.
- **pipeline**: `step` composes `drift ∘ mutate ∘ migrate` in that order
  (design §3.4); same seed ⇒ identical next state; the invariant holds after
  every stage.

### 4.7 `convergence/`

Ships with commits 4.1–4.3.

- Trailing-window criterion: a constant sequence is stable immediately once
  the window fills; a linearly drifting sequence is *not* stable; an
  oscillating sequence within tolerance *is* stable, one exceeding it is not.
- `max_generations` criterion always eventually fires regardless of the
  statistic's behavior (the safety valve — design §3.5).
- `ConvergenceMonitor.reason()` distinguishes "statistic converged" from
  "hit the cap"; a capped-unconverged run is a valid result, not an
  exception (design §5).
- The `μ=0` zero-variance case is detected by the same criterion as a fast
  special case (design §3.5), not a separate path.

### 4.8 `persistence/`

Ships with commits 5.1–5.4.

- `TrajectoryStore` round-trip: `write_generation` then `read` returns rows
  byte-faithful to the schema, for a multi-generation, multi-deme,
  multi-locus run.
- `JSONLTrajectoryStore` appends incrementally (each generation is a
  flushed set of lines; a truncated file still parses every complete line).
- Rows carry only nonzero frequencies (sparse — design §6).
- Manifest captures the full `SimulationParams` incl. seed, convergence
  outcome, and version; a test reconstructs `SimulationParams` from the
  manifest and confirms it re-runs to an identical trajectory (the replay
  contract — design §6).

### 4.9 `engine.py`

Ships with commits 6.1–6.3 and 9.3.

- A tiny seeded `fim(...)` run is bit-reproducible across two invocations.
- Converged and capped runs both return a valid `RunResult` with the correct
  `reason`.
- The report computed live at `t=T` equals the report re-computed from the
  persisted trajectory (design §4.1 — statistics never depend on the
  engine).
- Replicate batching (commit 9.3): `n_replicates` runs are independent and
  each individually reproducible from the run seed; the scalar (`n=1`) case
  is unchanged.

### 4.10 `cli.py`

Ships with commits 8.1–8.6 (functional detail in §8).

- Config parsing maps every YAML key to the right `SimulationParams` field
  and rejects unknown keys with a message naming the key.
- `fim --version` prints `version.txt`'s value.
- `fim update --check` is tested against a mocked Releases response only —
  newer, equal, and older tags each produce the right message; **no test
  performs a live request** (§1).

## 5. Property-based invariants for the statistics module

Ships with commit 3.4. Checked with Hypothesis over `freq_tables`
(derandomized in CI). These are the differentiation-measures guide's Part V
identities, asserted as properties rather than point cases:

- `H_T ≥ H_S` for every table.
- `G_ST ≤ 1 − H_S` — the ceiling identity (Part V).
- `H_T = H_S + H_ST − H_S · H_ST` — the correct subadditive partition, with
  `H_ST` equal to `D`'s own first bracket (Part V).
- `D ∈ [0, 1]`; `D = 1` iff demes share no alleles; `D = 0` iff demes are
  identical.
- **Replication principle**: pooling two equally sized, equally diverse,
  completely disjoint groups exactly doubles `^HD_T / ^HD_S` (Part V).
- `G_ST`, `D`, `E_ST`, `K_ST` are all in `[0, 1]` for valid tables.
- Deme-weighting: `D` is invariant to the `deme_weighting` setting (it is
  fixed to equal weighting by construction — design §7); `E_ST` responds to
  it. A property test confirms this separation.

## 6. Golden worked examples

Ships with commit 3.4; fixtures in `test/data/`. Asserted to **exact**
values (these were independently recomputed from first principles in the
differentiation guide's Part IV, not copied from the paper):

| Fixture family | Configuration | Expected | Note |
|---|---|---|---|
| Nine-fixed-for-A, one-for-B | three configurations | `D` = 0.20, 0.5556, 1.00 | The `0.5556` case documents the erratum against the paper's printed `0.5` — asserted as `0.5556`, not `0.5`. |
| Three-species `G_ST`-near-zero | Species A/B/C | `G_ST ≈ 0`, `D` near 1 | The `G_ST`-hides-differentiation case. |
| "98% within demes" trap | recomputation | Part IV value | The misleading-`G_ST` recomputation. |
| `D`-vs-`K_ST` disagreement | as given | Part IV values | Confirms the two measures diverge as documented. |
| Five-for-A / five-for-B | two demes | `D = 0.5556` | The erratum's canonical case. |

Each fixture is a JSON frequency table plus its expected scalars; the test
loads the table, runs the statistics module, and asserts equality to the
stored value within floating-point tolerance (exact for the rationals).

## 7. Statistical and asymptotic tests

Ships with commits 2.5, 9.1, 9.2. Marked `@pytest.mark.statistical`. These
exercise the *model*, not just the statistics module, and are the tests the
determinism contract (§1) most directly governs.

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

### 7.2 Drift variance

A single-locus, single-deme drift step starting from a known `p` has
per-generation sampling variance `p(1−p)/N` (design §3.1's gene-copy-count
`N`). Over `R` seeded replicates of one step, the sample variance of the
resulting frequency is asserted within its analytically derived band. This
is the "validate before trusting anything built on top of it" check the
prior research doc flags (design §10).

### 7.3 Equilibrium formulas

Many-replicate runs at fixed `(N, m, μ, d)`, carried to stochastic
equilibrium, have sample-mean `G_ST` and `D` approaching the
differentiation guide's Part VI Eq. 2 and Eq. 4. Each is asserted within a
band derived from the replicate count. The letter's own closed-form `H_S`
and `H_T` approximations provide an independent analytic cross-check used
the same way.

### 7.4 Published-scenario fixtures

The two haploid Dear-Nolan scenarios (design §4.3), asserted as
**tolerance-banded statistical oracles**, never exact equalities — they are
themselves simulation output from a colleague's independent tool, not
analytic values (design §10):

| Scenario | `N` | `d` | `m` | `μ` | Expected `G_ST` | Expected `D` (observed) |
|---|---|---|---|---|---|---|
| Low migration, low mutation | 100 | 5 | 0.0001 | 0.000001 | 0.97 | 0.04 (0.04) |
| Higher migration, higher mutation | 2000 | 100 | 0.01 | 0.001 | 0.02 | 0.91 (0.90) |

Both plug in directly under the ploidy-neutral `N` convention (no
conversion — design §4.3). Each test runs many seeded replicates and
asserts the sample-mean `G_ST` and `D` fall within a band derived from the
replicate count and the letter's own expected/observed spread.

## 8. Functional and end-to-end tests

Ships with commits 6.3, 8.6. Home: `test/cli/`, `test/engine/`. These
exercise the product as a researcher uses it (design §13), on `tiny_params`
scenarios small enough to finish in well under a second.

- **`fim run` produces exactly the documented artifacts**: given a small
  YAML config and a fixed seed, the output directory contains
  `trajectory.jsonl`, `manifest.json`, `report.json`, and `scatter.png`,
  and `report.json` carries every scalar in design §13's example shape
  (requirement 6a). Two runs at the same seed produce identical
  `trajectory.jsonl` and `report.json`.
- **Config validation**: malformed configs fail with a message naming the
  offending key/value; a valid config round-trips into `SimulationParams`.
- **`fim stats` re-analysis**: re-computing a statistic (including a swept
  `q`) from a persisted `trajectory.jsonl` reproduces the live report,
  without re-running the simulation (design §4.1).
- **Manifest replay**: reconstructing `SimulationParams` from a run's
  `manifest.json` and re-running yields an identical trajectory (design §6).
- **Cap vs. converge**: a config with a low `max_generations` and a tight
  tolerance returns a capped-but-valid result whose `report.json` says so
  plainly (design §5), not an error.
- **`fim init`**: first run drops the starter config into the run folder
  (design §13).
- **`fim update --check`**: fully mocked HTTP; asserts the newer/equal/older
  messaging; never performs a live request.

## 9. Visualization tests

Ships with commit 7.4. Home: `test/viz/`. Rendered with the non-interactive
`Agg` backend so they are headless and reproducible.

- Plots are asserted by **structure, not pixels**: the figure exists, has
  the expected number of axes/panels, the axis labels name the demes, and
  the title carries the run's `N, m, μ, d` (the house style — design §8).
  Pixel-diffing is avoided because it is brittle across Matplotlib patch
  versions; structural assertions plus a pinned Matplotlib (detailed design
  §4) give reproducibility without fragility.
- `d ≤ 3` renders the direct scatter; `d > 3` dispatches to the pairwise
  matrix (moderate `d`) or the labeled PCA projection (large `d`), and the
  projection is explicitly labeled as such (design §8).
- Coincidence-count marker scaling and common/rare coloring appear when many
  points coincide (design §8).
- Diagnostics: the convergence-trace series has one point per recorded
  generation; the STRUCTURE-style bar chart has `d` stacked bars.

## 10. Packaging smoke tests

Ships with commits 10.1–10.3. Marked `@pytest.mark.packaging`; run in the CI
package and release jobs (detailed design §5.2/§5.4), not in the default
`pytest`.

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

### 10.1 Git-hook and doc-freshness checks

Ships with detailed-design commits 0.8–0.9. The repository-managed git
hooks (detailed design §8.2) and the generated-API-doc freshness gate
(§8.1) are themselves tested, mirroring the reference project's
`test/bin/*-hook` coverage. These are fast, Docker-free shell/pytest checks
that obey the determinism contract (§1): no network, no wall-clock, fully
reproducible.

- **`commit-msg`**: accepts valid Conventional Commit subjects (including
  `merge`, `revert`, `fixup!`, and `squash!` forms) and rejects malformed
  ones, driven by a table of subject/expected-result cases.
- **`pre-commit`**: formats a deliberately messy staged Python file and
  confirms the re-staged content is `ruff`-clean; regenerates
  `src/fim/API.md` only when a staged `.py` changed (a staged docs-only or
  non-Python change leaves it untouched); rejects a newly added non-ASCII
  filename.
- **Doc-freshness gate**: a test edits a public docstring *without*
  regenerating `src/fim/API.md`, then asserts both `build --ci` and the CI
  `git diff --exit-code` step fail; regenerating makes them pass. This is
  the direct proof that a stale API reference cannot reach `main` (§8.1).
- **Graceful degradation**: each hook is run in a fixture repository with
  the relevant tool (or `pyproject.toml`) absent and asserted to no-op with
  an informational message rather than error — the property that lets the
  hooks install at Milestone 0 before `src/` exists (detailed design §8.2).
- **Doc-navigation checker** (`dev/bin/check-doc-links`, detailed design
  §9.14 commit 11.5): a fixture set of small Markdown files exercises the
  pass and fail paths — a valid relative link and in-page anchor pass; a
  link to a missing file, an anchor with no matching heading, and an
  orphan document each fail with a message naming the offending target.
  The checker is offline and deterministic (§1); it never resolves external
  `http(s)` URLs, so its result is a pure function of the tree.

## 11. Coverage targets and CI gating

- Branch coverage gate: **90%** of `src/fim`, excluding `viz/` (which is
  smoke-tested by structure, not line-covered) and `cli.py`'s
  `update --check` network wrapper (mocked, its live branch is unreachable
  in test by design).
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
| 1. `fim(N,m,μ,d;P)` yields per-generation state; ends on convergence | §4.9 engine reproducibility; §4.7 convergence; §8 end-to-end run |
| 2. Alleles are identity-only (`same` = integer equality) | §4.1 registry uniqueness + equality-only contract |
| 3. Locus carries `length`; per-locus `L` is future-ready | §4.2 `LocusSpec`; §4.4 array-typed params |
| 4. Random initial conditions; convergence is a modeling assumption | §4.5 initial conditions; §4.7 stochastic-equilibrium detection |
| 5. Every generation persisted; converged final state reported | §4.8 persistence round-trip; §8 artifacts on disk |
| 6a. Final differentiation scalars | §6 golden values; §5 invariants; §8 `report.json` shape |
| 6b. Per-deme allele-frequency distributions + canonical scatter | §8 report table; §9 scatter structure |

## Metadata

```text
generator-name: Copilot CLI
generator-version: Claude Opus 4.8
generator-model-token: claude-opus-4-8
generator-provider: Anthropic
generation-date: 2026-08-14
generator-responsibility: primary
```
