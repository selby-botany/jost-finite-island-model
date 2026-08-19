# Using `fim`

This guide covers every version 1.0.0 command and output, not what the
simulator models or why — see [what this simulates](../README.md#what-this-simulates)
for that first. For parameter types and defaults, use the
[configuration reference](configuration.md). Return to the
[project overview](../README.md) for installation and documentation links.

## Contents

- [Create a configuration](#create-a-configuration)
- [Run a simulation](#run-a-simulation)
- [Re-analyze a trajectory](#re-analyze-a-trajectory)
- [Check for updates](#check-for-updates)
- [Global flags](#global-flags)
- [Output schemas](#output-schemas)
- [Reproduce a run](#reproduce-a-run)
- [Troubleshooting](#troubleshooting)

## Create a configuration

```console
fim init [--output PATH] [--force]
```

`fim init` writes the documented development scenario. Without `--output`, the
path is `project-root/results/example-run.yaml`. Existing files are protected
unless `--force` is present.

## Run a simulation

```console
fim run CONFIG [-o DIRECTORY | --output DIRECTORY] [--quiet]
    [--workers N] [--sequential]
```

`CONFIG` is a YAML file described in
[configuration.md](configuration.md). Without `--output`, a timestamped
directory is created under `project-root/results/`. The timestamp affects only
the folder name and manifest metadata; it never affects the trajectory,
statistics, convergence decision, or deterministic `run_id`.

`--quiet` suppresses progress and artifact-path messages. Validation errors
name the offending key or value and return status 2. A run that reaches
`max_generations` also returns status 0 because it is a valid, inspectable
non-converged result.

### Batches (`n_replicates` greater than one)

A config's `n_replicates` (see [configuration.md](configuration.md#n_replicates))
controls whether one run or a whole batch executes:

- **`n_replicates: 1`** (the default): the four-file scalar-run contract
  below, directly in the output directory.
- **`n_replicates` greater than one**: each replicate gets its own
  `replicate-NNN/` subdirectory, keeping that same four-file contract, plus
  a batch-level `manifest.json` and `summary.json` — see
  [Output schemas](#output-schemas).

Batch replicates run in parallel by default, using one worker process per
CPU (real OS processes, not threads — the per-generation state is
Python-object sparse maps that never release the GIL). `--workers N` sets
an explicit worker count; `--sequential` runs replicates one at a time.
Either way, every replicate's own trajectory, report, and statistics are
identical to running it alone with the same seed — parallelism only
changes how fast the batch completes, never what it computes.

With `replicate_tolerance` unset in the config, exactly `n_replicates`
replicates run. With it set, the batch can stop earlier, once every watched
statistic's across-replicate confidence interval has tightened enough (see
[configuration.md](configuration.md#replicate_tolerance)) — the number of
`replicate-NNN/` subdirectories written can then be less than `n_replicates`.

## Re-analyze a trajectory

```console
fim stats TRAJECTORY [--manifest PATH] [--generation N]
    [--q ORDER ...] [-o PATH | --output PATH]
```

The default manifest is `manifest.json` beside `TRAJECTORY`. The final
generation is analyzed unless `--generation` selects another persisted
generation. Repeat `--q` to evaluate several Hill-number differentiation
orders without re-running the simulation:

```console
fim stats run/trajectory.jsonl --q 0 --q 1 --q 2
```

JSON is printed to standard output. `--output` also writes the same result.

## Check for updates

```console
fim update --check
```

This explicit command queries the latest GitHub Release and prints its download
page when a newer version exists. It does not download or modify anything.
This is the application's only network path; `run`, `stats`, and `init` are
offline.

## Global flags

```console
fim --help
fim --version
```

`--version` comes from the same `version.txt` value used by packages,
manifests, and release tags.

## Output schemas

### `trajectory.jsonl`

One JSON object is appended for every nonzero frequency:

```json
{"allele_id":0,"deme":1,"frequency":0.5,"generation":0,"locus_id":1,"run_id":"run-example"}
```

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | Deterministic identity for one parameter set and seed |
| `generation` | integer | Generation, beginning at 0 |
| `deme` | integer | One-based deme number |
| `locus_id` | integer | Configured positive locus identifier |
| `allele_id` | integer | Opaque identity-only allele label |
| `frequency` | number | Positive allele frequency |

### `manifest.json`

The manifest records the complete parameter mapping, seed, software version,
UTC start/end timestamps, generation, watched statistic, and whether
convergence or the hard cap ended the run.

### `report.json`

The final report contains:

- run identity, generation, convergence flag, watched statistic, and reason;
- `H_S`, `H_T`, and the correctly partitioned `H_ST`;
- `G_ST` (`null` when all demes are fixed for the same allele);
- Jost's `D`, entropy differentiation `E_ST`, and allele-number
  differentiation `K_ST`.

Multiple loci are independent repeats; report scalars are their arithmetic
means. `D` and `K_ST` always use equal deme weighting. `deme_weighting` affects
`E_ST`.

### `scatter.png`

- `d = 2`: direct Deme 1 versus Deme 2 scatter with a diagonal reference.
- `d = 3`: direct three-dimensional scatter.
- `4 <= d <= 6`: every pairwise deme projection.
- `d > 6`: a single plot explicitly labeled as a two-dimensional PCA
  projection.

One point represents one `(locus, allele)` pair. Coincident points are enlarged
and annotated.

### Batch `summary.json` and `manifest.json`

Written only for `n_replicates` greater than one, alongside the
`replicate-NNN/` subdirectories, each of which holds the four scalar-run
files above.

`summary.json` maps each reported statistic name (`D`, `G_ST`, `E_ST`,
`K_ST`, `H_S`, `H_T`) to its across-replicate confidence interval:

```json
{
  "D": {
    "mean": 0.643,
    "half_width": 0.021,
    "low": 0.622,
    "high": 0.664,
    "sample_count": 40,
    "confidence": 0.95
  }
}
```

`G_ST` can have a smaller `sample_count` than the other statistics: a
replicate whose locus is monomorphic across every deme reports `G_ST` as
`null` in its own `report.json`, and that replicate is excluded from
`G_ST`'s interval rather than papered over with a substitute value. A
statistic left with fewer than two defined replicates is omitted from
`summary.json` entirely.

The batch's own `manifest.json` (distinct from each replicate's own) records
the batch `run_id`, every `replicate_run_ids` entry, `replicate_count`, the
shared `parameters`, batch start/end timestamps, and `software_version` —
not a per-run convergence outcome, since each replicate has its own.

## Reproduce a run

1. Copy `manifest.json`.
2. Use its `parameters` object as a new YAML config.
3. Run the same `fim` version shown in `software_version`.
4. Compare `trajectory.jsonl` and `report.json` byte for byte.

Given the same version, parameters, and seed, those files are identical.
Manifest timestamps may differ.

## Troubleshooting

- **Unknown key:** compare the named key with
  [configuration.md](configuration.md); typos are never ignored.
- **Output already exists:** select a new output directory. Existing run
  artifacts are never appended or overwritten.
- **Reached the cap:** inspect the trajectory and report, then increase
  `max_generations`, relax the tolerance, increase the window, or select
  another convergence statistic based on the study's needs.
- **Windows warning:** verify the release checksum before running the unsigned
  executable. See [SECURITY.md](../SECURITY.md).
