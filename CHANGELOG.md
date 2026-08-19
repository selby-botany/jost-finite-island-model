# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `ci.yml` ran the entire test suite as one `./build --ci` step, so the
  `slow`/`statistical` scenario suite's own wall-clock cost (18m07s at
  review time) was invisible in the Actions run summary — indistinguishable
  from lint, type-checking, docs, and packaging, all bundled into the same
  opaque step, with no budget bounding any of it. Split into two named
  steps: a fast step re-running `build`'s own default marker-filtered
  layer (no coverage, `--no-lint --no-type --no-docs --no-package`) to
  fail in seconds before the expensive layer runs at all, then the
  unchanged authoritative `./build --ci` gate. Each step declares its
  own `timeout-minutes` (5 / 30) — a hard budget enforced by the runner
  itself, not a wall-clock assertion inside the test run, which
  machine-speed variance would make a non-deterministic pass/fail
  signal. Added `test/validation/test_ci_runtime_budget.py`, statically
  checking both steps stay present, correctly ordered, and budgeted.
- The across-replicate Student's-t critical value interpolates an
  untabled degrees of freedom in `1/df` between two printed-table
  rows — a defensible dependency-free choice, but nothing previously
  bounded the interpolation's actual error against a reference
  independent of that same table and formula; every existing test
  either read a tabled row back or recomputed the identical 1/df
  formula the production code already used, so a wrong interpolation
  weight or a transposed table row would have passed as long as it
  was internally self-consistent. Added a test-only oracle
  (`test/statistics/test_interval.py`) that numerically integrates
  the closed-form Student's-t density via Simpson's rule and checks
  each interpolated critical value's implied tail probability against
  it, for ten untabled degrees-of-freedom values across the whole
  table; verified sensitive by deliberately breaking the production
  interpolation weight and confirming the new test fails from its own
  computation, not merely alongside the pre-existing formula-echo
  test. Pure standard library — no new runtime or test dependency.
- `SimulationParams` accepted stopping rules that could structurally
  never fire. `convergence_window` could exceed `max_generations + 1`
  (the most generations a run can ever record — generation 0 plus
  `max_generations` steps), so `TrailingWindowCriterion.is_stable`
  could never see a full window before the generation cap ended the
  run; independently, `replicate_minimum` could exceed `n_replicates`
  whenever `replicate_tolerance` was set (and `n_replicates > 1`), so
  the adaptive replicate-batch criterion could never even be
  evaluated before the batch's own `n_replicates` cap ended it. Both
  configs ran to completion and reported an ordinary-looking "hit the
  cap" / "batch ended" result with no indication the stopping rule
  they described was unreachable from the start. Added
  `_validate_stopping_rules`, called from `SimulationParams.
  __post_init__`, rejecting both at construction. The per-replicate
  `dataclasses.replace()` reconstruction inside `fim.engine` forces
  `n_replicates=1` for each individual replicate run while copying
  `replicate_minimum`/`replicate_tolerance` through unchanged, which
  would otherwise make the new check spuriously reject every adaptive
  batch's internal replicates; guarded with `n_replicates > 1` since
  the adaptive-replicate machinery is provably inert at
  `n_replicates == 1` (`fim()` returns a scalar result before ever
  constructing the replicate monitor). Added a parametrized rejection
  case to `test/model/test_params.py`'s scalar-contract table for the
  window rule, `test_replicate_minimum_exceeding_n_replicates_is_
  rejected` to `test/engine/test_engine.py` for the replicate rule,
  and fixed the four existing tests whose fixtures happened to
  construct a now-invalid combination.
