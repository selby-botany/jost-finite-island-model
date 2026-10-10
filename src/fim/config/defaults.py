"""Default values of regular settings.

The value a setting takes when a configuration leaves it out. Each is a
regular setting (or an Expert Setting) a user can change; the default is a
policy choice recorded here with its reason.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

DEFAULT_LOCUS_LENGTH: Final = 200
"""`DEFAULT_LOCUS_LENGTH`.

Kind: policy; a regular setting.
"""

DEFAULT_AUTO_VECTOR_MIN_D: Final = 2
"""`"auto"`'s own default deme-count cutover, below which it never picks
`"generational-vector"` even when the config is otherwise eligible for it.

Lives here, not in `fim.engine`, because it is a `SimulationParams` field
default like any other (`replicate_minimum`'s own `10`, for one) —
`fim.engine` imports it from here rather than the other way around,
matching this project's own one-directional dependency rule (the engine
depends on the model; the model depends on nothing in the engine).

Measured, not guessed — the generation-first design's own Stage 4/vector
design's own Stage V3 deme-axis sweep found Backend V crosses over from
slower than Backend L to clearly faster somewhere between `d=30` and
`d=40` on the primary benchmarking machine. **This default has not been
re-measured since a later correctness fix
(`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §10
Stage F8) changed the underlying performance picture materially — a
2026-09-02 re-measurement (`dev/bin/benchmark-engines`) found
`"generational-vector"` already ahead at `d=4`, the smallest value
tested, not just past this threshold.** Kept at `35` rather than changed
alongside that finding: altering a shipped default needs its own
deliberate confirmation, not a silent edit. `auto_vector_min_d` stays a
caller-supplied `SimulationParams` field for exactly this kind of
drift — see `dev/bin/benchmark-engines --sweep d` to re-characterize it
on any given machine.

**Re-measured again 2026-09-05 (`FIM-52`, Phase 7 item 6,
`20260904-claude-sonnet-5-fim-engine-review-remediations.md`) — on
different, native hardware this time (`citrus-2`, Intel Core Ultra 9
185H, x86_64 Linux, not this project's own Apple Silicon development
machine), and after every Phase 1-7 correctness/performance fix, not
just Stage F8: `dev/bin/benchmark-engines --sweep d --values
2,4,8,16,25,35,50,70,100,150,250 --replicates 16 --generations 100
--trials 5` found `"generational-vector"` fastest at *every* tested
`d`, from `2` (the smallest value `SimulationParams` accepts at all)
through `250` — never losing even once, and never approaching a
crossover from below. Its own margin over `"generational"` with
`jit="numba"` (the closest competitor at every point) shrinks as `d`
grows (from roughly 2x at `d=2` to roughly 6x at `d=250`, both favoring
V) but never comes close to reversing. This confirms, on a second,
independent, materially different machine, that `35` is not merely
stale but has never been correct against any post-Stage-F8 build of
this codebase — every tested value below it would have been routed to
the slower engine by `"auto"`.

**Changed 2026-09-05, `35` -> `2` (the floor `SimulationParams.d`
accepts at all)**, after a further, joint `d` x locus-length heatmap
(`dev/bin/generate-heatmap-queue`/`benchmark-queue`, 104 points, `d` in
`{2,4,8,...,500}` x locus length `1`-`8`, `citrus-2`, run explicitly to
check whether this axis and `auto_vector_max_capacity`'s own axis
interact before changing either value — see that constant's own
docstring for why a single-axis result alone was not enough to trust).
That joint sweep found no `d`, at any capacity up to and including
`4096` (locus length `6`), where `"generational-vector"` loses — the
`d`-axis crossover this constant thresholds simply does not exist
inside the region `auto_vector_max_capacity` now admits, so gating on
`d` at all, within that region, only ever excludes configurations V
would have won. (Above capacity `4096`, a real, narrower `d`-dependent
region does exist — see `auto_vector_max_capacity`'s own docstring —
but a single scalar `auto_vector_min_d` cannot express "conditional on
capacity" at all, so lowering this threshold to `2` is what the data
supports regardless: the region where a *higher* `min_d` would help is
already excluded by `max_capacity`, and everywhere `max_capacity`
admits, no `min_d` value was ever justified by real evidence.)

Kind: policy; already an Expert Setting.
"""

DEFAULT_AUTO_VECTOR_MAX_CAPACITY: Final = 4096
"""`"auto"`'s own default per-locus capacity ceiling for `"generational-
vector"` — above it, `"auto"` picks `"generational"` instead, regardless
of `d`/`auto_vector_min_d`.

Applies under `mutation_model="finite_alleles"` only: a finite-alleles
table is `capacity` columns wide however few states are in use. An
infinite-alleles table is as wide as the alleles alive at once, so `"auto"`
does not read this ceiling there (the measurements below were all
finite-alleles).

Closes a real, previously-unaddressed gap: `"auto"`'s own resolution
used to read `params.d` alone, never any locus's own capacity
(`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §10
item 10b — "a large-`d`, large-capacity config could pick the wrong
engine"). Measured, not guessed, the same way `auto_vector_min_d`
itself was: that same document's own loci-length sweep found
`"generational-vector"` winning through capacity `1024` (locus length
`2`-`5`) and losing to `"generational"` + `jit="numba"` at capacity
`4096` (length `6`, `71.3s` vs `92.4s`) — the array-native path touches
every cell of a locus's own `(d, capacity)` grid every generation
regardless of how much of it is actually occupied, where the dict-based
backends only ever touch what is present. `1024`, not a value strictly
between the two, because a real capacity is always `4 ** length` for
some integer `length` — there is no config that could ever land between
`1024` and `4096`, so the boundary sits exactly at the last *tested,
winning* value rather than an interpolated one nothing could reach
anyway. Applies to the largest capacity across every locus in `params.
loci` — one large-capacity locus already pays this cost even if every
other locus in the same run is small, the same "one disqualifying
property anywhere disqualifies the whole choice" logic `mutation_model`/
`migrant_sampling` eligibility already uses. Not yet re-measured on
different hardware, and not yet re-measured against the same-day
`ThreadedAdvancer`/`migrate_vectorized` fixes that already made
`auto_vector_min_d`'s own default doubly stale — see that constant's
own docstring for the precedent this one inherits, and `dev/bin/
benchmark-engines --sweep loci-length` to re-characterize it.

**Re-measured 2026-09-05 (`FIM-52`, Phase 7 item 6,
`20260904-claude-sonnet-5-fim-engine-review-remediations.md`) — on
`citrus-2` (Intel Core Ultra 9 185H, x86_64 Linux), after every Phase
1-7 fix: `dev/bin/benchmark-engines --sweep loci-length --values
1,2,3,4,5,6,7,8 --replicates 8 --generations 50 --trials 3` found the
crossover has moved, not merely shifted within noise — `"generational-
vector"` now wins through capacity `4096` (locus length `6`, `1.942s`
vs `"generational"` + `jit="numba"`'s `5.152s` — V faster, reversing
the earlier `71.3s` vs `92.4s` finding at this same capacity), and
loses starting at capacity `16384` (locus length `7`, `7.333s` vs
`5.077s`), with the gap widening sharply by capacity `65536` (length
`8`: `28.158s` vs `5.547s`, V now the slower engine by roughly `5x`).
The likely mechanism: several of the same Phase 7 fixes measured
against `d` above (`FIM-53`/`FIM-54`/`FIM-27`/`FIM-28`) reduce V's own
per-generation cost in ways that scale with capacity specifically
(fewer full `(d, capacity)`-shaped temporaries, fewer full-array
copies) — exactly the dimension this constant thresholds, so a
capacity-sensitive fix category moving this specific crossover, while
leaving `auto_vector_min_d`'s own `d`-axis crossover unmoved (still no
reversal found at any tested `d`, see that constant's own docstring),
is the expected shape of the result, not a surprising one. `1024`
significantly understates what current code can actually do — real
data now supports `4096`, still not an interpolated value (capacity is
always `4 ** length`; nothing could land between `4096` and `16384`
either).

**Changed 2026-09-05, `1024` -> `4096`**, confirmed by the same joint
`d` x locus-length heatmap `auto_vector_min_d`'s own docstring
describes (104 points, `citrus-2`, run specifically to check whether
this axis and `auto_vector_min_d`'s own axis interact before changing
either): `"generational-vector"` won at *every* tested `d` (`2` through
`500`) at capacity `4096` — the single-axis result above already found
this at one fixed `d`; the joint sweep confirms it holds at every `d`
this project has ever benchmarked, not only that one. The real,
`d`-dependent losing region the joint sweep also found (capacity
`16384`: G-jit wins for `16 <= d <= 70`, V regains the lead at `d >=
100`; capacity `65536`: G-jit wins through `d=250`, V only recovers at
`d >= 350`) is exactly the "diagonal boundary" shape a single pair of
independent scalar thresholds cannot express at any choice of values —
raising `auto_vector_max_capacity` to `16384` to chase that region's
own large-`d` recovery would require also raising `auto_vector_min_d`
high enough to exclude its own losing sub-region, which would then
incorrectly exclude every small-`d` configuration at capacity `<=
4096` that the data shows V winning unconditionally. `4096` is
therefore not a compromise pending a future fix — it is the largest
capacity at which a single threshold, paired with any `auto_vector_
min_d`, can never misroute a config to the slower engine, given every
point this project has actually measured. Capacities above it are
correctly left to `"generational"` by `"auto"`, even in the sub-regions
above `d=100`/`d=350` where V would actually win — expressing a
diagonal boundary correctly needs a resolution rule that reads both
`d` and capacity jointly, not two independent thresholds; that is a
real design question of its own, not a parameter tweak, and remains
open (see `doc/fim-simulator-design.md` §B.5's own conclusion, which
reached the identical judgment from the pre-Phase-7 data this session's
own joint sweep superseded).

Kind: policy; already an Expert Setting.
"""

DEFAULT_N_REPLICATES: Final = 200
"""How many independently seeded replicates a run tries by default.

Not `1` — the most useful ordinary use of this tool is a measurement
*with* a confidence interval (`precision`, below), not a
single point estimate, so that is what an unconfigured run now does by
default: run up to `DEFAULT_N_REPLICATES` replicates, stopping early
once `DEFAULT_PRECISION` is reached. `200` is a generous cap,
not an expectation of always reaching it — chosen to match this
project's own worked examples and test scenarios that already use a
comparable count for a real confidence interval, giving the adaptive
stop (`replicate_minimum` onward) real room to tighten before the cap
would ever bind. A caller who wants the old single-run behavior back
sets `n_replicates: 1` explicitly, same as always; nothing about what an
explicit `n_replicates` means has changed, only what an *absent* one now
means.

Kind: policy; a regular setting.
"""

DEFAULT_PRECISION: Final = 0.01
"""Default `precision`: plus or minus this much, in each statistic's units.

One number answers "how precise?" for a single run and a batch alike: a
run averages over time until its mean is known to within this at the
configured confidence, and a batch adds replicates until the interval
across replicates is this narrow. `0.01` is the default both of the old
within-run tolerance and of the old replicate tolerance, which this
setting merged. Paired with `DEFAULT_N_REPLICATES` above, it makes an
unconfigured run compute a real confidence interval by default rather
than a single, uncertainty-free-looking point estimate.

Kind: policy; a regular setting.
"""

DEFAULT_PAIRWISE_MAX_DEMES: Final = 1024
"""Largest deme count whose full all-pairs matrices are saved by default.

At d = 1024 one run's `pairwise.json` is about 52 MB (five matrices of
523,776 values) and takes about a second to compute and write. Above the
limit the file records only that the matrices were skipped; any specific
pair can still be recomputed from the saved trajectory. A researcher can raise or
lower the limit in Settings or with `fim run --pairwise-max-demes`.

Kind: policy; a regular setting.
"""
