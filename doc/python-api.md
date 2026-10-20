# Using `fim` from Python

Run the finite island model in a Python script or notebook, save results,
and reopen them for analysis. No desktop or command-line knowledge is
required. The code blocks form a sequence checked by automated tests.

- [Using `fim` from Python](#using-fim-from-python)
  - [Describe a run](#describe-a-run)
  - [Run it and read the result](#run-it-and-read-the-result)
  - [Change how it runs, not what it computes](#change-how-it-runs-not-what-it-computes)
  - [Save a run and open it again](#save-a-run-and-open-it-again)
  - [Pause and resume a long run](#pause-and-resume-a-long-run)
  - [Use a configuration file](#use-a-configuration-file)
  - [Statistics and errors](#statistics-and-errors)
  - [What decides a run's identity](#what-decides-a-runs-identity)
  - [Where to go next](#where-to-go-next)

## Describe a run

A `RunSpec` is an immutable specification that can be printed, compared,
and reused. It groups settings into `Model` (population and processes),
`Stopping` (replicate convergence), and `Replication` (replicate count).

```python
import fim

spec = fim.RunSpec(
    model=fim.Model(
        demes=5,
        population=fim.Population(individuals=100, ploidy=1),
        migration=1e-4,
        mutation=fim.InfiniteAlleles(rate=1e-6),
    ),
    seed=1,
    stopping=fim.Stopping(burn_in=10, max_generations=60),
    replication=fim.Replication(n=3, stop_early=False),
)
```

Everything not stated has a default that matches the configuration file's:
for example, `Stopping` watches `D` and `G_ST`, and `"auto"` (the default for
`burn_in` and `max_generations`) derives the value from the model. Every field
is validated at construction; errors name the field:

```python
try:
    fim.Stopping(precision=-1.0)
except fim.ConfigurationError as error:
    print(error)
```

`Population` takes **individuals**, not gene copies, and the ploidy is always
stated: the simulation tracks `individuals * ploidy` gene copies per deme.

## Run it and read the result

`fim.simulate` runs the specification and always returns a `Result`, whether
there was one replicate or two hundred.

```python
result = fim.simulate(spec)

print(result.run_id)
print(len(result.replicates), result.batch_stop)
```

Each replicate records its seed, stop reason, final statistics, and watched
statistic histories:

```python
first = result.replicates[0]
print(first.seed, first.stop.reason, first.stop.generation)
print(first.report["D"], first.report.G_ST)
print(first.histories.names[:4])
```

`first.report` is a read-only mapping from statistic ID to value. A value of
`None` means the statistic is undefined for that replicate (`G_ST` when a
locus is monomorphic everywhere). Across replicates, `result.summary` holds a
confidence interval for each statistic:

```python
interval = result.summary["D"]
print(round(interval["mean"], 3), round(interval["half_width"], 3))
```

A single replicate is a `Result` of length one: use `result.only()` for it.

## Change how it runs, not what it computes

`Execution` chooses the engine. It never changes the numbers: the same
`RunSpec` gives the same trajectory under any backend, and the same run ID.

```python
other = fim.simulate(spec, execution=fim.Execution(backend="generational"))
assert other.run_id == result.run_id
assert other.replicates[0].report.to_dict() == first.report.to_dict()
```

The default backend is `"lineal"`, the reference implementation. The faster
`"generational"` and `"generational-vector"` backends, and `"auto"` (which
picks between them), return identical results.

## Save a run and open it again

Set `Recording.directory` to publish results in the same layout as `fim run`.
The target must not exist. Publication is atomic: the target directory
appears only after the run finishes.

```python
import tempfile
from pathlib import Path

folder = Path(tempfile.mkdtemp()) / "my-run"
fim.simulate(spec, recording=fim.Recording(directory=folder))

stored = fim.open_run(folder)
print(stored.run_id == result.run_id, len(stored.replicates))
```

A stored run gives back the same types a fresh one does. It can also read the
stored trajectory, check it against the manifest's checksum, and recompute the
statistics of any stored generation:

```python
replicate = stored.replicates[0]
replicate.verify()
rows = replicate.rows([0])
print(len(rows[0]) > 0)
print(replicate.reanalyze().report["D"] == replicate.report["D"])
```

## Pause and resume a long run

A `Workspace` stores checkpointed runs. After a pause or interruption,
resuming from the latest committed checkpoint reproduces an uninterrupted
run. The workspace path is explicit.

```python
long_spec = fim.RunSpec(
    model=spec.model,
    seed=1,
    stopping=fim.Stopping(burn_in=10, max_generations=300),
    replication=fim.Replication(n=2, stop_early=False),
)
workspace = fim.Workspace(Path(tempfile.mkdtemp()) / "results")
run = workspace.plan(long_spec)
print(run.state.value)

polls = 0


def pause_soon() -> bool:
    global polls
    polls += 1
    return polls > 2


assert run.start(stop_requested=pause_soon) is None
print(run.state.value)

resumed = run.resume()
assert resumed is not None
print(run.state.value)

uninterrupted = fim.simulate(long_spec)
assert [r.report.to_dict() for r in resumed.replicates] == [
    r.report.to_dict() for r in uninterrupted.replicates
]
```

`start` and `resume` are the same call. They return the `Result` when the run
finishes in that call, and `None` when it paused. `run.open()` reads a
completed run's files, and `workspace.handles()` lists the saved runs.

## Use a configuration file

The configuration files the command line and the desktop app use load into the
same objects. Keys are the flat names documented in
[the configuration reference](configuration.md).

```python
config_path = Path(tempfile.mkdtemp()) / "run.yaml"
config_path.write_text(
    "N: 40\n"
    "ploidy: haploid\n"
    "d: 3\n"
    "m: 0.1\n"
    "mu: 0.01\n"
    "seed: 3\n"
    "convergence_burn_in: 5\n"
    "max_generations: 40\n"
    "n_replicates: 2\n"
    "stop_batch_early: false\n",
    encoding="utf-8",
)
config = fim.load_config(config_path)
print(config.spec.model.demes, config.execution.backend)
print(fim.simulate(config).replicates[0].report["D"] is not None)
```

A `RunConfig` also carries the file's labels (`name`, `description`, `class`).
Keyword arguments to `simulate` override the execution and recording it holds.

## Statistics and errors

Statistic IDs (`"D"`, `"G_ST"`, `"H_S"`, and so on) come from a shared catalog.
Reports use its keys:

```python
from fim.statistics.catalog import report_keys

print(report_keys()[:5])
```

Expected package errors inherit from `fim.FimError`.
`ConfigurationError` means the specification or file is invalid,
`UnsupportedError` means it is valid but no available engine can run it (for
example a backend that needs `numba`, without it installed), and both are also
`ValueError`s. The package includes type information for tools such as mypy.

## What decides a run's identity

`fim.run_identity(spec)` is the run's ID. It depends on the
model, the stopping and replication settings, the analysis, and the seed. It
does not depend on the backend, JIT, worker processes, or how much of the
trajectory is stored. The concurrency setting
`Replication(max_concurrent=...)` can change adaptive-batch results,
because the first wave of replicates sets the averaging window of the later
ones, so it is part of the identity.

```python
assert fim.run_identity(spec) == result.run_id
```

Replicate `i` of a batch uses child `i` of `numpy.random.SeedSequence(seed)`,
so two batches with nearby seeds share no replicate. A single run uses `seed`
itself.

## Where to go next

- [The configuration reference](configuration.md) explains every setting.
- [The statistics reference](statistics.md) links definitions and
  interpretation to API functions and implementation source.
- [The engine API](fim-simulator-functional-api.md) lists the lower-level
  modules (`fim.statistics`, `fim.model`, `fim.persistence`, `fim.viz`).
- [The saved-run lifecycle](run-lifecycle.md) explains planned, paused, and
  completed runs.
