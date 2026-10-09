# Developer and extension guide

This guide is for a maintainer extending the simulator without relying on
institutional knowledge. Start with the [project overview](../README.md), then
use the [generated API reference](../src/fim/API.md) for exact signatures.

## Contents

- [Architecture](#architecture)
- [Build environment](#build-environment)
- [Generation pipeline](#generation-pipeline)
- [Engine backends](#engine-backends)
- [Determinism](#determinism)
- [Persistence and reports](#persistence-and-reports)
- [Adding a statistic](#adding-a-statistic)
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
   with its locus's own probability (`mu`: a shared scalar, an explicit
   per-locus list, or one derived per locus from a per-base rate,
   μ<sub>b</sub>), so an allele carried by `n` copies loses
   `Binomial(n, mu)` of them. By default (mutation_model:
   infinite_alleles) each mutant copy receives a globally novel ID; under
   the opt-in finite_alleles model, its target is drawn uniformly from the
   other states of its own locus's bounded state space and can recur.

This is the textbook Wright-Fisher island model: two gene copies of the
next generation are identical only if their parental copies were and
neither mutated, so the identity recursions behind the closed-form
trajectories (`fim.statistics.identity_recursion`) and the
equilibrium-split burn-in (`fim.convergence.defaults.
panmictic_equilibration`) carry the factor `(1 − μ)²`. Releases before
this order was adopted ran Migrate → Mutate → Drift with a mutation step
that scaled every allele's frequency down in proportion to a deme-wide
event count; that added an `O(μ/N)` excess identity each generation and
was not the textbook model.

Every operator receives all changing inputs explicitly and returns a new
`ModelState`. `ModelState` enforces one normalized sparse frequency map per
deme/locus.

## Engine backends

The pipeline above (Migrate → Drift → Mutate) is what gets computed;
`fim.engine` offers three implementations of *how* it gets driven,
behind one shared `EngineBackend` protocol (`run(params, store, run_id,
clock) -> RunResult | tuple[RunResult, ...]`), selected via `fim()`'s
own `engine_backend` keyword (`build_engine_backend`, `fim.engine`). See
the [simulator design's own §4.6](fim-simulator-design.md#46-choosing-an-engine-backend)
for the user-facing "what/why/how" version of this; this section is the
implementation-level view, plus what building it actually cost.

- **`LinealBackend`** (`"lineal"`, the default). Replica-first: one
  replicate runs to completion (or the configured process pool runs
  several in parallel — `max_workers`) before statistics/reports get
  assembled. Permanently unmodified as the golden reference every other
  backend's own output is checked against — see
  [Determinism](#determinism) below.
- **`GenerationalBackend`** (`"generational"`/`"generational-vector"`,
  both share this one class). Generation-first instead: every
  still-active replicate in a batch (`ReplicaLane`) advances by exactly
  one generation before any of them moves to the next, via a pluggable
  `Advancer`. `SequentialAdvancer` does this with no new concurrency (a
  pure reshuffle, still bit-identical to `LinealBackend`);
  `ThreadedAdvancer` fans the same per-generation work out across real
  threads (`ThreadPoolExecutor`, one pool per generation tick); this
  project's own factory only ever reaches `ThreadedAdvancer` through
  `"generational"` — building `GenerationalBackend(SequentialAdvancer())`
  directly is possible but not exposed as its own `engine_backend`
  string, since it buys nothing `"lineal"` does not already give a
  caller who just wants the reference behavior.
- **`VectorizedAdvancer`** (`"generational-vector"`). A third
  `Advancer`: keeps each replicate's state in one dense NumPy table
  (`fim.model.vector_block.VectorBlock`: `loci x demes x width`
  frequencies and counts, an allele-id array per locus) and advances a
  whole generation, every locus, with one compiled kernel call
  (`fim.model.vector_kernels.run_generation`) instead of one Python-level
  operator call per deme. Both mutation models run on it; stochastic
  migrant counts do not yet (`ValueError`, with a message pointing to
  `lineal`/`generational`; the kernel's migration step is already split
  into a draw phase and a blend phase for that follow-up). Under infinite
  alleles the table's columns are the alleles alive now, kept in
  ascending id order, appended for each new mutant, compacted when
  extinct, grown by doubling and shrunk after a long quiet stretch, with
  a per-replicate memory ceiling that fails early with the remedies
  named. Requires `numba` unconditionally — see `pyproject.toml`'s own
  `jit` extra comment for the two-different-import-paths distinction
  this cost a real CI outage to get right (below); the kernel module is
  imported lazily, so `import fim` never needs it. `fim.model.vectorized`
  is the earlier per-locus implementation, superseded and no longer
  called by the engine.

**What keeps Backend V bit-identical to L and G — the rules a change to
`fim.model.operators` must not silently break.** Each is checked by exact
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
4. Drift normalization with NumPy's own pairwise `ndarray.sum()` over the
   present probabilities (ported to the kernel).
5. The same inversion binomial, one uniform per real draw.
6. Infinite alleles: identities handed out deme-major, locus-minor from
   one counter; finite alleles: the K-allele target draws of
   `FiniteAlleleSpace`, interleaved with the next pair's counts.

A change to `operators` that alters any of these (a summation order, a
clamp, a normalization, a draw order) makes the parity tests fail at the
first generation, which is the intent. The statistics `D`, `G_ST`, `H_S`,
`H_T` and `H_ST` are computed in the kernel and must equal
`statistics_report`'s bits for the same reason (a different last bit can,
rarely, move a convergence stop).

**A cross-backend RNG-unification story worth knowing before touching
any of this code.** For a long stretch of this feature's own
development, `"generational-vector"`'s output only matched the other
two backends *statistically* (same distribution, not the same
trajectory) — an accepted, documented gap, until a direct instruction
to make it exact changed that. Getting there took a genuinely new
primitive, `fim.model.operators._inversion_binomial` (mode-anchored
inverse-CDF sampling, exactly one `rng.random()` per draw, replacing
NumPy's own opaque-draw-count `rng.binomial()`), because two
independent implementations cannot consume the same seed's own
random-number stream identically unless each draw's own cost in
"how many uniforms did that consume" is fixed and known in advance —
`rng.binomial()`'s own internal algorithm choice is not. Two real,
data-losing bugs were found and fixed *while building that primitive
alone*, before it ever reached `drift`: a first draft anchored at
`k=0` and underflowed to a literal `0.0` for `n` in the thousands
(this project's own ordinary deme population sizes), silently wrong
100% of the time; a second draft conflated a point mass with a
cumulative probability, wrong across nearly the whole `n`/`p` range.
Both were caught by a test that actually failed, not by inspection —
the general lesson this whole story keeps re-teaching.

**The more expensive lesson: a per-operator exact-match test suite is
not the same thing as an exact-match *run*.** Every operator
(`migrate`, `mutate`, `drift`) eventually passed its own isolated
exact-match test against the dict-based backends — and a full,
real, multi-generation batch still did not match, because
`build_vectorized_state` re-derived Backend V's own finite-alleles
"which allele IDs have ever existed" bookkeeping from scratch every
generation, using only whichever IDs were currently present. That
silently forgets any allele that went extinct in the very generation
it was minted — the *ordinary* fate of a fresh low-frequency mutant
under drift, not a rare edge case — letting Backend V re-mint an
identity `LinealBackend`'s own registry had already permanently
retired. No isolated single-call test could have caught this, by
construction: each one started from a manually built common state
rather than a real, round-tripping, generation-to-generation driving
loop. Fixed by carrying that bookkeeping forward across generations
explicitly (`ReplicaLane.vectorized_locus_states`,
`build_vectorized_state`'s own `previous_locus_states` parameter) —
found and fixed only because a full run was actually executed and
checked end to end, not assumed correct from the per-operator proofs
alone. **If you extend or refactor any of `fim.model.vectorized`,
re-run a full multi-generation batch against `LinealBackend` before
trusting a change — an isolated operator test is necessary, and has
already been proven not sufficient.**

**A real, currently-unaddressed regression, found by profiling rather
than assumed away.** The RNG-unification work above made `drift`
bit-identical across backends at a real, measured cost:
`_inversion_binomial` is pure Python, and profiling a `"generational"`
run (`cProfile`, `dev/bin/benchmark-engines`) found it now dominates
`drift`'s own wall-clock time and holds the GIL for essentially all of
it — where NumPy's own C-level `rng.binomial()` used to spend at least
some of that time with the GIL released. The measurable consequence: a
thread-count scaling sweep at this project's own reference scale
(`d=60`) found `ThreadedAdvancer` delivers no real speedup at any
thread count from 1 to 14 today, flat to actively worse than one
thread past 4-6 threads — a real regression against a benchmark this
project had already recorded, confirmed by checking git commit
timestamps rather than assumed coincidental (the earlier, better
number was measured before the RNG-unification commits landed, the
same day). `jit="numba"` helps the single-thread case for real but does
not restore thread scaling, because `migrate`'s/`mutate`'s own RNG
calls stay unjitted and GIL-bound regardless of that setting — already
correctly documented on `ThreadedAdvancer.__init__`'s own docstring, now
backed by a direct measurement rather than inference. **Not fixed as
part of this profiling pass** — a real fix needs a `nogil=True`-
compiled, bit-identical replacement for `migrate`'s/`mutate`'s own RNG
calls too (the same pattern `_inversion_binomial`'s own nested-closure
JIT wiring already establishes for `drift`), and/or caching
`ThreadedAdvancer`'s own `ThreadPoolExecutor` across generation ticks
instead of rebuilding it every one (found by reading the code this
profiling pointed at — a real, separate cost, not yet isolated from the
GIL-contention cost above). If you pick this up: re-run
`dev/bin/benchmark-engines --sweep d` before and after, the same way
every other performance claim in this codebase is checked, not
reasoned about.

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
- **Which `engine_backend` drives a run is itself a determinism axis, not
  an orthogonal performance-only knob** ([Engine backends](#engine-backends)
  above has the full story). `"lineal"` and `"generational"` are
  bit-identical for the same seed, always, by construction — different
  execution order over the identical dict-based arithmetic.
  `"generational-vector"` is bit-identical to both too, for the same
  seed *on the same machine*, under either mutation model, with or
  without migration, for any number of loci (the rules above). Its one
  limit is the compiled mathematics: Numba's `lgamma`, `log`, `log1p`
  and `exp` are verified against CPython's only on the development
  platform, the same caveat `jit="numba"` carries, so across machines
  results agree statistically, not row for row. (Before the compiled
  kernel it matched only statistically, for two reasons — migration by a
  BLAS matrix product, and a locus-major draw order — both gone.) Check
  `manifest.engine_backend` to know which engine produced an archived
  run. The
  [simulator design's own §4.6](fim-simulator-design.md#46-choosing-an-engine-backend)
  has the equation-level explanation of why (the same weighted-blend
  formula, two different summation orders, occasionally landing on
  opposite sides of one of drift's own discrete decision boundaries) —
  written for a scientist audience, not a maintainer one; worth pointing
  a collaborator there directly rather than re-deriving it in a reply.

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

**The default store is the binary log.** `BinaryLogStore` writes
`trajectory.tlog` (and `equilibrium_trajectory.tlog`): a chained-checksum
block log with sparse delta records, written by a background thread, read
back with random access (`LogReader`), exported to the canonical
`trajectory.jsonl` on request (`fim export`), and resumable from a
checkpoint. [The trajectory log](trajectory-log.md) describes the format, the
modules, the determinism rules and the tests. The JSON Lines backend
(`JSONLTrajectoryStore`) remains for library use and as the test oracle for
the export: it flushes each generation so an interrupted file retains every
complete line.

`JSONLTrajectoryStore` keeps one append handle open between generations
(opened on the first write) instead of re-opening the file for every
generation, which costs several milliseconds on some filesystems, more than
encoding a generation of a small model. Each `write_generation` call still
flushes, so "on disk once the call returns" holds at the operating-system
level and a reader (`read`, the live GUI view) sees every flushed generation
while the run is in progress. The lifecycle rules:

- `close()` (also the `with` form) releases the handle and the ancestral-phase
  companion's. It is idempotent and never ends the store's life: the next
  write re-opens the file in append mode, and `read`/`discard` do not need
  the handle. `close` is optional on the protocol (`ClosableStore`,
  `fim.persistence.store.close_store`); `InMemoryTrajectoryStore` has a
  no-op one.
- The engine closes what it writes: `fim.engine._run_one` closes the store
  when a replicate ends (or raises), `ReplicateFanoutStore.close_run` closes
  a generational lane's store the moment it finishes (a batch holds open
  only its running lanes, so a large batch cannot exhaust file descriptors),
  and `GenerationalBackend` closes the rest at the end. The scalar run owners
  (`fim run`, the GUI runner) also close in a `with`/`closing` block.
- Close before the directory is renamed or removed. A run directory is built
  in a hidden sibling and renamed into place by `fim.paths.atomic_directory`;
  on POSIX an open handle follows the file through the rename, but Windows
  refuses to rename or delete a directory holding an open file. Any new owner
  of a store inside an `atomic_directory` block must close it before the
  block ends.
- A pickled store (a `RunResult.store` returned from a worker process) is
  sent without its handle and re-opens lazily in append mode.
- `discard(run_id)` closes the handle before it rewrites or removes the file;
  a file deleted from under an open handle is re-created by the next write.
- Tests prove a real entry point leaves nothing open with
  `conftest.tracked_jsonl_stores` and `assert_none_open`.

Rows are encoded by `fim.persistence.jsonl_store.encode_rows`, not by a
`json.dumps` call per row (about 30 microseconds per locus generation at 400
loci, against about 5). For a row with exactly the six trajectory keys, plain
`int` ids and a finite `float` frequency it builds the line directly, keys
already sorted, using the pieces `json` itself uses (`int.__repr__`,
`float.__repr__` and `json.encoder.encode_basestring_ascii`). Any other row, or
a non-finite frequency, goes through `json.dumps(row, sort_keys=True,
separators=(",", ":"), allow_nan=False)`, so the file and every error are
unchanged. If the row schema changes (a field, a key, a type), update
`encode_rows` or its fast path will quietly stop applying; the byte-identity
tests in `test/persistence/test_jsonl_encoder.py` (a property test, a seeded
bulk comparison, and the whole row streams of real runs) fail on any
difference from `json.dumps`.

A replicate batch needs one store *per replicate*, not one shared instance —
mandatory once max_workers is set, since a single store object cannot
cross a worker-process boundary. `fim`'s store_factory builds one given a
replicate's run_id; it must itself be picklable under max_workers (a
module-level function, or `functools.partial` over one — never a closure or
lambda), which is exactly how the CLI wires each replicate to its own real
`replicate-NNN/trajectory.tlog`.

Statistics are computed per locus, then arithmetic-mean aggregated in the final
report. Keep locus-specific analysis in pure statistics functions rather than
adding engine state.

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
the scientific proof. Every Backend-V-only test (`fim.model.vector_kernels`,
`fim.model.vector_block`, `VectorizedAdvancer`) starts with `pytest.importorskip("numba")` rather
than assuming it is installed, so `.[dev]` alone still runs cleanly —
those tests skip instead of failing. CI's own coverage-gated job installs
`.[dev,jit]` specifically, not `.[dev]`: skipped tests contribute no
coverage, and Backend V's own code is too large a share of `src/fim` for
the 90 percent gate to pass without it actually running (a real
regression this project found and fixed once already — reproduce the
gate locally with `.[dev,jit]` installed, not `.[dev]` alone, or you will
see a coverage number CI does not).

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
