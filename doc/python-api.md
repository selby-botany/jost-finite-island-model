# Using `fim` from Python

This guide is for a researcher who writes Python and wants to run the finite
island model in a script or a notebook, save the results, and read them back.
It needs no knowledge of the desktop app or the command line. Every code block
below runs as written (a test runs them in order), so you can copy them.

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

A run is described by a `RunSpec`: a frozen (immutable) value you build once
and can print, compare, and reuse. It is grouped by what each part decides:
the `Model` (what is simulated), `Stopping` (when a replicate counts as
converged), and `Replication` (how many independent replicates).

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
is checked when you build the object, and a mistake names its field:

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

Each replicate has its own seed, why it stopped, its final statistics, and the
history of the statistics it watched:

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

Give `simulate` a directory in `Recording` and it publishes the run there, in
the same layout the `fim run` command writes. The directory must not exist
yet; a run is published whole or not at all.

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

A `Workspace` is a results directory of runs that are checkpointed as they go.
A run survives a closed laptop or a crash and resumes to exactly the numbers
an uninterrupted run would have given. The path is always explicit.

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

Statistic IDs (`"D"`, `"G_ST"`, `"H_S"`, and so on) come from one catalog,
and a report holds exactly those the catalog lists:

```python
from fim.statistics.catalog import report_keys

print(report_keys()[:5])
```

Every exception the package raises on purpose is a `fim.FimError`.
`ConfigurationError` means the specification or file is invalid,
`UnsupportedError` means it is valid but no available engine can run it (for
example a backend that needs `numba`, without it installed), and both are also
`ValueError`s. The package ships type information, so a type checker such as
mypy checks your calls.

## What decides a run's identity

`fim.run_identity(spec)` is the run's ID. It depends on the science only: the
model, the stopping and replication settings, the analysis, and the seed. It
does not depend on the backend, JIT, worker processes, or how much of the
trajectory is stored. One setting looks like execution but is not:
`Replication(max_concurrent=...)` changes the numbers of an adaptive batch,
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
- [The engine API](fim-simulator-functional-api.md) lists the lower-level
  modules (`fim.statistics`, `fim.model`, `fim.persistence`, `fim.viz`).
- [The saved-run lifecycle](run-lifecycle.md) explains planned, paused, and
  completed runs.
