# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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