- `src/fim/viz/*` was entirely excluded from the coverage gate
  (`omit = ["src/fim/viz/*"]`), and its own test file
  (`test/viz/test_plots.py`) covered only happy paths — none of six
  `ValueError` guards across `scatter.py`/`diagnostics.py`, the
  pairwise-matrix grid-cell-hiding branch, the PCA single-point
  special case (which skips `numpy.linalg.svd` entirely), the
  legend-count cap, or either diagnostic view's file-writing branch
  had a test. Removed the coverage omit and added ten tests covering
  every reachable branch; `viz/` is now at 100% branch coverage (the
  overall gate moved from 95.76% to 96.06%). One guard
  (`_frequency_points`'s empty-point-matrix check) is unreachable
  through any validly constructed `ModelState` — marked
  `# pragma: no cover` with the specific invariant it depends on
  stated inline, rather than forced to pass via a contrived fixture
  that doesn't represent a real caller.
- Nothing guarded the test suite's own Hypothesis configuration:
  `test/conftest.py` registers and loads a `derandomize=True` profile
  so every property-based test is a pure function of its seed, but a
  silent future edit to that registration — or a competing
  `load_profile` call sequenced after it — would only have shown up as
  an intermittently flipping property test, not a named, immediate
  failure. Added `test/test_hypothesis_profile.py`, asserting
  `settings.default.derandomize` directly (the profile actually active
  for whatever test runs next, not merely that a correctly configured
  one exists somewhere unused).
- `dev/bin/check-doc-links` scanned `doc/dev/` — scratch and review
  material declared out of scope by its own `doc/.gitignore` ("dev")
  — as if it were reviewable documentation, reporting broken links,
  broken anchors, and orphan-document errors for content never meant
  to be linked into the doc set at all. The checker now excludes the
  `doc/dev/` subtree specifically (by path, not by directory name —
  a bare `"dev"` exclusion would have incorrectly also skipped the
  real, wanted `dev/git-hooks/README.md` at the repository root, which
  happens to share the same bare directory name one level up). Added
  `test_checker_skips_doc_dev_but_not_repository_root_dev` asserting
  both halves of that distinction.
- The `packaging` pytest marker (`"requires a built distribution"`) was
  declared in `pyproject.toml` but carried by zero tests, and `build`'s
  `--ci` invocation hard-coded `not packaging` even in CI mode — so
  wheel/sdist validation lived only in `build`'s own venv-based smoke
  step (raw shell, not `pytest`), which no contributor exercised under
  plain `pytest` and which a `packaging`-marked test could never reach
  through any path CI or a contributor actually runs. `build`'s
  coverage-mode test invocation now drops its `-m` marker filter
  entirely under `--ci` (`pytest_marker_args=()`) instead of hard-coding
  `not packaging`, so a marker added in the future is included by
  default rather than silently excluded; the local (non-`--ci`) default
  still excludes `slow`, `statistical`, and `packaging` for fast
  iteration. The `packaging`-marked test added for R12
  (`test/validation/test_sdist_contents.py`) is the first test this
  marker has ever actually carried.
- The source distribution (`sdist`) excluded `doc/`, `test/`, `dev/`,
  `CHANGELOG.md`, and `SECURITY.md` — only `LICENSE.md`, `README.md`,
  `src/fim`, and `version.txt` shipped, leaving a scientific package's
  source archive without the documentation, test suite, maintainer
  tooling, or release metadata a reviewer or downstream packager needs
  to actually review or rebuild it end to end. `[tool.hatch.build.
  targets.sdist]`'s `include` now also lists `/CHANGELOG.md`,
  `/CONTRIBUTING.md`, `/SECURITY.md`, `/dev`, `/doc`, and `/test`.
  Building a real sdist to verify surfaced a second, undocumented
  defect this change would otherwise have introduced silently:
  `hatchling`'s explicit `include` for `/doc` walks the filesystem
  rather than consulting `doc/.gitignore` (a *nested* `.gitignore`,
  which `hatchling` does not read), sweeping the entire gitignored
  `doc/dev/` scratch directory — AI review drafts and reference PDFs
  never meant for distribution — straight into the built archive. Added
  a matching `exclude = ["/doc/dev"]`. Added
  `test/validation/test_sdist_contents.py`: one static test asserting
  the required paths are declared, and one `packaging`-marked test
  (R17) that builds a real sdist and asserts `doc/dev/` is genuinely
  absent from it — a config-only test cannot see `hatchling`'s own
  file-walk behavior.
- Release-workflow dependencies and permissions were not immutably
  pinned or scoped, and `publish` had several rough edges. Every
  `uses:` reference in `ci.yml` and `gitleaks-ci.yml` (`actions/
  checkout`, `actions/setup-python`, `actions/upload-artifact`,
  `actions/download-artifact`, `gitleaks/gitleaks-action`) now names a
  full 40-character commit SHA instead of a mutable major-version tag
  a repository owner could silently repoint. Added `.github/
  dependabot.yml` tracking both `pip` and `github-actions` on a weekly
  cadence, so those pins and version ranges move forward on a
  reviewable schedule instead of drifting or never moving at all.
  `publish` now generates one consolidated `SHA256SUMS` manifest
  covering the wheel, sdist, and Windows executable (previously only
  the executable had a checksum, via its own pre-existing sidecar,
  kept as-is since `README.md` documents it specifically). Separately:
  `contents: write` — previously workflow-wide in the old
  `release.yml` — was already scoped to the `publish` job alone as a
  side effect of folding the release jobs into `ci.yml` (R8); this pass
  adds the regression test formalizing it. The Windows inter-job
  artifact now has `retention-days: 1` (it exists only to hand the exe
  from `windows` to `publish` within one run; its durable home is the
  GitHub Release itself). `publish` now verifies the downloaded exe
  against its own `.sha256` sidecar before shipping either, rather than
  trusting the inter-job hand-off blindly. The tag/version check now
  requires the tag to match `vX.Y.Z` before comparing it to
  `version.txt`, rather than a bare string-equality check with no
  shape guard. Deliberately deferred: a hash-locked pip constraints
  file, which needs a lock-file tool (`pip-tools` or `uv`) this project
  does not currently depend on and an ongoing regeneration process —
  left as a follow-up rather than adopted silently as a side effect of
  this pass.
- The Windows executable was built with `upx=True` in
  `packaging/fim.spec`. UPX-compressed executables are a well-known
  antivirus/SmartScreen false-positive trigger — for a project whose
  README already has to talk Windows users past SmartScreen for the
  unsigned binary alone — and `upx` is an undeclared build dependency
  that PyInstaller silently skips compression for when it is absent, so
  a compressed build was a function of whichever runner image happened
  to build it, not of the tag. Set to `upx=False`; added a regression
  test (`test/validation/test_packaging_spec.py`) so it cannot come
  back without also pinning `upx` as an explicit, versioned build
  dependency.
- Any `v*` tag published a release from any branch with no CI gate.
  `release.yml`'s `windows` and `publish` jobs were triggered
  independently by the same tag push as `ci.yml`, with no dependency
  between the two workflows — a tag could publish a release before CI
  had even started, let alone passed, and nothing checked that the
  tag's commit was reachable from `main` or that the tag was more than
  a bare, message-less ref. `windows` and `publish` now live inside
  `ci.yml` itself, gated by `needs: [build, verify-tag]` and
  `needs: windows` respectively — a structural dependency GitHub
  Actions enforces, not an independent trigger that merely happened to
  fire around the same time. The new `verify-tag` job requires the tag
  to be annotated (`git rev-parse --verify "$GITHUB_REF_NAME^{tag}"`,
  which only an annotated tag object satisfies) and an ancestor of
  `main` (`git merge-base --is-ancestor`). `release.yml` is removed;
  `dev/bin/validate-repository`'s `yamllint` invocation updated to
  match. Repository-level branch protection on `main` and tag
  protection on `v*` are GitHub settings outside version control and
  are documented as a one-time manual checklist in `CONTRIBUTING.md`
  rather than applied automatically.
- A run's output directory was neither atomic nor integrity-checked. A
  scalar or batch run wrote its files directly at the final `-o` path as
  it went, so an interrupted run (a crash, a killed process, a full
  disk) left a partial directory with no way to tell it apart from a
  complete one; nothing recorded whether `trajectory.jsonl` still
  matched what the run actually wrote, so an edited or truncated
  trajectory re-analyzed silently under `fim stats`. Every run's output
  directory (the whole four-file set for a scalar run, or the whole
  `replicate-NNN/` tree plus `summary.json`/`manifest.json` for a batch)
  is now built in a hidden temporary sibling and published at the final
  path with a single atomic rename, only once every artifact is durable
  — see `_atomic_directory` in `fim.cli`. `manifest.json` is now written
  *last*, augmented with each sibling artifact's SHA-256 digest and byte
  count (`fim.persistence.manifest.hash_file`); `RunManifest` gained
  `schema_version`, `generation_count`, and an optional `artifacts`
  field recording those digests. `fim stats` now recomputes and checks
  the trajectory's digest and generation count against the manifest
  before reading a single row, refusing a tampered or mismatched file
  with a named error instead of silently proceeding. `-o` now refuses
  *any* pre-existing output directory, empty or not — stricter than the
  prior any-artifact-file check, and necessary for the atomic rename to
  be unambiguous. Added failure-injection tests at the trajectory-write,
  report-write, and plot-render boundaries (both the scalar and batch
  paths) confirming the target directory never exists after an
  interruption at any of them.
- The mypy type gate's configuration and its actual invocations disagreed
  with each other. `[tool.mypy]` set both `files = ["src", "test"]` and
  `packages = ["fim"]`, which mypy rejects together (it accepts at most
  one of `files`, `packages`, or explicit CLI arguments), so a bare
  `mypy` failed immediately with "May only specify one of: module/
  package, files, or command" — before checking anything. `build` and
  `dev/git-hooks/pre-push` both sidestepped the crash by always passing
  an explicit `mypy --strict src`, which happens to override a
  conflicting config instead of erroring, but that same explicit `src`
  argument also silently narrowed the checked scope away from `test/`
  regardless of what `files` said — so the type gate actually run in CI
  and at push time had never covered `test/` at all. Running `mypy src
  test` directly surfaced 13 real errors across four test files that had
  been accumulating unnoticed. Dropped `packages` from `[tool.mypy]`;
  moved `build` and `pre-push` to the bare `mypy` invocation so the
  config is the single source of truth for scope; fixed all 13 errors,
  preferring the real underlying issue over a suppression in every case
  — a private `Figure._suptitle` access became the public
  `get_suptitle()`; two return-type/annotation gaps in
  `test/statistics/test_differentiation.py` and
  `test/model/test_state_validation.py` got explicit type narrowing or
  annotations; two 3D/scatter-specific matplotlib return types
  (`Axes3D.get_zlabel`, `PathCollection.get_sizes`) got `isinstance`
  narrowing that doubles as a genuine runtime assertion instead of an
  ignore; one deliberately-invalid-type test call and one genuinely
  unstubbed alias method (`get_facecolors`, matplotlib's own
  `_api.define_aliases`-generated alias for the stubbed
  `get_facecolor`) kept the project's existing targeted
  `# type: ignore[arg-type/attr-defined]` pattern; and one
  `urllib.error.HTTPError` test construction switched from a bare `{}`
  to a real `email.message.Message()` for its `hdrs` argument. Added
  `test/test_mypy_scope.py`, asserting statically (no mypy subprocess)
  that `[tool.mypy]` never declares a conflicting `packages` key
  alongside `files`, and that neither `build` nor `pre-push` passes mypy
  a positional path argument that would narrow it away from that
  config again.
- Adaptive `G_ST` batches raised `ValueError` on partial monomorphism
  (one tracked locus fixed for the same allele while another locus is
  still polymorphic) because three layers disagreed about what an
  undefined `G_ST` means: within-run watching substituted `0.0` per
  locus, the final report voided the whole multi-locus average if *any*
  locus was undefined, and the adaptive replicate-batch monitor tried to
  rescue a `None` report value only when the run's *averaged* `H_T` was
  exactly zero — a condition partial monomorphism never satisfies, since
  the polymorphic locus keeps the average above zero, so it raised
  instead. All three layers now agree on one rule: drop whatever is
  undefined and average what remains, at both locus and replicate
  granularity, and never fabricate a substitute or raise for a
  legitimately undefined value.
  - `_mean_g_st_across_loci` (new, in `fim.engine`) drops any locus
    where `G_ST` is undefined and averages the rest, returning `None`
    only when *every* locus is undefined. `report_for_state` and the
    within-run convergence watcher (`_convergence_values` /
    `_mean_statistic_across_loci`) both use it, so they can no longer
    disagree with each other.
  - `_replicate_stopping_values` now drops an undefined statistic for a
    replicate instead of raising or substituting `0.0` — the same rule
    `replicate_summary` already used for the *published* interval, so
    the stopping decision is now judged against the same sample the
    summary reports from, not a different one.
  - `ConvergenceMonitor.record()` accepts a mapping that omits any
    statistic undefined this round (previously it required exact
    coverage of every configured name); an omitted statistic simply
    does not advance its own trailing window that round. An unknown
    statistic name not in the configured set still raises.
  - Net effect on a run/batch watching only `G_ST` where it is
    *permanently* undefined (every locus, every generation or
    replicate, monomorphic): its trailing window never fills, so the
    run/batch now correctly falls back to `max_generations`/
    `n_replicates` instead of the prior behavior, where padding the
    window with fabricated `0.0`s reported spurious immediate
    "convergence" regardless of what the run was actually doing.
- `FiniteAlleleSpace` (the finite-alleles/K-allele mutation model) minted
  allele IDs outside `0 .. capacity - 1` whenever the generation-zero
  founding IDs at a locus were non-contiguous (for example `{0, 3}` at
  capacity 4). `_next_unminted` was seeded from `max(initial_ids) + 1`,
  so the gap between founders (`1`, `2`) was never mintable and later
  mutations walked past `capacity - 1` instead. A `finite_alleles` run
  seeded from a sparse `p_0` therefore silently modeled more than `K`
  alleles and never surfaced an error. New allele IDs are now allocated
  from the smallest unminted state in `0 .. capacity - 1`, and minting a
  state at or beyond `capacity` now raises instead of succeeding
  silently. `FiniteAlleleSpace` also now rejects a `capacity` below 2 at
  construction, rather than dividing by zero on the first mutation.
- `p_0` allele IDs and sparse `m` deme keys silently truncated a
  non-integral float and accepted a negative identifier through a bare
  `int(raw_value)` coercion. A truncated float (`1.9` parsed as `1`) ran
  the wrong allele to completion with no error; a negative allele ID was
  accepted at config-parse time and only failed much later inside
  `store.write_generation`, with an error naming persistence rather than
  the config. `SimulationParams.from_mapping` and direct
  `SimulationParams(...)` construction both reach this coercion (the
  latter only through `_normalize_initial_frequencies`, independently of
  the former's own parsing), so both paths are now guarded by a shared
  `_parse_integer_identifier` helper that accepts a native integer, an
  integral float, or a numeric string, and rejects a non-integral float,
  a boolean, or any other type outright. `p_0` allele IDs are further
  rejected if negative.
- The engine-validation test suite's equilibrium oracle
  (`test/validation/test_simulator_equilibrium.py`) approximated the
  mutation step's per-generation survival factor as `(1 - mu) ** 2`, its
  mean-only value, while the recursion actually needs that factor's
  second moment because it tracks a pairwise (two-lineage) identity
  quantity. The exact second moment is `(1 - mu) ** 2 + mu * (1 - mu) /
  N`; the omitted `mu * (1 - mu) / N` term was documented as an `O(1/N)`
  residual in five places even though the file's own module docstring
  calls the recursion "exact". Every scientific test's verdict is judged
  against this oracle, so the gap was invisible to every other test. Now
  computed exactly via a new `_mutation_survival` helper, with a
  derivation showing the fix is a closed-form identity rather than a
  truncated series (there is no missing `O(1/N^2)` or higher term to add
  on top of it).
- `SimulationParams.__post_init__` validated `seed` as "any integer",
  with no lower bound, so a negative seed passed config validation and
  was only ever rejected once `fim()` actually started and constructed
  NumPy's `PCG64` — by which point, in the CLI, the output directory
  could already exist. `seed` is now required to be `>= 0` at
  construction time, matching PCG64's actual legal range (its
  `SeedSequence` hashes any non-negative integer into a fixed-size
  entropy pool rather than enforcing an upper bound, verified up to a
  1024-bit seed, so non-negativity is the whole constraint). Every
  batch replicate's derived seed (`seed + replicate_index`) is
  therefore also always in range, with no separate bound needed, since
  `n_replicates` is already required to be at least 1 and Python
  integers never overflow.
- `dev/bin/check-doc-links` stripped every underscore and asterisk from a
  heading's anchor, including ones inside a `` `code span` `` where they
  are literal identifier characters, not markdown emphasis syntax — so a
  heading like `` ### `convergence_statistic` `` anchored at
  `#convergencestatistic` instead of GitHub's actual
  `#convergence_statistic`, and any link to it was rejected as broken.
  No heading anchor with an underscore had ever been linked to before, so
  the defect was latent rather than previously caught. Code-span content
  is now unwrapped to its literal text before anchor slugging, instead of
  going through markup stripping a second time.

### Added

- Support for watching several convergence statistics at once:
  `convergence_statistic` accepts a list of statistic names (in addition
  to the existing single-statistic default) and a new
  `convergence_combinator` (`"all"`, the default, or `"any"`) decides
  whether every watched statistic must be simultaneously stable before a
  run stops, or just one of them. `ConvergenceMonitor` now tracks an
  independent trailing-window history per watched statistic; a single
  statistic remains that mechanism's one-element special case, with no
  change to its existing behavior, report shape, or manifest format.
  `RunResult` gained `convergence_histories`, exposing every watched
  statistic's recorded trajectory, not only the primary one.
- Dedicated test coverage for the several-statistic feature: monitor-level
  tests for the `"all"`/`"any"` combinator semantics and the mapping
  contract `record()` requires once more than one statistic is watched; a
  params test for list acceptance, duplicate/unknown rejection, and
  round-tripping; an engine-level test proving the ordinary
  single-statistic report shape is unchanged; a reproducible
  several-statistic engine run reporting every statistic's history; a
  deterministic, seed-pinned test proving `"any"` stops a real run earlier
  than `"all"` (generation 5 vs. 15 for the same seed and parameters); a
  CLI test running a config with several statistics and a combinator; and
  manifest round-trip and malformed-input tests for the widened
  `statistic` field.
- Dedicated test coverage for per-deme island sizes (`N` as a length-`d`
  array): a per-deme drift-variance statistical test, an end-to-end engine
  run that bounds every generation's per-deme support by its own `N_i`,
  an engine-level check that `deme_weighting: size` uses actual per-deme
  sizes rather than an equal split, a params test bounding
  `initial_allele_count` by the smallest configured `N_i`, and a CLI test
  running a config with `N` as a list.
- Dedicated test coverage for a full asymmetric migration matrix (`m` as a
  `d` by `d` matrix): an operator test with genuinely directional, per-row
  weights (not merely a symmetric square matrix), a test proving the
  matrix path ignores `population_size` even when deme sizes differ by
  three orders of magnitude, a test proving the scalar rate is exactly the
  matrix's equal-size symmetric special case, a reproducible three-deme
  engine run over an asymmetric matrix, and a CLI test running a config
  with `m` as a matrix.
- Dedicated test coverage for per-locus length (`LocusSpec.length`
  varying across a run's `loci`): a params test with genuinely distinct
  lengths per locus rather than one shared value, an engine-level test
  proving the report is bit-identical regardless of which locus carries
  which length (length is inert data — it drives no statistic), a
  reproducible end-to-end run over loci with very different lengths, and
  a CLI test running a config with unequal per-locus lengths.
- Sparse, 1D stepping-stone migration topologies. `m` accepts two new
  compact config forms, both expanding to the existing dense matrix at
  load time: a one-based sparse neighbor map (`{deme: {neighbor: weight,
  ...}, ...}`, self-retention implied) covering any irregular topology
  by hand, and `{topology: ring|linear, rate}` sugar for the two classic
  1D stepping-stone cases. New module `fim.model.topology` provides
  `stepping_stone_neighbors` (generates the sparse map) and
  `dense_matrix_from_neighbors` (validates and densifies any sparse map,
  hand-written or generated) for direct use from Python.
- Dedicated test coverage for stepping-stone migration: exact hand-
  derived matrices for both topologies; confirming every row is
  row-stochastic across a range of deme counts; every documented
  validation rule (non-positive `d`, a ring below 3 demes, an
  out-of-range or self-referencing neighbor, weights summing past 1); a
  params test covering both the sparse-map and topology-sugar config
  forms, including JSON's string-keyed variant and round-tripping; an
  operator-level test proving migration through a ring/linear matrix is
  actually local — an allele
  private to one deme reaches only its direct neighbors after one
  generation, completely absent everywhere else, with the ring's
  wraparound neighbor as the one distinguishing data point from the
  linear chain; a reproducible end-to-end engine run; and a CLI test.
- An opt-in stochastic migrant-count model. A new `migrant_sampling`
  config key (`"continuous"`, the default and the only behavior in every
  prior release, or `"stochastic"`) controls whether each deme's migrant
  count is applied as the exact `rate * N` fraction it always was, or
  drawn fresh every generation from `Binomial(N_i, rate)` — mean
  `N_i * rate`, matching the continuous case in expectation but varying
  generation to generation. Migrant composition is unaffected either
  way: migrants still carry exactly the deterministic, weighted pool
  average, so `drift()` remains the pipeline's only operator that
  resamples every gene copy — the new option adds one random process
  (how many migrate) without duplicating the one already there. Applies
  uniformly to the scalar, matrix, and topology-derived `m` forms.
  `migrate()` gained an optional `rng` parameter; the default
  `"continuous"` path never passes one, so `migrate()`'s deterministic
  arithmetic, iteration order, and every existing exact-value test are
  unchanged, and no run that omits the new key is affected in any way.
- Dedicated test coverage for stochastic migrant-count sampling: a
  requires-`population_size` validation test; fixed-seed statistical
  tests (mirroring `drift`'s own variance-check style) confirming the
  sampled migrant fraction's mean and variance match `Binomial` theory
  for both the scalar and matrix code paths independently; a
  composition-preservation test proving two pool-only alleles always
  arrive in the pool's exact ratio regardless of the random count drawn
  that generation; a self-weight-of-1 edge case proving the stochastic
  and continuous matrix paths agree exactly when there is nothing left
  to sample; a params test for the new key's default, validation, and
  round-trip; two engine-level reproducible-run tests (one exercising
  `"stochastic"` directly, one proving a config that omits the key is
  byte-identical to one that spells out `"continuous"`); and a CLI test.
- An opt-in finite-alleles (K-allele) mutation model. A new
  `mutation_model` config key (`"infinite_alleles"`, the default and the
  only behavior in every prior release, or `"finite_alleles"`) controls
  whether a mutation event always mints a globally novel allele identity,
  or instead targets one of a bounded `4 ** length` states per locus
  (`fim.model.locus.finite_allele_capacity`), landing uniformly on any
  state other than its own current one — including, unlike the
  infinite-alleles model, a state that already exists elsewhere in the
  run (a *recurrence*). This removes an artifact of the infinite-alleles
  assumption at short loci, where treating every mutation as novel
  stops being a good approximation. The new `FiniteAlleleSpace` (one
  bounded state space per locus) and `FiniteAlleleRegistry` (dispatches
  to the right locus's space) never materialize the full state space,
  even when astronomically large: a target is decided as "a specific
  already-minted state" or "any not-yet-minted state" via one float
  probability that underflows cleanly toward `0.0` as capacity grows,
  recovering the infinite-alleles model in that limit without ever
  overflowing a fixed-width integer type. `mutate()` gained an optional
  `finite_alleles` parameter; the default `"infinite_alleles"` path never
  passes one, so `mutate()`'s existing arithmetic, iteration order, and
  every existing exact-value test are unchanged, and no run that omits
  the new key is affected in any way. `SimulationParams` also validates,
  per locus, that every starting allele ID (the founding range, or an
  explicit `p_0`'s specific IDs) fits inside that locus's own capacity.
- Dedicated test coverage for the finite-alleles model: `FiniteAlleleSpace`
  construction-validation tests; a deterministic property test proving a
  small state space's targets never equal their own source and never
  exceed capacity; an exhausted-capacity edge case where every draw must
  be a recurrence; an astronomically large capacity case proving
  recurrence becomes exactly impossible, not merely rare; a
  `@pytest.mark.statistical` test confirming the sampled recurrence rate
  matches theory within a pre-derived five-sigma band; a
  `FiniteAlleleRegistry` dispatch test proving two loci keep independent
  state spaces; `mutate()`-level tests covering per-locus capacity
  enforcement, mass accumulation (rather than overwriting) when two
  mutation events land on the same target, the opt-in contract (omitting
  `finite_alleles` matches passing `None` explicitly), and an end-to-end
  statistical recurrence-rate check through `mutate()`'s own
  source-attribution logic; two engine-level reproducible-run tests (one
  exercising `finite_alleles` directly and confirming the global capacity
  bound holds across an entire run, one proving a config that omits
  `mutation_model` is byte-identical to one that spells out
  `"infinite_alleles"`); and a CLI test.
- Per-locus mutation rates. `mu` generalizes to accept either a scalar
  (shared by every locus, unchanged from every prior release) or an
  explicit list with exactly one rate per locus, mirroring `N`'s own
  scalar-or-per-deme shape. A new `mu_b` config key, mutually exclusive
  with `mu`, derives each locus's own rate from a single per-base-pair
  probability and that locus's own `length`, via the differentiation-
  measures guide's exact Eq. 5 relation `mu = 1 - (1 - mu_b) ** length`
  (not its linear `mu_b * length` approximation). Previously, `mu` was a
  single value applied identically to every locus regardless of length,
  so two loci of very different lengths silently mutated at the same
  rate — an artifact this closes. `mu_b` is config-file sugar, exactly
  like the compact `n_loci`/`locus_lengths` locus form, the migration
  sparse map, and the stepping-stone topology mapping: it always expands
  to an explicit per-locus `mu` at load time, and `to_dict()`/
  `manifest.json` record only the expanded form, never `mu_b` itself. A
  per-locus list that happens to be uniform collapses back to a scalar
  for storage, matching `N`'s own collapsing behavior. `mutate()`'s `mu`
  parameter widens from `float` to accept either shape; every existing
  call site (which always passes a plain scalar) is unaffected, since a
  scalar broadcasts to every locus exactly as it always implicitly did.
- Dedicated test coverage for per-locus mutation rates: params tests for
  an explicit per-locus list, list-to-scalar collapsing when uniform, the
  exact `mu_b` derivation formula, `mu_b`'s own probability validation,
  and `mu`/`mu_b` mutual exclusivity (both given, or neither); `mutate()`
  tests proving a per-locus rate tuple only mutates the locus it belongs
  to and that a uniform tuple matches scalar-broadcast behavior exactly;
  an engine-level test proving a `mu_b` run is byte-identical to the
  equivalent hand-expanded per-locus `mu` list, not just equal in the
  derived rates; an engine-level test proving `mu_b` composes correctly
  with the (separately shipped) `finite_alleles` mutation model; and a
  CLI test.
- Adaptive, confidence-interval-driven replicate batching, and CLI support
  for replicate batches at all. A new opt-in `replicate_tolerance`
  (default: unset, `n_replicates` runs in full exactly as before) stops a
  batch early, once at least `replicate_minimum` replicates exist and
  every statistic named in `convergence_statistic` has an across-replicate
  Student's-t confidence interval (`fim.statistics.interval`, a
  dependency-free table-based critical value with linear interpolation in
  `1/df` and an exact normal-quantile tail) tightened to at most
  `replicate_tolerance` — combined across several watched statistics by
  the same `convergence_combinator` used within one run. `n_replicates`
  becomes the hard cap rather than a fixed count; a new
  `ConfidenceIntervalCriterion` implements the existing
  `ConvergenceCriterion` protocol and plugs into the unmodified
  `ConvergenceMonitor`, so nothing about the monitor itself changed. A new
  `fim.engine.replicate_summary` computes each reported statistic's
  across-replicate confidence interval from a batch of results; `G_ST` is
  dropped from a replicate whose locus is monomorphic across every deme
  (`H_T == 0`) rather than papered over with a substitute value, and a
  statistic left with fewer than two defined replicates is omitted
  entirely. `fim`'s batch loop also gained opt-in parallel execution
  (`max_workers`, real worker processes, not threads, since per-generation
  state is Python-object sparse maps that never release the GIL) and a
  `store_factory` for giving each replicate its own trajectory store —
  required across a process boundary, and available for the ordinary
  sequential loop too. `fim run` no longer rejects `n_replicates` greater
  than one: each replicate gets its own `replicate-NNN/` subdirectory
  keeping the existing four-file scalar-run contract, plus a batch-level
  `summary.json` and `manifest.json`; a new `--workers`/`--sequential`
  flag pair controls execution (parallel, one worker per CPU, by default).
- Dedicated test coverage for adaptive replicate batching: a table-based
  Student's-t critical-value test against published values plus
  interpolated and normal-quantile-tail cases; a confidence-interval test
  against a hand-computed interval and a zero-width identical-sample case;
  `ConfidenceIntervalCriterion` validation and monitor-composition tests;
  params tests for every new field's validation and `to_dict()`
  round-tripping (including that `replicate_tolerance` is omitted unless
  configured); engine tests proving the unset default is unaffected, an
  adaptive stop before the cap, a fallback to the cap when the minimum is
  unreachable, and the `G_ST`-substitution/summary-omission split; a
  parallel-versus-sequential equivalence test, worker-count and
  shared-store-rejection validation tests, a `store_factory` test in both
  execution modes, and a `MappingProxyType` pickle round-trip test (the
  defect that first surfaced this need); and CLI tests for the batch
  directory/summary/manifest contract, the default-parallel and
  explicit-`--workers` paths, adaptive early stopping through the CLI, and
  the non-empty-output-directory guard.

### Changed

- Updated the simulator design document (§9, §12) to record that unequal
  deme sizes, a full migration matrix, and per-locus length all shipped
  in the very first `SimulationParams`/`locus.py` implementation rather
  than remaining deferred, and to point at the new tests and
  `doc/configuration.md` as the supporting evidence and user-facing
  contract.
- Expanded `doc/configuration.md`'s `m` section with a worked asymmetric
  matrix example and two explicit behavioral notes: a matrix's rows are
  authoritative and are never rescaled by `N`, and the scalar rate is the
  matrix's equal-size symmetric special case.
- Expanded `doc/configuration.md`'s `loci` section with a worked example
  of two loci with different lengths and a note that length currently has
  no effect on simulation behavior.
- Updated the simulator design document (§5, §9) and `doc/developer.md`'s
  "Adding a new what-if" table to record that watching several convergence
  statistics is implemented, and to distinguish it from the pre-existing
  `AnyCriterion`/`AllCriterion` combinators (which combine several
  *criteria* over one shared history — a different axis from combining
  several *statistics*, each with their own history).
- Added `doc/configuration.md`'s `convergence_combinator` key and expanded
  `convergence_statistic` to document the list form.
- Updated the simulator design document (§9, §12) and `doc/developer.md`'s
  "Adding a new what-if" table to record that 1D stepping-stone migration
  shipped, narrowing the design document's stepping-stone out-of-scope
  entry to what is genuinely still unbuilt (a 2D lattice topology and a
  `MigrantPoolStrategy` interface for neighbor-selection logic that is
  not reducible to a precomputed matrix).
- Added `doc/configuration.md`'s sparse-map and topology-sugar `m`
  subsection, with worked examples of both compact forms and the
  validation rules each enforces.
- Corrected §9's several-convergence-statistics note, mislabeled "Note on
  the seventh row" when it documents the table's ninth row, and reordered
  all five "shipped" notes to match the table's own row order (they had
  drifted to 1st, 2nd, 4th, 9th, 3rd as each was added across separate
  passes).
- Corrected two rows of §9's extensibility table that had drifted from
  what the code actually does: `n_replicates` batches sequentially
  (`engine.py`'s own run loop), not as a vectorized NumPy array dimension
  as the row previously claimed; and "a different statistic should drive
  convergence" is retitled to be about swapping the convergence *rule*
  (`ConvergenceCriterion`, still unexposed via config — only
  `TrailingWindowCriterion` exists and `engine.py` hardcodes it) rather
  than about choosing which statistic to watch, which has always been
  free via `convergence_statistic` and predates every row in this table.
- Renamed the five pre-production, dated/model-tagged design documents to
  clean, user-facing names: `finite-island-model-introduction.md`,
  `jost-differentiation-measures.md`, `fim-simulator-design.md`,
  `fim-simulator-detailed-design.md`, and `fim-simulator-test-plan.md`
  (each previously prefixed with its generation date and model, e.g.
  `20260814-claude-sonnet-5-fim-simulator-design.md`). Also renamed
  `doc/img/20260814-fim-simulator-design/` to
  `doc/img/fim-simulator-design/` to match, and updated every internal
  and external cross-reference (`README.md`, `CONTRIBUTING.md`,
  `doc/developer.md`, and the five documents' own links to each other).
- Removed development-history narration from `doc/fim-simulator-design.md`,
  `doc/fim-simulator-detailed-design.md`, `doc/fim-simulator-test-plan.md`,
  `doc/configuration.md`, and `doc/developer.md`, now that every "what if"
  extension the design document originally deferred (per-deme island
  sizes, an asymmetric migration matrix, per-locus length, several
  convergence statistics, 1D stepping-stone topology) has shipped and is
  documented as current behavior elsewhere in the same document set.
  Deleted `fim-simulator-design.md`'s "Open questions requiring a
  decision" section (a resolved drafting-history log) and
  `fim-simulator-detailed-design.md`'s commit-by-commit milestone plan and
  "definition of done" section (both describing work that is complete);
  folded the milestone plan's still-relevant documentation-navigation
  content into a new engineering-reference subsection instead of
  discarding it. Stripped "ships with commit N.N" cross-references
  throughout the test plan, "(mocked)"/"initial pass"/"this pass" framing
  from the design document, and "fully supported in version 1.0.0"
  defensive phrasing from the configuration reference, keeping only the
  one genuinely version-scoped limitation (`n_replicates` on the CLI).
  Replaced the design document's fabricated walkthrough transcript with
  real, reproducible `fim run` console output, `report.json`, and
  `trajectory.jsonl` captured from an actual run of the document's own
  example scenario. Renumbered sections and fixed every resulting
  in-document and cross-document section reference; `usage.md`,
  `finite-island-model-introduction.md`, and
  `jost-differentiation-measures.md` needed no changes.
- Reviewed the whole documentation set for completeness, currency,
  language, and fit to its stated audience. The test plan gained the
  coverage it had no entry for — `fim.statistics.interval`,
  `ConfidenceIntervalCriterion`, the replicate-batch configuration keys,
  adaptive stopping, parallel replicate execution, and the batch artifact
  contract — and lost the stale claim that the CLI rejects a
  multi-replicate configuration. The engineering reference now describes
  `pre-push` as the working-tree gate it is rather than a check of the
  pushed commits, records the repository-file checks as digest-pinned
  Docker wrappers driven by `dev/bin/validate-repository` rather than a
  nonexistent markdownlint GitHub action, lists `bin/`,
  `include/dot-bashrc`, and the remaining `dev/bin` programs in its
  repository layout, and documents the deliberate `pyparsing` pin. The
  design document gained `statistics/interval.py` and the replicate-batch
  layer, split its extensibility table into variations reachable from
  configuration and landing spots for changes that are not built, moved
  the graphical front-end sketches into an appendix, and had its
  walkthrough transcript's run identity corrected against a re-run of the
  documented scenario. Remaining version-anchored and
  development-relative phrasing was removed from `doc/usage.md`,
  `doc/configuration.md`, `doc/developer.md`, and `src/README.md`, along
  with an implementation aside about worker processes and the interpreter
  lock that did not belong in a user guide. Each design document now
  records this review in a `Revisions` subsection beneath its original
  generation metadata.

## [1.0.0] - 2026-08-16

### Added

- Deterministic finite island model simulation with migration, infinite-alleles
  mutation, genetic drift, and configurable convergence monitoring.
- Incremental JSON Lines persistence for every generation, replayable manifests,
  final differentiation reports, and headless visualizations.
- Command-line workflows for initialization, simulation, persisted-trajectory
  analysis, version reporting, and opt-in update checks.
- Python library API, deterministic tests, local quality gates, release
  packaging, and user and maintainer documentation.

### Fixed

- Moved default starter configurations and run artifacts from the user
  `Documents` directory to the portable, space-free `project-root/results/`
  directory.
- Added repository-local Python, Ruff, mypy, pytest, API-documentation, and
  PyInstaller wrappers so `./build --ci` works without activating a virtual
  environment.
- Made the Git-hook installer portable to native macOS utilities.
- Constrained Pyparsing to the warning-free line supported by Matplotlib 3.9.
- Made API generation enumerate every source module explicitly so Linux and
  macOS editable installs produce identical documentation.

[Unreleased]: https://github.com/selby-botany/jost-finite-island-model/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/selby-botany/jost-finite-island-model/releases/tag/v1.0.0
