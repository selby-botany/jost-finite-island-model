# Source tree

The `fim` package implements a migration → drift → mutation pipeline,
statistics independent of the engine, incremental persistence, convergence
monitoring, headless plots, and a thin command-line boundary.

## Module map

- [`model`](fim/model/) — validated model values, initialization, and operators
- [`statistics`](fim/statistics/) — diversity and differentiation formulas, and
  across-replicate confidence intervals
- [`config`](fim/config/) — named policy constants and numerical guards, with a
  table ([`config/README`](fim/config/README.md)) and a test that keeps magic numbers out of
  the other modules
- [`convergence`](fim/convergence/) — stability and confidence-interval criteria, the
  monitor, and stop outcomes
- [`persistence`](fim/persistence/) — trajectory protocol, binary trajectory log, manifests
- [`engine`](fim/engine/) — seeded run loops and backend selection
- [`viz`](fim/viz/) — frequency scatters and diagnostic plots
- [`cli`](fim/cli.py) — YAML and command dispatch
- [`gui`](fim/gui/) — desktop bridge, background run coordination, and web assets
- [`spec`](fim/spec/) — structured public run specifications
- [`run_service`](fim/run_service.py) — saved-run lifecycle coordination

Read the [developer guide](../doc/developer.md) before changing module
boundaries. Exact public signatures and docstrings are in the
[generated API reference](fim/API.md).
The [model design](../doc/fim-simulator-design.md) defines the update
equations; the [Python guide](../doc/python-api.md) documents supported
library workflows.

## Quality gates

From the repository root:

```console
./build
./build --ci
dev/bin/generate-api-docs
dev/bin/check-doc-links
```

[`src/fim/API.md`](./fim/API.md) is generated and committed. Do not edit it directly.
