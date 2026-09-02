# Maintainer scripts

- [Maintainer scripts](#maintainer-scripts)
  - [Why generated files exist at all](#why-generated-files-exist-at-all)
  - [At a glance](#at-a-glance)
  - [`benchmark-engines`](#benchmark-engines)
  - [`calibrate-statistical-bands`](#calibrate-statistical-bands)
  - [`check-doc-links`](#check-doc-links)
  - [`compare-against-hierfstat`](#compare-against-hierfstat)
  - [`extract-release-notes`](#extract-release-notes)
  - [`generate-api-docs`](#generate-api-docs)
  - [`generate-help-html`](#generate-help-html)
  - [`validate-repository`](#validate-repository)
  - [Related documents](#related-documents)

These seven commands keep the project trustworthy: they make sure the
documentation you read matches the code that actually runs, that a
release's own history is recorded accurately, and that no credential or
badly formed file ever gets committed. None of them run a simulation --
that is what [`bin/fim` and `bin/fim-gui`](../../README.md) are for. If
you only ever want to *run* the model, you can stop reading here; you
will probably never need this page.

You will need it if you become the one keeping this project working --
this project is written to be handed to a successor with no other
contact available (see the [maintainer runbook](../../CONTRIBUTING.md)),
and you do not need a software background to use the commands below.
Each one prints a plain-language explanation of what it does and why if
you run it with `--help`, and every explanation here uses the same
words: no assumed familiarity with Git, continuous integration, or
Python packaging beyond what is spelled out as it comes up.

## Why generated files exist at all

Several of the ideas below only make sense once one fact is clear: a few
files in this repository -- `src/fim/API.md` and the two files under
`src/fim/gui/webui/help/` -- are **generated**, not written by hand.
Someone (a maintainer, or the desktop app's own Help screen) reads them
as plain text, but the actual words in them are produced automatically
from other, more authoritative sources: Python docstrings for the API
reference, and the `doc/usage.md`/`doc/configuration.md` guides for the
in-app help. The generated file is committed to the repository like any
other file, so it is easy to browse without running anything -- but it
is only ever *updated* by re-running the script that produced it, never
by opening it and typing.

This matters because it means "the documentation is wrong" almost never
means "go fix the file that shows the wrong text." It means "the real
source changed and nobody re-ran the generator" -- and the fix is to run
the generator, not to hand-edit words that will just be overwritten (and
silently drift further from the truth) the next time someone does
remember to run it. Two of the commands below (`generate-api-docs`,
`generate-help-html`) are exactly this kind of generator; most of the
project's ordinary editing hooks already run them for you automatically,
so you mainly need to know they exist for the rare time one needs to be
run by hand.

## At a glance

| Command | What it does |
|---|---|
| [`benchmark-engines`](#benchmark-engines) | Times how long each `engine_backend` choice takes as one setting (deme count, population size, mutation/migration rate, locus length) sweeps across a range, so a choice between them can be made from evidence instead of a guess |
| [`calibrate-statistical-bands`](#calibrate-statistical-bands) | Re-measures how much random variation is normal for the three published-science validation scenarios, so the tests that check the simulator against them use an honest, evidence-based tolerance |
| [`check-doc-links`](#check-doc-links) | Confirms every link between documentation pages actually goes somewhere, and that no page is orphaned with nothing linking to it |
| [`compare-against-hierfstat`](#compare-against-hierfstat) | Runs an independently-written simulator (hierfstat, an R package, inside Docker) alongside this project's own, and prints how closely the two agree |
| [`extract-release-notes`](#extract-release-notes) | Pulls one version's own section out of `CHANGELOG.md`, for GitHub's release page |
| [`generate-api-docs`](#generate-api-docs) | Rebuilds the generated API reference (`src/fim/API.md`) from the code's own docstrings |
| [`generate-help-html`](#generate-help-html) | Rebuilds the desktop app's in-app Help screen content from `doc/usage.md`/`doc/configuration.md` |
| [`validate-repository`](#validate-repository) | Runs every repository-hygiene checker (shell scripts, YAML, Markdown, leaked secrets) over the whole checkout |

Every command supports `-h`/`--help` for the same explanation you are
reading now, plus its exact command-line syntax.

## `benchmark-engines`

**What it does:** Runs the same simulation several times over, at
several different values of one setting you choose (deme count by
default; population size, mutation rate, migration rate, and locus
length are the other options), and times how long each of the three
engines (`"lineal"`, `"generational"`, `"generational-vector"`) takes.
Every number it reports is normalized against `"lineal"` run with a
single lineage at that same setting -- so instead of raw seconds (which
depend entirely on whatever computer happens to run it), you get "how
many single-lineage-run-equivalents did this batch cost," a number
that means roughly the same thing on a different machine, or a year
from now on faster hardware.

**Why it matters:** This project offers a choice between three engines
that trade determinism, speed, and memory use against each other in
ways that are not obvious from reading the code alone (see the
[developer guide](../../doc/developer.md) for what each one actually
does differently) -- and the right choice genuinely depends on how big
a simulation you are running, not on a fixed rule. `engine_backend=
"auto"` already picks between two of them using a threshold
(`auto_vector_min_d`) that was measured, once, on one specific machine;
this script is how that measurement gets made in the first place, and
how it gets checked again later if the underlying code changes in a way
that might move it (this project's own history already has one example:
a correctness fix to how randomness is drawn turned out to also change
how much benefit multi-threading provides -- exactly the kind of thing
this script is meant to catch, by measuring directly rather than
trusting an old number).

**When to run it:** Whenever you want real evidence for how the engines
compare at a scale you actually care about, or whenever you suspect a
recent code change might have shifted that balance. Not run
automatically by any test, build, or release step -- like
`calibrate-statistical-bands` below, a benchmark that ran on every push
would make timing-sensitive CI results depend on how busy the CI
runner happened to be that day, not on the code. Run it on an otherwise
idle machine for a trustworthy result (a browser doing something in the
background, another simulation running at the same time, or a laptop on
battery-saving mode can all skew the numbers).

**Usage:**

```console
dev/bin/benchmark-engines --sweep d --values 4,10,20,35,50,80,120 \
    --replicates 16 --generations 100 --trials 3 \
    --output /tmp/benchmark-d-sweep.json
```

`--sweep` picks which setting varies (`d`, `N`, `mu`, `m`, or
`loci-length`); `--values` is the comma-separated list of values to try
for it. `--replicates` and `--generations` size each individual
simulation run; `--trials` is how many times each one is repeated (the
*middle* value across those repeats is what gets reported, which is
more resistant to one unlucky, unrelated slowdown than an average
would be). `--output` is optional -- without it, you get the printed
table only; with it, you also get a JSON file recording the full
result, including which computer produced it and when, so a later
comparison can tell whether two runs are actually measuring the same
thing. That file is not meant to be committed to this repository —
timing numbers are only meaningful for the machine that produced them;
a finding worth keeping belongs in prose (a design document, an issue,
a commit message), the way every earlier benchmark sweep in this
project's own history was recorded.

## `calibrate-statistical-bands`

**What it does:** Runs each of the project's three scientific validation
scenarios (see [the worked examples](../../doc/examples/README.md) for
what these scenarios actually model biologically) many times over with
different random seeds, and measures how much the result naturally
varies from one run to the next just by chance. It writes those
measurements to a JSON file
(`test/validation/statistical-calibration-evidence.json`) that is
committed to the repository.

**Why it matters:** The automated tests that check the simulator's
output against these three published scenarios cannot demand an *exact*
match -- the model is stochastic (randomness is part of what it
simulates), so even a perfectly correct implementation gives a slightly
different answer every time it runs with a different seed, the same way
flipping a fair coin 100 times rarely lands on exactly 50 heads even
though 50 is the "expected" answer. The tests instead check that the
result falls within a tolerance band wide enough to absorb that natural
randomness, but no wider -- a band that is too narrow makes the test
fail on perfectly good runs (a "flaky" test, which this project treats
as seriously as a wrong answer, never as something to shrug off and
re-run); a band that is too wide would let a genuinely broken simulator
sneak through undetected. Before this script existed, that band's width
was set once, by hand, from a one-off measurement whose own method,
inputs, and results were never written down or kept -- meaning nobody
could later check *how* the number was chosen, or reproduce the
reasoning behind it. This script makes that measurement a real,
repeatable, versioned procedure: its own output file, committed to the
repository, is the permanent record of exactly how wide each tolerance
band is and why.

**When to run it:** Essentially never, unless you deliberately change
one of the three validation scenarios' own configuration (population
size, migration rate, number of generations, and so on) enough that the
old measurement might no longer apply. This script is intentionally
*not* run automatically by any test, build, or release step -- doing so
would make the tests themselves randomly pass or fail from run to run,
exactly the "flaky test" problem the band exists to prevent. It is a
tool you run by hand, on purpose, and then commit the resulting JSON
file as its own, clearly-described change.

**Usage:**

```console
dev/bin/calibrate-statistical-bands \
    --replicates-part-vi 30 --replicates-dear-nolan-low 40 \
    --replicates-dear-nolan-high 20 \
    --output test/validation/statistical-calibration-evidence.json
```

Every flag has a sensible default (shown by `--help`); running the
command with no arguments at all uses those defaults and writes the
same committed output file. Increasing a `--replicates-*` count makes
the measurement more precise (a larger sample gives a steadier estimate
of the natural variation) at the cost of a longer run.

## `check-doc-links`

**What it does:** Reads every `.md` (Markdown) file in the repository
and checks three things: that every link to another local file actually
points at a file that exists, that every link with a `#section-name`
actually names a real heading in that file, and that every documentation
page can be reached by following links from somewhere else (a page with
nothing linking to it is flagged as an "orphan").

**Why it matters:** This project's documentation is deliberately split
across many small, cross-linked pages -- a main `README.md`, several
guides under `doc/`, the [worked examples](../../doc/examples/README.md)
-- on the idea that a reader should be able to click through to exactly
the section they need, rather than scroll one enormous page. That only
works for as long as every link is actually correct. A file gets
renamed, a heading gets reworded, a page gets moved into a new folder --
any of these silently breaks a link elsewhere that nobody thought to go
back and check, and the person who eventually clicks it (maybe months
later, maybe a future maintainer with no memory of the change at all)
just hits a dead end with no explanation. Nothing else in this project's
toolchain catches this: a Markdown formatter only checks that a file is
well-formed on its own, not that its links go anywhere real.

**When to run it:** Any time you add, rename, move, or delete a
documentation file, or retitle a heading that something else links to.
It also runs automatically as part of `./build --ci` and this project's
`pre-push` git hook, so a broken link is caught before it is ever
pushed, even if you forget to check by hand.

**Usage:**

```console
dev/bin/check-doc-links
```

Takes no arguments. It always checks the whole repository, starting
from wherever it is run. Prints one line per problem found (if any) and
exits with a non-zero status; prints a single confirmation line and
exits successfully if everything checks out.

## `compare-against-hierfstat`

**What it does:** Builds a small Docker image containing
[hierfstat](https://cran.r-project.org/package=hierfstat), an R package
from a different research group that independently implements the same
underlying model this project does (a migration matrix, random genetic
drift, and mutation, applied generation by generation). It runs hierfstat
with the same population size, migration rate, mutation rate, and deme
count as one of this project's own already-tested scenarios, reads back
the genotypes hierfstat itself simulated, and computes this project's own
statistics (`H_S`, `H_T`, `G_ST`, `D`) from them -- then prints those
numbers next to this project's own closed-form theoretical prediction for
the same scenario, so you can see how closely an entirely separate
implementation, written by someone else, in a different programming
language, agrees.

**Why it matters:** Every other check in this project's test suite
compares this project's own code against a formula, a published number,
or another part of this project's own code -- never against a genuinely
different program's own simulation of the same process. This script is
the one place that happens. Deliberate care went into *what* gets
compared: hierfstat's own built-in summary statistics use a different
convention (correcting for the fact that real studies only ever sample a
handful of individuals from a much bigger population), so this script
does not use them -- it reads hierfstat's simulated genotypes directly
and runs this project's own statistics on them instead, so both sides of
the comparison are computed the same way, and the only real difference
left is which program generated the underlying random simulation.

**When to run it:** Whenever you want independent reassurance that this
project's own simulator is not just internally consistent but agrees
with someone else's separately-written code -- there is no fixed
schedule for this. It is deliberately **not** run automatically by any
test, build, or release step: it needs Docker installed and running, the
first run takes a few minutes to build the image, and it only ever
prints a comparison for you to read, rather than a pass/fail answer (see
its own `--help` text for why a firm pass/fail tolerance does not exist
yet).

**Usage:**

```console
dev/bin/compare-against-hierfstat \
    --population-size 100 --m 0.01 --mu 0.005 --d 4 \
    --generations 4000 --nbal 99 --nbloc 200 --seed 900001
```

Every flag has a sensible default matching one of this project's own
already-tested scenarios; running the command with no arguments at all
uses those defaults. Pass `--skip-build` on a second run to reuse the
already-built image instead of checking for updates to it.

## `extract-release-notes`

**What it does:** Reads `CHANGELOG.md` and prints just the section for
one specific version -- the bullet points describing what changed in
that release -- with the surrounding heading and everything else
stripped away.

**Why it matters:** `CHANGELOG.md` is the project's single, ongoing,
hand-written record of what changed in each release; it is kept up to
date as part of normal work, not written specially at release time. When
a new version is actually published as a GitHub Release, that release
needs its own description text for people to read on GitHub. Rather than
maintaining that description as a second piece of writing (which would
be tedious to keep in sync, and an easy way for the two texts to quietly
drift apart from each other over time), the automated release process
runs this script to lift the relevant section straight out of the
changelog. There should never be a reason to describe the same release
twice, once by hand and once for GitHub.

**When to run it:** In ordinary use, never by hand -- it runs
automatically as part of the release workflow whenever a version tag
(like `v1.2.0`) is pushed. You would only run it yourself to preview
what a release's notes will look like before actually publishing it.

**Usage:**

```console
dev/bin/extract-release-notes v1.2.0
dev/bin/extract-release-notes 1.2.0 --changelog path/to/CHANGELOG.md
```

The version may be written with or without a leading `v` (a real release
tag looks like `v1.2.0`; `CHANGELOG.md`'s own heading for it is written
as `## [1.2.0]`, without the `v` -- either spelling given here works).
`--changelog` is only useful for testing against a copy of the file
somewhere other than the repository root; day to day, the default
(`CHANGELOG.md` in the current directory) is always what you want.

## `generate-api-docs`

**What it does:** Rebuilds `src/fim/API.md`, the committed reference
that lists every public part of the simulator's own code (`fim` Python
package) -- every module, class, and function -- together with its
docstring (the explanation written directly above it in the source
code).

**Why it matters:** Anyone who wants to know exactly what a function
does or what values it expects should be able to look that up on GitHub
without installing Python or opening a code editor -- that is what
`src/fim/API.md` is for. Its content comes directly from the same
docstrings a developer reads in the source file itself, which is the
whole point: the reference can never say something *different* from
what the code's own documentation already says, because it is not a
separate description written by hand -- it is that same description,
copied out automatically. It can only ever become *stale* (showing an
older version of a docstring that has since been improved), which this
script fixes immediately by simply being re-run. See "[Why generated
files exist at all](#why-generated-files-exist-at-all)" above for why
`src/fim/API.md` itself should never be hand-edited even to fix a small
mistake in it -- the real fix is always to correct the docstring in the
actual source file and regenerate.

**When to run it:** You rarely need to remember this yourself. The
`pre-commit` git hook already runs it automatically -- and re-includes
its output in your commit -- whenever a change you are committing
touches any file under `src/fim/`. `pre-push` and `./build --ci`
separately double-check that the committed file still matches what
running this script again right now would produce, in case a commit was
somehow made without that hook running (for instance, from a tool that
bypasses Git hooks entirely).

**Usage:**

```console
dev/bin/generate-api-docs
dev/bin/generate-api-docs /tmp/scratch-api.md
```

With no arguments, overwrites the real, committed `src/fim/API.md`. A
path argument writes there instead, without touching the committed
file -- used by the freshness check described above, which renders into
a temporary location and compares it against what is actually
committed, rather than overwriting the real file just to check it.

## `generate-help-html`

**What it does:** Converts the two operational guides
`doc/usage.md`/`doc/configuration.md` into the HTML shown by the desktop
app's (`fim-gui`) own built-in Help screen, and writes that HTML into
`src/fim/gui/webui/help/`.

**Why it matters:** The Help screen exists so you can look up how a
command or a configuration field works without leaving the app or
needing an internet connection -- useful in the field, or anywhere a
connection is not reliable. Its text is not written separately from the
guides you would read on GitHub; it is produced directly from the exact
same `doc/usage.md`/`doc/configuration.md` files, for the identical
reason `generate-api-docs` above reads docstrings instead of a
hand-written summary: one source of truth that cannot say two different
things in two different places. See "[Why generated files exist at
all](#why-generated-files-exist-at-all)" above -- the generated HTML
under `src/fim/gui/webui/help/` should never be hand-edited; edit the
Markdown guide instead and regenerate.

**When to run it:** You rarely need to remember this yourself. The
`pre-commit` git hook already runs it automatically whenever a change
you are committing touches `doc/usage.md` or `doc/configuration.md`.
`pre-push` and `./build --ci` separately double-check that the committed
HTML still matches what running this script again right now would
produce.

**Usage:**

```console
dev/bin/generate-help-html
dev/bin/generate-help-html --output-dir /tmp/scratch-help
```

With no arguments, overwrites the real, committed HTML files under
`src/fim/gui/webui/help/`. `--output-dir PATH` writes to a scratch
directory of your choosing instead, without touching the committed
files -- used by the freshness check described above.

## `validate-repository`

**What it does:** Runs four separate checking tools over the whole
repository in one command: `shellcheck` (checks every shell script for
real bugs), `yamllint` (checks the GitHub Actions/Dependabot
configuration files for structural mistakes), `markdownlint` (checks
every Markdown file for consistent formatting), and `gitleaks` (scans
everything for text that looks like an accidentally-committed password,
API key, or other credential).

**Why it matters:** These four checks catch a different kind of mistake
than the test suite does -- not "does the simulator compute the right
answer," but "is the surrounding project itself sound": a shell script
with a bug that only shows up on someone else's machine, a broken
continuous-integration configuration file, an inconsistently formatted
guide, or -- most seriously -- a real credential accidentally left in a
file that gets committed and pushed to a public repository, where it
would then need to be treated as compromised and rotated immediately.
This matters for the project's overall trustworthiness even if you never
personally touch a shell script or a CI configuration file yourself. You
do not need any of the four tools installed on your own computer to run
this: each one is a specific, version-pinned tool that runs inside
Docker (see the [maintainer runbook](../../CONTRIBUTING.md)'s own note
on the Docker-backed wrappers in `bin/`), the same exact version every
time, on every machine and in the automated CI checks alike -- so a
result never depends on which version of a tool someone happened to
have lying around.

**When to run it:** Before a release, or any time you want reassurance
that the whole repository -- not just the specific files you changed --
is in good shape. It also runs as part of `./build --ci`.

**Usage:**

```console
dev/bin/validate-repository
```

Takes no arguments. Requires Docker to be installed and running --
each tool's own pinned image is downloaded automatically the first time
it is needed, then reused after that.

## Related documents

- [Maintainer runbook](../../CONTRIBUTING.md)
- [Repository-managed hooks](../git-hooks/README.md)
- [Developer and extension guide](../../doc/developer.md)
- [Source-tree orientation](../../src/README.md)
- [Project overview](../../README.md)
