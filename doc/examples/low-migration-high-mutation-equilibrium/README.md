# Low migration, higher mutation

## What this demonstrates

A direct answer to a specific question: for low migration, shouldn't `D`
be close to 1 rather than close to 0? [Dear-Nolan low
migration](../dear-nolan-low/README.md) uses `m = 0.0001`, which does
read as "low migration" by the usual `Nm` yardstick (`Nm = 0.01`), and
yet its `D` sits near 0.04. This example keeps `N`, `d`, and `m`
identical to Dear-Nolan low migration — the *same* low migration rate —
and changes only `mu`, from `0.000001` to `0.0006`. `D` moves from 0.04
to 0.96. Migration never changed.

The reason is that `G_ST` and `D` are controlled by different ratios
(see [Part VI of the differentiation-measures
guide](../../jost-differentiation-measures.md#equilibrium-d-controlled-by-the-mutation-ratio)):
`G_ST` by `Nm` alone, `D` by `m / (mu (d - 1))`. Dear-Nolan low migration
has `m / (mu (d - 1))` = 25 (migration 25 times the mutation input,
scaled by deme count) — not small, so `D` sits near its own floor
despite `Nm` being tiny. Here the same arithmetic gives `m / (mu (d -
1))` = 1/24 — now mutation dominates the ratio, so `D` sits near its
ceiling instead. `Nm` did not move; only the ratio that actually
controls `D` did.

## Batch result

The batch ran 10 replicates and stopped at the minimum of 10 because the
95% confidence interval for `D` (this example watches `D`, like
Dear-Nolan low migration) was already within ±0.01. Each replicate
burned in for 4,243 generations (about 5.3 relaxation times of 801 —
mutation this much stronger relaxes the model about 24 times faster
than Dear-Nolan low migration's 19,700), averaged over a
16,017-generation window, and stopped at generation 20,259. Across the
replicates `D` averages **0.9603 ± 0.0032** (95% confidence half-width,
an interval of 0.9571 to 0.9635). The closed-form equilibrium is `1 / (1
+ m / (mu (d - 1)))` = **0.96** exactly, inside the interval. `G_ST`
averages **0.8475 ± 0.0022** (0.8453 to 0.8497); the closed-form
equilibrium (the same Crow & Aoki correction Dear-Nolan low migration
uses, now with its mutation term no longer negligible) is **0.8466**,
also inside the interval.

The whole batch took about four minutes on `citrus-2` (a 22-core
development host), against Dear-Nolan low migration's roughly 3.5
minutes for a vastly longer run (2.6 million generations) — mutation
this much stronger makes the model relax, and its replicates finish,
far faster, not slower.

## Same migration, opposite reading

| | Dear-Nolan low migration | This example |
|---|---|---|
| N, d, m | 100, 5, 0.0001 | 100, 5, 0.0001 (unchanged) |
| mu | 0.000001 | 0.0006 |
| Nm | 0.01 | 0.01 (unchanged) |
| m / (mu (d - 1)) | 25 | 1/24 |
| D (measured) | 0.0390 ± 0.0031 | 0.9603 ± 0.0032 |
| G_ST (measured) | 0.9695 ± 0.0005 | 0.8475 ± 0.0022 |
| G_ST_HEDRICK (standardized) | ≈ 0.991 | 0.9929 ± 0.0013 |

`G_ST_HEDRICK` — Hedrick's `G_ST` standardized by its own `H_S`-dependent
ceiling — reads close to 1 in *both* rows, because in both cases nearly
everything the within-deme diversity allows for has, in fact,
differentiated between demes. Raw `G_ST` swings from 0.97 to 0.85
between the two rows for the reason [the differentiation-measures
guide's "ceiling argument"](../../jost-differentiation-measures.md#the-ceiling-argument)
gives: `G_ST`'s own ceiling falls as `H_S` rises, and `H_S` rose
sharply here (0.001 to 0.121) simply because mutation now replenishes
within-deme variation faster. `D` has no such ceiling tied to `H_S`, so
it reports the actual allele-sharing answer directly: these demes share
almost nothing (`D` near 1), exactly where Dear-Nolan low migration's
demes shared almost everything (`D` near 0) — under the identical
migration rate.

## Why this is not a contradiction of "low migration isolates demes"

Migration still isolates these demes exactly as much as in Dear-Nolan
low migration (`Nm` is unchanged). What changed is how much there is
*to* differentiate. With `mu` this much higher, each deme keeps
generating new alleles fast enough that different demes are almost
never left fixed for the same one by chance — the opposite of
Dear-Nolan low migration's regime, where mutation is so rare that
drift-fixed demes mostly land on the same, ancestral allele simply for
lack of an alternative (`doc/examples/dear-nolan-low/README.md`'s own
"Biological context" section). `D` measures whether demes actually
share alleles; it is not, and was never meant to be, a direct readout
of the migration rate on its own.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme (unchanged from Dear-Nolan low migration) |
| d | 5 | Number of demes (unchanged) |
| m | 0.0001 | Very low symmetric migration rate (unchanged) |
| mu | 0.0006 | 600 times Dear-Nolan low migration's mutation rate |
| loci | 30 | Independent loci of length 200 (infinite-alleles model) |
| convergence | `auto` | Burn-in derived from the model; cap set far above need (1,000,000,000) |
| seed | 20260825 | Same seed as Dear-Nolan low migration |

## Running the example

```console
fim run doc/examples/low-migration-high-mutation-equilibrium/config.yaml \
    --output results/low-migration-high-mutation-equilibrium --quiet
```

Took about four minutes on a 22-core host with `engine_backend: auto`
(ten replicates of about 20,000 generations of 30 loci each), resolving
to `generational-vector` as Dear-Nolan low migration's configuration
does (infinite-alleles mutation, `d = 5` meets `auto_vector_min_d`).

## Expected output

`summary.json` (abbreviated to the two statistics this document
discusses):

```json
{
  "D": {
    "confidence": 0.95,
    "half_width": 0.0032,
    "high": 0.9635,
    "low": 0.9571,
    "mean": 0.9603,
    "sample_count": 10,
    "sample_std": 0.0045
  },
  "G_ST": {
    "confidence": 0.95,
    "half_width": 0.0022,
    "high": 0.8497,
    "low": 0.8453,
    "mean": 0.8475,
    "sample_count": 10,
    "sample_std": 0.0031
  }
}
```

## Related literature

- Jost L (2008). GST and its relatives do not measure differentiation.
  *Molecular Ecology* 17(18):4015-4026.
  [doi:10.1111/j.1365-294X.2008.03887.x](https://doi.org/10.1111/j.1365-294X.2008.03887.x).
- Crow JF, Aoki K (1984). Group selection for a polygenic behavioral
  trait: estimating the degree of population subdivision.
  *Proceedings of the National Academy of Sciences* 81(19):6073-6077.
  [doi:10.1073/pnas.81.19.6073](https://doi.org/10.1073/pnas.81.19.6073).

See the [differentiation-measures
guide](../../jost-differentiation-measures.md), Part VI, for the
closed-form equilibria this example checks against, and
[Dear-Nolan low migration](../dear-nolan-low/README.md) for the
complementary case this example changes exactly one parameter from.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `summary.json` | The batch's mean and confidence interval for every statistic |
| `replicate-NNN/` | Each replicate's own `manifest.json`, `report.json` and trajectory |
| `manifest.json` | Batch metadata: parameters, timing, the replicates that ran |
| `README.md` | This document |
