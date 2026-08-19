# Source tree

The `fim` package implements a pure migration → mutation → drift pipeline,
statistics independent of the engine, incremental persistence, convergence
monitoring, headless plots, and a thin command-line boundary.

## Module map

- `fim/model/` — validated model values, initialization, and operators
- `fim/statistics/` — diversity and differentiation formulas, and
  across-replicate confidence intervals
- `fim/convergence/` — stability and confidence-interval criteria, the
  monitor, and stop outcomes
- `fim/persistence/` — trajectory protocol, JSON Lines store, manifests
- `fim/engine.py` — deterministic run loop
- `fim/viz/` — botanist-facing and diagnostic plots
- `fim/cli.py` — YAML and command dispatch

Read the [developer guide](../doc/developer.md) before changing module
boundaries. Exact public signatures and docstrings are in the
[generated API reference](fim/API.md).

## Quality gates

From the repository root:

```console
./build
./build --ci
dev/bin/generate-api-docs
dev/bin/check-doc-links
```

`src/fim/API.md` is generated and committed. Do not edit it directly.
