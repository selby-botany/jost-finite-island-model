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

## Engine backends

The pipeline above (Migrate → Mutate → Drift) is what gets computed;
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
  `Advancer`: converts each replicate's own state to a dense
  `(deme, allele)` NumPy array once per generation and runs a fused
  `migrate`/`mutate`/`drift` on that array instead of one Python-level
  operator call per deme (`fim.model.vectorized`). Raises `ValueError`
  immediately for any config outside `mutation_model="finite_alleles"`
  and `migrant_sampling="continuous"` together — there is no silent
  fallback path.
  Requires `numba` unconditionally (its own JIT-batched multinomial
  decomposition is what makes it competitive at all, unlike Backend
  L/G's optional, genuinely-optional `jit="numba"`) — see
  `pyproject.toml`'s own `jit` extra comment for the two-different-
  import-paths distinction this cost a real CI outage to get right
  (below).

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
  `"generational-vector"` is bit-identical to both *only* when migration
  is off (`m: 0`); with migration active it matches them statistically
  (no directional bias, confirmed) rather than row-for-row, because its
  own dense-matrix migration blend is a different, equally valid
  floating-point reduction order than the dict-based backends' own
  arithmetic. Never assume "same seed" alone is enough to reproduce an
  archived `"generational-vector"` trajectory exactly if that run used
  nonzero migration — check `manifest.engine_backend` first. The
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
the scientific proof. Every Backend-V-only test (`fim.model.vectorized`,
`VectorizedAdvancer`) starts with `pytest.importorskip("numba")` rather
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
